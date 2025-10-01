from fastapi import FastAPI, HTTPException, Request, Depends, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Optional
import uvicorn
from datetime import datetime
import io

from agent import SmartSchedulerAgent
from config import config
from utils.logger import setup_logger
from utils.auth import get_current_user
from database import User
from auth_routes import router as auth_router
from voice_service import voice_service

# Setup logger
logger = setup_logger("api", "api.log")

# Validate configuration
try:
    config.validate()
    logger.info("Configuration validated successfully")
except Exception as e:
    logger.error(f"Configuration validation failed: {str(e)}")
    raise

# Initialize FastAPI app
app = FastAPI(
    title="Smart Scheduler AI Agent",
    description="Intelligent voice-enabled scheduling assistant with Google Calendar integration",
    version="2.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount auth routes
app.include_router(auth_router, prefix="/api")

# Initialize agent
agent = SmartSchedulerAgent()
logger.info("Smart Scheduler Agent initialized")

# Pydantic models
class ChatMessage(BaseModel):
    message: str = Field(..., description="User message", min_length=1)
    session_id: Optional[str] = Field(default=None, description="Session identifier")

class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tools_used: List[dict]
    metadata: dict
    turn_count: int
    timestamp: str
    audio_url: Optional[str] = None

class VoiceMessage(BaseModel):
    session_id: Optional[str] = None

# Exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "timestamp": datetime.now().isoformat()
        }
    )

# Routes
@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Smart Scheduler AI Agent",
        "version": "2.0.0",
        "status": "running",
        "features": ["voice", "multi-user", "oauth"],
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "current_time": datetime.now().strftime("%A, %B %d, %Y at %I:%M %p"),
        "timezone": config.DEFAULT_TIMEZONE,
        "llm_provider": config.LLM_PROVIDER,
        "voice_enabled": config.VOICE_ENABLED,
        "active_sessions": len(agent.sessions)
    }

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatMessage, user: User = Depends(get_current_user)):
    """Main chat endpoint for scheduling conversations"""
    
    # Use consistent session_id
    session_id = request.session_id or f"{user.id}_chat_session"
    
    logger.info(f"Chat request: user={user.email}, session={session_id}")
    
    try:
        result = await agent.process_message(
            session_id=session_id,
            user_message=request.message,
            user=user
        )
        
        # Generate voice response if enabled
        audio_url = None
        if config.VOICE_ENABLED:
            audio_data = await voice_service.synthesize_speech(result['reply'])
            if audio_data:
                audio_id = f"{session_id}_{result['turn_count']}"
                agent.store_audio(audio_id, audio_data)
                audio_url = f"/audio/{audio_id}"
        
        result['audio_url'] = audio_url
        
        logger.info(f"Chat completed: session={session_id}")
        return ChatResponse(**result)
    
    except Exception as e:
        logger.error(f"Error in chat: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    
    
@app.post("/voice/transcribe")
async def transcribe_voice(
    audio: UploadFile = File(...),
    session_id: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """Transcribe voice input and process as chat message"""
    
    # Generate consistent session_id if not provided
    if not session_id:
        session_id = f"{user.id}_voice_session"
    
    logger.info(f"Voice transcribe request: user={user.email}, session_id={session_id}")
    
    try:
        # Read audio data
        audio_data = await audio.read()
        
        # Transcribe
        transcript = await voice_service.transcribe_audio(audio_data, audio.content_type)
        
        if not transcript:
            raise HTTPException(status_code=400, detail="Failed to transcribe audio")
        
        logger.info(f"Transcribed: '{transcript}'")
        
        # Process message with consistent session_id
        result = await agent.process_message(
            session_id=session_id,
            user_message=transcript,
            user=user
        )
        
        # Generate voice response
        audio_url = None
        if config.VOICE_ENABLED:
            audio_response = await voice_service.synthesize_speech(result['reply'])
            if audio_response:
                audio_id = f"{session_id}_{result['turn_count']}"
                agent.store_audio(audio_id, audio_response)
                audio_url = f"/audio/{audio_id}"
        
        return {
            "transcript": transcript,
            "reply": result['reply'],
            "audio_url": audio_url,
            "session_id": session_id,  # Always return session_id
            "turn_count": result['turn_count'],
            "tools_used": result['tools_used']
        }
    
    except Exception as e:
        logger.error(f"Voice transcribe error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    
    
@app.get("/audio/{audio_id}")
async def get_audio(audio_id: str):
    """Stream audio response"""
    audio_data = agent.get_audio(audio_id)
    if not audio_data:
        raise HTTPException(status_code=404, detail="Audio not found")
    
    return StreamingResponse(
        io.BytesIO(audio_data),
        media_type="audio/mpeg"
    )

@app.get("/context")
async def get_context(user: User = Depends(get_current_user)):
    """Get current time context"""
    now = datetime.now()
    return {
        "current_time": now.isoformat(),
        "formatted_time": now.strftime("%A, %B %d, %Y at %I:%M %p %Z"),
        "day": now.strftime("%A"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "timezone": config.DEFAULT_TIMEZONE,
        "user_email": user.email,
        "is_main_account": user.is_main_account
    }

# Startup event
@app.on_event("startup")
async def startup_event():
    logger.info("=" * 80)
    logger.info("Smart Scheduler AI Agent Starting")
    logger.info(f"Version: 2.0.0")
    logger.info(f"LLM Provider: {config.LLM_PROVIDER}")
    logger.info(f"Voice Enabled: {config.VOICE_ENABLED}")
    logger.info(f"Main Calendar: {config.MAIN_CALENDAR_EMAIL}")
    logger.info(f"Timezone: {config.DEFAULT_TIMEZONE}")
    logger.info("=" * 80)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",   # listen on all interfaces
        port=8080,        # backend port (Nginx will proxy to this)
        reload=False,     # disable reload in production
        log_level="info"
    )