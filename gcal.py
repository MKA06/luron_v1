from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Literal, Dict, Tuple, Union
from dateutil.parser import isoparse

from pydantic import BaseModel, Field, field_validator
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


class GoogleOAuthPayload(BaseModel):
    """Payload sent from the frontend containing user's Google OAuth authorization code or tokens.

    Notes
    - Can contain either authorization_code (for initial auth) or access_token (for existing auth)
    - If authorization_code is provided, backend will exchange it for tokens
    - If access_token is provided with refresh_token, backend can refresh when needed
    """

    user_id: str
    # Either authorization code or access token
    authorization_code: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_uri: str = "https://oauth2.googleapis.com/token"
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    scopes: Optional[List[str]] = None
    expiry: Optional[Union[datetime, str]] = None  # Can be datetime or RFC3339 string
    # Optional context fields
    agent_id: Optional[str] = None
    # Explicit service hint to gate verification (calendar or gmail)
    service: Optional[Literal['calendar', 'gmail']] = None
    # Additional fields from frontend
    now: Optional[str] = None  # RFC3339 timestamp
    timezone: Optional[str] = None  # IANA timezone name

    @field_validator('expiry', mode='before')
    @classmethod
    def parse_expiry(cls, v):
        """Parse expiry from RFC3339 string to datetime if needed."""
        if v is None:
            return None
        if isinstance(v, datetime):
            # Already a datetime, ensure it's timezone-aware
            if v.tzinfo is None:
                return v.replace(tzinfo=timezone.utc)
            return v
        if isinstance(v, str):
            try:
                # Parse RFC3339 string with explicit offset (e.g., +00:00)
                parsed = isoparse(v)
                # Ensure timezone-aware
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
            except Exception as e:
                print(f"Error parsing expiry datetime: {v} - {e}")
                return None
        return v


def exchange_authorization_code(code: str, client_id: str, client_secret: str,
                                token_uri: str = "https://oauth2.googleapis.com/token") -> Dict:
    """Exchange authorization code for access and refresh tokens."""
    import requests

    # The redirect URI must match what was used in the frontend
    # For Google Identity Services popup flow, use 'postmessage'
    data = {
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': 'postmessage',  # Special value for GIS popup flow
        'grant_type': 'authorization_code'
    }

    response = requests.post(token_uri, data=data)
    response.raise_for_status()
    return response.json()


def refresh_access_token(refresh_token: str, client_id: str, client_secret: str,
                         token_uri: str = "https://oauth2.googleapis.com/token") -> Dict:
    """Refresh an access token using a refresh token."""
    import requests

    data = {
        'refresh_token': refresh_token,
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'refresh_token'
    }

    response = requests.post(token_uri, data=data)
    response.raise_for_status()
    return response.json()


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

    # Handle expiry - Google's library expects a NAIVE datetime in UTC
    # The library's internal _helpers.utcnow() returns naive datetime
    if payload.expiry is not None:
        if isinstance(payload.expiry, str):
            # This shouldn't happen since the validator should convert it
            # but just in case, parse it again
            try:
                expiry_dt = isoparse(payload.expiry)
                # Convert to UTC and make NAIVE for Google library compatibility
                if expiry_dt.tzinfo is not None:
                    expiry_dt = expiry_dt.astimezone(timezone.utc).replace(tzinfo=None)
                creds.expiry = expiry_dt
            except Exception as e:
                print(f"Warning: Could not parse expiry: {e}")
        elif isinstance(payload.expiry, datetime):
            # Convert to UTC and make NAIVE for Google library compatibility
            if payload.expiry.tzinfo is not None:
                # Convert to UTC then remove timezone info
                creds.expiry = payload.expiry.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                # Already naive, assume it's UTC
                creds.expiry = payload.expiry

    return creds


def get_calendar_service(credentials: Credentials):
    """Create a Google Calendar API client (v3) with automatic token refresh."""
    # The credentials object will automatically refresh if it has refresh_token
    if credentials.expired and credentials.refresh_token:
        try:
            import google.auth.transport.requests
            request = google.auth.transport.requests.Request()
            credentials.refresh(request)
            print("Successfully refreshed access token")
        except Exception as e:
            print(f"Error refreshing token: {e}")
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def get_gmail_service(credentials: Credentials):
    """Create a Gmail API client (v1)."""
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def get_calendar_events(credentials: Credentials,
                        time_min: Optional[datetime] = None,
                        time_max: Optional[datetime] = None,
                        max_results: int = 250,
                        calendar_id: str = 'primary') -> List[Dict]:
    """Fetch calendar events within a time range.

    Args:
        credentials: Google OAuth credentials
        time_min: Start time (defaults to now)
        time_max: End time (defaults to 7 days from now)
        max_results: Maximum number of events to fetch
        calendar_id: Calendar ID to fetch from (default 'primary')

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

    print(f"Fetching events from {time_min_str} to {time_max_str}")
    print(f"Calendar ID: {calendar_id}")

    try:
        # First, let's check if we can access the calendar
        calendar = service.calendars().get(calendarId=calendar_id).execute()
        print(f"Calendar timezone: {calendar.get('timeZone', 'Not specified')}")

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min_str,
            timeMax=time_max_str,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime',
            showDeleted=False,
            showHiddenInvitations=False,
            timeZone='UTC'  # Request events in UTC
        ).execute()

        events = events_result.get('items', [])
        print(f"Found {len(events)} events")

        # Debug first few events
        for i, event in enumerate(events[:3]):
            print(f"Event {i+1}: {event.get('summary', 'No title')}")
            if 'dateTime' in event.get('start', {}):
                print(f"  Start: {event['start']['dateTime']}")
            elif 'date' in event.get('start', {}):
                print(f"  All-day: {event['start']['date']}")

        return events
    except Exception as e:
        print(f"Error fetching calendar events: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
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
    print(f"Processing {len(events)} events for availability calculation")

    for event in events:
        event_summary = event.get('summary', 'No title')
        print(f"Processing event: {event_summary}")

        # Handle all-day events
        if 'dateTime' in event.get('start', {}):
            # Parse datetime - handle various ISO formats
            start_str = event['start']['dateTime']
            end_str = event['end']['dateTime']

            # Parse with proper timezone handling
            try:
                # Handle Z suffix (UTC)
                if start_str.endswith('Z'):
                    start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                # Handle timezone offset like -08:00 or +05:30
                elif '+' in start_str or (start_str.count('-') > 2):
                    start = datetime.fromisoformat(start_str)
                else:
                    # No timezone info, assume UTC
                    start = datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc)

                if end_str.endswith('Z'):
                    end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                elif '+' in end_str or (end_str.count('-') > 2):
                    end = datetime.fromisoformat(end_str)
                else:
                    end = datetime.fromisoformat(end_str).replace(tzinfo=timezone.utc)

                print(f"  DateTime event: {start} to {end}")
            except Exception as e:
                print(f"  Error parsing datetime: {e}")
                continue

        elif 'date' in event.get('start', {}):
            # All-day event
            try:
                start = datetime.fromisoformat(event['start']['date']).replace(tzinfo=timezone.utc)
                end = datetime.fromisoformat(event['end']['date']).replace(tzinfo=timezone.utc)
                print(f"  All-day event: {start.date()} to {end.date()}")
            except Exception as e:
                print(f"  Error parsing date: {e}")
                continue
        else:
            print(f"  Skipping event - no start time found")
            continue

        busy_periods.append({
            'start': start,
            'end': end,
            'summary': event_summary
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


def create_calendar_event(credentials: Credentials,
                         summary: str,
                         start_time: datetime,
                         end_time: Optional[datetime] = None,
                         description: Optional[str] = None,
                         location: Optional[str] = None,
                         attendees: Optional[List[str]] = None) -> Dict:
    """Create a new event in the user's Google Calendar.

    Args:
        credentials: Google OAuth credentials
        summary: Event title/summary
        start_time: Event start time (timezone-aware datetime)
        end_time: Event end time (defaults to 1 hour after start)
        description: Event description
        location: Event location
        attendees: List of attendee email addresses

    Returns:
        Dictionary with event details or error information
    """
    service = get_calendar_service(credentials)

    # Ensure timezone-aware datetimes
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)

    # Default end time to 1 hour after start
    if end_time is None:
        end_time = start_time + timedelta(hours=1)
    elif end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)

    # Build event body
    event = {
        'summary': summary,
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': 'UTC',
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': 'UTC',
        }
    }

    # Add optional fields
    if description:
        event['description'] = description
    if location:
        event['location'] = location
    if attendees:
        event['attendees'] = [{'email': email} for email in attendees]

    try:
        # Create the event
        print(f"Creating event with body: {event}")
        created_event = service.events().insert(
            calendarId='primary',
            body=event
        ).execute()

        print(f"Event created successfully: {created_event}")
        return {
            'success': True,
            'event_id': created_event.get('id'),
            'html_link': created_event.get('htmlLink'),
            'summary': created_event.get('summary'),
            'start': created_event.get('start', {}).get('dateTime'),
            'end': created_event.get('end', {}).get('dateTime')
        }
    except Exception as e:
        print(f"Error creating calendar event: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return {
            'success': False,
            'error': str(e)
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
