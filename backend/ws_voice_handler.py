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
    """Handles real-time voice conversation with smart interruption"""
    
    def __init__(self, websocket: WebSocket, user: User, agent):
        self.websocket = websocket
        self.user = user
        self.agent = agent
        self.session_id = f"{user.id}_voice_{int(time.time())}"
        
        # Connection state
        self.is_connected = True
        self.is_processing = False
        
        # Speech detection state
        self.is_user_speaking = False
        self.is_ai_speaking = False
        self.last_activity = time.time()
        self.silence_warnings = 0
        
        # Audio management
        self.audio_queue = asyncio.Queue()
        self.tts_tasks = []  # Track active TTS tasks for interruption
        self.interrupt_flag = False  # Signal to stop TTS
        
        logger.info(f"VoiceStreamHandler created for user: {user.email}, session: {self.session_id}")
    
    async def handle_connection(self):
        """Main connection handler"""
        try:
            await self.websocket.accept()
            logger.info(f"WebSocket accepted for session: {self.session_id}")
            
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
        """Receive audio chunks from client"""
        try:
            while self.is_connected:
                try:
                    message = await self.websocket.receive()
                    
                    if "bytes" in message:
                        self.last_activity = time.time()
                        
                        # Mark user as speaking (STT will confirm with actual words)
                        self.is_user_speaking = True
                        
                        # Queue audio for STT processing
                        await self.audio_queue.put(message["bytes"])
                        
                    elif "text" in message:
                        await self.handle_control_message(json.loads(message["text"]))
                        
                except Exception as e:
                    if "disconnect" in str(e).lower():
                        break
                    logger.error(f"Receive error: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"Audio receiver error: {e}", exc_info=True)
        finally:
            await self.audio_queue.put(None)  # Signal end
    
    async def audio_processor(self):
        """Process audio with Deepgram STT"""
        accumulated_transcript = ""
        
        try:
            async def audio_generator():
                """Generator yielding audio chunks"""
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
            
            # Callbacks for Deepgram
            async def on_partial(text: str):
                """Handle partial transcripts (interim results)"""
                nonlocal accumulated_transcript
                accumulated_transcript = text
                
                # Send to frontend for display
                await self.send_message({
                    "type": "partial_transcript",
                    "text": text
                })
            
            async def on_final(text: str):
                """Handle final transcripts - ONLY interrupt on meaningful speech"""
                nonlocal accumulated_transcript
                
                if not text.strip():
                    return
                
                # Clean and validate the transcript
                cleaned_text = text.strip()
                word_count = len(cleaned_text.split())
                
                logger.info(f"Final transcript: {cleaned_text} (words: {word_count})")
                accumulated_transcript = ""
                self.last_activity = time.time()
                
                # CRITICAL: Only interrupt if this is MEANINGFUL speech
                # Filter out noise, single words, or very short utterances
                MIN_WORDS_FOR_INTERRUPTION = 2  # At least 2 words
                MIN_CHARS_FOR_INTERRUPTION = 6  # At least 6 characters
                
                is_meaningful_speech = (
                    word_count >= MIN_WORDS_FOR_INTERRUPTION and 
                    len(cleaned_text) >= MIN_CHARS_FOR_INTERRUPTION
                )
                
                if self.is_ai_speaking and is_meaningful_speech:
                    logger.warning(f"🛑 INTERRUPTION DETECTED: User said '{cleaned_text}' while AI speaking")
                    await self.interrupt_ai_speech()
                elif self.is_ai_speaking and not is_meaningful_speech:
                    logger.info(f"⚠️ Ignoring noise/short utterance during AI speech: '{cleaned_text}'")
                    # Don't send transcript or process - just ignore
                    return
                
                # Send confirmed transcript only if not noise
                await self.send_message({
                    "type": "transcript",
                    "text": cleaned_text
                })
                
                # Reset user speaking flag
                self.is_user_speaking = False
                
                # Process the query
                await self.process_query(cleaned_text)
                
                
            # Start Deepgram transcription stream
            await streaming_voice_service.transcribe_stream(
                audio_stream=audio_generator(),
                on_transcript=on_partial,
                on_final=on_final
            )
            
        except Exception as e:
            logger.error(f"Audio processor error: {e}", exc_info=True)
    
    async def interrupt_ai_speech(self):
        """Interrupt AI speech immediately"""
        logger.info("Interrupting AI speech...")
        
        # Set interrupt flag
        self.interrupt_flag = True
        self.is_ai_speaking = False
        
        # Cancel all active TTS tasks
        for task in self.tts_tasks:
            if not task.done():
                task.cancel()
        self.tts_tasks.clear()
        
        # Notify client
        await self.send_message({"type": "interrupted"})
        
        # Small delay to ensure cancellation
        await asyncio.sleep(0.1)
        
        # Reset interrupt flag
        self.interrupt_flag = False
    
    async def process_query(self, query: str):
        """Process query with streaming LLM → TTS → Audio"""
        if self.is_processing:
            logger.warning("Already processing a query")
            return
        
        self.is_processing = True
        
        try:
            await self.send_message({"type": "thinking"})
            
            from tools_gcal import set_user_context
            set_user_context(self.user)
            
            # Signal audio start
            await self.send_message({"type": "audio_start"})
            self.is_ai_speaking = True
            
            # Stream processing with sentence-by-sentence TTS
            full_text = ""
            sentence_buffer = ""
            sentences_sent = 0
            
            async for chunk_data in self.agent.process_message_streaming(
                session_id=self.session_id,
                user_message=query,
                user=self.user
            ):
                # Check for interruption
                if self.interrupt_flag:
                    logger.info("Stream interrupted by user speech")
                    break
                
                chunk_type = chunk_data.get('type')
                
                if chunk_type == 'content_chunk':
                    content = chunk_data.get('content', '')
                    full_text += content
                    sentence_buffer += content
                    
                    # Split on sentence boundaries (. ! ?)
                    # Keep accumulating until we have a complete sentence
                    if any(punct in sentence_buffer for punct in ['. ', '! ', '? ', '.\n', '!\n', '?\n']):
                        # Split into sentences
                        import re
                        sentences = re.split(r'([.!?]+\s*)', sentence_buffer)
                        
                        # Process complete sentences (pairs of text + punctuation)
                        for i in range(0, len(sentences) - 1, 2):
                            if i + 1 < len(sentences):
                                complete_sentence = sentences[i] + sentences[i + 1]
                                cleaned = clean_response_for_tts(complete_sentence.strip())
                                
                                if cleaned and len(cleaned) > 10:
                                    logger.info(f"Streaming sentence {sentences_sent + 1}: {cleaned[:50]}...")
                                    await self.stream_sentence_audio(cleaned)
                                    sentences_sent += 1
                        
                        # Keep any remaining incomplete sentence
                        sentence_buffer = sentences[-1] if len(sentences) % 2 == 1 else ""
                
                elif chunk_type == 'complete':
                    # Send any remaining text
                    if sentence_buffer.strip():
                        cleaned = clean_response_for_tts(sentence_buffer.strip())
                        if cleaned:
                            logger.info(f"Streaming final fragment: {cleaned[:50]}...")
                            await self.stream_sentence_audio(cleaned)
                            sentences_sent += 1
                    
                    logger.info(f"Total sentences streamed: {sentences_sent}")
                
                elif chunk_type == 'error':
                    await self.send_error("Failed to process request")
                    return
            
            # CRITICAL: Wait a moment to ensure all audio chunks are sent
            await asyncio.sleep(0.3)
            
            # Send complete text for display
            if not self.interrupt_flag:
                cleaned_full = clean_response_for_tts(full_text)
                await self.send_message({
                    "type": "response_text",
                    "text": cleaned_full
                })
                
                # Now signal audio is truly complete
                await self.send_message({"type": "audio_complete"})
            
            self.is_ai_speaking = False
            await self.send_message({"type": "ready"})
            
        except Exception as e:
            logger.error(f"Query processing error: {e}", exc_info=True)
            await self.send_error("Failed to process your request")
        finally:
            self.is_processing = False
            self.is_ai_speaking = False
    
    async def stream_sentence_audio(self, text: str):
        """Stream single sentence to TTS and immediately to client - WAITS for completion"""
        try:
            logger.info(f"🎤 Starting TTS for: {text[:60]}...")
            
            async def single_text_gen():
                yield text
            
            chunk_count = 0
            async for audio_chunk in streaming_voice_service.synthesize_stream(single_text_gen()):
                # Check for interruption
                if self.interrupt_flag or not self.is_ai_speaking or not self.is_connected:
                    logger.info(f"TTS interrupted for: {text[:30]}...")
                    break
                
                try:
                    await self.websocket.send_bytes(audio_chunk)
                    chunk_count += 1
                except Exception as e:
                    logger.error(f"Send audio error: {e}")
                    break
            
            # CRITICAL: Small delay to ensure chunks are received before next sentence
            await asyncio.sleep(0.05)
            
            logger.info(f"✅ Completed TTS - sent {chunk_count} chunks for: {text[:60]}...")
                
        except asyncio.CancelledError:
            logger.info(f"TTS task cancelled: {text[:30]}...")
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
            await self.interrupt_ai_speech()
        elif msg_type == "stop":
            await self.interrupt_ai_speech()
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
        self.is_ai_speaking = False
        self.is_processing = False
        self.is_connected = False
        
        # Cancel any remaining TTS tasks
        for task in self.tts_tasks:
            if not task.done():
                task.cancel()