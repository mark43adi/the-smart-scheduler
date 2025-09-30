import asyncio
import base64
from typing import Optional
import aiohttp
from config import config
from utils.logger import setup_logger

logger = setup_logger("voice")

class VoiceService:
    """Handle STT (Speech-to-Text) and TTS (Text-to-Speech)"""
    
    def __init__(self):
        self.deepgram_key = config.DEEPGRAM_API_KEY
        self.elevenlabs_key = config.ELEVENLABS_API_KEY
        self.enabled = config.VOICE_ENABLED
    
    async def transcribe_audio(self, audio_data: bytes, mime_type: str = "audio/webm") -> Optional[str]:
        """
        Convert speech to text using Deepgram
        
        Args:
            audio_data: Audio bytes
            mime_type: Audio MIME type
        
        Returns:
            Transcribed text or None
        """
        if not self.enabled or not self.deepgram_key:
            logger.warning("Voice service disabled or Deepgram key missing")
            return None
        
        try:
            url = "https://api.deepgram.com/v1/listen"
            headers = {
                "Authorization": f"Token {self.deepgram_key}",
                "Content-Type": mime_type
            }
            
            params = {
                "punctuate": "true",
                "model": "nova-2",
                "language": "en"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, params=params, data=audio_data) as response:
                    if response.status == 200:
                        result = await response.json()
                        transcript = result['results']['channels'][0]['alternatives'][0]['transcript']
                        logger.info(f"Transcribed: '{transcript[:50]}...'")
                        return transcript
                    else:
                        error = await response.text()
                        logger.error(f"Deepgram error: {error}")
                        return None
        
        except Exception as e:
            logger.error(f"Transcription error: {str(e)}", exc_info=True)
            return None
    
    async def synthesize_speech(self, text: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM") -> Optional[bytes]:
        """
        Convert text to speech using ElevenLabs
        
        Args:
            text: Text to synthesize
            voice_id: ElevenLabs voice ID (default: Rachel)
        
        Returns:
            Audio bytes or None
        """
        if not self.enabled or not self.elevenlabs_key:
            logger.warning("Voice service disabled or ElevenLabs key missing")
            return None
        
        try:
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": self.elevenlabs_key
            }
            
            data = {
                "text": text,
                "model_id": "eleven_monolingual_v1",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.5
                }
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status == 200:
                        audio_data = await response.read()
                        logger.info(f"Synthesized speech for text: '{text[:50]}...'")
                        return audio_data
                    else:
                        error = await response.text()
                        logger.error(f"ElevenLabs error: {error}")
                        return None
        
        except Exception as e:
            logger.error(f"TTS error: {str(e)}", exc_info=True)
            return None

voice_service = VoiceService()