from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Literal, Dict, Tuple

from pydantic import BaseModel
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


class GoogleOAuthPayload(BaseModel):
    """Payload sent from the frontend containing user's Google OAuth tokens.

    Notes
    - access_token is sufficient for short-lived use. If refresh_token, client_id,
      and client_secret are provided, the backend can refresh when needed.
    - expiry is optional; if provided it will be applied to Credentials.
    """

    user_id: str
    access_token: str
    refresh_token: Optional[str] = None
    token_uri: str = "https://oauth2.googleapis.com/token"
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    scopes: Optional[List[str]] = None
    expiry: Optional[datetime] = None
    # Optional context fields
    agent_id: Optional[str] = None
    # Explicit service hint to gate verification (calendar or gmail)
    service: Optional[Literal['calendar', 'gmail']] = None


def build_credentials(payload: GoogleOAuthPayload) -> Credentials:
    """Construct google.oauth2.credentials.Credentials from payload."""
    kwargs = {
        "token": payload.access_token,
        "refresh_token": payload.refresh_token,
        "token_uri": payload.token_uri,
        "client_id": payload.client_id,
        "client_secret": payload.client_secret,
        "scopes": payload.scopes,
    }
    # Remove None values to avoid type issues in the underlying library
    filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
    creds = Credentials(**filtered_kwargs)
    if payload.expiry is not None:
        creds.expiry = payload.expiry
    return creds


def get_calendar_service(credentials: Credentials):
    """Create a Google Calendar API client (v3)."""
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def get_gmail_service(credentials: Credentials):
    """Create a Gmail API client (v1)."""
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def get_calendar_events(credentials: Credentials,
                        time_min: Optional[datetime] = None,
                        time_max: Optional[datetime] = None,
                        max_results: int = 250) -> List[Dict]:
    """Fetch calendar events within a time range.

    Args:
        credentials: Google OAuth credentials
        time_min: Start time (defaults to now)
        time_max: End time (defaults to 7 days from now)
        max_results: Maximum number of events to fetch

    Returns:
        List of calendar events
    """
    service = get_calendar_service(credentials)

    # Default time range: now to 7 days from now (using UTC)
    if time_min is None:
        time_min = datetime.now(timezone.utc)
    if time_max is None:
        time_max = time_min + timedelta(days=7)

    # Ensure datetimes are timezone-aware
    if time_min.tzinfo is None:
        time_min = time_min.replace(tzinfo=timezone.utc)
    if time_max.tzinfo is None:
        time_max = time_max.replace(tzinfo=timezone.utc)

    # Convert to RFC3339 timestamp format required by Google Calendar API
    time_min_str = time_min.isoformat()
    time_max_str = time_max.isoformat()

    try:
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min_str,
            timeMax=time_max_str,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        return events
    except Exception as e:
        print(f"Error fetching calendar events: {e}")
        return []


def calculate_availability(credentials: Credentials,
                          time_min: Optional[datetime] = None,
                          time_max: Optional[datetime] = None,
                          business_hours: Tuple[int, int] = (9, 17),
                          time_slot_minutes: int = 30) -> Dict:
    """Calculate availability based on calendar events.

    Args:
        credentials: Google OAuth credentials
        time_min: Start time for availability check
        time_max: End time for availability check
        business_hours: Tuple of (start_hour, end_hour) in 24h format
        time_slot_minutes: Duration of each time slot in minutes

    Returns:
        Dictionary containing availability information
    """
    if time_min is None:
        time_min = datetime.now(timezone.utc)
    if time_max is None:
        time_max = time_min + timedelta(days=7)

    # Ensure datetimes are timezone-aware
    if time_min.tzinfo is None:
        time_min = time_min.replace(tzinfo=timezone.utc)
    if time_max.tzinfo is None:
        time_max = time_max.replace(tzinfo=timezone.utc)

    # Get all events in the time range
    events = get_calendar_events(credentials, time_min, time_max)

    # Build list of busy periods
    busy_periods = []
    for event in events:
        # Handle all-day events
        if 'dateTime' in event.get('start', {}):
            start = datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00'))
            end = datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00'))
        elif 'date' in event.get('start', {}):
            # All-day event
            start = datetime.fromisoformat(event['start']['date'])
            end = datetime.fromisoformat(event['end']['date'])
        else:
            continue

        busy_periods.append({
            'start': start,
            'end': end,
            'summary': event.get('summary', 'Busy')
        })

    # Sort busy periods by start time
    busy_periods.sort(key=lambda x: x['start'])

    # Calculate available slots
    available_slots = []
    current_date = time_min.date()
    end_date = time_max.date()

    while current_date <= end_date:
        # Only check weekdays
        if current_date.weekday() < 5:  # Monday = 0, Sunday = 6
            # Create time slots for the day (with UTC timezone)
            day_start = datetime.combine(current_date, datetime.min.time().replace(hour=business_hours[0]), tzinfo=timezone.utc)
            day_end = datetime.combine(current_date, datetime.min.time().replace(hour=business_hours[1]), tzinfo=timezone.utc)

            # Check each potential time slot
            slot_start = day_start
            while slot_start + timedelta(minutes=time_slot_minutes) <= day_end:
                slot_end = slot_start + timedelta(minutes=time_slot_minutes)

                # Check if slot conflicts with any busy period
                is_available = True
                for busy in busy_periods:
                    if not (slot_end <= busy['start'] or slot_start >= busy['end']):
                        is_available = False
                        break

                if is_available and slot_start >= time_min:
                    available_slots.append({
                        'start': slot_start,
                        'end': slot_end
                    })

                slot_start = slot_end

        current_date += timedelta(days=1)

    return {
        'time_range': {
            'start': time_min.isoformat(),
            'end': time_max.isoformat()
        },
        'busy_periods': [
            {
                'start': bp['start'].isoformat(),
                'end': bp['end'].isoformat(),
                'summary': bp['summary']
            }
            for bp in busy_periods
        ],
        'available_slots': [
            {
                'start': slot['start'].isoformat(),
                'end': slot['end'].isoformat()
            }
            for slot in available_slots[:20]  # Limit to first 20 slots for readability
        ],
        'total_available_slots': len(available_slots)
    }


def print_availability(credentials: Credentials, days_ahead: int = 7):
    """Print a formatted availability summary.

    Args:
        credentials: Google OAuth credentials
        days_ahead: Number of days to check ahead
    """
    time_min = datetime.now(timezone.utc)
    time_max = time_min + timedelta(days=days_ahead)

    availability = calculate_availability(credentials, time_min, time_max)

    print("\n" + "="*60)
    print("CALENDAR AVAILABILITY SUMMARY")
    print("="*60)
    print(f"Time range: {time_min.strftime('%Y-%m-%d %H:%M')} to {time_max.strftime('%Y-%m-%d %H:%M')}")
    print(f"Total available slots: {availability['total_available_slots']}")

    print("\n📅 BUSY PERIODS:")
    print("-" * 40)
    if availability['busy_periods']:
        for busy in availability['busy_periods']:
            start = datetime.fromisoformat(busy['start'])
            end = datetime.fromisoformat(busy['end'])
            print(f"  • {start.strftime('%a %b %d, %I:%M %p')} - {end.strftime('%I:%M %p')}: {busy['summary']}")
    else:
        print("  No busy periods found")

    print("\n✅ AVAILABLE TIME SLOTS (first 20):")
    print("-" * 40)
    if availability['available_slots']:
        current_day = None
        for slot in availability['available_slots']:
            start = datetime.fromisoformat(slot['start'])
            end = datetime.fromisoformat(slot['end'])

            # Group by day
            day_str = start.strftime('%A, %B %d')
            if day_str != current_day:
                current_day = day_str
                print(f"\n  {day_str}:")

            print(f"    • {start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}")
    else:
        print("  No available slots found")

    print("\n" + "="*60)
    print()

