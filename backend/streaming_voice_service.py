import asyncio
import io
from typing import Optional, AsyncGenerator, Callable
from datetime import datetime
from utils.logger import setup_logger
from config import config
import aiohttp
import json

logger = setup_logger("streaming_voice")

class StreamingVoiceService:
    """Real-time streaming voice service with transcription and TTS"""
    
    def __init__(self):
        self.deepgram_api_key = config.DEEPGRAM_API_KEY
        self.deepgram_ws_url = "wss://api.deepgram.com/v1/listen"
        
    async def transcribe_stream(
        self, 
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: Callable,
        on_final: Callable
    ):
        """Stream audio to Deepgram for real-time transcription"""
        # Deepgram supports opus codec directly without specifying encoding
        # Just specify the container format
        url = (
            f"{self.deepgram_ws_url}"
            f"?model=nova-2"
            f"&interim_results=true"
            f"&smart_format=true"
            f"&endpointing=300"
            f"&vad_events=true"
        )
        
        headers = {
            "Authorization": f"Token {self.deepgram_api_key}",
            "Content-Type": "audio/webm"  # Specify content type instead
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, headers=headers) as ws:
                    logger.info("Connected to Deepgram STT")
                    
                    stream_active = True
                    chunk_count = 0
                    
                    async def send_audio():
                        """Send audio chunks to Deepgram"""
                        nonlocal chunk_count
                        try:
                            async for chunk in audio_stream:
                                if not stream_active:
                                    break
                                if chunk:
                                    chunk_count += 1
                                    if chunk_count <= 5 or chunk_count % 50 == 0:
                                        logger.info(f"Sending chunk {chunk_count}: {len(chunk)} bytes")
                                    await ws.send_bytes(chunk)
                                    await asyncio.sleep(0.01)
                        except Exception as e:
                            logger.error(f"Error sending audio: {e}")
                        finally:
                            logger.info(f"Audio stream ended. Sent {chunk_count} chunks")
                            try:
                                await ws.send_json({"type": "CloseStream"})
                            except Exception as e:
                                logger.error(f"Error closing stream: {e}")
                    
                    async def receive_transcripts():
                        """Receive transcripts from Deepgram"""
                        try:
                            async for msg in ws:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    data = json.loads(msg.data)
                                    
                                    msg_type = data.get("type")
                                    
                                    if msg_type == "Results":
                                        channel = data.get("channel", {})
                                        alternatives = channel.get("alternatives", [])
                                        
                                        if alternatives:
                                            transcript = alternatives[0]
                                            text = transcript.get("transcript", "")
                                            is_final = data.get("is_final", False)
                                            
                                            if text:
                                                logger.info(f"{'FINAL' if is_final else 'PARTIAL'}: {text}")
                                                if is_final:
                                                    await on_final(text)
                                                else:
                                                    await on_transcript(text)
                                    
                                    elif msg_type == "Metadata":
                                        logger.info(f"Metadata: {data.get('transaction_key')}")
                                    
                                    elif msg_type == "UtteranceEnd":
                                        logger.info("Utterance ended")
                                    
                                    elif msg_type == "SpeechStarted":
                                        logger.info("Speech started")
                                        
                                    else:
                                        logger.debug(f"Received: {msg_type}")
                                        
                                elif msg.type == aiohttp.WSMsgType.ERROR:
                                    logger.error(f"WS protocol error: {ws.exception()}")
                                    break
                                elif msg.type == aiohttp.WSMsgType.CLOSED:
                                    logger.info("WebSocket closed by server")
                                    break
                                    
                        except Exception as e:
                            logger.error(f"Error receiving transcripts: {e}", exc_info=True)
                        finally:
                            stream_active = False
                    
                    # Run both concurrently
                    await asyncio.gather(
                        send_audio(),
                        receive_transcripts(),
                        return_exceptions=True
                    )
                    
        except aiohttp.ClientError as e:
            logger.error(f"Deepgram connection error: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Deepgram streaming error: {e}", exc_info=True)
            raise
    
    async def synthesize_stream(
        self, 
        text_stream: AsyncGenerator[str, None]
    ) -> AsyncGenerator[bytes, None]:
        """Stream text to ElevenLabs for real-time TTS"""
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}/stream"
        
        headers = {
            "xi-api-key": config.ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async for text_chunk in text_stream:
                    if not text_chunk.strip():
                        continue
                    
                    logger.info(f"Synthesizing: {text_chunk[:50]}...")
                    
                    data = {
                        "text": text_chunk,
                        "model_id": "eleven_turbo_v2_5",  # Latest turbo model
                        "voice_settings": {
                            "stability": 0.3,  # Lower for faster
                            "similarity_boost": 0.5,  # Lower for faster
                            "style": 0,
                            "use_speaker_boost": False
                        },
                        "optimize_streaming_latency": 4  # Maximum optimization
                    }
                    
                    async with session.post(url, headers=headers, json=data) as response:
                        if response.status == 200:
                            chunk_count = 0
                            async for audio_chunk in response.content.iter_chunked(1024):
                                chunk_count += 1
                                yield audio_chunk
                            logger.info(f"TTS completed: {chunk_count} audio chunks")
                        else:
                            error_text = await response.text()
                            logger.error(f"TTS error {response.status}: {error_text}")
                                
        except Exception as e:
            logger.error(f"TTS streaming error: {e}", exc_info=True)

# Global instance
streaming_voice_service = StreamingVoiceService()