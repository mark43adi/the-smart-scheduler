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
        self.audio_cache: Dict[str, bytes] = {}  # Store audio temporarily
        
        # All available tools
        self.tools = [
            calendar_list_upcoming,
            calendar_find_event_by_title,
            calendar_today_summary,
            calendar_list_events_by_date,        # NEW: View any day's schedule
            calendar_freebusy,
            calendar_create_event,
            calendar_update_event_attendees,      # NEW: Update existing events
        ]
        
        # Map for tool execution
        self.tool_map = {tool.name: tool for tool in self.tools}
        
        logger.info(f"SmartSchedulerAgent initialized with {len(self.tools)} tools")
        logger.info(f"Available tools: {[t.name for t in self.tools]}")

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

    # async def process_message(self, session_id: str, user_message: str, user: User) -> Dict[str, Any]:
    #     """
    #     Main conversation flow with context awareness
        
    #     Args:
    #         session_id: Unique session identifier
    #         user_message: User's message text
    #         user: User object from database
        
    #     Returns:
    #         Dictionary with reply, tools_used, and metadata
    #     """
    #     logger.info(f"[Session: {session_id}] [User: {user.email}] Processing message: '{user_message[:100]}...'")
        
    #     # CRITICAL: Set user context for tools BEFORE any tool execution
    #     set_user_context(user)
        
    #     state = self.get_or_create_session(session_id)
        
    #     # Add user info to metadata
    #     state.metadata['user_email'] = user.email
    #     state.metadata['user_name'] = user.name
    #     state.metadata['is_main_account'] = user.is_main_account

    #     # Enrich message with extracted information
    #     enriched_message = self._enrich_user_message(user_message, state)
        
    #     # Add user message to conversation history
    #     state.add_message(HumanMessage(content=enriched_message))
        
    #     # Prepare LLM with tools
    #     llm = get_llm().bind_tools(self.tools)
        
    #     try:
    #         # Step 1: LLM processes message and decides action
    #         logger.info(f"[Session: {session_id}] Invoking LLM with {len(state.messages)} messages in history")
            
    #         # Use recent messages to stay within context limits
    #         recent_messages = state.get_recent_messages()
    #         response = await llm.ainvoke(recent_messages)
            
    #         logger.info(f"[Session: {session_id}] LLM response received")
    #         logger.debug(f"[Session: {session_id}] Response content length: {len(response.content) if response.content else 0}")
            
    #         # Check if LLM wants to call tools
    #         tool_calls = getattr(response, "tool_calls", None) or []
            
    #         if tool_calls:
    #             logger.info(f"[Session: {session_id}] LLM requested {len(tool_calls)} tool call(s)")
                
    #             # Add AI message with tool calls to history
    #             state.add_message(response)
                
    #             # Step 2: Execute all requested tools
    #             tool_results = []
    #             for idx, tool_call in enumerate(tool_calls):
    #                 tool_name = tool_call.get("name")
    #                 tool_args = tool_call.get("args", {})
    #                 tool_id = tool_call.get("id")
                    
    #                 logger.info(f"[Session: {session_id}] Executing tool {idx+1}/{len(tool_calls)}: {tool_name}")
    #                 logger.debug(f"[Session: {session_id}] Tool arguments: {json.dumps(tool_args, indent=2)}")
                    
    #                 try:
    #                     # Execute the tool (user context already set)
    #                     result = await self._execute_tool(tool_name, tool_args)
                        
    #                     logger.info(f"[Session: {session_id}] Tool {tool_name} executed successfully")
    #                     logger.debug(f"[Session: {session_id}] Tool result preview: {str(result)[:300]}...")
                        
    #                     tool_results.append({
    #                         "tool": tool_name,
    #                         "args": tool_args,
    #                         "result": result,
    #                         "success": True
    #                     })
                        
    #                     # Add tool result to conversation history
    #                     tool_message = ToolMessage(
    #                         content=json.dumps(result, default=str),
    #                         tool_call_id=tool_id
    #                     )
    #                     state.add_message(tool_message)
                        
    #                 except Exception as e:
    #                     logger.error(f"[Session: {session_id}] Tool {tool_name} failed: {str(e)}", exc_info=True)
                        
    #                     error_result = {
    #                         "error": str(e),
    #                         "tool": tool_name,
    #                         "args": tool_args
    #                     }
    #                     tool_results.append({
    #                         "tool": tool_name,
    #                         "args": tool_args,
    #                         "result": error_result,
    #                         "success": False
    #                     })
                        
    #                     # Add error message to history
    #                     tool_message = ToolMessage(
    #                         content=json.dumps(error_result),
    #                         tool_call_id=tool_id
    #                     )
    #                     state.add_message(tool_message)
                
    #             # Step 3: Feed tool results back to LLM for final response
    #             logger.info(f"[Session: {session_id}] Feeding {len(tool_results)} tool results back to LLM")
                
    #             recent_messages = state.get_recent_messages()
    #             final_response = await llm.ainvoke(recent_messages)
                
    #             # Add final response to history
    #             state.add_message(final_response)
                
    #             logger.info(f"[Session: {session_id}] Final response generated after tool execution")
                
    #             return {
    #                 "session_id": session_id,
    #                 "reply": final_response.content,
    #                 "tools_used": tool_results,
    #                 "metadata": state.metadata,
    #                 "turn_count": state.turn_count,
    #                 "timestamp": datetime.now().isoformat()
    #             }
            
    #         else:
    #             # No tools called, direct response
    #             logger.info(f"[Session: {session_id}] Direct response (no tools called)")
                
    #             # Add AI response to history
    #             state.add_message(response)
                
    #             return {
    #                 "session_id": session_id,
    #                 "reply": response.content,
    #                 "tools_used": [],
    #                 "metadata": state.metadata,
    #                 "turn_count": state.turn_count,
    #                 "timestamp": datetime.now().isoformat()
    #             }
        
    #     except Exception as e:
    #         logger.error(f"[Session: {session_id}] Error in process_message: {str(e)}", exc_info=True)
            
    #         error_response = (
    #             "I apologize, but I encountered an error processing your request. "
    #             "Could you please try again or rephrase your question?"
    #         )
            
    #         return {
    #             "session_id": session_id,
    #             "reply": error_response,
    #             "tools_used": [],
    #             "error": str(e),
    #             "metadata": state.metadata,
    #             "turn_count": state.turn_count,
    #             "timestamp": datetime.now().isoformat()
    #         }


    async def process_message(self, session_id: str, user_message: str, user: User) -> AsyncGenerator[str, None]:
        """Stream LLM responses token by token"""
        logger.info(f"[Session: {session_id}] Processing message")
        
        set_user_context(user)
        state = self.get_or_create_session(session_id)
        
        enriched_message = self._enrich_user_message(user_message, state)
        state.add_message(HumanMessage(content=enriched_message))
        
        llm = get_llm().bind_tools(self.tools)
        
        try:
            recent_messages = state.get_recent_messages()
            
            # STREAM the initial response
            response_content = ""
            tool_calls = []
            
            async for chunk in llm.astream(recent_messages):
                # Accumulate for tool detection
                if hasattr(chunk, 'content') and chunk.content:
                    response_content += chunk.content
                    # Yield immediately for TTS
                    yield chunk.content
                
                # Collect tool calls
                if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                    tool_calls.extend(chunk.tool_calls)
            
            # Handle tool calls if any
            if tool_calls:
                # Execute tools
                tool_results = []
                for tool_call in tool_calls:
                    result = await self._execute_tool(
                        tool_call.get("name"), 
                        tool_call.get("args", {})
                    )
                    tool_results.append(result)
                    
                    # Add tool message
                    state.add_message(ToolMessage(
                        content=json.dumps(result, default=str),
                        tool_call_id=tool_call.get("id")
                    ))
                
                # Stream final response with tool context
                async for chunk in llm.astream(state.get_recent_messages()):
                    if hasattr(chunk, 'content') and chunk.content:
                        yield chunk.content
            
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            yield "I apologize, but I encountered an error."
    

    async def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """
        Execute a tool and return its result
        
        Args:
            tool_name: Name of the tool to execute
            args: Tool arguments
        
        Returns:
            Tool execution result
        """
        tool = self.tool_map.get(tool_name)
        if not tool:
            logger.error(f"Tool not found: {tool_name}")
            raise ValueError(f"Unknown tool: {tool_name}")
        
        try:
            # Tools are synchronous, so we call invoke directly
            logger.debug(f"Invoking tool {tool_name} with args: {args}")
            result = tool.invoke(args)
            return result
        except Exception as e:
            logger.error(f"Tool execution failed for {tool_name}: {str(e)}", exc_info=True)
            raise

    def store_audio(self, audio_id: str, audio_data: bytes):
        """Store audio temporarily"""
        self.audio_cache[audio_id] = audio_data
        logger.debug(f"Stored audio: {audio_id} ({len(audio_data)} bytes)")

    def get_audio(self, audio_id: str) -> Optional[bytes]:
        """Retrieve stored audio"""
        audio = self.audio_cache.get(audio_id)
        if audio:
            logger.debug(f"Retrieved audio: {audio_id}")
        else:
            logger.warning(f"Audio not found: {audio_id}")
        return audio

    def get_session_info(self, session_id: str) -> Optional[Dict]:
        """Get session information"""
        state = self.sessions.get(session_id)
        if state:
            info = state.to_dict()
            logger.info(f"Retrieved session info for {session_id}: {info}")
            return info
        logger.warning(f"Session not found: {session_id}")
        return None

    def list_sessions(self) -> List[Dict]:
        """List all active sessions"""
        return [state.to_dict() for state in self.sessions.values()]

    def clear_session(self, session_id: str) -> bool:
        """Clear a session"""
        if session_id in self.sessions:
            logger.info(f"Clearing session: {session_id}")
            del self.sessions[session_id]
            return True
        logger.warning(f"Cannot clear session (not found): {session_id}")
        return False

    def clear_all_sessions(self):
        """Clear all sessions"""
        count = len(self.sessions)
        self.sessions.clear()
        self.audio_cache.clear()
        logger.info(f"Cleared {count} sessions and audio cache")