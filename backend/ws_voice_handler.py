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
    
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+[\.)]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\b(\d{1,2}):00\s*(AM|PM|am|pm)\b', r'\1 \2', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[→←↑↓➜➝✓✗✘✔︎×]', '', text)
    text = re.sub(r'\(\s*\)', '', text)
    text = re.sub(r'\.{2,}', '.', text)
    text = re.sub(r',{2,}', ',', text)
    text = re.sub(r'([.!?])\s*([A-Z])', r'\1 \2', text)
    
    return text.strip()


class VoiceStreamHandler:
    """Ultra-low latency voice handler with true streaming"""
    
    def __init__(self, websocket: WebSocket, user: User, agent):
        self.websocket = websocket
        self.user = user
        self.agent = agent
        self.session_id = f"{user.id}_voice_{int(time.time())}"
        
        # Connection state
        self.is_connected = True
        self.is_processing = False
        
        # Speech state
        self.is_user_speaking = False
        self.is_ai_speaking = False
        self.last_activity = time.time()
        self.silence_warnings = 0
        
        # Audio management
        self.audio_queue = asyncio.Queue()
        self.interrupt_flag = False
        self.tts_task = None
        
        logger.info(f"VoiceStreamHandler created: {user.email}, session: {self.session_id}")
    
    async def handle_connection(self):
        """Main connection handler"""
        try:
            await self.websocket.accept()
            logger.info(f"WebSocket accepted: {self.session_id}")
            
            await self.send_message({
                "type": "connected",
                "session_id": self.session_id,
                "message": "Voice connection established"
            })
            
            tasks = [
                asyncio.create_task(self.audio_receiver()),
                asyncio.create_task(self.audio_processor()),
                asyncio.create_task(self.silence_monitor())
            ]
            
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED
            )
            
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
                        self.is_user_speaking = True
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
            await self.audio_queue.put(None)
    
    async def audio_processor(self):
        """Process audio with Deepgram STT"""
        accumulated_transcript = ""
        
        try:
            async def audio_generator():
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
            
            async def on_partial(text: str):
                """Handle partial transcripts"""
                nonlocal accumulated_transcript
                accumulated_transcript = text
                await self.send_message({
                    "type": "partial_transcript",
                    "text": text
                })
            
            async def on_final(text: str):
                """Handle final transcripts - check for meaningful interruption"""
                nonlocal accumulated_transcript
                
                if not text.strip():
                    return
                
                cleaned_text = text.strip()
                word_count = len(cleaned_text.split())
                
                logger.info(f"Final transcript: {cleaned_text} (words: {word_count})")
                accumulated_transcript = ""
                self.last_activity = time.time()
                
                # INTERRUPTION LOGIC: Only meaningful speech interrupts
                MIN_WORDS_FOR_INTERRUPTION = 2
                MIN_CHARS_FOR_INTERRUPTION = 6
                
                is_meaningful_speech = (
                    word_count >= MIN_WORDS_FOR_INTERRUPTION and 
                    len(cleaned_text) >= MIN_CHARS_FOR_INTERRUPTION
                )
                
                if self.is_ai_speaking and is_meaningful_speech:
                    logger.warning(f"🛑 INTERRUPTION: User said '{cleaned_text}' while AI speaking")
                    await self.interrupt_ai_speech()
                elif self.is_ai_speaking and not is_meaningful_speech:
                    logger.info(f"⚠️ Ignoring noise during AI speech: '{cleaned_text}'")
                    return
                
                # Send confirmed transcript
                await self.send_message({
                    "type": "transcript",
                    "text": cleaned_text
                })
                
                self.is_user_speaking = False
                await self.process_query(cleaned_text)
            
            # Start Deepgram transcription
            await streaming_voice_service.transcribe_stream(
                audio_stream=audio_generator(),
                on_transcript=on_partial,
                on_final=on_final
            )
            
        except Exception as e:
            logger.error(f"Audio processor error: {e}", exc_info=True)
    
    async def interrupt_ai_speech(self):
        """Interrupt AI speech immediately"""
        logger.info("🛑 Interrupting AI speech...")
        
        self.interrupt_flag = True
        self.is_ai_speaking = False
        
        # Cancel TTS task
        if self.tts_task and not self.tts_task.done():
            self.tts_task.cancel()
            try:
                await self.tts_task
            except asyncio.CancelledError:
                pass
        
        # Notify frontend to stop playback
        await self.send_message({"type": "interrupted"})
        
        await asyncio.sleep(0.1)
        self.interrupt_flag = False
    
    async def process_query(self, query: str):
        """Process query with TRUE streaming: LLM → ElevenLabs WS → Frontend"""
        if self.is_processing:
            logger.warning("Already processing a query")
            return
        
        self.is_processing = True
        query_start_time = time.time()
        first_audio_sent = None
        
        try:
            await self.send_message({"type": "thinking"})
            
            from tools_gcal import set_user_context
            set_user_context(self.user)
            
            # Signal audio start
            await self.send_message({"type": "audio_start"})
            self.is_ai_speaking = True
            self.interrupt_flag = False
            
            # Create text stream for TTS
            text_queue = asyncio.Queue()
            tts_complete = asyncio.Event()
            
            async def text_generator():
                """Generator that yields text chunks for TTS"""
                try:
                    while True:
                        text = await text_queue.get()
                        if text is None:  # End signal
                            break
                        if self.interrupt_flag:
                            break
                        yield text
                except Exception as e:
                    logger.error(f"Text generator error: {e}")
            
            async def stream_llm_to_tts():
                """Stream LLM tokens to TTS as they arrive"""
                try:
                    full_text = ""
                    sentence_buffer = ""
                    sentences_sent = 0
                    
                    async for chunk_data in self.agent.process_message_streaming(
                        session_id=self.session_id,
                        user_message=query,
                        user=self.user
                    ):
                        if self.interrupt_flag:
                            logger.info("LLM stream interrupted")
                            break
                        
                        chunk_type = chunk_data.get('type')
                        
                        if chunk_type == 'content_chunk':
                            content = chunk_data.get('content', '')
                            full_text += content
                            sentence_buffer += content
                            
                            # Send complete sentences to TTS immediately
                            if any(punct in sentence_buffer for punct in ['. ', '! ', '? ', '.\n', '!\n', '?\n']):
                                import re
                                sentences = re.split(r'([.!?]+\s*)', sentence_buffer)
                                
                                for i in range(0, len(sentences) - 1, 2):
                                    if i + 1 < len(sentences):
                                        complete_sentence = sentences[i] + sentences[i + 1]
                                        cleaned = clean_response_for_tts(complete_sentence.strip())
                                        
                                        if cleaned and len(cleaned) > 10:
                                            sentences_sent += 1
                                            logger.info(f"→ Streaming sentence {sentences_sent} to TTS: {cleaned[:50]}...")
                                            await text_queue.put(cleaned)
                                
                                sentence_buffer = sentences[-1] if len(sentences) % 2 == 1 else ""
                        
                        elif chunk_type == 'complete':
                            # Send remaining text
                            if sentence_buffer.strip():
                                cleaned = clean_response_for_tts(sentence_buffer.strip())
                                if cleaned:
                                    sentences_sent += 1
                                    logger.info(f"→ Streaming final sentence {sentences_sent} to TTS: {cleaned[:50]}...")
                                    await text_queue.put(cleaned)
                            
                            logger.info(f"✓ LLM complete - sent {sentences_sent} sentences to TTS")
                        
                        elif chunk_type == 'error':
                            await self.send_error("Failed to process request")
                            return
                    
                    # Signal end of text stream
                    await text_queue.put(None)
                    logger.info("→ Sent EOS to TTS queue")
                    
                    # Send full text for display
                    if not self.interrupt_flag:
                        cleaned_full = clean_response_for_tts(full_text)
                        await self.send_message({
                            "type": "response_text",
                            "text": cleaned_full
                        })
                    
                except Exception as e:
                    logger.error(f"LLM streaming error: {e}", exc_info=True)
                    await text_queue.put(None)
            
            async def stream_tts_to_frontend():
                """Stream TTS audio directly to frontend as it's generated"""
                nonlocal first_audio_sent
                try:
                    audio_chunk_count = 0
                    
                    async def on_audio_chunk(audio_bytes: bytes):
                        """Callback for each audio chunk from ElevenLabs"""
                        nonlocal first_audio_sent, audio_chunk_count
                        
                        if self.interrupt_flag or not self.is_connected:
                            return
                        
                        try:
                            # Track first audio chunk latency
                            if first_audio_sent is None:
                                first_audio_sent = time.time()
                                ttfa = (first_audio_sent - query_start_time) * 1000
                                logger.info(f"⚡ TTFA (Time To First Audio): {ttfa:.0f}ms")
                                
                                # Send latency metric to frontend
                                await self.send_message({
                                    "type": "latency_metric",
                                    "ttfa_ms": int(ttfa)
                                })
                            
                            audio_chunk_count += 1
                            await self.websocket.send_bytes(audio_bytes)
                            
                        except Exception as e:
                            logger.error(f"Send audio error: {e}")
                    
                    # Start ElevenLabs WebSocket TTS
                    await streaming_voice_service.synthesize_stream_ws(
                        text_stream=text_generator(),
                        on_audio_chunk=on_audio_chunk
                    )
                    
                    logger.info(f"✓ TTS streaming complete - sent {audio_chunk_count} audio chunks")
                    tts_complete.set()
                    
                except Exception as e:
                    logger.error(f"TTS streaming error: {e}", exc_info=True)
                    tts_complete.set()
            
            # Run LLM and TTS concurrently for TRUE streaming
            self.tts_task = asyncio.create_task(stream_tts_to_frontend())
            await asyncio.gather(
                stream_llm_to_tts(),
                self.tts_task,
                return_exceptions=True
            )
            
            # Wait for TTS to complete
            await tts_complete.wait()
            
            # Calculate total processing time
            total_time = (time.time() - query_start_time) * 1000
            logger.info(f"✓ Total query processing time: {total_time:.0f}ms")
            
            # Signal completion
            if not self.interrupt_flag:
                await self.send_message({"type": "audio_complete"})
            
            self.is_ai_speaking = False
            await self.send_message({"type": "ready"})
            
        except Exception as e:
            logger.error(f"Query processing error: {e}", exc_info=True)
            await self.send_error("Failed to process your request")
        finally:
            self.is_processing = False
            self.is_ai_speaking = False
    
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
        
        if self.tts_task and not self.tts_task.done():
            self.tts_task.cancel()