from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from langchain_core.tools import tool
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import pytz

from config import config
from database import User, get_db
from utils.logger import setup_logger

logger = setup_logger("tools_gcal")

# Track current user making the request (for attribution)
_user_context = None

def set_user_context(user: User):
    """Set the current user making requests for attribution"""
    global _user_context
    _user_context = {
        'user_name': user.name,
        'user_email': user.email,
        'user_id': user.id
    }
    logger.info(f"User context set: {user.email}")

def get_main_calendar_service():
    """
    ALWAYS get the main/host calendar service
    regardless of which user is logged in
    """
    db = next(get_db())
    try:
        main_user = db.query(User).filter(User.is_main_account == True).first()
        if not main_user:
            raise RuntimeError("Main calendar account not found in database")
        
        logger.info(f"Using main calendar: {main_user.email}")
        
        credentials = Credentials(
            token=main_user.access_token,
            refresh_token=main_user.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=config.GOOGLE_CLIENT_ID,
            client_secret=config.GOOGLE_CLIENT_SECRET,
            scopes=[
                'https://www.googleapis.com/auth/calendar.events',
                'https://www.googleapis.com/auth/calendar.readonly'
            ]
        )
        return build('calendar', 'v3', credentials=credentials)
    finally:
        db.close()


# ---------------- TOOLS ---------------- #

@tool
def calendar_list_upcoming(n: int = 5) -> Dict[str, Any]:
    """List upcoming events (next n events) from the MAIN shared calendar"""
    try:
        service = get_main_calendar_service()
        tz = pytz.timezone(config.DEFAULT_TIMEZONE)
        now = datetime.now(tz)

        logger.info(f"Fetching {n} upcoming events from main calendar")

        events_result = service.events().list(
            calendarId='primary',
            timeMin=now.isoformat(),
            maxResults=n,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        if not events:
            return {"message": f"No upcoming events"}

        formatted = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            formatted.append({
                "title": event.get('summary', 'Untitled'),
                "start": start_dt.strftime("%A, %B %d at %I:%M %p"),
                "end": end_dt.strftime("%I:%M %p"),
                "attendees": [a['email'] for a in event.get('attendees', [])]
            })

        return {"events": formatted, "count": len(formatted)}

    except Exception as e:
        logger.error(f"Error listing upcoming events: {str(e)}", exc_info=True)
        return {"error": str(e)}


@tool
def calendar_list_events_by_date(date: str) -> Dict[str, Any]:
    """List all events for a given date (expects YYYY-MM-DD)"""
    try:
        service = get_main_calendar_service()
        tz = pytz.timezone(config.DEFAULT_TIMEZONE)

        parsed = datetime.strptime(date, "%Y-%m-%d")
        parsed = tz.localize(parsed)

        start_dt = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = parsed.replace(hour=23, minute=59, second=59, microsecond=0)

        logger.info(f"Fetching events for {date} from main calendar")

        events_result = service.events().list(
            calendarId='primary',
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        if not events:
            return {"date": date, "events": [], "message": f"No events scheduled"}

        formatted = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            formatted.append({
                "title": event.get('summary', 'Untitled'),
                "start": start_dt.strftime("%I:%M %p"),
                "end": end_dt.strftime("%I:%M %p"),
                "attendees": [a['email'] for a in event.get('attendees', [])]
            })

        return {"date": date, "events": formatted, "count": len(formatted)}

    except Exception as e:
        logger.error(f"Error listing events by date: {str(e)}", exc_info=True)
        return {"error": str(e)}


@tool
def calendar_find_event_by_title(query: str) -> Dict[str, Any]:
    """Find events by title/keyword (next 30 days)"""
    try:
        service = get_main_calendar_service()
        tz = pytz.timezone(config.DEFAULT_TIMEZONE)
        now = datetime.now(tz)

        events_result = service.events().list(
            calendarId='primary',
            timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=30)).isoformat(),
            q=query,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        if not events:
            return {"message": f"No events found matching '{query}'"}

        formatted = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            formatted.append({
                "title": event.get('summary', 'Untitled'),
                "start": start_dt.strftime("%A, %B %d at %I:%M %p"),
                "end": end_dt.strftime("%I:%M %p"),
                "attendees": [a['email'] for a in event.get('attendees', [])]
            })

        return {"events": formatted, "count": len(formatted)}

    except Exception as e:
        logger.error(f"Error finding events: {str(e)}", exc_info=True)
        return {"error": str(e)}


@tool
def calendar_today_summary() -> Dict[str, Any]:
    """Get today's schedule summary"""
    try:
        service = get_main_calendar_service()
        tz = pytz.timezone(config.DEFAULT_TIMEZONE)
        now = datetime.now(tz)

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)

        events_result = service.events().list(
            calendarId='primary',
            timeMin=today_start.isoformat(),
            timeMax=today_end.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        if not events:
            return {"date": now.strftime("%Y-%m-%d"), "message": "No events scheduled"}

        formatted = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            formatted.append({
                "title": event.get('summary', 'Untitled'),
                "start": start_dt.strftime("%I:%M %p"),
                "end": end_dt.strftime("%I:%M %p"),
                "attendees": [a['email'] for a in event.get('attendees', [])]
            })

        return {"date": now.strftime("%Y-%m-%d"), "events": formatted, "count": len(formatted)}

    except Exception as e:
        logger.error(f"Error getting today's summary: {str(e)}", exc_info=True)
        return {"error": str(e)}


@tool
def calendar_freebusy(
    duration_min: int,
    date: str,
    time_pref: Optional[str] = None
) -> Dict[str, Any]:
    """Find available slots on a given day (expects YYYY-MM-DD)"""
    try:
        service = get_main_calendar_service()
        tz = pytz.timezone(config.DEFAULT_TIMEZONE)

        search_date = datetime.strptime(date, "%Y-%m-%d")
        search_date = tz.localize(search_date)

        # Default window
        search_start = search_date.replace(hour=9, minute=0)
        search_end = search_date.replace(hour=17, minute=0)

        if time_pref:
            tl = time_pref.lower()
            if 'morning' in tl:
                search_start, search_end = search_date.replace(hour=9), search_date.replace(hour=12)
            elif 'afternoon' in tl:
                search_start, search_end = search_date.replace(hour=12), search_date.replace(hour=17)
            elif 'evening' in tl or 'after' in tl:
                search_start = search_date.replace(hour=17)
                search_end = search_date.replace(hour=21)

        body = {
            "timeMin": search_start.isoformat(),
            "timeMax": search_end.isoformat(),
            "items": [{"id": "primary"}]
        }

        freebusy = service.freebusy().query(body=body).execute()
        busy_times = freebusy['calendars']['primary'].get('busy', [])

        free_slots = []
        current = search_start

        for busy in busy_times:
            busy_start = datetime.fromisoformat(busy['start'].replace("Z", "+00:00"))
            busy_end = datetime.fromisoformat(busy['end'].replace("Z", "+00:00"))

            if current < busy_start:
                gap = (busy_start - current).total_seconds() / 60
                if gap >= duration_min:
                    free_slots.append({
                        "start": current.strftime("%I:%M %p"),
                        "end": (current + timedelta(minutes=duration_min)).strftime("%I:%M %p"),
                        "start_iso": current.isoformat(),
                        "end_iso": (current + timedelta(minutes=duration_min)).isoformat()
                    })
            current = max(current, busy_end)

        if current < search_end:
            gap = (search_end - current).total_seconds() / 60
            if gap >= duration_min:
                free_slots.append({
                    "start": current.strftime("%I:%M %p"),
                    "end": (current + timedelta(minutes=duration_min)).strftime("%I:%M %p"),
                    "start_iso": current.isoformat(),
                    "end_iso": (current + timedelta(minutes=duration_min)).isoformat()
                })

        return {"date": date, "slots": free_slots[:5], "count": len(free_slots)}

    except Exception as e:
        logger.error(f"Error finding free slots: {str(e)}", exc_info=True)
        return {"error": str(e)}


@tool
def calendar_create_event(
    title: str,
    start_iso: str,
    end_iso: str,
    attendees: List[str] = [],
    description: str = "",
    location: str = ""
) -> Dict[str, Any]:
    """Create a calendar event on the MAIN shared calendar (expects ISO times)."""
    try:
        service = get_main_calendar_service()

        requesting_user = _user_context.get('user_name', 'Unknown') if _user_context else 'Unknown'
        requesting_email = _user_context.get('user_email', '') if _user_context else ''

        full_description = f"Scheduled by: {requesting_user} ({requesting_email})"
        if description:
            full_description += f"\n\n{description}"

        event = {
            'summary': title,
            'location': location,
            'description': full_description,
            'start': {'dateTime': start_iso, 'timeZone': config.DEFAULT_TIMEZONE},
            'end': {'dateTime': end_iso, 'timeZone': config.DEFAULT_TIMEZONE},
            'attendees': [{'email': email} for email in attendees],
            'reminders': {'useDefault': True},
        }

        created = service.events().insert(
            calendarId='primary',
            body=event,
            sendUpdates='all'
        ).execute()

        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))

        return {
            "success": True,
            "event_id": created['id'],
            "title": title,
            "start": start_dt.strftime("%A, %B %d at %I:%M %p"),
            "end": end_dt.strftime("%I:%M %p"),
            "attendees": attendees,
            "scheduled_by": requesting_user
        }

    except Exception as e:
        logger.error(f"Error creating event: {str(e)}", exc_info=True)
        return {"error": str(e)}


@tool
def calendar_update_event_attendees(event_title: str, attendees: List[str]) -> Dict[str, Any]:
    """Add attendees to an existing event (expects exact title match)."""
    try:
        service = get_main_calendar_service()
        tz = pytz.timezone(config.DEFAULT_TIMEZONE)
        now = datetime.now(tz)

        events_result = service.events().list(
            calendarId='primary',
            timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=30)).isoformat(),
            q=event_title,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        if not events:
            return {"error": f"No event found with title '{event_title}'"}

        event = events[0]
        event_id = event['id']

        current_attendees = event.get('attendees', [])
        current_emails = {a['email'] for a in current_attendees}

        new_count = 0
        for email in attendees:
            if email not in current_emails:
                current_attendees.append({'email': email, 'responseStatus': 'needsAction'})
                new_count += 1

        if new_count == 0:
            return {"success": True, "message": "All specified attendees already invited"}

        event['attendees'] = current_attendees
        updated = service.events().update(
            calendarId='primary',
            eventId=event_id,
            body=event,
            sendUpdates='all'
        ).execute()

        start = updated['start'].get('dateTime', updated['start'].get('date'))
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))

        return {
            "success": True,
            "title": updated['summary'],
            "start": start_dt.strftime("%A, %B %d at %I:%M %p"),
            "attendees": [a['email'] for a in updated.get('attendees', [])],
            "added_count": new_count,
            "message": f"Added {new_count} attendee(s) to '{event_title}'"
        }

    except Exception as e:
        logger.error(f"Error updating attendees: {str(e)}", exc_info=True)
        return {"error": str(e)}
