import os
import json
import base64
import asyncio
import uuid
from typing import Optional, List, Dict

import websockets
from fastapi import FastAPI, WebSocket, Request, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from dotenv import load_dotenv
from pydantic import BaseModel
from langdetect import detect, LangDetectException

try:
    # Twilio is used for TwiML generation and to initiate the outbound call
    from twilio.rest import Client as TwilioClient
    from twilio.twiml.voice_response import VoiceResponse, Connect
except Exception:
    TwilioClient = None  # type: ignore
    VoiceResponse = None  # type: ignore
    Connect = None  # type: ignore


load_dotenv()

# ------------------------------------------------------------
# Supabase Configuration
# ------------------------------------------------------------
from supabase import create_client, Client

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError('Missing Supabase credentials. Please set SUPABASE_URL and SUPABASE_KEY in the .env file.')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("Missing the OpenAI API key. Please set it in the .env file.")

# Server port
PORT = int(os.getenv("PORT", 8000))

# Realtime model + voice config
VOICE = os.getenv("OUTBOUND_NEW_VOICE", "shimmer")
TEMPERATURE = float(os.getenv("OUTBOUND_NEW_TEMPERATURE", os.getenv("TEMPERATURE", 0.8)))

# Twilio + public URL
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")  # e.g. https://<your-ngrok>.ngrok-free.app


# ------------------------------------------------------------
# Voice mappings for different languages (same as main.py)
# ------------------------------------------------------------
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
    'tr': 'Google.tr-TR-Chirp3-HD-Aoede',  # Turkish
    'pl': 'Google.pl-PL-Neural2-A',        # Polish
    'sv': 'Google.sv-SE-Neural2-A',        # Swedish
    'da': 'Google.da-DK-Neural2-A',        # Danish
    'no': 'Google.nb-NO-Neural2-A',        # Norwegian
    'fi': 'Google.fi-FI-Neural2-A',        # Finnish
    'he': 'Google.he-IL-Neural2-A',        # Hebrew
    'id': 'Google.id-ID-Neural2-A',        # Indonesian
    'th': 'Google.th-TH-Neural2-A',        # Thai
    'vi': 'Google.vi-VN-Neural2-A',        # Vietnamese
    'default': 'Google.en-US-Chirp3-HD-Aoede'  # Default to English
}

def get_voice_for_text(text: str) -> str:
    """
    Detect the language of the text and return the appropriate voice.
    Falls back to English voice if detection fails.
    """
    try:
        detected_lang = detect(text)
        voice = VOICE_MAPPINGS.get(detected_lang, VOICE_MAPPINGS['default'])
        print(f"Detected language: {detected_lang}, using voice: {voice}")
        return voice
    except LangDetectException as e:
        print(f"Language detection failed: {e}, using default voice")
        return VOICE_MAPPINGS['default']
    except Exception as e:
        print(f"Error in voice selection: {e}, using default voice")
        return VOICE_MAPPINGS['default']


# ------------------------------------------------------------
# Session storage for call configurations
# ------------------------------------------------------------
call_sessions: Dict[str, Dict[str, str]] = {}


# ------------------------------------------------------------
# Models
# ------------------------------------------------------------
class CallRequest(BaseModel):
    number: str
    welcome_message: str
    prompt: str
    user_id: str

class BatchCallRequest(BaseModel):
    numbers: List[str]
    welcome_message: str
    prompt: str
    user_id: str


# ------------------------------------------------------------
# App
# ------------------------------------------------------------
app = FastAPI(title="Outbound Voice Agent")

# CORS middleware to handle preflight OPTIONS requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for outbound calls
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods including OPTIONS
    allow_headers=["*"],  # Allow all headers
)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _format_e164_us(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("+"):
        return raw
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    raise HTTPException(status_code=400, detail="Phone must be E.164 or 10/11-digit US number")


# ------------------------------------------------------------
# HTTP: health
# ------------------------------------------------------------
@app.get("/")
async def root():
    return {"service": "outbound-new-agent", "status": "ok"}


# ------------------------------------------------------------
# HTTP: initiate outbound call with custom message and prompt
#   Example: curl -X POST https://<ngrok>/call -H "Content-Type: application/json" -d '{"number": "5105427979", "welcome_message": "Hello!", "prompt": "You are a helpful assistant"}'
# ------------------------------------------------------------
@app.post("/call")
async def create_outbound_call_with_config(call_request: CallRequest, request: Request):
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER]):
        raise HTTPException(status_code=500, detail="Missing Twilio credentials")
    if TwilioClient is None:
        raise HTTPException(status_code=500, detail="twilio library not installed")

    # Guardrail: Check if user is on free tier
    try:
        profile_result = supabase.table('profiles').select('subscription_tier').eq('user_id', call_request.user_id).single().execute()
        if profile_result.data:
            subscription_tier = profile_result.data.get('subscription_tier')
            if subscription_tier == 'free':
                raise HTTPException(
                    status_code=403,
                    detail="Outbound calls are not available on the free tier. Please upgrade your subscription."
                )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error checking subscription tier: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify subscription status")

    to_number = _format_e164_us(call_request.number)

    # Generate unique session ID
    session_id = str(uuid.uuid4())

    # Store welcome message and prompt for this session
    call_sessions[session_id] = {
        "welcome_message": call_request.welcome_message,
        "prompt": call_request.prompt
    }

    host = request.url.hostname or request.headers.get("host", "localhost")
    twiml_url = f"https://{host}/outbound-twiml?session_id={session_id}"
    if PUBLIC_BASE_URL:
        twiml_url = f"{PUBLIC_BASE_URL.rstrip('/')}/outbound-twiml?session_id={session_id}"

    client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    try:
        call = client.calls.create(
            to=to_number,
            from_=TWILIO_FROM_NUMBER,
            url=twiml_url,
            method="GET",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Twilio call create failed: {e}")

    return JSONResponse({
        "status": "initiated",
        "to": to_number,
        "from": TWILIO_FROM_NUMBER,
        "callSid": call.sid,
        "sessionId": session_id,
        "twiml_url": twiml_url,
    })


# ------------------------------------------------------------
# HTTP: initiate outbound call (legacy endpoint, no custom config)
#   Example: curl -sS https://<ngrok>/call/5105427979
# ------------------------------------------------------------
@app.get("/call/{number}")
async def create_outbound_call_legacy(number: str, request: Request):
    """Legacy endpoint - requires default welcome message and prompt to be set."""
    raise HTTPException(
        status_code=400,
        detail="This endpoint requires welcome_message and prompt. Use POST /call instead with JSON body: {\"number\": \"...\", \"welcome_message\": \"...\", \"prompt\": \"...\"}"
    )


# ------------------------------------------------------------
# HTTP: batch outbound calls
#   Example: curl -X POST https://<ngrok>/batch-call -H "Content-Type: application/json" -d '{"numbers": ["5105427979", "4155551234"], "welcome_message": "Hello!", "prompt": "You are a helpful assistant"}'
# ------------------------------------------------------------
@app.post("/batch-call")
async def create_batch_outbound_calls(batch_request: BatchCallRequest, request: Request):
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER]):
        raise HTTPException(status_code=500, detail="Missing Twilio credentials")
    if TwilioClient is None:
        raise HTTPException(status_code=500, detail="twilio library not installed")

    # Guardrail: Check if user is on free tier
    try:
        profile_result = supabase.table('profiles').select('subscription_tier').eq('user_id', batch_request.user_id).single().execute()
        if profile_result.data:
            subscription_tier = profile_result.data.get('subscription_tier')
            if subscription_tier == 'free':
                raise HTTPException(
                    status_code=403,
                    detail="Outbound calls are not available on the free tier. Please upgrade your subscription."
                )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error checking subscription tier: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify subscription status")

    if not batch_request.numbers:
        raise HTTPException(status_code=400, detail="No phone numbers provided")

    host = request.url.hostname or request.headers.get("host", "localhost")

    client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    async def call_single_number(number: str):
        """Helper function to call a single number and return result."""
        try:
            to_number = _format_e164_us(number)

            # Generate unique session ID for each call
            session_id = str(uuid.uuid4())

            # Store welcome message and prompt for this session
            call_sessions[session_id] = {
                "welcome_message": batch_request.welcome_message,
                "prompt": batch_request.prompt
            }

            # Create twiml_url with session_id
            twiml_url = f"https://{host}/outbound-twiml?session_id={session_id}"
            if PUBLIC_BASE_URL:
                twiml_url = f"{PUBLIC_BASE_URL.rstrip('/')}/outbound-twiml?session_id={session_id}"

            # Twilio client is synchronous, but we can still run them concurrently
            call = await asyncio.to_thread(
                client.calls.create,
                to=to_number,
                from_=TWILIO_FROM_NUMBER,
                url=twiml_url,
                method="GET"
            )
            return {
                "status": "initiated",
                "to": to_number,
                "from": TWILIO_FROM_NUMBER,
                "callSid": call.sid,
                "sessionId": session_id,
                "error": None
            }
        except Exception as e:
            return {
                "status": "failed",
                "to": number,
                "from": TWILIO_FROM_NUMBER,
                "callSid": None,
                "sessionId": None,
                "error": str(e)
            }

    # Call all numbers concurrently
    tasks = [call_single_number(number) for number in batch_request.numbers]
    results = await asyncio.gather(*tasks)

    return JSONResponse({
        "total_calls": len(results),
        "successful_calls": sum(1 for r in results if r["status"] == "initiated"),
        "failed_calls": sum(1 for r in results if r["status"] == "failed"),
        "results": results
    })


# ------------------------------------------------------------
# HTTP: TwiML for the outbound call
# ------------------------------------------------------------
@app.api_route("/outbound-twiml", methods=["GET", "POST"])
async def outbound_twiml(request: Request, session_id: Optional[str] = None):
    """Handle outbound call and return TwiML response to connect to Media Stream."""
    if VoiceResponse is None:
        raise HTTPException(status_code=500, detail="twilio library not installed")

    # Retrieve session configuration
    session_config = call_sessions.get(session_id) if session_id else None
    if not session_config:
        raise HTTPException(status_code=400, detail="Invalid or missing session_id")

    welcome_message = session_config.get("welcome_message", "")

    response = VoiceResponse()

    # Say the welcome message using language detection
    if welcome_message:
        voice = get_voice_for_text(welcome_message)
        response.say(welcome_message, voice=voice)

    host = request.url.hostname
    connect = Connect()
    # Use path parameter instead of query parameter (Twilio doesn't pass query params to WebSocket)
    connect.stream(url=f'wss://{host}/media-stream/outbound/{session_id}')
    response.append(connect)

    return HTMLResponse(content=str(response), media_type="application/xml")


# ------------------------------------------------------------
# WebSocket handlers
# ------------------------------------------------------------
LOG_EVENT_TYPES = [
    'response.content.done', 'rate_limits.updated', 'response.done',
    'input_audio_buffer.committed', 'input_audio_buffer.speech_stopped',
    'input_audio_buffer.speech_started', 'session.created'
]


@app.websocket("/media-stream/outbound/{session_id}")
async def handle_media_stream(websocket: WebSocket, session_id: str):
    """Handle WebSocket connections between Twilio and OpenAI."""
    print(f"Outbound: Client connected with session_id: {session_id}")
    await websocket.accept()

    # Retrieve session configuration
    session_config = call_sessions.get(session_id) if session_id else None
    if not session_config:
        print(f"Invalid or missing session_id: {session_id}")
        print(f"Available sessions: {list(call_sessions.keys())}")
        await websocket.close(code=1008, reason="Invalid session")
        return

    prompt = session_config.get("prompt", "")
    print(f"Using prompt: {prompt[:100]}...")

    async with websockets.connect(
        f"wss://api.openai.com/v1/realtime?model=gpt-realtime&temperature={TEMPERATURE}",
        additional_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}"
        }
    ) as openai_ws:
        await send_session_update(openai_ws, prompt)
        stream_sid = None

        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.state.name == 'OPEN':
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Outbound stream has started {stream_sid}")
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.state.name == 'OPEN':
                    await openai_ws.close()

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)
                    if response['type'] == 'response.done':
                        # Extract assistant output message (transcript/text) and log separately
                        try:
                            resp_obj = response.get('response', {})
                            outputs = resp_obj.get('output', [])
                            extracted_texts = []
                            for item in outputs:
                                if isinstance(item, dict) and item.get('type') == 'message':
                                    for piece in item.get('content', []) or []:
                                        if not isinstance(piece, dict):
                                            continue
                                        # Prefer explicit output_text if present
                                        if piece.get('type') == 'output_text' and 'text' in piece:
                                            extracted_texts.append(piece['text'])
                                        # Fallback to transcript from output_audio
                                        elif piece.get('type') == 'output_audio' and 'transcript' in piece:
                                            extracted_texts.append(piece['transcript'])
                            if extracted_texts:
                                message = " ".join(t for t in extracted_texts if t)
                                print("***************")
                                print(f"MESSAGE: {message}")
                        except Exception as e:
                            print(f"Error extracting message from response.done: {e}")
                    if response['type'] == 'session.updated':
                        print("Session updated successfully:", response)

                    # Handle barge-in when user starts speaking - improves STT quality
                    if response['type'] == 'input_audio_buffer.speech_started':
                        # Clear Twilio's audio buffer to prevent overlap
                        clear_message = {
                            "event": "clear",
                            "streamSid": stream_sid
                        }
                        await websocket.send_json(clear_message)
                        # Cancel OpenAI's response to stop AI from talking
                        cancel_message = {
                            "type": "response.cancel"
                        }
                        await openai_ws.send(json.dumps(cancel_message))

                    if response['type'] == 'response.output_audio.delta' and response.get('delta'):
                        # Audio from OpenAI
                        try:
                            audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
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
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

        # Cleanup: Remove session data after call completes
        if session_id and session_id in call_sessions:
            del call_sessions[session_id]
            print(f"Cleaned up session: {session_id}")


async def send_session_update(openai_ws, instructions: str):
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
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
