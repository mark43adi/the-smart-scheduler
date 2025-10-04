import json
from typing import Dict, Any, List, Optional, AsyncGenerator
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from llm import get_llm, SYSTEM_PROMPT
from tools_gcal import (
    calendar_list_upcoming,
    calendar_find_event_by_title,
    calendar_today_summary,
    calendar_list_events_by_date,
    calendar_freebusy,
    calendar_create_event,
    calendar_update_event_attendees,
    set_user_context
)
from utils.logger import setup_logger
from utils.time_parser import TimeParser
from config import config
from database import User
import pytz

logger = setup_logger("agent", "agent.log")

@dataclass
class ConversationState:
    """Maintains conversation state and context"""
    session_id: str
    messages: List[Any] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())
    turn_count: int = 0

    def update(self):
        """Update timestamp and turn count"""
        self.last_updated = datetime.now().isoformat()
        self.turn_count += 1

    def add_message(self, message):
        """Add message to history"""
        self.messages.append(message)
        self.update()

    def get_recent_messages(self, n: int = None) -> List:
        """Get recent messages, respecting max history"""
        limit = n or config.MAX_CONVERSATION_HISTORY
        return self.messages[-limit:]

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization"""
        return {
            "session_id": self.session_id,
            "turn_count": self.turn_count,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "message_count": len(self.messages)
        }


class SmartSchedulerAgent:
    """
    Intelligent scheduling agent for SHARED CALENDAR system
    
    All users interact with the SAME shared calendar.
    User Query -> LLM decides tools -> Execute tools -> Feed context back -> LLM generates response
    """
    
    def __init__(self):
        """Initialize the agent with tools and configuration"""
        self.sessions: Dict[str, ConversationState] = {}
        self.time_parser = TimeParser()
        self.audio_cache: Dict[str, bytes] = {}
        
        # Initialize LLM ONCE (not per request)
        self.llm = get_llm()
        logger.info("LLM initialized and cached")
        
        self.tools = [
            calendar_list_upcoming,
            calendar_find_event_by_title,
            calendar_today_summary,
            calendar_list_events_by_date,
            calendar_freebusy,
            calendar_create_event,
            calendar_update_event_attendees,
        ]
        
        self.tool_map = {tool.name: tool for tool in self.tools}
        
        logger.info(f"SmartSchedulerAgent initialized with {len(self.tools)} tools")
        
        
    def get_or_create_session(self, session_id: str) -> ConversationState:
        """Get existing session or create new one"""
        if session_id not in self.sessions:
            logger.info(f"Creating new session: {session_id}")
            state = ConversationState(session_id=session_id)
            
            # Create complete system message with context upfront
            context = self.get_current_context()
            full_prompt = f"{SYSTEM_PROMPT}\n\n{context}"
            state.add_message(SystemMessage(content=full_prompt))
            
            self.sessions[session_id] = state
        else:
            logger.debug(f"Retrieved existing session: {session_id}")
        
        return self.sessions[session_id]

    

    def get_current_context(self) -> str:
        """Get current date/time context for LLM"""
        tz = pytz.timezone(config.DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        
        # Calculate helpful reference dates
        tomorrow = now + timedelta(days=1)
        day_after = now + timedelta(days=2)
        
        # Find next Monday
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        next_monday = now + timedelta(days=days_until_monday)
        
        return f"""
    CURRENT CONTEXT (User's Timezone: {config.DEFAULT_TIMEZONE}):
    - Current Date & Time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}
    - Today: {now.strftime('%A, %Y-%m-%d')}
    - Tomorrow: {tomorrow.strftime('%A, %Y-%m-%d')}
    - Day After Tomorrow: {day_after.strftime('%A, %Y-%m-%d')}
    - Next Monday: {next_monday.strftime('%Y-%m-%d')}
    - Current Week: Week of {now.strftime('%B %d, %Y')}

    Use these dates to convert relative time expressions into YYYY-MM-DD format for tools.
    """


    def _enrich_user_message(self, user_message: str, state: ConversationState) -> str:
        """
        Extract and store information from user message
        This helps track context without explicit parsing
        """
        extracted = self.time_parser.extract_all_info(user_message)
        
        # Update metadata with extracted info
        for key, value in extracted.items():
            if value:
                if key == "attendees" and value:
                    state.metadata.setdefault("attendees", []).extend(value)
                    state.metadata["attendees"] = list(set(state.metadata["attendees"]))
                elif key in ["duration_min", "day_pref", "time_pref"]:
                    if key not in state.metadata or not state.metadata[key]:
                        state.metadata[key] = value
                        logger.info(f"Extracted {key}: {value}")
        
        return user_message

    
    async def process_message_streaming(
        self, 
        session_id: str, 
        user_message: str, 
        user: User
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream LLM responses token by token for immediate TTS
        
        Yields:
            Dict with 'type' and content:
            - {'type': 'content_chunk', 'content': str}
            - {'type': 'complete', 'session_id': str, 'turn_count': int}
            - {'type': 'error', 'error': str}
        """
        logger.info(f"[Session: {session_id}] Processing message (STREAMING MODE)")
        
        set_user_context(user)
        state = self.get_or_create_session(session_id)
        
        state.metadata['user_email'] = user.email
        state.metadata['user_name'] = user.name
        state.metadata['is_main_account'] = user.is_main_account
        
        enriched_message = self._enrich_user_message(user_message, state)
        state.add_message(HumanMessage(content=enriched_message))
        
        # Use cached LLM with tools
        llm_with_tools = self.llm.bind_tools(self.tools)
        
        try:
            recent_messages = state.get_recent_messages()
            
            # Phase 1: Stream initial LLM response
            full_content = ""
            tool_calls_accumulated = []
            
            logger.info(f"[Session: {session_id}] Starting LLM streaming...")
            
            async for chunk in llm_with_tools.astream(recent_messages):
                # Stream content tokens immediately
                if hasattr(chunk, 'content') and chunk.content:
                    full_content += chunk.content
                    yield {
                        'type': 'content_chunk',
                        'content': chunk.content
                    }
                
                # Accumulate tool calls
                if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                    tool_calls_accumulated.extend(chunk.tool_calls)
            
            logger.info(f"[Session: {session_id}] LLM streaming complete. Tools: {len(tool_calls_accumulated)}")
            
            # Phase 2: If tools were called, execute and stream final response
            if tool_calls_accumulated:
                logger.info(f"[Session: {session_id}] Executing {len(tool_calls_accumulated)} tools")
                
                # Add AI message with tool calls to history
                state.add_message(AIMessage(
                    content=full_content,
                    tool_calls=tool_calls_accumulated
                ))
                
                # Execute all tools
                for tool_call in tool_calls_accumulated:
                    tool_name = tool_call.get("name")
                    tool_args = tool_call.get("args", {})
                    tool_id = tool_call.get("id")
                    
                    try:
                        logger.info(f"[Session: {session_id}] Executing: {tool_name}")
                        result = await self._execute_tool(tool_name, tool_args)
                        
                        tool_message = ToolMessage(
                            content=json.dumps(result, default=str),
                            tool_call_id=tool_id
                        )
                        state.add_message(tool_message)
                        
                    except Exception as e:
                        logger.error(f"[Session: {session_id}] Tool {tool_name} failed: {e}")
                        error_message = ToolMessage(
                            content=json.dumps({"error": str(e)}),
                            tool_call_id=tool_id
                        )
                        state.add_message(error_message)
                
                # Stream final response with tool context
                logger.info(f"[Session: {session_id}] Streaming final response after tools...")
                recent_messages = state.get_recent_messages()
                
                async for chunk in llm_with_tools.astream(recent_messages):
                    if hasattr(chunk, 'content') and chunk.content:
                        yield {
                            'type': 'content_chunk',
                            'content': chunk.content
                        }
            
            # Signal completion
            yield {
                'type': 'complete',
                'session_id': session_id,
                'turn_count': state.turn_count
            }
            
            logger.info(f"[Session: {session_id}] Streaming complete")
        
        except Exception as e:
            logger.error(f"[Session: {session_id}] Error in streaming: {e}", exc_info=True)
            yield {
                'type': 'error',
                'error': str(e)
            }

    async def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Execute a tool and return its result"""
        tool = self.tool_map.get(tool_name)
        if not tool:
            logger.error(f"Tool not found: {tool_name}")
            raise ValueError(f"Unknown tool: {tool_name}")
        
        try:
            result = tool.invoke(args)
            return result
        except Exception as e:
            logger.error(f"Tool execution failed for {tool_name}: {e}", exc_info=True)
            raise

    # Keep legacy method for non-streaming endpoints
    async def process_message(self, session_id: str, user_message: str, user: User) -> Dict[str, Any]:
        """Legacy method - collects streaming response into single dict"""
        full_reply = ""
        
        async for chunk_data in self.process_message_streaming(session_id, user_message, user):
            if chunk_data['type'] == 'content_chunk':
                full_reply += chunk_data['content']
            elif chunk_data['type'] == 'complete':
                return {
                    "session_id": chunk_data['session_id'],
                    "reply": full_reply,
                    "tools_used": [],
                    "metadata": self.sessions[session_id].metadata,
                    "turn_count": chunk_data['turn_count'],
                    "timestamp": datetime.now().isoformat()
                }
            elif chunk_data['type'] == 'error':
                return {
                    "session_id": session_id,
                    "reply": "I apologize, but I encountered an error.",
                    "tools_used": [],
                    "error": chunk_data['error'],
                    "metadata": {},
                    "turn_count": 0,
                    "timestamp": datetime.now().isoformat()
                }

    def store_audio(self, audio_id: str, audio_data: bytes):
        """Store audio temporarily"""
        self.audio_cache[audio_id] = audio_data

    def get_audio(self, audio_id: str) -> Optional[bytes]:
        """Retrieve stored audio"""
        return self.audio_cache.get(audio_id)

    def get_session_info(self, session_id: str) -> Optional[Dict]:
        """Get session information"""
        state = self.sessions.get(session_id)
        return state.to_dict() if state else None

    def list_sessions(self) -> List[Dict]:
        """List all active sessions"""
        return [state.to_dict() for state in self.sessions.values()]

    def clear_session(self, session_id: str) -> bool:
        """Clear a session"""
        if session_id in self.sessions:
            logger.info(f"Clearing session: {session_id}")
            del self.sessions[session_id]
            return True
        return False

    def clear_all_sessions(self):
        """Clear all sessions"""
        count = len(self.sessions)
        self.sessions.clear()
        self.audio_cache.clear()
        logger.info(f"Cleared {count} sessions and audio cache")

    
    