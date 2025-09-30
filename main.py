import os
import json
import base64
import asyncio
import websockets
import contextlib
import wave
import io
import time
import pytz
from typing import Any, Dict, Optional
from fastapi import FastAPI, WebSocket, Request, UploadFile, File, Form, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
from supabase import create_client, Client
from openai import OpenAI
from post_call import process_post_call
from langdetect import detect, LangDetectException
from call_recording import CallRecorder

load_dotenv()
# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY') # requires OpenAI Realtime API Access
PORT = int(os.getenv('PORT', 8000))
VOICE = 'shimmer'
openai_client = OpenAI()

# Voice mappings for different languages
# Using Google Chirp3-HD voices for best quality, fallback to Polly for wider language support
VOICE_MAPPINGS = {
    'en': 'Google.en-US-Chirp3-HD-Aoede',  # English
    'es': 'Google.es-ES-Neural2-A',        # Spanish
    'fr': 'Google.fr-FR-Neural2-A',        # French
    'de': 'Google.de-DE-Neural2-A',        # German
    'it': 'Google.it-IT-Neural2-A',        # Italian
    'pt': 'Google.pt-BR-Neural2-A',        # Portuguese
    'nl': 'Google.nl-NL-Neural2-A',        # Dutch
    'ja': 'Google.ja-JP-Neural2-A',        # Japanese
    'ko': 'Google.ko-KR-Neural2-A',        # Korean
    'zh': 'Google.cmn-CN-Neural2-A',       # Chinese (Simplified)
    'ru': 'Google.ru-RU-Neural2-A',        # Russian
    'ar': 'Google.ar-XA-Neural2-A',        # Arabic
    'hi': 'Google.hi-IN-Neural2-A',        # Hindi
    'tr': 'Google.tr-TR-Chirp3-HD-Aoede',        # Turkish (Google's female voice)
    'pl': 'Google.pl-PL-Neural2-A',        # Polish
    'sv': 'Google.sv-SE-Neural2-A',        # Swedish
    'da': 'Google.da-DK-Neural2-A',        # Danish
    'no': 'Google.nb-NO-Neural2-A',        # Norwegian
    'fi': 'Google.fi-FI-Neural2-A',        # Finnish
    'he': 'Google.he-IL-Neural2-A',        # Hebrew
    'id': 'Google.id-ID-Neural2-A',        # Indonesian
    'th': 'Google.th-TH-Neural2-A',        # Thai
    'vi': 'Google.vi-VN-Neural2-A',        # Vietnamese
    'default': 'Google.en-US-Chirp3-HD-Aoede'  # Default to English if language not detected
}

def get_voice_for_text(text: str) -> str:
    """
    Detect the language of the text and return the appropriate voice.
    Falls back to English voice if detection fails.
    """
    try:
        # Detect language
        detected_lang = detect(text)

        # Get the appropriate voice for the detected language
        voice = VOICE_MAPPINGS.get(detected_lang, VOICE_MAPPINGS['default'])

        print(f"Detected language: {detected_lang}, using voice: {voice}")
        return voice

    except LangDetectException as e:
        print(f"Language detection failed: {e}, using default voice")
        return VOICE_MAPPINGS['default']
    except Exception as e:
        print(f"Error in voice selection: {e}, using default voice")
        return VOICE_MAPPINGS['default']

# Google OAuth Configuration - YOUR app's credentials
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')

# Square OAuth Configuration
SQUARE_CLIENT_ID = os.getenv('SQUARE_OAUTH_CLIENT_ID')
SQUARE_CLIENT_SECRET = os.getenv('SQUARE_OAUTH_CLIENT_SECRET')

# Supabase configuration
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError('Missing Supabase credentials. Please set SUPABASE_URL and SUPABASE_KEY in the .env file.')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

async def weather():
    # Legacy stub; unused. Keeping for backward-compatibility.
    await asyncio.sleep(10)
    return "the weather is sunny"


TEMPERATURE = float(os.getenv('TEMPERATURE', 0.8))
LOG_EVENT_TYPES = [
    'response.content.done', 'rate_limits.updated', 'response.done',
    'input_audio_buffer.committed', 'input_audio_buffer.speech_stopped',
    'input_audio_buffer.speech_started', 'session.created'
]
app = FastAPI()

# CORS for frontend OAuth calls
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:4000",
        "http://127.0.0.1:4000",
        "https://app.luron.ai",
        # add your deployed frontend origins here
    ],
    allow_origin_regex=r"https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

# Include outbound call routes if available
try:
    from outbound import app as outbound_app
    # Mount the outbound app at a prefix
    app.mount("/outbound", outbound_app)
except ImportError:
    print("Outbound app not available")
except Exception as e:
    print(f"Error loading outbound app: {e}")

# Google OAuth credential intake endpoint

from fastapi import APIRouter
from gcal import GoogleOAuthPayload, build_credentials, get_calendar_service, get_gmail_service, print_availability, calculate_availability, exchange_authorization_code, refresh_access_token

google_router = APIRouter(prefix="/google", tags=["google"])

@google_router.post("/auth")
async def receive_google_oauth(payload: GoogleOAuthPayload):
    """Accept Google OAuth authorization code or tokens for a user.

    Frontend can send either:
    - authorization_code: Will be exchanged for tokens
    - access_token: Direct token (with optional refresh_token)
    """
    from datetime import datetime, timezone, timedelta

    # Use app's OAuth credentials from environment
    client_id = GOOGLE_CLIENT_ID or payload.client_id
    client_secret = GOOGLE_CLIENT_SECRET or payload.client_secret

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Missing Google OAuth client credentials")

    # Handle authorization code exchange
    if payload.authorization_code:
        try:
            # Exchange authorization code for tokens
            token_response = exchange_authorization_code(
                code=payload.authorization_code,
                client_id=client_id,
                client_secret=client_secret,
                token_uri=payload.token_uri
            )

            # Update payload with received tokens
            payload.access_token = token_response['access_token']
            payload.refresh_token = token_response.get('refresh_token')

            # Calculate expiry time
            expires_in = token_response.get('expires_in', 3600)  # Default to 1 hour
            payload.expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            print(f"Successfully exchanged authorization code for tokens")
            print(f"Got refresh token: {'Yes' if payload.refresh_token else 'No'}")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to exchange authorization code: {e}")

    # Ensure we have required credentials
    if not payload.access_token:
        raise HTTPException(status_code=400, detail="No access_token or authorization_code provided")

    # Update client credentials
    payload.client_id = client_id
    payload.client_secret = client_secret

    try:
        creds = build_credentials(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid credentials payload: {e}")

    # Optionally: perform a lightweight verification call. Gate by requested service.
    try:
        print("/google/auth: credential payload received")
        services: set[str] = set()
        if payload.service:
            # Strict gating: if service is specified, verify only that service
            services.add(payload.service)
        else:
            # Fallback inference from scopes, if provided
            if payload.scopes:
                if any(s.startswith("https://www.googleapis.com/auth/calendar") for s in payload.scopes):
                    services.add("calendar")
                if any(s.startswith("https://www.googleapis.com/auth/gmail") for s in payload.scopes):
                    services.add("gmail")

        if "calendar" in services:
            cal = get_calendar_service(creds)
            # Probe events on primary; compatible with calendar.events.readonly or calendar.readonly
            cal.events().list(calendarId='primary', maxResults=1).execute()
            print(f"Calendar verification successful for user {payload.user_id}")
            print(f"Scopes granted: {payload.scopes}")

        if "gmail" in services:
            gmail = get_gmail_service(creds)
            # Probe profile call
            gmail.users().getProfile(userId="me").execute()
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Credential verification failed: {e}")

    # Store credentials in Supabase
    try:
        # Handle expiry - could be datetime or string after validation
        expiry_dt = None
        if payload.expiry:
            if isinstance(payload.expiry, datetime):
                expiry_dt = payload.expiry
            elif isinstance(payload.expiry, str):
                # Parse string to datetime for Supabase timestamptz
                from dateutil.parser import isoparse
                expiry_dt = isoparse(payload.expiry)

        credentials_data = {
            'user_id': payload.user_id,
            'access_token': payload.access_token,
            'refresh_token': payload.refresh_token,
            'token_uri': payload.token_uri or 'https://oauth2.googleapis.com/token',
            'client_id': client_id,
            'client_secret': client_secret,
            'scopes': payload.scopes,
            'expiry': expiry_dt.isoformat() if expiry_dt else None,
            'service': payload.service or 'calendar'
        }

        # Upsert credentials (insert or update if exists)
        supabase.table('google_credentials').upsert(
            credentials_data,
            on_conflict='user_id'
        ).execute()

        print(f"✅ Stored Google credentials for user: {payload.user_id}")
    except Exception as e:
        print(f"⚠️ Error storing credentials in Supabase: {e}")
        # Continue even if storage fails - credentials are still valid for this session

    # Print the availability to console after successful authentication
    print("\n🔍 FETCHING AND DISPLAYING USER AVAILABILITY...")
    try:
        # Then print availability
        print_availability(creds, days_ahead=7)
    except Exception as e:
        print(f"Error displaying availability: {e}")

    return {"ok": True}

app.include_router(google_router)

# Square OAuth credential intake endpoint
from square_bookings import (
    SquareOAuthPayload,
    exchange_authorization_code as square_exchange_code,
    refresh_access_token as square_refresh_token,
    get_merchant_info,
    list_locations
)

square_router = APIRouter(prefix="/square", tags=["square"])

@square_router.post("/auth")
async def receive_square_oauth(payload: SquareOAuthPayload):
    """Accept Square OAuth authorization code or tokens for a user.

    Frontend can send either:
    - authorization_code: Will be exchanged for tokens
    - access_token: Direct token (with optional refresh_token)
    """
    from datetime import datetime, timezone, timedelta

    # Use app's OAuth credentials from environment
    client_id = SQUARE_CLIENT_ID or payload.client_id
    client_secret = SQUARE_CLIENT_SECRET or payload.client_secret

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Missing Square OAuth client credentials")

    # Handle authorization code exchange
    if payload.authorization_code:
        try:
            # Exchange authorization code for tokens
            token_response = square_exchange_code(
                code=payload.authorization_code,
                client_id=client_id,
                client_secret=client_secret,
                token_uri=payload.token_uri,
                redirect_uri=payload.redirect_uri
            )

            # Update payload with received tokens
            payload.access_token = token_response['access_token']
            payload.refresh_token = token_response.get('refresh_token')
            payload.expires_at = token_response.get('expires_at')
            payload.merchant_id = token_response.get('merchant_id')

            print(f"Successfully exchanged authorization code for tokens")
            print(f"Got refresh token: {'Yes' if payload.refresh_token else 'No'}")
            print(f"Merchant ID: {payload.merchant_id}")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to exchange authorization code: {e}")

    # Ensure we have required credentials
    if not payload.access_token:
        raise HTTPException(status_code=400, detail="No access_token or authorization_code provided")

    # Update client credentials
    payload.client_id = client_id
    payload.client_secret = client_secret

    # Verify credentials by fetching merchant info
    try:
        print("/square/auth: credential payload received")

        merchant_info = get_merchant_info(payload.access_token)
        merchants = merchant_info.get('merchant', [])

        if merchants:
            merchant = merchants[0] if isinstance(merchants, list) else merchants
            payload.merchant_id = merchant.get('id')
            print(f"Square verification successful for user {payload.user_id}")
            print(f"Merchant: {merchant.get('business_name', 'N/A')}")
            print(f"Merchant ID: {payload.merchant_id}")

        # Also fetch locations for display
        locations_info = list_locations(payload.access_token)
        locations = locations_info.get('locations', [])
        print(f"Found {len(locations)} location(s)")
        for loc in locations:
            print(f"  - {loc.get('name', 'Unnamed')}: {loc.get('id')}")

    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Credential verification failed: {e}")

    # Store credentials in Supabase
    try:
        # Handle expires_at - could be datetime or string after validation
        expires_at_dt = None
        if payload.expires_at:
            if isinstance(payload.expires_at, datetime):
                expires_at_dt = payload.expires_at
            elif isinstance(payload.expires_at, str):
                # Parse string to datetime for Supabase timestamptz
                from dateutil.parser import isoparse
                expires_at_dt = isoparse(payload.expires_at)

        credentials_data = {
            'user_id': payload.user_id,
            'access_token': payload.access_token,
            'refresh_token': payload.refresh_token,
            'token_uri': payload.token_uri or 'https://connect.squareup.com/oauth2/token',
            'client_id': client_id,
            'client_secret': client_secret,
            'scopes': payload.scopes,
            'expires_at': expires_at_dt.isoformat() if expires_at_dt else None,
            'merchant_id': payload.merchant_id
        }

        # Upsert credentials (insert or update if exists)
        supabase.table('square_credentials').upsert(
            credentials_data,
            on_conflict='user_id'
        ).execute()

        print(f"✅ Stored Square credentials for user: {payload.user_id}")
    except Exception as e:
        print(f"⚠️ Error storing credentials in Supabase: {e}")
        # Continue even if storage fails - credentials are still valid for this session

    return {"ok": True, "merchant_id": payload.merchant_id}

app.include_router(square_router)


async def get_weather():
    print("started")
    await asyncio.sleep(10)
    print("HEY HEY HEY WHAT'S HAPPENING YOUTUBE")
    return "The weather right now is sunny"

async def set_meeting(user_id: Optional[str] = None,
                      meeting_name: Optional[str] = None,
                      meeting_time: Optional[str] = None,
                      duration_minutes: int = 60,
                      description: Optional[str] = None,
                      location: Optional[str] = None):
    """Schedule a meeting in the user's Google Calendar.
    
    This is a wrapper function that calls the actual implementation in gcal.py.
    
    Args:
        user_id: The user ID whose calendar to update
        meeting_name: Title/summary of the meeting
        meeting_time: ISO format datetime string or natural language time
        duration_minutes: Meeting duration in minutes (default 60)
        description: Optional meeting description
        location: Optional meeting location

    Returns:
        A formatted string with meeting creation status
    """
    from gcal import set_meeting as gcal_set_meeting
    
    # Validate required parameters
    if not user_id:
        raise ValueError("user_id is required")
    if not meeting_name:
        raise ValueError("meeting_name is required")
    if not meeting_time:
        raise ValueError("meeting_time is required")
    
    return await gcal_set_meeting(
        supabase=supabase,
        user_id=user_id,
        meeting_name=meeting_name,
        meeting_time=meeting_time,
        duration_minutes=duration_minutes,
        description=description or "",
        location=location or ""
    )


async def get_availability(user_id: Optional[str] = None, days_ahead: int = 7):
    """Get user's calendar availability for agents to use.
    
    This is a wrapper function that calls the actual implementation in gcal.py.

    Args:
        user_id: The user ID to fetch availability for
        days_ahead: Number of days to check ahead (default 7)

    Returns:
        A formatted string with availability information
    """
    from gcal import get_availability as gcal_get_availability
    
    # Validate required parameters
    if not user_id:
        raise ValueError("user_id is required")
    
    return await gcal_get_availability(
        supabase=supabase,
        user_id=user_id,
        days_ahead=days_ahead
    )


async def end_call(sales_item: Optional[str] = None, summary: Optional[str] = None, caller_number: Optional[str] = None):
    """End the call after recording what the caller is trying to sell.

    This function is used when the caller is identified as trying to sell something.
    It records what they're selling and ends the call politely.

    Args:
        sales_item: Description of what the caller is trying to sell
        summary: Summary of the sales inquiry
        caller_number: The phone number of the caller

    Returns:
        A signal that indicates the call should end
    """
    # Record the sales attempt if sales_item is provided
    if sales_item:
        print(f"Caller trying to sell: {sales_item}")
        # TODO: Optionally store this in the database for tracking
    if summary:
        print(f"Sales summary: {summary}")
        try:
            from email_service import send_simple_email
            # Include caller's number in the email message
            email_message = f"Caller Number: {caller_number or 'Unknown'}\n\n{summary}"
            email_result = send_simple_email(
                to="bridget@wellesleytestosterone.com",
                subject="Sales Inquiry",
                message=email_message
            )
            print(f"End-call email sent: {email_result}")
        except Exception as e:
            print(f"Failed to send end-call email: {e}")

    # Return a signal that indicates the call should end
    return "END_CALL_SIGNAL"

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

# Knowledge base webhook: summarize links+files and attach to agent prompt
@app.post("/webhook/agents/{agent_id}/summarize", response_class=JSONResponse)
async def webhook_agent_summarize(agent_id: str,
                                  links: list[str] | None = Form(None),
                                  url: str | None = Form(None),  # fallback single url
                                  files: list[UploadFile] | None = File(None)):
    try:
        from kb import summarize_and_update_agent  # local import to avoid circular import issues
        link_list = links or ([url] if url else None)
        result = await summarize_and_update_agent(agent_id, link_list, files)
        return JSONResponse(content={"ok": True, **result})
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})

@app.api_route("/twilio/agents/{agent_id}", methods=["GET", "POST"])
async def handle_agent_call(agent_id: str, request: Request):
    """Handle incoming call for specific agent and return TwiML response to connect to Media Stream."""
    print(f"handle_agent_call: Received agent_id: {agent_id}")

    # Fetch agent data from Supabase
    result = supabase.table('agents').select('prompt, welcome_message, user_id').eq('id', agent_id).single().execute()
    agent_data = result.data
    print(f"handle_agent_call: Fetched agent data: welcome_message={agent_data.get('welcome_message')[:50]}...")

    # Store agent data for this call session
    agent_prompt = agent_data.get('prompt')
    agent_welcome = agent_data.get('welcome_message')
    user_id = agent_data.get('user_id')

    form = await request.form()
    call_sid = form.get('CallSid')
    from_number = form.get('From')
    to_number = form.get('To')

    payload = {
        'agent_id': agent_id,
        'user_id': user_id,
        'call_status': 'in-progress',
    }
    payload['twilio_call_sid'] = call_sid
    payload['from_number'] = from_number
    payload['to_number'] = to_number
    supabase.table('calls').insert(payload).execute()

    response = VoiceResponse()

    # Say the welcome message first using appropriate multilingual voice
    if agent_welcome:
        # Detect language and select appropriate voice
        voice = get_voice_for_text(agent_welcome)
        response.say(
            agent_welcome,
            voice=voice
        )

    host = request.url.hostname
    connect = Connect()

    stream_url = f'wss://{host}/media-stream/{agent_id}'
    print(f"handle_agent_call: Stream URL: {stream_url}")
    connect.stream(url=stream_url)
    response.append(connect)

    print(f"handle_agent_call: TwiML Response: {str(response)}")
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream/{agent_id}")
async def handle_media_stream_with_agent(websocket: WebSocket, agent_id: str):
    """Handle WebSocket connections between Twilio and OpenAI for specific agent."""
    print(f"Client connected with agent_id: {agent_id}")
    await websocket.accept()

    # Fetch agent prompt and welcome message from Supabase
    result = supabase.table('agents').select('prompt, welcome_message').eq('id', agent_id).single().execute()
    agent_data = result.data
    agent_prompt = agent_data.get('prompt')
    agent_welcome = agent_data.get('welcome_message')
    print(f"Using agent {agent_id} with custom prompt")

    # Initialize call recorder
    call_recorder = CallRecorder(
        supabase=supabase,
        agent_id=agent_id,
        call_sid=None  # Will be set when we get stream info
    )
    # Remove the queue-based approach - handle audio directly with proper timestamps
    current_assistant_response_start: Optional[float] = None  # Track when assistant starts speaking

    async with websockets.connect(
         f"wss://api.openai.com/v1/realtime?model=gpt-realtime&temperature={TEMPERATURE}",
         additional_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}"
        }
    ) as openai_ws:
        # Per-connection tool queue and worker
        tool_queue: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()
        # Buffer inbound Twilio audio (PCMU/u-law) for post-call transcription
        ulaw_chunks: list[bytes] = []
        # Conversation capture - Initialize with welcome message if present
        conversation: list[dict] = []  # sequence of {role: 'user'|'assistant', text?: str, audio_bytes?: bytes}
        # Add the welcome message to conversation history so AI doesn't repeat it
        if agent_welcome:
            conversation.append({
                "role": "assistant",
                "text": agent_welcome
            })
        is_user_speaking: bool = False
        current_user_buffer: bytearray = bytearray()
        call_started_monotonic: Optional[float] = None
        call_ended_monotonic: Optional[float] = None
        db_call_id: Optional[str] = None
        twilio_call_sid: Optional[str] = None
        should_end_call: bool = False  # Flag to signal call termination
        goodbye_audio_bytes: int = 0    # Bytes of goodbye audio sent after end_call
        call_recording_start_time: float = time.time()  # Reference time for recording

        async def tool_worker():
            nonlocal websocket, should_end_call, goodbye_audio_bytes  # Add needed nonlocals
            while True:
                job = await tool_queue.get()
                if job is None:  # shutdown signal
                    break
                name: str = job.get("name", "")
                call_id: str = job.get("call_id", "")
                args: Dict[str, Any] = job.get("arguments") or {}

                try:
                    # Execute the tool
                    if name == "get_weather":
                        result = await get_weather()
                        output_obj = {"weather": result}
                    elif name == "get_availability":
                        # Get user_id from agent's user_id
                        user_id = args.get("user_id")
                        if not user_id:
                            # Try to get from agent's owner
                            agent_result = supabase.table('agents').select('user_id').eq('id', agent_id).single().execute()
                            if agent_result.data:
                                user_id = agent_result.data.get('user_id')

                        days_ahead = args.get("days_ahead", 7)
                        if not user_id:
                            output_obj = {"error": "user_id is required for availability check"}
                        else:
                            result = await get_availability(
                                user_id=user_id,
                                days_ahead=days_ahead
                            )
                            output_obj = {"availability": result}
                    elif name == "set_meeting":
                        # Get user_id from agent's user_id
                        user_id = args.get("user_id")
                        if not user_id:
                            # Try to get from agent's owner
                            agent_result = supabase.table('agents').select('user_id').eq('id', agent_id).single().execute()
                            if agent_result.data:
                                user_id = agent_result.data.get('user_id')

                        meeting_name = args.get("meeting_name")
                        meeting_time = args.get("meeting_time")
                        duration_minutes = args.get("duration_minutes", 60)
                        description = args.get("description")
                        location = args.get("location")

                        if not user_id:
                            output_obj = {"error": "user_id is required for meeting scheduling"}
                        elif not meeting_name:
                            output_obj = {"error": "meeting_name is required"}
                        elif not meeting_time:
                            output_obj = {"error": "meeting_time is required"}
                        else:
                            result = await set_meeting(
                                user_id=user_id,
                                meeting_name=meeting_name,
                                meeting_time=meeting_time,
                                duration_minutes=duration_minutes,
                                description=description,
                                location=location
                            )
                            output_obj = {"meeting": result}
                    elif name == "get_square_availability":
                        # Get user_id from agent's user_id
                        user_id = args.get("user_id")
                        if not user_id:
                            # Try to get from agent's owner
                            agent_result = supabase.table('agents').select('user_id').eq('id', agent_id).single().execute()
                            if agent_result.data:
                                user_id = agent_result.data.get('user_id')

                        days_ahead = args.get("days_ahead", 7)
                        location_id = args.get("location_id")

                        if not user_id:
                            output_obj = {"error": "user_id is required for availability check"}
                        else:
                            from square_bookings import get_square_availability
                            result = await get_square_availability(
                                supabase=supabase,
                                user_id=user_id,
                                days_ahead=days_ahead,
                                location_id=location_id
                            )
                            output_obj = {"availability": result}
                    elif name == "create_square_booking":
                        # Get user_id from agent's user_id
                        user_id = args.get("user_id")
                        if not user_id:
                            # Try to get from agent's owner
                            agent_result = supabase.table('agents').select('user_id').eq('id', agent_id).single().execute()
                            if agent_result.data:
                                user_id = agent_result.data.get('user_id')

                        booking_time = args.get("booking_time")
                        customer_name = args.get("customer_name")
                        customer_phone = args.get("customer_phone")
                        customer_email = args.get("customer_email")
                        customer_note = args.get("customer_note")
                        location_id = args.get("location_id")

                        # Auto-add caller's phone number if not provided
                        if not customer_phone and db_call_id:
                            try:
                                call_lookup = supabase.table('calls').select('from_number').eq('id', db_call_id).single().execute()
                                if call_lookup.data:
                                    caller_number = call_lookup.data.get('from_number')
                                    if caller_number:
                                        customer_phone = caller_number
                                        print(f"Auto-added caller's phone number: {customer_phone}")
                            except Exception as e:
                                print(f"Could not fetch caller number: {e}")

                        if not user_id:
                            output_obj = {"error": "user_id is required for booking creation"}
                        elif not booking_time:
                            output_obj = {"error": "booking_time is required"}
                        else:
                            from square_bookings import create_square_booking
                            result = await create_square_booking(
                                supabase=supabase,
                                user_id=user_id,
                                booking_time=booking_time,
                                customer_name=customer_name,
                                customer_phone=customer_phone,
                                customer_email=customer_email,
                                customer_note=customer_note,
                                location_id=location_id
                            )
                            output_obj = {"booking": result}
                    elif name == "end_call":
                        # Get sales_item from arguments
                        sales_item = args.get("sales_item")
                        summary = args.get("summary")
                        # Get caller's phone number from the lookup we did earlier
                        caller_number = None
                        if db_call_id:
                            try:
                                lookup = supabase.table('calls').select('from_number').eq('id', db_call_id).single().execute()
                                if lookup.data:
                                    caller_number = lookup.data.get('from_number')
                            except Exception as e:
                                print(f"Failed to get caller number: {e}")
                        result = await end_call(
                            sales_item=sales_item or "",
                            summary=summary or "",
                            caller_number=caller_number or ""
                        )

                        # If this is the end call signal, we need to close the connection
                        if result == "END_CALL_SIGNAL":
                            # Send a final message before closing
                            output_obj = {"message": "Thank you for your time. Have a great day!"}

                            # Set the flag to end the call
                            should_end_call = True
                            goodbye_audio_bytes = 0
                            print("End call signal set - call will terminate after response")
                        else:
                            output_obj = {"status": result}
                    else:
                        output_obj = {"error": f"Unknown tool: {name}"}

                    # Send function_call_output back to the conversation
                    item_event = {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            # API expects a JSON-encoded string
                            "output": json.dumps(output_obj),
                        },
                    }
                    await openai_ws.send(json.dumps(item_event))

                    await openai_ws.send(json.dumps({"type": "response.create"}))
                    
                    # If end_call was triggered, wait for response to be sent
                    if name == "end_call" and should_end_call:
                        await asyncio.sleep(2)  # Give time for the response to be generated and sent
                except Exception as e:
                    # On error, still inform the model so it can recover
                    error_item = {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps({"error": str(e)}),
                        },
                    }
                    try:
                        await openai_ws.send(json.dumps(error_item))
                        await openai_ws.send(json.dumps({"type": "response.create"}))
                    except Exception:
                        pass
                finally:
                    tool_queue.task_done()

        worker_task = asyncio.create_task(tool_worker())

        await send_session_update(openai_ws, agent_prompt, agent_id, agent_welcome)
        stream_sid = None
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, is_user_speaking, current_user_buffer, call_started_monotonic, call_ended_monotonic, twilio_call_sid, db_call_id, should_end_call
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.state.name == 'OPEN':
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        # Decode and buffer raw u-law audio for transcription
                        try:
                            decoded = base64.b64decode(data['media']['payload'])
                            ulaw_chunks.append(decoded)
                            # Record user audio with exact timestamp
                            current_timestamp = time.time() - call_recording_start_time
                            call_recorder.append_user_audio(decoded, timestamp=current_timestamp)
                            if is_user_speaking:
                                current_user_buffer.extend(decoded)
                        except Exception:
                            pass
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {stream_sid}")
                        # Capture CallSid and resolve DB call id for later update
                        try:
                            twilio_call_sid = data['start'].get('callSid')
                            # Set call_sid for recorder and reset timing
                            call_recorder.call_sid = twilio_call_sid
                            call_recording_start_time = time.time()
                            call_recorder.call_start_time = call_recording_start_time
                            if twilio_call_sid:
                                try:
                                    lookup = supabase.table('calls').select('id, from_number').eq('twilio_call_sid', twilio_call_sid).eq('agent_id', agent_id).order('created_at', desc=True).limit(1).execute()
                                    if lookup.data:
                                        db_call_id = lookup.data[0].get('id')
                                        from_number = lookup.data[0].get('from_number')
                                        print(f"Call from: {from_number}")
                                except Exception as e:
                                    print(f"Failed to lookup call record: {e}")
                        except Exception:
                            pass
                        if call_started_monotonic is None:
                            call_started_monotonic = time.monotonic()
                        # Welcome message is now handled by Twilio's .say() before connecting
                    elif data['event'] == 'stop':
                        # Call ending
                        print(f"Stream stopped {stream_sid}")
                        if is_user_speaking and current_user_buffer:
                            conversation.append({
                                "role": "user",
                                "audio_bytes": bytes(current_user_buffer)
                            })
                            is_user_speaking = False
                        call_ended_monotonic = time.monotonic()
                        if openai_ws.state.name == 'OPEN':
                            await openai_ws.close()
                        return
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.state.name == 'OPEN':
                    await openai_ws.close()
            except RuntimeError as e:
                # This can happen if the websocket is closed by the other task while we're awaiting reads
                print(f"Receive loop ended due to runtime error: {e}")
                with contextlib.suppress(Exception):
                    await openai_ws.close()
        # Removed the queue processor - we'll handle audio directly with timestamps

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, is_user_speaking, current_user_buffer, should_end_call, goodbye_audio_bytes, call_ended_monotonic, current_assistant_response_start
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)
                    if response['type'] == 'session.updated':
                        print("Session updated successfully:", response)

                    if response['type'] == 'input_audio_buffer.speech_started':
                        # Begin capturing a user utterance and interrupt assistant
                        current_timestamp = time.time() - call_recording_start_time
                        is_user_speaking = True
                        current_user_buffer = bytearray()

                        # IMPORTANT: User barge-in - notify recorder to cut off assistant audio
                        call_recorder.user_started_speaking(timestamp=current_timestamp)

                        # Reset assistant response tracking when user interrupts
                        current_assistant_response_start = None

                        # Clear Twilio's audio buffer
                        clear_message = {
                            "event": "clear",
                            "streamSid": stream_sid
                        }
                        await websocket.send_json(clear_message)
                        # Cancel OpenAI's response
                        cancel_message = {
                            "type": "response.cancel"
                        }
                        await openai_ws.send(json.dumps(cancel_message)) 

                    if response['type'] == 'input_audio_buffer.speech_stopped':
                        # Finish capturing the current user utterance
                        current_timestamp = time.time() - call_recording_start_time
                        if is_user_speaking and current_user_buffer:
                            conversation.append({
                                "role": "user",
                                "audio_bytes": bytes(current_user_buffer)
                            })
                        is_user_speaking = False

                        # Notify recorder that user stopped speaking
                        call_recorder.user_stopped_speaking(timestamp=current_timestamp)

                        # Mark that assistant can start a new response
                        current_assistant_response_start = None

                    
                    if response['type'] == 'response.output_audio.delta' and response.get('delta'):
                        # Audio from OpenAI
                        try:
                            raw = base64.b64decode(response['delta'])
                            current_timestamp = time.time() - call_recording_start_time

                            # Track start of new assistant response
                            if current_assistant_response_start is None:
                                current_assistant_response_start = current_timestamp
                                call_recorder.assistant_started_speaking(timestamp=current_timestamp)
                                print(f"Assistant starting new turn at {current_timestamp:.2f}s")

                            # Directly append to recorder with exact timestamp
                            call_recorder.append_assistant_audio(raw, timestamp=current_timestamp)

                            if should_end_call:
                                goodbye_audio_bytes += len(raw)
                            audio_payload = base64.b64encode(raw).decode('utf-8')
                            audio_delta = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": audio_payload
                                }
                            }
                            await websocket.send_json(audio_delta)
                        except Exception as e:
                            print(f"Error processing audio data: {e}")
                    # Handle response completion for turn tracking
                    if response.get('type') == 'response.audio_done':
                        # Audio response completed - reset for next turn
                        current_timestamp = time.time() - call_recording_start_time
                        if current_assistant_response_start is not None:
                            call_recorder.assistant_stopped_speaking(timestamp=current_timestamp)
                            print(f"Assistant audio response completed at {current_timestamp:.2f}s")
                        current_assistant_response_start = None

                    # Detect function calling and queue tools; also capture assistant text
                    if response.get('type') == 'response.done':
                        # If we were asked to end the call, wait for the goodbye audio playout time
                        if should_end_call:
                            status = (response.get('response') or {}).get('status')
                            if status == 'completed':
                                # u-law PCMU is 8000 bytes/sec; add small safety margin + extra 1s per request
                                wait_seconds = min(10.0, (goodbye_audio_bytes / 8000.0) + 0.5)
                                print(f"Goodbye completed. Waiting {wait_seconds:.2f}s for playout, then hanging up.")
                                await asyncio.sleep(wait_seconds)
                                call_ended_monotonic = time.monotonic()
                                with contextlib.suppress(Exception):
                                    await websocket.close()
                                with contextlib.suppress(Exception):
                                    await openai_ws.close()
                                return
                            else:
                                # Not a completed response (e.g., cancelled by turn_detected). Do not hang up yet.
                                goodbye_audio_bytes = 0
                        try:
                            out = response.get('response', {}).get('output', [])
                            # Extract assistant message text/transcript for transcript log
                            extracted_texts: list[str] = []
                            for item in out:
                                if isinstance(item, dict) and item.get('type') == 'message':
                                    for piece in (item.get('content', []) or []):
                                        if not isinstance(piece, dict):
                                            continue
                                        if piece.get('type') == 'output_text' and 'text' in piece:
                                            extracted_texts.append(piece['text'])
                                        elif piece.get('type') == 'output_audio' and 'transcript' in piece:
                                            extracted_texts.append(piece['transcript'])
                            if extracted_texts:
                                conversation.append({
                                    "role": "assistant",
                                    "text": " ".join(t for t in extracted_texts if t)
                                })
                            for item in out:
                                if item.get('type') == 'function_call':
                                    name = item.get('name')
                                    call_id = item.get('call_id')
                                    args_json = item.get('arguments') or '{}'
                                    try:
                                        args = json.loads(args_json)
                                    except Exception:
                                        args = {}
                                    
                                    # Queue the tool execution without interrupting current speech
                                    await tool_queue.put({
                                        "name": name,
                                        "call_id": call_id,
                                        "arguments": args,
                                    })
                        except Exception as e:
                            print(f"Error handling function call: {e}")
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")
        try:
            # Guard against exceptions bubbling up; we'll still run cleanup in finally
            await asyncio.gather(receive_from_twilio(), send_to_twilio())
        except Exception as e:
            print(f"Error during media loop: {e}")
        finally:
            # No queue to process anymore - audio is handled directly

            # Stop worker gracefully
            try:
                await tool_queue.put(None)  # Signal shutdown
            except Exception:
                pass
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
            
            # Ensure websockets are closed
            try:
                await openai_ws.close()
            except Exception:
                pass
            try:
                await websocket.close()
            except Exception:
                pass
                
            # Save recording asynchronously (non-blocking)
            recording_task = asyncio.create_task(call_recorder.save_recording())

            # After call ends, assemble transcript and update DB
            await process_post_call(
                conversation=conversation,
                agent_id=agent_id,
                db_call_id=db_call_id,
                twilio_call_sid=twilio_call_sid,
                call_started_monotonic=call_started_monotonic,
                call_ended_monotonic=call_ended_monotonic,
            )

            # Wait for recording to complete and update DB with recording URL
            try:
                recording_url = await recording_task
                if recording_url and db_call_id:
                    # Update call record with recording URL
                    supabase.table('calls').update({
                        'recording_url': recording_url,
                        'recording_duration': call_recorder.get_duration_seconds()
                    }).eq('id', db_call_id).execute()
                    print(f"✅ Recording URL saved to database: {recording_url}")
            except Exception as e:
                print(f"❌ Failed to save recording URL: {e}")



async def send_session_update(openai_ws, instructions, agent_id=None, welcome_message=None):
    """Send session update to OpenAI WebSocket."""
    # Start with empty tools list
    tools = []

    print("*******************************")
    print("AGENT ID: ", agent_id)

    # Check if agent has Square enabled
    agent_has_square = False
    if agent_id:
        try:
            agent_result = supabase.table('agents').select('square').eq('id', agent_id).single().execute()
            if agent_result.data:
                agent_has_square = agent_result.data.get('square', False)
                print(f"Agent has Square enabled: {agent_has_square}")
        except Exception as e:
            print(f"Could not check Square status for agent: {e}")

    # Add Square tools if enabled for this agent
    if agent_has_square:
        tools.extend([
            {
                "type": "function",
                "name": "get_square_availability",
                "description": "Check Square booking availability. Use this to see what appointment times are available for scheduling.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user ID to check availability for (optional, uses agent's owner if not provided)"
                        },
                        "days_ahead": {
                            "type": "integer",
                            "description": "Number of days ahead to check availability (default: 7)"
                        },
                        "location_id": {
                            "type": "string",
                            "description": "Optional Square location ID (uses first location if not provided)"
                        }
                    },
                    "required": []
                }
            },
            {
                "type": "function",
                "name": "create_square_booking",
                "description": "Create a booking appointment in Square. Use this to schedule appointments for customers. Always ask for the customer's name before booking.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "booking_time": {
                            "type": "string",
                            "description": "The date and time for the booking (e.g., '2024-12-25 14:00' or 'tomorrow at 2pm')"
                        },
                        "customer_name": {
                            "type": "string",
                            "description": "Customer's full name (e.g., 'John Smith')"
                        },
                        "customer_phone": {
                            "type": "string",
                            "description": "Optional customer phone number"
                        },
                        "customer_email": {
                            "type": "string",
                            "description": "Optional customer email address"
                        },
                        "customer_note": {
                            "type": "string",
                            "description": "Optional note or message from the customer"
                        },
                        "location_id": {
                            "type": "string",
                            "description": "Optional Square location ID (uses first location if not provided)"
                        },
                        "user_id": {
                            "type": "string",
                            "description": "The user ID whose Square account to use (optional, uses agent's owner if not provided)"
                        }
                    },
                    "required": ["booking_time", "customer_name"]
                }
            }
        ])

    # Additional tools for specific agent
    if agent_id == "398d539b-cc3b-430c-bbc8-3394d940c03c":
        tools.extend([
            {
                "type": "function",
                "name": "get_weather",
                "description": "Get the current weather conditions.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "type": "function",
                "name": "get_availability",
                "description": "Check the user's calendar availability for scheduling meetings or appointments.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user ID to check availability for (optional, uses agent's owner if not provided)"
                        },
                        "days_ahead": {
                            "type": "integer",
                            "description": "Number of days ahead to check availability (default: 7)"
                        }
                    },
                    "required": []
                }
            },
            {
                "type": "function",
                "name": "set_meeting",
                "description": "Schedule a meeting in the user's Google Calendar.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "meeting_name": {
                            "type": "string",
                            "description": "The title or name of the meeting"
                        },
                        "meeting_time": {
                            "type": "string",
                            "description": "The date and time for the meeting (e.g., '2024-12-25 14:00' or 'tomorrow at 2pm')"
                        },
                        "duration_minutes": {
                            "type": "integer",
                            "description": "Duration of the meeting in minutes (default: 60)"
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional description or agenda for the meeting"
                        },
                        "location": {
                            "type": "string",
                            "description": "Optional location for the meeting"
                        },
                        "user_id": {
                            "type": "string",
                            "description": "The user ID whose calendar to update (optional, uses agent's owner if not provided)"
                        }
                    },
                    "required": ["meeting_name", "meeting_time"]
                }
            },
            {
                "type": "function",
                "name": "end_call",
                "description": "End the call when the caller is trying to sell something. First record what they're trying to sell, say thank you, then end the call politely. Also accepts a short summary text for follow-up email which will include the caller's phone number.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sales_item": {
                            "type": "string",
                            "description": "Description of what the caller is trying to sell"
                        },
                        "summary": {
                            "type": "string",
                            "description": "Short summary of the sales inquiry to email after call"
                        }
                    },
                    "required": []
                }
            }
        ])

    session_update = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": "gpt-realtime",
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcmu"},
                    "turn_detection": {"type": "server_vad"}
                },
                "output": {
                    "format": {"type": "audio/pcmu"},
                    "voice": VOICE
                }
            },
            "instructions": instructions + "\n\nCRITICAL RULES:\n1. NEVER greet the caller - no 'hello', 'hi', 'welcome', or any greeting words.\n2. The caller has already been greeted by the system.\n3. Wait for the caller to speak first.\n4. Only respond to what the caller says - do not initiate conversation.\n5. If the caller greets you, acknowledge briefly without greeting back (e.g., 'How can I help you?' instead of 'Hello').",
            # Configure function calling tools at the session level
            "tools": tools,
            "tool_choice": "auto",
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
