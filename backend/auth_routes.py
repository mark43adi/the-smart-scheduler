from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy.orm import Session
import uuid
from datetime import datetime

from config import config
from database import User, get_db
from utils.auth import create_access_token
from utils.logger import setup_logger
from utils.auth import get_current_user

logger = setup_logger("auth")
router = APIRouter(prefix="/auth", tags=["Authentication"])

# OAuth flow
flow = Flow.from_client_config(
    {
        "web": {
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [config.GOOGLE_REDIRECT_URI],
        }
    },
    scopes=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.readonly",
    ],
    redirect_uri=config.GOOGLE_REDIRECT_URI,
)

@router.get("/login")
async def login():
    """Initiate Google OAuth login"""
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    logger.info(f"OAuth login initiated")
    return {"auth_url": authorization_url, "state": state}

@router.get("/callback")
async def auth_callback(code: str, db: Session = Depends(get_db)):
    """Handle OAuth callback"""
    try:
        # Exchange code for credentials
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        # Get user info
        service = build('oauth2', 'v2', credentials=credentials)
        user_info = service.userinfo().get().execute()
        
        email = user_info['email']
        google_id = user_info['id']
        name = user_info.get('name', '')
        picture = user_info.get('picture', '')
        
        logger.info(f"User authenticated: {email}")
        
        # Check if user exists
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            # Create new user
            user = User(
                id=str(uuid.uuid4()),
                email=email,
                google_id=google_id,
                name=name,
                picture=picture,
                is_main_account=(email == config.MAIN_CALENDAR_EMAIL),
                access_token=credentials.token,
                refresh_token=credentials.refresh_token,
                token_expiry=credentials.expiry
            )
            db.add(user)
            logger.info(f"New user created: {email}")
        else:
            # Update existing user
            user.last_login = datetime.utcnow()
            user.access_token = credentials.token
            user.refresh_token = credentials.refresh_token
            user.token_expiry = credentials.expiry
            logger.info(f"Existing user updated: {email}")
        
        db.commit()
        
        # Create JWT token
        access_token = create_access_token(data={"sub": user.id, "email": user.email})
        
        # Redirect to frontend with token
        frontend_url = f"http://localhost:3000/?token={access_token}"
        return RedirectResponse(url=frontend_url)
    
    except Exception as e:
        logger.error(f"Auth callback error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    """Get current user info"""
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "is_main_account": user.is_main_account
    }

@router.post("/logout")
async def logout():
    """Logout user"""
    return {"message": "Logged out successfully"}