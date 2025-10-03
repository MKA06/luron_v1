from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Literal, Dict, Tuple, Union
from dateutil.parser import isoparse
from dateutil import parser

from pydantic import BaseModel, Field, field_validator
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import pytz
import traceback


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
                          business_hours: Tuple[int, int] = (6, 23),
                          user_timezone: str = 'America/New_York') -> Dict:
    """Calculate availability based on calendar events.

    Args:
        credentials: Google OAuth credentials
        time_min: Start time for availability check
        time_max: End time for availability check
        business_hours: Tuple of (start_hour, end_hour) in 24h format in user's timezone
        user_timezone: User's timezone for business hours calculation

    Returns:
        Dictionary containing availability information with free intervals
    """
    import pytz

    # Set up timezone
    user_tz = pytz.timezone(user_timezone)

    if time_min is None:
        time_min = datetime.now(timezone.utc)
    if time_max is None:
        time_max = time_min + timedelta(days=7)

    # Ensure datetimes are timezone-aware
    if time_min.tzinfo is None:
        time_min = time_min.replace(tzinfo=timezone.utc)
    if time_max.tzinfo is None:
        time_max = time_max.replace(tzinfo=timezone.utc)

    # Get all events in the time range (events come back in UTC)
    events = get_calendar_events(credentials, time_min, time_max)


    # Build list of busy periods (all in UTC)
    busy_periods = []

    for event in events:
        event_summary = event.get('summary', 'No title')

        if 'dateTime' in event.get('start', {}):
            # Regular timed event
            start_str = event['start']['dateTime']
            end_str = event['end']['dateTime']

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
            except Exception as e:
                print(f"  Error parsing datetime for '{event_summary}': {e}")
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

    # Calculate available intervals (gaps between busy periods)
    available_slots = []
    current_date = time_min.date()
    end_date = time_max.date()

    while current_date <= end_date:
        # Only check weekdays
        if current_date.weekday() < 5:  # Monday = 0, Sunday = 6
            # Create business hours in user's timezone, then convert to UTC
            day_start_local = user_tz.localize(datetime.combine(
                current_date,
                datetime.min.time().replace(hour=business_hours[0], minute=0, second=0, microsecond=0)
            ))
            day_end_local = user_tz.localize(datetime.combine(
                current_date,
                datetime.min.time().replace(hour=business_hours[1], minute=0, second=0, microsecond=0)
            ))

            # Convert to UTC for comparison with events
            day_start_utc = day_start_local.astimezone(pytz.UTC)
            day_end_utc = day_end_local.astimezone(pytz.UTC)

            # Get busy periods for this day only (already sorted from parent list)
            day_busy = [bp for bp in busy_periods
                       if not (bp['end'] <= day_start_utc or bp['start'] >= day_end_utc)]

            # Merge overlapping busy periods
            merged_busy = []
            for busy in day_busy:
                # Clamp busy period to business hours
                busy_start = max(busy['start'], day_start_utc)
                busy_end = min(busy['end'], day_end_utc)

                if merged_busy and busy_start <= merged_busy[-1]['end']:
                    # Overlapping or adjacent - merge
                    merged_busy[-1]['end'] = max(merged_busy[-1]['end'], busy_end)
                else:
                    # Non-overlapping - add new period
                    merged_busy.append({'start': busy_start, 'end': busy_end})

            # Find gaps between busy periods
            current_time = day_start_utc
            for busy in merged_busy:
                if current_time < busy['start']:
                    # There's a gap before this busy period
                    gap_start = max(current_time, time_min)
                    gap_end = busy['start']
                    if gap_start < gap_end:
                        available_slots.append({
                            'start': gap_start,
                            'end': gap_end,
                            'start_local': gap_start.astimezone(user_tz),
                            'end_local': gap_end.astimezone(user_tz)
                        })
                current_time = max(current_time, busy['end'])

            # Check if there's a gap after the last busy period
            if current_time < day_end_utc and current_time >= time_min:
                available_slots.append({
                    'start': current_time,
                    'end': day_end_utc,
                    'start_local': current_time.astimezone(user_tz),
                    'end_local': day_end_utc.astimezone(user_tz)
                })

        current_date += timedelta(days=1)


    return {
        'time_range': {
            'start': time_min.isoformat(),
            'end': time_max.isoformat()
        },
        'timezone': user_timezone,
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
                'end': slot['end'].isoformat(),
                'start_local': slot['start_local'].isoformat(),
                'end_local': slot['end_local'].isoformat()
            }
            for slot in available_slots
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


async def set_meeting(supabase, user_id: str = None,
                      meeting_name: str = None,
                      meeting_time: str = None,
                      duration_minutes: int = 120,
                      description: str = None,
                      location: str = None):
    """Schedule a meeting in the user's Google Calendar.

    Args:
        supabase: Supabase client instance
        user_id: The user ID whose calendar to update
        meeting_name: Title/summary of the meeting
        meeting_time: ISO format datetime string or natural language time
        duration_minutes: Meeting duration in minutes (default 120)
        description: Optional meeting description
        location: Optional meeting location

    Returns:
        A formatted string with meeting creation status
    """
    print("CALLED THE SET_MEETING FUNCTION")

    if not user_id:
        return "Error: No user_id provided for scheduling meeting"

    if not meeting_name:
        return "Error: No meeting name provided"

    if not meeting_time:
        return "Error: No meeting time provided"

    try:
        # Fetch credentials from Supabase
        print(f"Fetching credentials for user_id: {user_id}")
        result = supabase.table('google_credentials').select('*').eq('user_id', user_id).single().execute()

        print(f"Credentials query result: {result}")
        if not result.data:
            return f"No Google Calendar credentials found for user {user_id}. User needs to authenticate first."

        creds_data = result.data
        print(f"Found credentials for user: {creds_data.get('user_id')}")

        # Check if token needs refresh
        needs_refresh = False
        if creds_data.get('expiry'):
            expiry = datetime.fromisoformat(creds_data['expiry'].replace('Z', '+00:00'))
            if expiry <= datetime.now(timezone.utc) + timedelta(minutes=5):
                needs_refresh = True
                print(f"Token expired or expiring soon, refreshing...")

        # Refresh token if needed
        if needs_refresh and creds_data.get('refresh_token'):
            try:
                token_response = refresh_access_token(
                    refresh_token=creds_data['refresh_token'],
                    client_id=creds_data['client_id'],
                    client_secret=creds_data['client_secret'],
                    token_uri=creds_data.get('token_uri', 'https://oauth2.googleapis.com/token')
                )

                # Update credentials in database
                expires_in = token_response.get('expires_in', 3600)
                new_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

                update_data = {
                    'access_token': token_response['access_token'],
                    'expiry': new_expiry.isoformat(),
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }

                supabase.table('google_credentials').update(update_data).eq('user_id', user_id).execute()
                creds_data['access_token'] = token_response['access_token']
                creds_data['expiry'] = new_expiry.isoformat()
                print("Successfully refreshed access token")
            except Exception as e:
                print(f"Error refreshing token: {e}")
                return f"Error: Token expired and could not be refreshed. User needs to re-authenticate."

        # Rebuild credentials
        # Parse expiry if present
        expiry = None
        if creds_data.get('expiry'):
            expiry = datetime.fromisoformat(creds_data['expiry'].replace('Z', '+00:00'))

        payload = GoogleOAuthPayload(
            user_id=creds_data['user_id'],
            access_token=creds_data['access_token'],
            refresh_token=creds_data.get('refresh_token'),
            token_uri=creds_data.get('token_uri', 'https://oauth2.googleapis.com/token'),
            client_id=creds_data.get('client_id'),
            client_secret=creds_data.get('client_secret'),
            scopes=creds_data.get('scopes'),
            expiry=expiry
        )

        creds = build_credentials(payload)

        # Use New York Time as default
        user_timezone_str = 'America/New_York'
        print(f"Using timezone: {user_timezone_str}")
        user_tz = pytz.timezone(user_timezone_str)

        # Parse the meeting time
        try:
            # Try to parse as ISO format or natural language
            print(f"Parsing meeting time: {meeting_time}")

            # Handle relative date terms
            now_local = datetime.now(user_tz)
            meeting_lower = meeting_time.lower()

            if 'today' in meeting_lower:
                # Extract time from the string
                time_part = meeting_lower.replace('today', '').strip()
                time_part = time_part.replace('at', '').strip()
                # Parse just the time
                parsed_time_only = parser.parse(time_part)
                # Combine with today's date
                parsed_time = now_local.replace(
                    hour=parsed_time_only.hour,
                    minute=parsed_time_only.minute,
                    second=0,
                    microsecond=0
                )
                print(f"Parsed 'today' as: {parsed_time}")
            elif 'tomorrow' in meeting_lower:
                # Extract time from the string
                time_part = meeting_lower.replace('tomorrow', '').strip()
                time_part = time_part.replace('at', '').strip()
                # Parse just the time
                parsed_time_only = parser.parse(time_part)
                # Combine with tomorrow's date
                tomorrow = now_local + timedelta(days=1)
                parsed_time = tomorrow.replace(
                    hour=parsed_time_only.hour,
                    minute=parsed_time_only.minute,
                    second=0,
                    microsecond=0
                )
                print(f"Parsed 'tomorrow' as: {parsed_time}")
            else:
                # Try standard parsing
                parsed_time = parser.parse(meeting_time, default=now_local)

                # Handle timezone
                if parsed_time.tzinfo is None:
                    # If no timezone in input, assume it's in user's local timezone
                    print(f"No timezone in input, assuming user's timezone: {user_timezone_str}")
                    parsed_time = user_tz.localize(parsed_time)
                else:
                    print(f"Input already has timezone: {parsed_time.tzinfo}")

            # Convert to UTC for Google Calendar API
            start_time_utc = parsed_time.astimezone(pytz.UTC)
            end_time_utc = start_time_utc + timedelta(minutes=duration_minutes)

            # Also keep track of local time for display
            start_time_local = parsed_time.astimezone(user_tz)
            end_time_local = start_time_local + timedelta(minutes=duration_minutes)

            print(f"Meeting time in user's timezone ({user_timezone_str}): {start_time_local}")
            print(f"Meeting time in UTC: {start_time_utc}")

            start_time = start_time_utc
            end_time = end_time_utc

            print(f"Meeting details: {meeting_name}")
            print(f"  Local time: {start_time_local.strftime('%Y-%m-%d %I:%M %p %Z')}")
            print(f"  UTC time: {start_time.strftime('%Y-%m-%d %I:%M %p %Z')}")
        except Exception as e:
            print(f"Error parsing time: {e}")
            print(f"Traceback: {traceback.format_exc()}")
            return f"Error: Could not parse meeting time '{meeting_time}'. Please provide a valid date/time like 'tomorrow at 2pm' or '2024-12-25 14:00'."

        # Create the calendar event
        print("Calling create_calendar_event...")
        result = create_calendar_event(
            credentials=creds,
            summary=meeting_name,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location
        )

        # Update last_used_at
        supabase.table('google_credentials').update({
            'last_used_at': datetime.now(timezone.utc).isoformat()
        }).eq('user_id', user_id).execute()

        print(f"Create calendar event result: {result}")

        # Format response
        if result.get('success'):
            # Format response in user's local time
            local_time_str = start_time_local.strftime('%A, %B %d at %I:%M %p')
            tz_abbrev = start_time_local.strftime('%Z')  # PST/PDT, etc.

            response = f"✅ Meeting scheduled successfully!\n\n"
            response += f"📅 {meeting_name}\n"
            response += f"🕐 {local_time_str} {tz_abbrev}\n"
            response += f"⏱️ Duration: {duration_minutes} minutes\n"
            if location:
                response += f"📍 Location: {location}\n"
            response += f"\n🔗 Calendar link: {result.get('html_link', 'N/A')}"
            print(f"Success response: {response}")
            return response
        else:
            error_msg = f"❌ Failed to schedule meeting: {result.get('error', 'Unknown error')}"
            print(f"Error response: {error_msg}")
            return error_msg

    except Exception as e:
        error_msg = f"Error scheduling meeting: {str(e)}"
        print(f"Exception in set_meeting: {error_msg}")
        print(f"Traceback: {traceback.format_exc()}")
        return error_msg


async def get_availability(supabase, user_id: str = None, days_ahead: int = 60, specific_day: Optional[str] = None):
    """Get user's calendar availability for agents to use.

    Args:
        supabase: Supabase client instance
        user_id: The user ID to fetch availability for
        days_ahead: Number of days to check ahead (default 60)
        specific_day: Optional specific day to check (e.g., 'today', 'tomorrow', '2024-12-25', 'Monday')

    Returns:
        A formatted string with availability information
    """
    print( "CALLED THE GET_AVAILABILITY FUNCTION GRAHHH")
    if not user_id:
        return "Error: No user_id provided for availability check"

    try:
        # Fetch credentials from Supabase
        result = supabase.table('google_credentials').select('*').eq('user_id', user_id).single().execute()

        if not result.data:
            return f"No Google Calendar credentials found for user {user_id}. User needs to authenticate first."

        creds_data = result.data

        # Check if token needs refresh
        needs_refresh = False
        if creds_data.get('expiry'):
            expiry = datetime.fromisoformat(creds_data['expiry'].replace('Z', '+00:00'))
            if expiry <= datetime.now(timezone.utc) + timedelta(minutes=5):
                needs_refresh = True
                print(f"Token expired or expiring soon, refreshing...")

        # Refresh token if needed
        if needs_refresh and creds_data.get('refresh_token'):
            try:
                token_response = refresh_access_token(
                    refresh_token=creds_data['refresh_token'],
                    client_id=creds_data['client_id'],
                    client_secret=creds_data['client_secret'],
                    token_uri=creds_data.get('token_uri', 'https://oauth2.googleapis.com/token')
                )

                # Update credentials in database
                expires_in = token_response.get('expires_in', 3600)
                new_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

                update_data = {
                    'access_token': token_response['access_token'],
                    'expiry': new_expiry.isoformat(),
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }

                supabase.table('google_credentials').update(update_data).eq('user_id', user_id).execute()
                creds_data['access_token'] = token_response['access_token']
                creds_data['expiry'] = new_expiry.isoformat()
                print("Successfully refreshed access token")
            except Exception as e:
                print(f"Error refreshing token: {e}")
                return f"Error: Token expired and could not be refreshed. User needs to re-authenticate."

        # Rebuild credentials
        # Parse expiry if present
        expiry = None
        if creds_data.get('expiry'):
            expiry = datetime.fromisoformat(creds_data['expiry'].replace('Z', '+00:00'))

        payload = GoogleOAuthPayload(
            user_id=creds_data['user_id'],
            access_token=creds_data['access_token'],
            refresh_token=creds_data.get('refresh_token'),
            token_uri=creds_data.get('token_uri', 'https://oauth2.googleapis.com/token'),
            client_id=creds_data.get('client_id'),
            client_secret=creds_data.get('client_secret'),
            scopes=creds_data.get('scopes'),
            expiry=expiry
        )

        creds = build_credentials(payload)

        # Use New York Time as default
        user_timezone = 'America/New_York'
        user_tz = pytz.timezone(user_timezone)

        # Calculate availability with timezone
        availability = calculate_availability(creds, user_timezone=user_timezone)

        # Parse specific_day if provided
        target_date = None
        if specific_day:
            now_local = datetime.now(user_tz)
            specific_day_lower = specific_day.lower()

            if specific_day_lower == 'today':
                target_date = now_local.date()
            elif specific_day_lower == 'tomorrow':
                target_date = (now_local + timedelta(days=1)).date()
            else:
                # Try to parse as date or day of week
                try:
                    # Try ISO format date
                    target_date = parser.parse(specific_day).date()
                except:
                    # Try day of week (e.g., 'Monday', 'Tuesday')
                    try:
                        # Find next occurrence of this day
                        days_of_week = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
                        if specific_day_lower in days_of_week:
                            target_weekday = days_of_week.index(specific_day_lower)
                            current_weekday = now_local.weekday()
                            days_until = (target_weekday - current_weekday) % 7
                            if days_until == 0:
                                days_until = 7  # Next week if today is the same day
                            target_date = (now_local + timedelta(days=days_until)).date()
                    except:
                        pass

        # Format response
        if specific_day and target_date:
            response = f"📅 Calendar Availability for {target_date.strftime('%A, %B %d, %Y')} ({user_timezone}):\n\n"
        else:
            response = f"📅 Calendar Availability for next {days_ahead} days ({user_timezone}):\n\n"

        # Show busy periods in user's timezone
        busy_periods = availability.get('busy_periods', [])

        # Filter busy periods by specific_day if provided
        if target_date:
            busy_periods = [bp for bp in busy_periods
                          if datetime.fromisoformat(bp['start']).astimezone(user_tz).date() == target_date]
        if busy_periods:
            response += "Busy times:\n"
            for busy in busy_periods[:10]:  # Limit to first 10
                # Convert UTC to user's timezone for display
                start_utc = datetime.fromisoformat(busy['start'])
                end_utc = datetime.fromisoformat(busy['end'])
                start_local = start_utc.astimezone(user_tz)
                end_local = end_utc.astimezone(user_tz)
                response += f"- {start_local.strftime('%a %b %d, %I:%M %p')} to {end_local.strftime('%I:%M %p')}: Busy\n"
            response += "\n"

        # Show available slots
        available_slots = availability.get('available_slots', [])

        # Filter available slots by specific_day if provided
        if target_date:
            available_slots = [slot for slot in available_slots
                             if datetime.fromisoformat(slot['start']).astimezone(user_tz).date() == target_date]

        if available_slots:
            response += "Available slots:\n"
            current_day = None
            for slot in available_slots:
                start = datetime.fromisoformat(slot['start_local'])
                end = datetime.fromisoformat(slot['end_local'])

                day_str = start.strftime('%A, %B %d')
                if day_str != current_day:
                    current_day = day_str
                    response += f"\n{day_str}:\n"
                response += f"  - {start.strftime('%I:%M %p')} to {end.strftime('%I:%M %p')}\n"
        else:
            response += "No available slots found in the specified time range.\n"

        # Update last_used_at
        supabase.table('google_credentials').update({
            'last_used_at': datetime.now(timezone.utc).isoformat()
        }).eq('user_id', user_id).execute()
        print("*"*50)
        print("called for days ahead", days_ahead)
        print("called for day: ", specific_day)
        print(response)
        return response

    except Exception as e:
        return f"Error fetching availability: {str(e)}"
