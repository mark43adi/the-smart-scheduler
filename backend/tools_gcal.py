from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from langchain_core.tools import tool
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import pytz

from config import config
from database import User, get_db
from utils.logger import setup_logger
from utils.time_parser import TimeParser

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
        # Get the main account
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


@tool
def calendar_list_upcoming(n: int = 5) -> Dict[str, Any]:
    """
    List upcoming events from the MAIN shared calendar
    
    Args:
        n: Number of upcoming events to retrieve (default 5)
    
    Returns:
        Dictionary with list of upcoming events
    """
    try:
        service = get_main_calendar_service()
        
        now = datetime.now(pytz.timezone(config.DEFAULT_TIMEZONE))
        time_min = now.isoformat()
        time_max = (now + timedelta(days=n)).isoformat()
        
        logger.info(f"Fetching {n} upcoming events from main calendar")
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            maxResults=n,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return {"message": f"No upcoming events in the next {n} days"}
        
        formatted_events = []
        parser = TimeParser()
        
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            
            start_dt = parser.parse_iso_datetime(start)
            end_dt = parser.parse_iso_datetime(end)
            
            formatted_events.append({
                "title": event.get('summary', 'Untitled'),
                "start": start_dt.strftime("%A, %B %d at %I:%M %p"),
                "end": end_dt.strftime("%I:%M %p"),
                "attendees": [a['email'] for a in event.get('attendees', [])]
            })
        
        logger.info(f"Retrieved {len(formatted_events)} upcoming events")
        return {
            "events": formatted_events,
            "count": len(formatted_events)
        }
        
    except Exception as e:
        logger.error(f"Error listing upcoming events: {str(e)}", exc_info=True)
        return {"error": str(e)}


@tool
def calendar_list_events_by_date(date: str) -> Dict[str, Any]:
    """
    List all events for a specific date from the MAIN shared calendar
    
    Args:
        date: Date in YYYY-MM-DD format or relative terms like 'tomorrow', 'friday', 'next monday'
    
    Returns:
        Dictionary with events for that specific date
    """
    try:
        service = get_main_calendar_service()
        parser = TimeParser()
        
        # Parse the date
        parsed = parser.parse_relative_day(date)
        
        if not parsed:
            return {"error": f"Could not parse date: {date}"}
        
        # Get start and end of that day
        start_dt = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = parsed.replace(hour=23, minute=59, second=59, microsecond=0)
        
        logger.info(f"Fetching events for {parsed.strftime('%Y-%m-%d')} from main calendar")
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return {
                "date": parsed.strftime("%A, %B %d, %Y"),
                "events": [],
                "message": f"No events scheduled for {parsed.strftime('%A, %B %d')}"
            }
        
        formatted_events = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            
            start_dt = parser.parse_iso_datetime(start)
            end_dt = parser.parse_iso_datetime(end)
            
            formatted_events.append({
                "title": event.get('summary', 'Untitled'),
                "start": start_dt.strftime("%I:%M %p"),
                "end": end_dt.strftime("%I:%M %p"),
                "start_iso": start,
                "end_iso": end,
                "attendees": [a['email'] for a in event.get('attendees', [])]
            })
        
        logger.info(f"Retrieved {len(formatted_events)} events for {parsed.strftime('%A, %B %d')}")
        return {
            "date": parsed.strftime("%A, %B %d, %Y"),
            "events": formatted_events,
            "count": len(formatted_events)
        }
        
    except Exception as e:
        logger.error(f"Error listing events by date: {str(e)}", exc_info=True)
        return {"error": str(e)}


@tool
def calendar_find_event_by_title(query: str) -> Dict[str, Any]:
    """
    Find events by title/keyword from the MAIN shared calendar
    
    Args:
        query: Search keyword or title
    
    Returns:
        Dictionary with matching events
    """
    try:
        service = get_main_calendar_service()
        
        now = datetime.now(pytz.timezone(config.DEFAULT_TIMEZONE))
        time_min = now.isoformat()
        time_max = (now + timedelta(days=30)).isoformat()
        
        logger.info(f"Searching for events with query: '{query}'")
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            q=query,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return {"message": f"No events found matching '{query}'"}
        
        formatted_events = []
        parser = TimeParser()
        
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            
            start_dt = parser.parse_iso_datetime(start)
            end_dt = parser.parse_iso_datetime(end)
            
            formatted_events.append({
                "title": event.get('summary', 'Untitled'),
                "start": start_dt.strftime("%A, %B %d at %I:%M %p"),
                "end": end_dt.strftime("%I:%M %p"),
                "attendees": [a['email'] for a in event.get('attendees', [])]
            })
        
        logger.info(f"Found {len(formatted_events)} events matching '{query}'")
        return {
            "events": formatted_events,
            "count": len(formatted_events)
        }
        
    except Exception as e:
        logger.error(f"Error finding events: {str(e)}", exc_info=True)
        return {"error": str(e)}


@tool
def calendar_today_summary() -> Dict[str, Any]:
    """
    Get summary of today's events from the MAIN shared calendar
    
    Returns:
        Dictionary with today's schedule summary
    """
    try:
        service = get_main_calendar_service()
        
        tz = pytz.timezone(config.DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        
        # Today's start and end
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        
        logger.info("Fetching today's events from main calendar")
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=today_start.isoformat(),
            timeMax=today_end.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return {
                "date": now.strftime("%A, %B %d, %Y"),
                "message": "No events scheduled for today"
            }
        
        formatted_events = []
        parser = TimeParser()
        
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            
            start_dt = parser.parse_iso_datetime(start)
            end_dt = parser.parse_iso_datetime(end)
            
            formatted_events.append({
                "title": event.get('summary', 'Untitled'),
                "start": start_dt.strftime("%I:%M %p"),
                "end": end_dt.strftime("%I:%M %p"),
                "attendees": [a['email'] for a in event.get('attendees', [])]
            })
        
        logger.info(f"Retrieved {len(formatted_events)} events for today")
        return {
            "date": now.strftime("%A, %B %d, %Y"),
            "events": formatted_events,
            "count": len(formatted_events)
        }
        
    except Exception as e:
        logger.error(f"Error getting today's summary: {str(e)}", exc_info=True)
        return {"error": str(e)}


@tool
def calendar_freebusy(
    duration_min: int,
    day_pref: Optional[str] = None,
    time_pref: Optional[str] = None,
    attendees: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Find available time slots on the MAIN shared calendar
    
    Args:
        duration_min: Meeting duration in minutes (REQUIRED)
        day_pref: Preferred day (e.g., 'tomorrow', 'friday', '2025-10-05')
        time_pref: Preferred time of day ('morning', 'afternoon', 'evening')
        attendees: List of attendee email addresses
    
    Returns:
        Dictionary with available time slots
    """
    try:
        service = get_main_calendar_service()
        parser = TimeParser()
        tz = pytz.timezone(config.DEFAULT_TIMEZONE)
        
        # Parse day preference
        if day_pref:
            search_date = parser.parse_relative_day(day_pref)
            if not search_date:
                return {"error": f"Could not parse day: {day_pref}"}
        else:
            search_date = datetime.now(tz) + timedelta(days=1)
        
        # Set search window
        search_start = search_date.replace(hour=9, minute=0, second=0, microsecond=0)
        search_end = search_date.replace(hour=17, minute=0, second=0, microsecond=0)
        
        # Adjust for time preference
        if time_pref:
            if 'morning' in time_pref.lower():
                search_start = search_date.replace(hour=9, minute=0)
                search_end = search_date.replace(hour=12, minute=0)
            elif 'afternoon' in time_pref.lower():
                search_start = search_date.replace(hour=12, minute=0)
                search_end = search_date.replace(hour=17, minute=0)
            elif 'evening' in time_pref.lower():
                search_start = search_date.replace(hour=17, minute=0)
                search_end = search_date.replace(hour=20, minute=0)
        
        logger.info(f"Finding free slots: duration={duration_min}min, day={day_pref}, time={time_pref}")
        
        # Get freebusy info
        body = {
            "timeMin": search_start.isoformat(),
            "timeMax": search_end.isoformat(),
            "items": [{"id": "primary"}]
        }
        
        freebusy_result = service.freebusy().query(body=body).execute()
        busy_times = freebusy_result['calendars']['primary'].get('busy', [])
        
        # Find free slots
        free_slots = []
        current_time = search_start
        
        for busy in busy_times:
            busy_start = parser.parse_iso_datetime(busy['start'])
            busy_end = parser.parse_iso_datetime(busy['end'])
            
            # Check if there's a gap before this busy time
            if current_time < busy_start:
                gap_minutes = (busy_start - current_time).total_seconds() / 60
                if gap_minutes >= duration_min:
                    slot_end = current_time + timedelta(minutes=duration_min)
                    free_slots.append({
                        "start": current_time.strftime("%I:%M %p"),
                        "end": slot_end.strftime("%I:%M %p"),
                        "start_iso": current_time.isoformat(),
                        "end_iso": slot_end.isoformat()
                    })
            
            current_time = max(current_time, busy_end)
        
        # Check remaining time after last busy slot
        if current_time < search_end:
            gap_minutes = (search_end - current_time).total_seconds() / 60
            if gap_minutes >= duration_min:
                slot_end = current_time + timedelta(minutes=duration_min)
                free_slots.append({
                    "start": current_time.strftime("%I:%M %p"),
                    "end": slot_end.strftime("%I:%M %p"),
                    "start_iso": current_time.isoformat(),
                    "end_iso": slot_end.isoformat()
                })
        
        if not free_slots:
            return {
                "message": f"No {duration_min}-minute slots available on {search_date.strftime('%A, %B %d')}",
                "searched_date": search_date.strftime("%A, %B %d, %Y"),
                "searched_period": f"{search_start.strftime('%I:%M %p')} - {search_end.strftime('%I:%M %p')}"
            }
        
        logger.info(f"Found {len(free_slots)} available slots")
        return {
            "date": search_date.strftime("%A, %B %d, %Y"),
            "duration_minutes": duration_min,
            "slots": free_slots[:5],  # Return max 5 slots
            "count": len(free_slots)
        }
        
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
    """
    Create a calendar event on the MAIN shared calendar
    
    Args:
        title: Event title
        start_iso: Start time in ISO format
        end_iso: End time in ISO format
        attendees: List of attendee email addresses
        description: Event description
        location: Event location
    
    Returns:
        Dictionary with created event details
    """
    try:
        service = get_main_calendar_service()
        parser = TimeParser()
        
        # Add requesting user info to description
        requesting_user = _user_context.get('user_name', 'Unknown') if _user_context else 'Unknown'
        requesting_email = _user_context.get('user_email', '') if _user_context else ''
        
        full_description = f"Scheduled by: {requesting_user} ({requesting_email})"
        if description:
            full_description += f"\n\n{description}"
        
        logger.info(f"Creating event: '{title}' by {requesting_user}")
        
        event = {
            'summary': title,
            'location': location,
            'description': full_description,
            'start': {
                'dateTime': start_iso,
                'timeZone': config.DEFAULT_TIMEZONE,
            },
            'end': {
                'dateTime': end_iso,
                'timeZone': config.DEFAULT_TIMEZONE,
            },
            'attendees': [{'email': email} for email in attendees],
            'reminders': {
                'useDefault': True,
            },
        }
        
        created_event = service.events().insert(
            calendarId='primary',
            body=event,
            sendUpdates='all'
        ).execute()
        
        start_dt = parser.parse_iso_datetime(start_iso)
        end_dt = parser.parse_iso_datetime(end_iso)
        
        logger.info(f"Event created successfully: {created_event['id']}")
        return {
            "success": True,
            "event_id": created_event['id'],
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
def calendar_update_event_attendees(
    event_title: str,
    attendees: List[str]
) -> Dict[str, Any]:
    """
    Add attendees to an existing event on the MAIN shared calendar
    DO NOT create a new event - this updates an existing one
    
    Args:
        event_title: Title of the event to update
        attendees: List of email addresses to add
    
    Returns:
        Dictionary with update status
    """
    try:
        service = get_main_calendar_service()
        parser = TimeParser()
        
        # Find the event
        now = datetime.now(pytz.timezone(config.DEFAULT_TIMEZONE))
        time_min = now.isoformat()
        time_max = (now + timedelta(days=30)).isoformat()
        
        logger.info(f"Searching for event to update: '{event_title}'")
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            q=event_title,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return {"error": f"No event found with title '{event_title}'"}
        
        # Get the first matching event
        event = events[0]
        event_id = event['id']
        
        # Get current attendees
        current_attendees = event.get('attendees', [])
        current_emails = {a['email'] for a in current_attendees}
        
        # Add new attendees
        new_count = 0
        for email in attendees:
            if email not in current_emails:
                current_attendees.append({
                    'email': email,
                    'responseStatus': 'needsAction'
                })
                new_count += 1
                logger.info(f"Adding attendee: {email}")
        
        if new_count == 0:
            return {
                "success": True,
                "message": "All specified attendees are already invited",
                "title": event['summary']
            }
        
        # Update event
        event['attendees'] = current_attendees
        
        updated_event = service.events().update(
            calendarId='primary',
            eventId=event_id,
            body=event,
            sendUpdates='all'
        ).execute()
        
        start = updated_event['start'].get('dateTime', updated_event['start'].get('date'))
        start_dt = parser.parse_iso_datetime(start)
        
        logger.info(f"Event updated: {new_count} attendee(s) added")
        return {
            "success": True,
            "title": updated_event['summary'],
            "start": start_dt.strftime("%A, %B %d at %I:%M %p"),
            "attendees": [a['email'] for a in updated_event.get('attendees', [])],
            "added_count": new_count,
            "message": f"Added {new_count} attendee(s) to '{event_title}'"
        }
        
    except Exception as e:
        logger.error(f"Error updating event attendees: {str(e)}", exc_info=True)
        return {"error": str(e)}