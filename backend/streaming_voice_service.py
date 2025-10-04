import asyncio
import json
from typing import AsyncGenerator, Callable
from utils.logger import setup_logger
from config import config
import aiohttp
import base64

logger = setup_logger("streaming_voice")

class StreamingVoiceService:
    """Real-time streaming voice service optimized for minimum latency"""
    
    def __init__(self):
        self.deepgram_api_key = config.DEEPGRAM_API_KEY
        self.deepgram_ws_url = "wss://api.deepgram.com/v1/listen"
        self.elevenlabs_api_key = config.ELEVENLABS_API_KEY
        self.elevenlabs_voice_id = config.ELEVENLABS_VOICE_ID
        
    async def transcribe_stream(
        self, 
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: Callable,
        on_final: Callable
    ):
        """Stream audio to Deepgram for real-time transcription with VAD"""
        url = (
            f"{self.deepgram_ws_url}"
            f"?model=nova-2"
            f"&interim_results=true"
            f"&smart_format=true"
            f"&endpointing=500"
            f"&vad_events=true"
            f"&punctuate=true"
        )
        
        headers = {
            "Authorization": f"Token {self.deepgram_api_key}",
            "Content-Type": "audio/webm"
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
                                    
                                    elif msg_type == "SpeechStarted":
                                        logger.info("Speech started")
                                        
                        except Exception as e:
                            logger.error(f"Error receiving transcripts: {e}", exc_info=True)
                        finally:
                            stream_active = False
                    
                    await asyncio.gather(
                        send_audio(),
                        receive_transcripts(),
                        return_exceptions=True
                    )
                    
        except Exception as e:
            logger.error(f"Deepgram streaming error: {e}", exc_info=True)
            raise
    
    async def synthesize_stream_ws(
        self, 
        text_stream: AsyncGenerator[str, None],
        on_audio_chunk: Callable[[bytes], None]
    ):
        """
        Stream text to ElevenLabs WebSocket for MINIMUM latency TTS
        Sends text chunks as they arrive and receives audio immediately
        """
        url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{self.elevenlabs_voice_id}/stream-input"
            f"?model_id=eleven_turbo_v2_5"
            f"&output_format=mp3_22050_32"
            f"&auto_mode=true"  # Reduces latency by disabling buffers
        )
        
        headers = {
            "xi-api-key": self.elevenlabs_api_key
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, headers=headers) as ws:
                    logger.info("Connected to ElevenLabs TTS WebSocket")
                    
                    # Send initial config
                    config_msg = {
                        "text": " ",
                        "voice_settings": {
                            "stability": 0.3,
                            "similarity_boost": 0.5,
                            "speed": 1.0
                        },
                        "xi_api_key": self.elevenlabs_api_key
                    }
                    await ws.send_json(config_msg)
                    logger.info("Sent initial config to ElevenLabs")
                    
                    text_sending_done = False
                    
                    async def send_text():
                        """Send text chunks as they arrive"""
                        nonlocal text_sending_done
                        try:
                            async for text_chunk in text_stream:
                                if not text_chunk.strip():
                                    continue
                                
                                logger.info(f"→ Sending to TTS: {text_chunk[:50]}...")
                                
                                # Send text chunk with trigger
                                await ws.send_json({
                                    "text": text_chunk,
                                    "try_trigger_generation": True
                                })
                                
                                # Small delay to avoid overwhelming
                                await asyncio.sleep(0.01)
                            
                            # Send empty string to signal end
                            logger.info("→ Sending EOS signal")
                            await ws.send_json({"text": ""})
                            text_sending_done = True
                            
                        except Exception as e:
                            logger.error(f"Error sending text: {e}", exc_info=True)
                            text_sending_done = True
                    
                    async def receive_audio():
                        """Receive audio chunks as they're generated"""
                        try:
                            chunk_count = 0
                            async for msg in ws:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    data = json.loads(msg.data)
                                    
                                    # Check for audio data
                                    if "audio" in data and data["audio"]:
                                        # Decode base64 audio
                                        audio_bytes = base64.b64decode(data["audio"])
                                        chunk_count += 1
                                        
                                        if chunk_count <= 3 or chunk_count % 20 == 0:
                                            logger.info(f"← Received audio chunk {chunk_count}: {len(audio_bytes)} bytes")
                                        
                                        # Send to frontend immediately
                                        await on_audio_chunk(audio_bytes)
                                    
                                    # Check if final
                                    if data.get("isFinal", False):
                                        logger.info(f"✓ TTS complete - received {chunk_count} chunks")
                                        break
                                    
                                    # Log other message types for debugging
                                    if "audio" not in data:
                                        logger.debug(f"Non-audio message: {data.keys()}")
                                
                                elif msg.type == aiohttp.WSMsgType.ERROR:
                                    logger.error(f"WS error: {ws.exception()}")
                                    break
                                elif msg.type == aiohttp.WSMsgType.CLOSED:
                                    logger.info("ElevenLabs WS closed")
                                    break
                            
                        except Exception as e:
                            logger.error(f"Error receiving audio: {e}", exc_info=True)
                    
                    # Run both concurrently
                    await asyncio.gather(
                        send_text(),
                        receive_audio(),
                        return_exceptions=True
                    )
                    
        except Exception as e:
            logger.error(f"ElevenLabs WebSocket error: {e}", exc_info=True)
            raise

# Global instance
streaming_voice_service = StreamingVoiceService()