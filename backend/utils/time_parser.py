import re
from datetime import datetime, timedelta
from typing import Optional, Tuple
from dateutil import parser as dateutil_parser
from utils.logger import setup_logger
import pytz 
from config import config

logger = setup_logger("time_parser")

class TimeParser:
    """Parse natural language time expressions"""
    
    DAYS_OF_WEEK = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    
    @staticmethod
    def parse_duration(text: str) -> Optional[int]:
        """
        Extract meeting duration in minutes from text
        
        Examples:
            "1 hour" -> 60
            "30 minutes" -> 30
            "an hour and a half" -> 90
        """
        text = text.lower()
        
        patterns = [
            (r'(\d+)\s*hour(?:s)?\s+(?:and\s+)?(\d+)\s*min', lambda m: int(m.group(1)) * 60 + int(m.group(2))),
            (r'(\d+\.5|\d+\s+and\s+a\s+half)\s*hour', lambda m: 90),
            (r'an?\s+hour\s+and\s+a\s+half', lambda m: 90),
            (r'(\d+)\s*hour(?:s)?', lambda m: int(m.group(1)) * 60),
            (r'an?\s+hour', lambda m: 60),
            (r'(\d+)\s*min(?:ute)?(?:s)?', lambda m: int(m.group(1))),
            (r'half\s+an?\s+hour', lambda m: 30),
            (r'quarter\s+hour', lambda m: 15),
        ]
        
        for pattern, extractor in patterns:
            match = re.search(pattern, text)
            if match:
                result = extractor(match)
                logger.debug(f"Parsed duration: '{text}' -> {result} minutes")
                return result
        
        return None
    
    @staticmethod
    def parse_iso_datetime(value: str) -> datetime:
        """
        Parse ISO 8601 datetime string safely.

        Args:
            value (str): ISO formatted datetime string

        Returns:
            datetime: Parsed datetime object
        """
        try:
            return dateutil_parser.isoparse(value)
        except Exception as e:
            logger.error(f"Failed to parse ISO datetime: {value} -> {e}")
            raise
    
    @staticmethod
    def parse_day_preference(text: str) -> Optional[str]:
        """
        Extract day preference from text
        
        Examples:
            "tuesday" -> "tuesday"
            "next friday" -> "friday"
            "tomorrow" -> "tomorrow"
        """
        text = text.lower()
        
        # Check for relative days
        if "today" in text:
            return "today"
        if "tomorrow" in text:
            return "tomorrow"
        if "day after tomorrow" in text:
            return "day_after_tomorrow"
        
        # Check for specific days
        for day in TimeParser.DAYS_OF_WEEK:
            if day in text:
                return day
        
        # Check for date patterns (YYYY-MM-DD, MM/DD, etc.)
        date_patterns = [
            r'(\d{4})-(\d{1,2})-(\d{1,2})',
            r'(\d{1,2})/(\d{1,2})(?:/(\d{4}))?',
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        
        return None
    
    @staticmethod
    def parse_time_preference(text: str) -> Optional[str]:
        """
        Extract time of day preference
        
        Examples:
            "morning" -> "morning"
            "after 5 pm" -> "after 5 pm"
            "between 2 and 4" -> "afternoon"
        """
        text = text.lower()
        
        # Check for time of day
        if "morning" in text:
            return "morning"
        if "afternoon" in text or "lunch" in text:
            return "afternoon"
        if "evening" in text:
            return "evening"
        
        # Check for "after X" patterns
        after_match = re.search(r'after\s+(\d+)\s*(am|pm)?', text)
        if after_match:
            hour = after_match.group(1)
            period = after_match.group(2) or ""
            return f"after {hour} {period}".strip()
        
        # Check for "before X" patterns
        before_match = re.search(r'before\s+(\d+)\s*(am|pm)?', text)
        if before_match:
            hour = before_match.group(1)
            period = before_match.group(2) or ""
            return f"before {hour} {period}".strip()
        
        return None
    
    @staticmethod
    def parse_relative_day(text: str) -> Optional[datetime]:
        """
        Convert relative day expressions into a datetime object (date at local timezone).
        
        Examples:
            "today" -> datetime for today
            "tomorrow" -> datetime for tomorrow
            "day after tomorrow" -> datetime +2
            "friday" -> next Friday (from today)
            "2025-10-05" -> parsed exact date
        """
        tz = pytz.timezone(config.DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        text = text.lower().strip()

        if text in ["today"]:
            return now
        if text in ["tomorrow"]:
            return now + timedelta(days=1)
        if text in ["day after tomorrow"]:
            return now + timedelta(days=2)

        # Handle weekdays (next occurrence)
        if text in TimeParser.DAYS_OF_WEEK:
            today_idx = now.weekday()  # 0 = Monday
            target_idx = TimeParser.DAYS_OF_WEEK.index(text)
            days_ahead = (target_idx - today_idx) % 7
            if days_ahead == 0:  # same day word means "next week"
                days_ahead = 7
            return now + timedelta(days=days_ahead)

        # Handle explicit date formats
        try:
            dt = dateutil_parser.parse(text)
            return tz.localize(dt) if dt.tzinfo is None else dt
        except Exception:
            return None
    
    @staticmethod
    def parse_attendees(text: str) -> list:
        """Extract email addresses from text"""
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pattern, text)
        return list(set(emails))  # Remove duplicates
    
    @staticmethod
    def extract_all_info(text: str) -> dict:
        """Extract all scheduling information from text"""
        return {
            "duration_min": TimeParser.parse_duration(text),
            "day_pref": TimeParser.parse_day_preference(text),
            "time_pref": TimeParser.parse_time_preference(text),
            "attendees": TimeParser.parse_attendees(text),
        }