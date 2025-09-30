import os
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv; load_dotenv(override=True) 

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
print(f"{os.getenv("GOOGLE_API_KEY")}")

# CLIENT = {
#     "installed": {
#         "client_id": os.getenv("GOOGLE_CLIENT_ID"),
#         "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
#         "redirect_uris": ["http://localhost"],
#         "auth_uri": "https://accounts.google.com/o/oauth2/auth",
#         "token_uri": "https://oauth2.googleapis.com/token",
#     }
# }



# flow = InstalledAppFlow.from_client_config(CLIENT, SCOPES)
# creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
# print("REFRESH_TOKEN:", creds.refresh_token)