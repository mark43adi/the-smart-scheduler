import asyncio
import json
from datetime import datetime
from typing import Optional
from fastapi import WebSocket, WebSocketDisconnect
from utils.logger import setup_logger
from streaming_voice_service import streaming_voice_service
from database import User
import time
import re

logger = setup_logger("ws_voice_handler")

def clean_response_for_tts(text: str) -> str:
    """Clean LLM response for voice synthesis"""
    if not text:
        return ""
    
    # Remove markdown formatting
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    
    # Remove headers
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    
    # Remove list markers
    text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+[\.)]\s+', '', text, flags=re.MULTILINE)
    
    # Remove links
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    
    # Remove code blocks
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)
    
    # Clean time formats
    text = re.sub(r'\b(\d{1,2}):00\s*(AM|PM|am|pm)\b', r'\1 \2', text)
    
    # Clean whitespace
    text = re.sub(r'\n\s*\n', '\n', text)
    text = re.sub(r'\s+', ' ', text)
    
    # Remove special symbols
    text = re.sub(r'[→←↑↓➜➝✓✗✘✔︎×]', '', text)
    text = re.sub(r'\(\s*\)', '', text)
    text = re.sub(r'\.{2,}', '.', text)
    text = re.sub(r',{2,}', ',', text)
    text = re.sub(r'([.!?])\s*([A-Z])', r'\1 \2', text)
    
    return text.strip()


class VoiceStreamHandler:
    """Handles real-time voice conversation via WebSocket"""
    
    def __init__(self, websocket: WebSocket, user: User, agent):
        self.websocket = websocket
        self.user = user
        self.agent = agent
        self.session_id = f"{user.id}_voice_{int(time.time())}"
        
        # State management
        self.is_speaking = False
        self.is_processing = False
        self.last_activity = time.time()
        self.silence_warnings = 0
        self.is_connected = True
        
        # Audio buffers
        self.audio_queue = asyncio.Queue()
        self.transcript_buffer = ""
        
        logger.info(f"VoiceStreamHandler created for user: {user.email}")
    
    async def handle_connection(self):
        """Main connection handler"""
        try:
            await self.websocket.accept()
            logger.info(f"WebSocket accepted for session: {self.session_id}")
            
            # Send connection confirmation
            await self.send_message({
                "type": "connected",
                "session_id": self.session_id,
                "message": "Voice connection established"
            })
            
            # Start parallel tasks
            tasks = [
                asyncio.create_task(self.audio_receiver()),
                asyncio.create_task(self.audio_processor()),
                asyncio.create_task(self.silence_monitor())
            ]
            
            # Wait for any task to complete (usually disconnect)
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Cancel remaining tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
        except WebSocketDisconnect:
            logger.info(f"Client disconnected: {self.session_id}")
        except Exception as e:
            logger.error(f"Connection error: {e}", exc_info=True)
            await self.send_error("Connection error occurred")
        finally:
            self.is_connected = False
            await self.cleanup()
    
    async def audio_receiver(self):
        """Receive audio chunks and queue them"""
        try:
            while self.is_connected:
                try:
                    message = await self.websocket.receive()
                    
                    if "bytes" in message:
                        self.last_activity = time.time()
                        # Queue audio for processing
                        await self.audio_queue.put(message["bytes"])
                        
                    elif "text" in message:
                        # Handle control messages
                        await self.handle_control_message(json.loads(message["text"]))
                        
                except Exception as e:
                    if "disconnect" in str(e).lower():
                        break
                    logger.error(f"Receive error: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"Audio receiver error: {e}", exc_info=True)
        finally:
            # Signal end of audio stream
            await self.audio_queue.put(None)
    
    async def audio_processor(self):
        """Process queued audio chunks with Deepgram"""
        audio_buffer = []
        accumulated_transcript = ""
        last_transcript_time = time.time()
        
        try:
            async def audio_generator():
                """Generator that yields audio chunks"""
                while self.is_connected:
                    try:
                        chunk = await asyncio.wait_for(
                            self.audio_queue.get(),
                            timeout=1.0
                        )
                        if chunk is None:
                            break
                        yield chunk
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        logger.error(f"Generator error: {e}")
                        break
            
            # Callbacks for transcription
            async def on_partial(text: str):
                nonlocal accumulated_transcript, last_transcript_time
                accumulated_transcript = text
                await self.send_message({
                    "type": "partial_transcript",
                    "text": text
                })
            
            async def on_final(text: str):
                nonlocal accumulated_transcript, last_transcript_time
                if text.strip():
                    logger.info(f"Final transcript: {text}")
                    accumulated_transcript = ""
                    last_transcript_time = time.time()
                    
                    # Send confirmed transcript
                    await self.send_message({
                        "type": "transcript",
                        "text": text
                    })
                    
                    # Check for interruption
                    if self.is_speaking:
                        self.is_speaking = False
                        await self.send_message({"type": "interrupted"})
                    
                    # Process with agent
                    await self.process_query(text)
            
            # Start Deepgram transcription
            await streaming_voice_service.transcribe_stream(
                audio_stream=audio_generator(),
                on_transcript=on_partial,
                on_final=on_final
            )
            
        except Exception as e:
            logger.error(f"Audio processor error: {e}", exc_info=True)
    
    # async def process_query(self, query: str):
    #     """Process query with agent and stream response"""
    #     if self.is_processing:
    #         logger.warning("Already processing a query")
    #         return
        
    #     self.is_processing = True
        
    #     try:
    #         # Notify thinking
    #         await self.send_message({"type": "thinking"})
            
    #         # Process with agent
    #         from tools_gcal import set_user_context
    #         set_user_context(self.user)
            
    #         result = await self.agent.process_message(
    #             session_id=self.session_id,
    #             user_message=query,
    #             user=self.user
    #         )
            
    #         response_text = clean_response_for_tts(result['reply'])
    #         tools_used = result.get('tools_used', [])
            
    #         # Send text response
    #         await self.send_message({
    #             "type": "response_text",
    #             "text": response_text,
    #             "tools_used": tools_used
    #         })
            
    #         # Stream audio response
    #         await self.stream_audio_response(response_text)
            
    #     except Exception as e:
    #         logger.error(f"Query processing error: {e}", exc_info=True)
    #         await self.send_error("Failed to process your request")
    #     finally:
    #         self.is_processing = False
    
    # async def stream_audio_response(self, text: str):
    #     """Stream TTS audio to client"""
    #     self.is_speaking = True
        
    #     try:
    #         async def text_generator():
    #             """Generator for streaming text to TTS"""
    #             # Split into sentences for more natural speech
    #             import re
    #             sentences = re.split(r'([.!?]+)', text)
                
    #             for i in range(0, len(sentences), 2):
    #                 if i < len(sentences):
    #                     sentence = sentences[i]
    #                     if i + 1 < len(sentences):
    #                         sentence += sentences[i + 1]
    #                     if sentence.strip():
    #                         yield sentence
    #                         await asyncio.sleep(0.01)
            
    #         # Stream audio chunks
    #         async for audio_chunk in streaming_voice_service.synthesize_stream(text_generator()):
    #             if not self.is_speaking or not self.is_connected:
    #                 break
                
    #             try:
    #                 await self.websocket.send_bytes(audio_chunk)
    #             except Exception as e:
    #                 logger.error(f"Send audio error: {e}")
    #                 break
            
    #         # Notify completion
    #         if self.is_speaking and self.is_connected:
    #             await self.send_message({"type": "audio_complete"})
    #             self.is_speaking = False
    #             await self.send_message({"type": "ready"})
                
    #     except Exception as e:
    #         logger.error(f"Audio streaming error: {e}", exc_info=True)
    #         self.is_speaking = False
    
    async def process_query(self, query: str):
        """Process with streaming audio playback"""
        if self.is_processing:
            return
        
        self.is_processing = True
        
        try:
            await self.send_message({"type": "thinking"})
            
            from tools_gcal import set_user_context
            set_user_context(self.user)
            
            # Signal audio start immediately
            await self.send_message({"type": "audio_start"})
            self.isSpeaking = True
            
            # Stream LLM + TTS concurrently
            full_text = ""
            sentence_buffer = ""
            
            async for text_chunk in self.agent.process_message(
                session_id=self.session_id,
                user_message=query,
                user=self.user
            ):
                full_text += text_chunk
                sentence_buffer += text_chunk
                
                # Send for TTS when we have a complete sentence
                if any(p in sentence_buffer for p in ['. ', '! ', '? ', '\n']):
                    cleaned = clean_response_for_tts(sentence_buffer)
                    if cleaned:
                        # Stream this sentence to TTS immediately
                        await self.stream_sentence_audio(cleaned)
                    sentence_buffer = ""
            
            # Send remaining text
            if sentence_buffer.strip():
                cleaned = clean_response_for_tts(sentence_buffer)
                if cleaned:
                    await self.stream_sentence_audio(cleaned)
            
            # Send complete text for display
            await self.send_message({
                "type": "response_text",
                "text": clean_response_for_tts(full_text)
            })
            
            await self.send_message({"type": "audio_complete"})
            self.isSpeaking = False
            await self.send_message({"type": "ready"})
            
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            await self.send_error("Failed to process request")
        finally:
            self.is_processing = False

    async def stream_sentence_audio(self, text: str):
        """Stream single sentence to TTS and immediately to client"""
        try:
            async def single_text():
                yield text
            
            async for audio_chunk in streaming_voice_service.synthesize_stream(single_text()):
                if not self.is_speaking or not self.is_connected:
                    break
                
                try:
                    # Send immediately - don't buffer
                    await self.websocket.send_bytes(audio_chunk)
                except Exception as e:
                    logger.error(f"Send error: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"Sentence audio error: {e}", exc_info=True)
    
    async def silence_monitor(self):
        """Monitor for prolonged silence"""
        try:
            while self.is_connected:
                await asyncio.sleep(30)
                
                if time.time() - self.last_activity > 30:
                    self.silence_warnings += 1
                    
                    if self.silence_warnings >= 2:
                        await self.send_message({
                            "type": "timeout",
                            "message": "Connection closed due to inactivity"
                        })
                        self.is_connected = False
                        break
                    else:
                        await self.send_message({
                            "type": "silence_warning",
                            "message": "Are you still there?"
                        })
        except Exception as e:
            logger.error(f"Silence monitor error: {e}")
    
    async def handle_control_message(self, message: dict):
        """Handle control messages from client"""
        msg_type = message.get("type")
        
        if msg_type == "ping":
            self.last_activity = time.time()
            await self.send_message({"type": "pong"})
        elif msg_type == "interrupt":
            self.is_speaking = False
        elif msg_type == "stop":
            self.is_speaking = False
            self.is_processing = False
    
    async def send_message(self, message: dict):
        """Send JSON message to client"""
        if not self.is_connected:
            return
            
        try:
            await self.websocket.send_json(message)
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            self.is_connected = False
    
    async def send_error(self, error: str):
        """Send error message"""
        await self.send_message({
            "type": "error",
            "message": error,
            "timestamp": datetime.now().isoformat()
        })
    
    async def cleanup(self):
        """Cleanup resources"""
        logger.info(f"Cleaning up session: {self.session_id}")
        self.is_speaking = False
        self.is_processing = False
        self.is_connected = False