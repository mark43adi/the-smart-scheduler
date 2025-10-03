import os
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

class Config:
    """Centralized configuration"""
    
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    # LLM Configuration
    
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")  # gemini or openai
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4-turbo")
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))
    
    # Google Calendar Configuration
    CALENDAR_CREDENTIALS_FILE = os.getenv("CALENDAR_CREDENTIALS_FILE", "credentials.json")
    CALENDAR_TOKEN_FILE = os.getenv("CALENDAR_TOKEN_FILE", "token.json")
    CALENDAR_SCOPES = [
        'https://www.googleapis.com/auth/calendar',
        'https://www.googleapis.com/auth/calendar.events'
    ]
    
    # OAuth Configuration (NEW)
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8080/auth/callback")
    
    # JWT Configuration (NEW)
    JWT_SECRET = os.getenv("JWT_SECRET", "your-secret-key-change-in-production")
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRATION_HOURS = 24
    
    # Database (NEW)
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./scheduler.db")
    
    # Voice Services (NEW)
    DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")  # For STT
    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")  # For TTS
    VOICE_ENABLED = os.getenv("VOICE_ENABLED", "true").lower() == "true"
    ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM") 
    
    # Timezone & Scheduling
    DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")
    DEFAULT_WORKING_HOURS_START = int(os.getenv("DEFAULT_WORKING_HOURS_START", "9"))
    DEFAULT_WORKING_HOURS_END = int(os.getenv("DEFAULT_WORKING_HOURS_END", "18"))
    
    # Conversation
    MAX_CONVERSATION_HISTORY = int(os.getenv("MAX_CONVERSATION_HISTORY", "20"))
    
    # Server
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8080"))
    
    # Main Calendar Account (NEW)
    MAIN_CALENDAR_EMAIL = os.getenv("MAIN_CALENDAR_EMAIL")
    
    def validate(self):
        """Validate required configuration"""
        errors = []
        
        if self.LLM_PROVIDER == "gemini" and not self.GOOGLE_API_KEY:
            errors.append("GOOGLE_API_KEY required for Gemini")
        
        if self.LLM_PROVIDER == "openai" and not self.OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY required for OpenAI")
        
        if not self.GOOGLE_CLIENT_ID or not self.GOOGLE_CLIENT_SECRET:
            errors.append("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET required for OAuth")
        
        if not self.MAIN_CALENDAR_EMAIL:
            errors.append("MAIN_CALENDAR_EMAIL required")
        
        if errors:
            raise RuntimeError(f"Configuration errors: {', '.join(errors)}")

config = Config()