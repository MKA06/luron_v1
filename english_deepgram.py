import os
import json
import base64
import asyncio
import re
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
from openai import OpenAI, AsyncOpenAI
from post_call import process_post_call
from langdetect import detect, LangDetectException
from call_recording import CallRecorder

load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
DEEPGRAM_API_KEY = os.getenv('DEEPGRAM_API_KEY')
ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY')
PORT = int(os.getenv('PORT', 8000))
TEMPERATURE = float(os.getenv('TEMPERATURE', 0.8))

# Voice mappings for different languages
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
    'tr': 'Google.tr-TR-Chirp3-HD-Aoede',  # Turkish (Google's female voice)
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

# ElevenLabs voice mappings for different languages
ELEVENLABS_VOICE_MAPPINGS = {
    'en': 'RXtWW6etvimS8QJ5nhVk',  # Antoni - English
    'default': 'RXtWW6etvimS8QJ5nhVk'  # Default to English voice
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

def get_elevenlabs_voice_for_text(text: str) -> str:
    """
    Detect the language of the text and return the appropriate ElevenLabs voice.
    Falls back to English voice if detection fails.
    """
    try:
        # Detect language
        detected_lang = detect(text)

        # Get the appropriate voice for the detected language
        voice = ELEVENLABS_VOICE_MAPPINGS.get(detected_lang, ELEVENLABS_VOICE_MAPPINGS['default'])

        print(f"Detected language: {detected_lang}, using ElevenLabs voice: {voice}")
        return voice

    except LangDetectException as e:
        print(f"Language detection failed: {e}, using default voice")
        return ELEVENLABS_VOICE_MAPPINGS['default']
    except Exception as e:
        print(f"Error in voice selection: {e}, using default voice")
        return ELEVENLABS_VOICE_MAPPINGS['default']

# Google OAuth Configuration
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

# OpenAI client
openai_client = OpenAI()

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
    ],
    allow_origin_regex=r"https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')
if not DEEPGRAM_API_KEY:
    raise ValueError('Missing the Deepgram API key. Please set it in the .env file.')
if not ELEVENLABS_API_KEY:
    raise ValueError('Missing the ElevenLabs API key. Please set it in the .env file.')

# Initialize async OpenAI client for streaming
async_openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

async def weather():
    # Legacy stub; unused. Keeping for backward-compatibility.
    await asyncio.sleep(10)
    return "the weather is sunny"

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
    return {"message": "Twilio Media Stream Server with Deepgram Pipeline is running!"}

# Include outbound call routes if available
try:
    from outbound import (
        create_batch_outbound_calls,
        BatchCallRequest,
        outbound_twiml
    )

    @app.post("/batch-call")
    async def batch_call_endpoint(batch_request: BatchCallRequest, request: Request):
        """Proxy endpoint for batch calling - delegates to outbound.py logic"""
        return await create_batch_outbound_calls(batch_request, request)

    @app.api_route("/outbound-twiml", methods=["GET", "POST"])
    async def outbound_twiml_endpoint(request: Request, session_id: str = None):
        """Proxy endpoint for outbound TwiML - delegates to outbound.py logic"""
        return await outbound_twiml(request, session_id)

    # Note: Outbound media stream is handled by the main /media-stream/{agent_id} endpoint
    # The agent_id can be the session_id for outbound calls

    print("✅ Outbound endpoints loaded successfully (batch-call, twiml)")
except ImportError as e:
    print(f"Outbound app not available: {e}")
except Exception as e:
    print(f"Error loading outbound app: {e}")

# Google OAuth credential intake endpoint
from fastapi import APIRouter
from gcal import GoogleOAuthPayload, build_credentials, get_calendar_service, get_gmail_service, print_availability, calculate_availability, exchange_authorization_code, refresh_access_token

google_router = APIRouter(prefix="/google", tags=["google"])

@google_router.post("/auth")
async def receive_google_oauth(payload: GoogleOAuthPayload):
    """Accept Google OAuth authorization code or tokens for a user."""
    from datetime import datetime, timezone, timedelta

    client_id = GOOGLE_CLIENT_ID or payload.client_id
    client_secret = GOOGLE_CLIENT_SECRET or payload.client_secret

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Missing Google OAuth client credentials")

    if payload.authorization_code:
        try:
            token_response = exchange_authorization_code(
                code=payload.authorization_code,
                client_id=client_id,
                client_secret=client_secret,
                token_uri=payload.token_uri
            )

            payload.access_token = token_response['access_token']
            payload.refresh_token = token_response.get('refresh_token')

            expires_in = token_response.get('expires_in', 3600)
            payload.expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            print(f"Successfully exchanged authorization code for tokens")
            print(f"Got refresh token: {'Yes' if payload.refresh_token else 'No'}")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to exchange authorization code: {e}")

    if not payload.access_token:
        raise HTTPException(status_code=400, detail="No access_token or authorization_code provided")

    payload.client_id = client_id
    payload.client_secret = client_secret

    try:
        creds = build_credentials(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid credentials payload: {e}")

    try:
        print("/google/auth: credential payload received")
        services: set[str] = set()
        if payload.service:
            services.add(payload.service)
        else:
            if payload.scopes:
                if any(s.startswith("https://www.googleapis.com/auth/calendar") for s in payload.scopes):
                    services.add("calendar")
                if any(s.startswith("https://www.googleapis.com/auth/gmail") for s in payload.scopes):
                    services.add("gmail")

        if "calendar" in services:
            cal = get_calendar_service(creds)
            cal.events().list(calendarId='primary', maxResults=1).execute()
            print(f"Calendar verification successful for user {payload.user_id}")
            print(f"Scopes granted: {payload.scopes}")

        if "gmail" in services:
            gmail = get_gmail_service(creds)
            gmail.users().getProfile(userId="me").execute()
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Credential verification failed: {e}")

    try:
        expiry_dt = None
        if payload.expiry:
            if isinstance(payload.expiry, datetime):
                expiry_dt = payload.expiry
            elif isinstance(payload.expiry, str):
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

        supabase.table('google_credentials').upsert(
            credentials_data,
            on_conflict='user_id'
        ).execute()

        print(f"✅ Stored Google credentials for user: {payload.user_id}")
    except Exception as e:
        print(f"⚠️ Error storing credentials in Supabase: {e}")

    print("\n🔍 FETCHING AND DISPLAYING USER AVAILABILITY...")
    try:
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
    """Accept Square OAuth authorization code or tokens for a user."""
    from datetime import datetime, timezone, timedelta

    client_id = SQUARE_CLIENT_ID or payload.client_id
    client_secret = SQUARE_CLIENT_SECRET or payload.client_secret

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Missing Square OAuth client credentials")

    if payload.authorization_code:
        try:
            token_response = square_exchange_code(
                code=payload.authorization_code,
                client_id=client_id,
                client_secret=client_secret,
                token_uri=payload.token_uri,
                redirect_uri=payload.redirect_uri
            )

            payload.access_token = token_response['access_token']
            payload.refresh_token = token_response.get('refresh_token')
            payload.expires_at = token_response.get('expires_at')
            payload.merchant_id = token_response.get('merchant_id')

            print(f"Successfully exchanged authorization code for tokens")
            print(f"Got refresh token: {'Yes' if payload.refresh_token else 'No'}")
            print(f"Merchant ID: {payload.merchant_id}")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to exchange authorization code: {e}")

    if not payload.access_token:
        raise HTTPException(status_code=400, detail="No access_token or authorization_code provided")

    payload.client_id = client_id
    payload.client_secret = client_secret

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

        locations_info = list_locations(payload.access_token)
        locations = locations_info.get('locations', [])
        print(f"Found {len(locations)} location(s)")
        for loc in locations:
            print(f"  - {loc.get('name', 'Unnamed')}: {loc.get('id')}")

    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Credential verification failed: {e}")

    try:
        expires_at_dt = None
        if payload.expires_at:
            if isinstance(payload.expires_at, datetime):
                expires_at_dt = payload.expires_at
            elif isinstance(payload.expires_at, str):
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

        supabase.table('square_credentials').upsert(
            credentials_data,
            on_conflict='user_id'
        ).execute()

        print(f"✅ Stored Square credentials for user: {payload.user_id}")
    except Exception as e:
        print(f"⚠️ Error storing credentials in Supabase: {e}")

    return {"ok": True, "merchant_id": payload.merchant_id}

app.include_router(square_router)

# Knowledge base webhook: summarize links+files and attach to agent prompt
@app.post("/webhook/agents/{agent_id}/summarize", response_class=JSONResponse)
async def webhook_agent_summarize(agent_id: str,
                                  links: list[str] | None = Form(None),
                                  url: str | None = Form(None),
                                  files: list[UploadFile] | None = File(None)):
    try:
        from kb import summarize_and_update_agent
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

    agent_prompt = agent_data.get('prompt')
    agent_welcome = agent_data.get('welcome_message')
    user_id = agent_data.get('user_id')

    # Guardrail: Check if free tier user has exceeded 10 minutes (600 seconds)
    print(f"🔍 Checking subscription guardrail for user_id: {user_id}")
    if user_id:
        try:
            profile_result = supabase.table('profiles').select('subscription_tier, monthly_duration').eq('user_id', user_id).single().execute()
            print(f"📊 Profile result: {profile_result.data}")
            if profile_result.data:
                subscription_tier = profile_result.data.get('subscription_tier')
                monthly_duration = profile_result.data.get('monthly_duration', 0)

                print(f"💳 Subscription tier: {subscription_tier}, Monthly duration: {monthly_duration} seconds ({monthly_duration / 60:.2f} minutes)")

                if subscription_tier == 'free' and monthly_duration > 600:
                    print(f"❌ Call rejected: Free tier user {user_id} has exceeded 10 minutes (current: {monthly_duration / 60:.2f} minutes)")
                    response = VoiceResponse()
                    response.say(
                        "Your free tier minutes have been exceeded. Please upgrade your subscription to continue using this service.",
                        voice='Google.en-US-Chirp3-HD-Aoede'
                    )
                    return HTMLResponse(content=str(response), media_type="application/xml")
                else:
                    print(f"✅ Call allowed: tier={subscription_tier}, duration={monthly_duration}s")
        except Exception as e:
            print(f"⚠️ Error checking subscription tier: {e}")
            import traceback
            traceback.print_exc()
    else:
        print(f"⚠️ No user_id found for agent")

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
    """Handle WebSocket connections between Twilio and custom STT+LLM+TTS pipeline for specific agent."""
    print(f"Client connected with agent_id: {agent_id}")
    await websocket.accept()

    # Fetch agent prompt and welcome message from Supabase
    result = supabase.table('agents').select('prompt, welcome_message, square').eq('id', agent_id).single().execute()
    agent_data = result.data
    agent_prompt = agent_data.get('prompt')
    agent_welcome = agent_data.get('welcome_message')
    agent_has_square = agent_data.get('square', False)
    print(f"Using agent {agent_id} with custom prompt")
    print(f"Agent has Square enabled: {agent_has_square}")

    # Initialize call recorder
    call_recorder = CallRecorder(
        supabase=supabase,
        agent_id=agent_id,
        call_sid=None  # Will be set when we get stream info
    )

    stream_sid = None
    conversation_history = [{"role": "system", "content": agent_prompt}]

    # Conversation capture for post-call processing
    conversation: list[dict] = []
    if agent_welcome:
        conversation.append({
            "role": "assistant",
            "text": agent_welcome
        })

    # Barge-in state management
    is_ai_speaking = False
    is_user_speaking = False
    cancel_ai_response = asyncio.Event()

    # Generation tracking to prevent old responses from playing
    current_generation = 0
    audio_generation = 0
    flush_elevenlabs = asyncio.Event()

    # Call state tracking
    call_started_monotonic: Optional[float] = None
    call_ended_monotonic: Optional[float] = None
    db_call_id: Optional[str] = None
    twilio_call_sid: Optional[str] = None
    should_end_call: bool = False
    call_recording_start_time: float = time.time()

    # Tool execution queue
    tool_queue: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()

    # Connect to Deepgram STT
    deepgram_url = f"wss://api.deepgram.com/v1/listen?model=nova-3&language=en&encoding=mulaw&sample_rate=8000&channels=1&interim_results=true"

    try:
        async with websockets.connect(
            deepgram_url,
            additional_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"}
        ) as deepgram_ws:
            print("Connected to Deepgram (Nova-3)")

            # Queue for LLM processing
            transcript_queue = asyncio.Queue()
            # Queue for TTS processing
            tts_queue = asyncio.Queue()

            async def tool_worker():
                """Process tool calls asynchronously"""
                nonlocal websocket, should_end_call
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
                            user_id = args.get("user_id")
                            if not user_id:
                                agent_result = supabase.table('agents').select('user_id').eq('id', agent_id).single().execute()
                                if agent_result.data:
                                    user_id = agent_result.data.get('user_id')
                            days_ahead = args.get("days_ahead", 7)
                            if not user_id:
                                output_obj = {"error": "user_id is required for availability check"}
                            else:
                                result = await get_availability(user_id=user_id, days_ahead=days_ahead)
                                output_obj = {"availability": result}
                        elif name == "set_meeting":
                            user_id = args.get("user_id")
                            if not user_id:
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
                            user_id = args.get("user_id")
                            if not user_id:
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
                            user_id = args.get("user_id")
                            if not user_id:
                                agent_result = supabase.table('agents').select('user_id').eq('id', agent_id).single().execute()
                                if agent_result.data:
                                    user_id = agent_result.data.get('user_id')
                            booking_time = args.get("booking_time")
                            customer_name = args.get("customer_name")
                            customer_phone = args.get("customer_phone")
                            customer_email = args.get("customer_email")
                            customer_note = args.get("customer_note")
                            location_id = args.get("location_id")
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
                            sales_item = args.get("sales_item")
                            summary = args.get("summary")
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
                            if result == "END_CALL_SIGNAL":
                                output_obj = {"message": "Thank you for your time. Have a great day!"}
                                should_end_call = True
                                print("End call signal set - call will terminate after response")
                            else:
                                output_obj = {"status": result}
                        else:
                            output_obj = {"error": f"Unknown tool: {name}"}

                        # Add tool result to conversation history for LLM context
                        conversation_history.append({
                            "role": "function",
                            "name": name,
                            "content": json.dumps(output_obj)
                        })

                        # Queue the result for LLM to generate a response
                        await transcript_queue.put(f"[TOOL_RESULT:{name}] {json.dumps(output_obj)}")

                    except Exception as e:
                        print(f"Error executing tool {name}: {e}")
                        error_obj = {"error": str(e)}
                        conversation_history.append({
                            "role": "function",
                            "name": name,
                            "content": json.dumps(error_obj)
                        })
                        await transcript_queue.put(f"[TOOL_ERROR:{name}] {json.dumps(error_obj)}")
                    finally:
                        tool_queue.task_done()

            worker_task = asyncio.create_task(tool_worker())

            async def send_deepgram_keepalive():
                """Send keepalive messages to Deepgram to maintain connection."""
                try:
                    while True:
                        await asyncio.sleep(5)
                        if deepgram_ws.state.name == 'OPEN':
                            await deepgram_ws.send(json.dumps({"type": "KeepAlive"}))
                except Exception as e:
                    print(f"Error in send_deepgram_keepalive: {e}")

            async def receive_from_twilio():
                """Receive audio data from Twilio and send it to Deepgram."""
                nonlocal stream_sid, call_started_monotonic, call_ended_monotonic, twilio_call_sid, db_call_id, should_end_call
                try:
                    async for message in websocket.iter_text():
                        data = json.loads(message)
                        if data['event'] == 'media':
                            # Forward mulaw audio to Deepgram
                            audio_payload = base64.b64decode(data['media']['payload'])
                            await deepgram_ws.send(audio_payload)
                            # Record user audio with exact timestamp
                            current_timestamp = time.time() - call_recording_start_time
                            call_recorder.append_user_audio(audio_payload, timestamp=current_timestamp)
                        elif data['event'] == 'start':
                            stream_sid = data['start']['streamSid']
                            print(f"Incoming stream has started {stream_sid}")
                            # Capture CallSid and resolve DB call id
                            try:
                                twilio_call_sid = data['start'].get('callSid')
                                call_recorder.call_sid = twilio_call_sid
                                call_recorder.call_start_time = time.time()
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
                        elif data['event'] == 'stop':
                            print(f"Stream stopped {stream_sid}")
                            call_ended_monotonic = time.monotonic()
                            break
                except WebSocketDisconnect:
                    print("Client disconnected from Twilio")
                    call_ended_monotonic = time.monotonic()
                except Exception as e:
                    print(f"Error in receive_from_twilio: {e}")

            async def process_deepgram_transcripts():
                """Receive transcripts from Deepgram and queue them for LLM."""
                nonlocal is_ai_speaking, is_user_speaking, current_generation
                final_transcript_buffer = ""
                last_interim_transcript = ""

                try:
                    async for message in deepgram_ws:
                        data = json.loads(message)

                        if data.get('type') == 'Results':
                            transcript = data.get('channel', {}).get('alternatives', [{}])[0].get('transcript', '')
                            is_final = data.get('is_final', False)
                            speech_final = data.get('speech_final', False)

                            if transcript and len(transcript.strip()) > 0:
                                if not is_user_speaking:
                                    is_user_speaking = True
                                    # Mark user started speaking for recording
                                    current_timestamp = time.time() - call_recording_start_time
                                    call_recorder.user_started_speaking(timestamp=current_timestamp)

                                    # Check for barge-in
                                    if is_ai_speaking:
                                        print(f"🔴 BARGE-IN DETECTED: User speaking while AI is outputting audio")
                                        current_generation += 1
                                        print(f"🔄 Generation incremented to {current_generation}")
                                        flush_elevenlabs.set()

                                        if stream_sid:
                                            clear_message = {
                                                "event": "clear",
                                                "streamSid": stream_sid
                                            }
                                            await websocket.send_json(clear_message)

                                        cancel_ai_response.set()
                                        is_ai_speaking = False

                                        while not tts_queue.empty():
                                            try:
                                                tts_queue.get_nowait()
                                            except asyncio.QueueEmpty:
                                                break

                                        final_transcript_buffer = ""
                                        last_interim_transcript = ""
                                        print("✅ AI response cancelled, listening to user")

                                print(f"Deepgram transcript (final={is_final}, speech_final={speech_final}): {transcript}")

                                if not is_final:
                                    last_interim_transcript = transcript
                                else:
                                    if final_transcript_buffer:
                                        final_transcript_buffer += " " + transcript
                                    else:
                                        final_transcript_buffer = transcript
                                    print(f"📝 Accumulated final: '{final_transcript_buffer}'")

                                    if speech_final:
                                        print(f"📤 Processing complete utterance: '{final_transcript_buffer}'")
                                        # Add to conversation tracking
                                        conversation.append({
                                            "role": "user",
                                            "text": final_transcript_buffer
                                        })
                                        await transcript_queue.put(final_transcript_buffer)
                                        is_user_speaking = False
                                        # Mark user stopped speaking for recording
                                        current_timestamp = time.time() - call_recording_start_time
                                        call_recorder.user_stopped_speaking(timestamp=current_timestamp)
                                        final_transcript_buffer = ""
                                        last_interim_transcript = ""

                        elif data.get('type') == 'UtteranceEnd':
                            print("Utterance ended")
                            transcript_to_process = final_transcript_buffer or last_interim_transcript

                            if transcript_to_process and transcript_to_process.strip():
                                print(f"📤 Processing on UtteranceEnd: '{transcript_to_process}'")
                                conversation.append({
                                    "role": "user",
                                    "text": transcript_to_process
                                })
                                await transcript_queue.put(transcript_to_process)

                            if is_user_speaking:
                                current_timestamp = time.time() - call_recording_start_time
                                call_recorder.user_stopped_speaking(timestamp=current_timestamp)
                            is_user_speaking = False
                            final_transcript_buffer = ""
                            last_interim_transcript = ""

                except Exception as e:
                    print(f"Error in process_deepgram_transcripts: {e}")

            async def process_llm():
                """Process transcripts with GPT-4o and queue responses for TTS."""
                nonlocal current_generation, should_end_call

                # Build tools list based on agent configuration
                tools = []

                # Add Square tools if enabled
                if agent_has_square:
                    tools.extend([
                        {
                            "type": "function",
                            "function": {
                                "name": "get_square_availability",
                                "description": "Check Square booking availability",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "user_id": {"type": "string", "description": "User ID"},
                                        "days_ahead": {"type": "integer", "description": "Days ahead (default 7)"},
                                        "location_id": {"type": "string", "description": "Location ID"}
                                    },
                                    "required": []
                                }
                            }
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "create_square_booking",
                                "description": "Create a Square booking",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "booking_time": {"type": "string", "description": "Booking time"},
                                        "customer_name": {"type": "string", "description": "Customer name"},
                                        "customer_phone": {"type": "string", "description": "Customer phone"},
                                        "customer_email": {"type": "string", "description": "Customer email"},
                                        "customer_note": {"type": "string", "description": "Customer note"},
                                        "location_id": {"type": "string", "description": "Location ID"}
                                    },
                                    "required": ["booking_time", "customer_name"]
                                }
                            }
                        }
                    ])

                # Add agent-specific tools (for specific agent ID)
                if agent_id == "398d539b-cc3b-430c-bbc8-3394d940c03c":
                    tools.extend([
                        {
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "description": "Get current weather",
                                "parameters": {"type": "object", "properties": {}, "required": []}
                            }
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "get_availability",
                                "description": "Check calendar availability",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "user_id": {"type": "string"},
                                        "days_ahead": {"type": "integer"}
                                    },
                                    "required": []
                                }
                            }
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "set_meeting",
                                "description": "Schedule a meeting",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "meeting_name": {"type": "string"},
                                        "meeting_time": {"type": "string"},
                                        "duration_minutes": {"type": "integer"},
                                        "description": {"type": "string"},
                                        "location": {"type": "string"}
                                    },
                                    "required": ["meeting_name", "meeting_time"]
                                }
                            }
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "end_call",
                                "description": "End the call",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "sales_item": {"type": "string"},
                                        "summary": {"type": "string"}
                                    },
                                    "required": []
                                }
                            }
                        }
                    ])

                while True:
                    try:
                        transcript = await transcript_queue.get()
                        print(f"Processing with GPT-4o: {transcript}")

                        cancel_ai_response.clear()
                        my_generation = current_generation
                        print(f"🆔 LLM starting with generation {my_generation}")

                        conversation_history.append({"role": "user", "content": transcript})

                        sentence_buffer = ""
                        full_response = ""
                        was_cancelled = False

                        # Call GPT-4o with tools if available
                        kwargs = {
                            "model": "gpt-4o",
                            "messages": conversation_history,
                            "stream": True,
                            "temperature": TEMPERATURE
                        }
                        if tools:
                            kwargs["tools"] = tools
                            kwargs["tool_choice"] = "auto"

                        stream = await async_openai_client.chat.completions.create(**kwargs)

                        tool_calls_buffer = []
                        async for chunk in stream:
                            if cancel_ai_response.is_set():
                                print("🛑 LLM generation cancelled due to barge-in")
                                was_cancelled = True
                                break

                            delta = chunk.choices[0].delta

                            # Handle tool calls
                            if delta.tool_calls:
                                for tool_call_delta in delta.tool_calls:
                                    # Accumulate tool call data
                                    while len(tool_calls_buffer) <= tool_call_delta.index:
                                        tool_calls_buffer.append({"id": "", "name": "", "arguments": ""})

                                    if tool_call_delta.id:
                                        tool_calls_buffer[tool_call_delta.index]["id"] = tool_call_delta.id
                                    if tool_call_delta.function.name:
                                        tool_calls_buffer[tool_call_delta.index]["name"] = tool_call_delta.function.name
                                    if tool_call_delta.function.arguments:
                                        tool_calls_buffer[tool_call_delta.index]["arguments"] += tool_call_delta.function.arguments

                            # Handle regular content
                            if delta.content:
                                content = delta.content
                                sentence_buffer += content
                                full_response += content

                                sentences = re.split(r'([.!?]+(?:\s+|$))', sentence_buffer)
                                if len(sentences) > 2:
                                    for i in range(0, len(sentences) - 2, 2):
                                        complete_sentence = sentences[i] + sentences[i + 1]
                                        if complete_sentence.strip():
                                            await tts_queue.put((my_generation, complete_sentence.strip()))
                                    sentence_buffer = sentences[-1]

                        if not was_cancelled:
                            # Send any remaining text
                            if sentence_buffer.strip():
                                await tts_queue.put((my_generation, sentence_buffer.strip()))

                            # Add response to conversation history
                            if full_response.strip():
                                conversation_history.append({"role": "assistant", "content": full_response})
                                conversation.append({"role": "assistant", "text": full_response})

                            # Process tool calls
                            if tool_calls_buffer:
                                for tool_call in tool_calls_buffer:
                                    if tool_call["name"] and tool_call["arguments"]:
                                        try:
                                            args = json.loads(tool_call["arguments"])
                                            await tool_queue.put({
                                                "name": tool_call["name"],
                                                "call_id": tool_call["id"],
                                                "arguments": args
                                            })
                                            print(f"Queued tool call: {tool_call['name']}")
                                        except json.JSONDecodeError as e:
                                            print(f"Error parsing tool arguments: {e}")

                            # Signal end of response
                            await tts_queue.put((my_generation, None))

                    except Exception as e:
                        print(f"Error in process_llm: {e}")
                        import traceback
                        traceback.print_exc()

            async def process_tts():
                """Process LLM responses with ElevenLabs TTS and send audio to Twilio."""
                # Get appropriate ElevenLabs voice based on agent language
                elevenlabs_voice_id = ELEVENLABS_VOICE_MAPPINGS.get('en', ELEVENLABS_VOICE_MAPPINGS['default'])
                elevenlabs_url = f"wss://api.elevenlabs.io/v1/text-to-speech/{elevenlabs_voice_id}/stream-input?model_id=eleven_turbo_v2_5&output_format=ulaw_8000&optimize_streaming_latency=4"

                try:
                    async with websockets.connect(elevenlabs_url) as elevenlabs_ws:
                        print("Connected to ElevenLabs TTS")

                        # Send initial configuration
                        await elevenlabs_ws.send(json.dumps({
                            "text": " ",
                            "voice_settings": {
                                "stability": 0.5,
                                "similarity_boost": 0.8,
                                "style": 0.0,
                                "use_speaker_boost": True
                            },
                            "generation_config": {
                                "chunk_length_schedule": [120, 160, 250, 290]
                            },
                            "xi_api_key": ELEVENLABS_API_KEY
                        }))

                        async def send_elevenlabs_keepalive():
                            """Send periodic keepalive to ElevenLabs."""
                            try:
                                while True:
                                    await asyncio.sleep(10)
                                    await elevenlabs_ws.send(json.dumps({"text": " "}))
                            except Exception as e:
                                print(f"Error in ElevenLabs keepalive: {e}")

                        async def send_text_to_elevenlabs():
                            """Send text chunks to ElevenLabs as they arrive."""
                            nonlocal current_generation, audio_generation, is_ai_speaking
                            try:
                                while True:
                                    # Wait for either queue item or flush signal
                                    get_task = asyncio.create_task(tts_queue.get())
                                    flush_task = asyncio.create_task(flush_elevenlabs.wait())

                                    done, pending = await asyncio.wait(
                                        [get_task, flush_task],
                                        return_when=asyncio.FIRST_COMPLETED
                                    )

                                    # Check if flush was triggered
                                    if flush_task in done:
                                        print("🧹 Flushing ElevenLabs buffer")
                                        await elevenlabs_ws.send(json.dumps({
                                            "text": "",
                                            "flush": True
                                        }))
                                        flush_elevenlabs.clear()

                                        if get_task in pending:
                                            get_task.cancel()
                                        continue

                                    # Cancel flush task if still pending
                                    if flush_task in pending:
                                        flush_task.cancel()

                                    # Get the queue item
                                    item = get_task.result()
                                    generation_id, text_chunk = item

                                    # Check if this is from an old generation
                                    if generation_id < current_generation:
                                        print(f"🚫 Skipping old generation {generation_id} text (current: {current_generation})")
                                        continue

                                    if text_chunk is None:
                                        # End of response marker - flush the stream
                                        await elevenlabs_ws.send(json.dumps({
                                            "text": "",
                                            "flush": True
                                        }))
                                        print("🔄 Flushing TTS (response complete)")
                                        continue

                                    # Check if cancelled
                                    if cancel_ai_response.is_set():
                                        print("🛑 Skipping text due to barge-in")
                                        continue

                                    # Mark AI as speaking
                                    if not is_ai_speaking:
                                        is_ai_speaking = True
                                        print("🎤 AI started speaking")

                                    print(f"Converting to speech: {text_chunk}")

                                    # Update audio generation
                                    audio_generation = generation_id

                                    # Send text chunk
                                    text_to_send = text_chunk if text_chunk.endswith(" ") else text_chunk + " "
                                    await elevenlabs_ws.send(json.dumps({
                                        "text": text_to_send,
                                        "try_trigger_generation": True
                                    }))
                            except Exception as e:
                                print(f"Error sending to ElevenLabs: {e}")

                        async def receive_audio_from_elevenlabs():
                            """Receive audio from ElevenLabs and forward to Twilio."""
                            nonlocal is_ai_speaking, audio_generation, current_generation, should_end_call, call_ended_monotonic
                            assistant_started_this_turn = False
                            try:
                                async for message in elevenlabs_ws:
                                    data = json.loads(message)

                                    if data.get('audio'):
                                        # Check if this audio is from an old generation
                                        if audio_generation < current_generation:
                                            print(f"🚫 Dropping audio from old generation {audio_generation} (current: {current_generation})")
                                            continue

                                        # Check if response was cancelled
                                        if cancel_ai_response.is_set():
                                            print("🛑 Dropping audio due to barge-in")
                                            continue

                                        audio_data = data['audio']
                                        audio_bytes_decoded = base64.b64decode(audio_data)

                                        # Mark assistant started speaking and record
                                        if not is_ai_speaking:
                                            is_ai_speaking = True
                                            assistant_started_this_turn = True
                                            current_timestamp = time.time() - call_recording_start_time
                                            call_recorder.assistant_started_speaking(timestamp=current_timestamp)
                                            print("🎤 AI started speaking")

                                        # Record assistant audio
                                        current_timestamp = time.time() - call_recording_start_time
                                        call_recorder.append_assistant_audio(audio_bytes_decoded, timestamp=current_timestamp)

                                        # Send to Twilio
                                        if stream_sid and not cancel_ai_response.is_set():
                                            audio_delta = {
                                                "event": "media",
                                                "streamSid": stream_sid,
                                                "media": {
                                                    "payload": audio_data
                                                }
                                            }
                                            await websocket.send_json(audio_delta)

                                            # Pacing delay
                                            audio_duration_ms = (len(audio_bytes_decoded) / 8000.0) * 1000
                                            pacing_delay = (audio_duration_ms * 0.7) / 1000.0
                                            pacing_delay = max(0.005, min(pacing_delay, 0.1))
                                            await asyncio.sleep(pacing_delay)

                                    if data.get('isFinal'):
                                        print("TTS chunk complete")
                                        await asyncio.sleep(0.3)
                                        if is_ai_speaking and assistant_started_this_turn:
                                            current_timestamp = time.time() - call_recording_start_time
                                            call_recorder.assistant_stopped_speaking(timestamp=current_timestamp)
                                            is_ai_speaking = False
                                            assistant_started_this_turn = False
                                            print("✅ AI finished speaking (isFinal)")

                                        # If end_call was triggered, wait and then close
                                        if should_end_call:
                                            print("Goodbye completed. Waiting for playout, then hanging up.")
                                            await asyncio.sleep(2)
                                            call_ended_monotonic = time.monotonic()
                                            with contextlib.suppress(Exception):
                                                await websocket.close()
                                            return

                            except Exception as e:
                                print(f"Error receiving from ElevenLabs: {e}")
                                if is_ai_speaking and assistant_started_this_turn:
                                    current_timestamp = time.time() - call_recording_start_time
                                    call_recorder.assistant_stopped_speaking(timestamp=current_timestamp)
                                is_ai_speaking = False

                        # Run send, receive, and keepalive concurrently
                        await asyncio.gather(
                            send_elevenlabs_keepalive(),
                            send_text_to_elevenlabs(),
                            receive_audio_from_elevenlabs()
                        )

                except Exception as e:
                    print(f"Error in process_tts: {e}")

            # Run all tasks concurrently
            await asyncio.gather(
                receive_from_twilio(),
                send_deepgram_keepalive(),
                process_deepgram_transcripts(),
                process_llm(),
                process_tts()
            )

    except Exception as e:
        print(f"Error in handle_media_stream: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Stop tool worker gracefully
        try:
            await tool_queue.put(None)
        except Exception:
            pass
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task

        # Save recording asynchronously
        recording_task = asyncio.create_task(call_recorder.save_recording())

        # Process post-call
        await process_post_call(
            conversation=conversation,
            agent_id=agent_id,
            db_call_id=db_call_id,
            twilio_call_sid=twilio_call_sid,
            call_started_monotonic=call_started_monotonic,
            call_ended_monotonic=call_ended_monotonic,
        )

        # Wait for recording to complete and update DB
        try:
            recording_url = await recording_task
            if recording_url and db_call_id:
                supabase.table('calls').update({
                    'recording_url': recording_url,
                    'recording_duration': call_recorder.get_duration_seconds()
                }).eq('id', db_call_id).execute()
                print(f"✅ Recording URL saved to database: {recording_url}")
        except Exception as e:
            print(f"❌ Failed to save recording URL: {e}")

        print("Media stream handler finished")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
