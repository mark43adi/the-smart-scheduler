# Smart Scheduler - AI Meeting Assistant

AI-powered scheduling assistant with voice support and shared Google Calendar integration.

---

## Project Structure
```
the-smart-scheduler/
├── backend/
│ ├── agent.py # Agent orchestration
│ ├── llm.py # LLM config
│ ├── tools_gcal.py # Calendar tools
│ ├── auth_routes.py # OAuth
│ ├── main.py # FastAPI app
│ ├── config.py # Config
│ ├── database.py # DB models
│ ├── voice_service.py # STT/TTS
│ └── requirements.txt
└── frontend/
├── index.html
├── login.html
└── css/js files
```
---

## Setup

### Install
```bash
git clone https://github.com/mark43adi/the-smart-scheduler.git
cd the-smart-scheduler/backend
pip install -r requirements.txt
Google OAuth Setup
Go to Google Cloud Console.
```
Enable Google Calendar API.

Create OAuth 2.0 Client ID (Web application).

Add redirect URI:
http://localhost:8080/auth/callback

Configure .env
```
LLM_PROVIDER=gemini
GOOGLE_API_KEY=your_gemini_key
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
GOOGLE_REDIRECT_URI=http://localhost:8080/auth/callback
MAIN_CALENDAR_EMAIL=host@gmail.com
JWT_SECRET=random_secret
DEFAULT_TIMEZONE=Asia/Kolkata
```
### Run
```
python main.py          
cd ../frontend
python -m http.server 3000
```
### Authentication
```OAuth Flow:
User logs in via Google → Backend stores tokens → Issues JWT → Frontend uses JWT for API calls.

Shared Calendar Model:

Main account (MAIN_CALENDAR_EMAIL) owns the calendar.

All users interact with this shared calendar.

Operations use main account’s credentials.
```

### Agent Logic

```
Flow:
User Message → LLM decides tools → Tools execute → LLM generates response.

Key Features:

Maintains conversation history per session.

Handles natural language dates (e.g., "tomorrow" → 2025-10-02).

Tracks user attribution for events.

Supports multi-turn conversations.
```

### Tools
```
All tools operate on the shared calendar:

calendar_list_upcoming – View next n events

calendar_list_events_by_date – View specific date

calendar_freebusy – Find available slots

calendar_create_event – Schedule meetings

calendar_update_event_attendees – Add guests

calendar_find_event_by_title – Search events

calendar_today_summary – Today’s schedule
```


