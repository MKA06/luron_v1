import os
import json
import base64
import asyncio
import websockets
import contextlib
import wave
import io
import time
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

load_dotenv()
# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY') # requires OpenAI Realtime API Access
PORT = int(os.getenv('PORT', 8000))
VOICE = 'shimmer'
openai_client = OpenAI()

# Google OAuth Configuration - YOUR app's credentials
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')

# Supabase configuration
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
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
    from outbound import router as outbound_router
    app.include_router(outbound_router)
except Exception:
    pass

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


async def get_weather():
    print("started")
    await asyncio.sleep(10)
    print("HEY HEY HEY WHAT'S HAPPENING YOUTUBE")
    return "The weather right now is sunny"

async def set_meeting(user_id: str = None,
                      meeting_name: str = None,
                      meeting_time: str = None,
                      duration_minutes: int = 60,
                      description: str = None,
                      location: str = None):
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
    return await gcal_set_meeting(
        supabase=supabase,
        user_id=user_id,
        meeting_name=meeting_name,
        meeting_time=meeting_time,
        duration_minutes=duration_minutes,
        description=description,
        location=location
    )


async def get_availability(user_id: str = None, days_ahead: int = 7):
    """Get user's calendar availability for agents to use.
    
    This is a wrapper function that calls the actual implementation in gcal.py.

    Args:
        user_id: The user ID to fetch availability for
        days_ahead: Number of days to check ahead (default 7)

    Returns:
        A formatted string with availability information
    """
    from gcal import get_availability as gcal_get_availability
    return await gcal_get_availability(
        supabase=supabase,
        user_id=user_id,
        days_ahead=days_ahead
    )


async def end_call(sales_item: str = None, summary: str = None, caller_number: str = None):
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
                to="mertkaanatan@gmail.com",
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

    async with websockets.connect(
         f"wss://api.openai.com/v1/realtime?model=gpt-realtime&temperature={TEMPERATURE}",
    additional_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}"
        }
    ) as openai_ws:
        # Per-connection tool queue and worker
        tool_queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        # Buffer inbound Twilio audio (PCMU/u-law) for post-call transcription
        ulaw_chunks: list[bytes] = []
        # Conversation capture
        conversation: list[dict] = []  # sequence of {role: 'user'|'assistant', text?: str, audio_bytes?: bytes}
        is_user_speaking: bool = False
        current_user_buffer: bytearray = bytearray()
        call_started_monotonic: Optional[float] = None
        call_ended_monotonic: Optional[float] = None
        db_call_id: Optional[str] = None
        twilio_call_sid: Optional[str] = None
        should_end_call: bool = False  # Flag to signal call termination
        goodbye_audio_bytes: int = 0    # Bytes of goodbye audio sent after end_call
        # Control barge-in behavior around the welcome message
        barge_in_allowed: bool = True
        welcome_in_progress: bool = False

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

                        result = await set_meeting(
                            user_id=user_id,
                            meeting_name=meeting_name,
                            meeting_time=meeting_time,
                            duration_minutes=duration_minutes,
                            description=description,
                            location=location
                        )
                        output_obj = {"meeting": result}
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
                        result = await end_call(sales_item=sales_item, summary=summary, caller_number=caller_number)
                        
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

        await send_session_update(openai_ws, agent_prompt)
        stream_sid = None
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, is_user_speaking, current_user_buffer, call_started_monotonic, call_ended_monotonic, twilio_call_sid, db_call_id, should_end_call, barge_in_allowed, welcome_in_progress
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
                        # Send welcome message
                        if agent_welcome:
                            await openai_ws.send(json.dumps({
                                "type": "response.create",
                                "response": {
                                    "instructions": f"Greet the user by saying exactly: {agent_welcome}"
                                }
                            }))
                            # Disable barge-in until welcome finishes
                            welcome_in_progress = True
                            barge_in_allowed = False
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
        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, is_user_speaking, current_user_buffer, should_end_call, barge_in_allowed, welcome_in_progress, goodbye_audio_bytes, call_ended_monotonic
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)
                    if response['type'] == 'session.updated':
                        print("Session updated successfully:", response)

                    if response['type'] == 'input_audio_buffer.speech_started':
                        if not barge_in_allowed:
                            # Ignore barge-in while welcome is playing
                            print("Barge-in disabled during welcome; ignoring user speech start.")
                        else:
                            # Begin capturing a user utterance and interrupt assistant
                            is_user_speaking = True
                            current_user_buffer = bytearray()
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
                        if is_user_speaking and current_user_buffer:
                            conversation.append({
                                "role": "user",
                                "audio_bytes": bytes(current_user_buffer)
                            })
                        is_user_speaking = False

                    
                    if response['type'] == 'response.output_audio.delta' and response.get('delta'):
                        # Audio from OpenAI
                        try:
                            raw = base64.b64decode(response['delta'])
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
                        # Re-enable barge-in once the welcome message has fully completed
                        if welcome_in_progress:
                            welcome_in_progress = False
                            barge_in_allowed = True
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
            # Stop worker gracefully
            try:
                await tool_queue.put(None)
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
                
            # After call ends, assemble transcript and update DB
            await process_post_call(
                conversation=conversation,
                agent_id=agent_id,
                db_call_id=db_call_id,
                twilio_call_sid=twilio_call_sid,
                call_started_monotonic=call_started_monotonic,
                call_ended_monotonic=call_ended_monotonic,
            )



async def send_session_update(openai_ws, instructions):
    """Send session update to OpenAI WebSocket."""
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
            "instructions": instructions,
            # Configure function calling tools at the session level
            "tools": [
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
            ],
            "tool_choice": "auto",
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
