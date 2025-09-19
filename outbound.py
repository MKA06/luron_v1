import os
import json
import base64
import asyncio
from typing import Optional

import websockets
from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from dotenv import load_dotenv

try:
    # Twilio is used for TwiML generation and to initiate the outbound call
    from twilio.rest import Client
    from twilio.twiml.voice_response import VoiceResponse, Connect
except Exception:
    Client = None  # type: ignore
    VoiceResponse = None  # type: ignore
    Connect = None  # type: ignore


load_dotenv()

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("Missing the OpenAI API key. Please set it in the .env file.")

# Server port
PORT = int(os.getenv("PORT", 8000))

# Realtime model + voice config
VOICE = os.getenv("OUTBOUND_NEW_VOICE", "alloy")
TEMPERATURE = float(os.getenv("OUTBOUND_NEW_TEMPERATURE", os.getenv("TEMPERATURE", 0.8)))

# Twilio + public URL
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")  # e.g. https://<your-ngrok>.ngrok-free.app


# ------------------------------------------------------------
# Prompt (outbound-specific)
# ------------------------------------------------------------
SYSTEM_MESSAGE = """Şu an burak aktaş ile konuşuyorsun. Burak'a nasıl olduğunu sor, mutlu muymuş?"""


# ------------------------------------------------------------
# App
# ------------------------------------------------------------
app = FastAPI(title="Outbound Voice Agent")


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
# HTTP: initiate outbound call
#   Example: curl -sS https://<ngrok>/call/5105427979
# ------------------------------------------------------------
@app.get("/call/{number}")
async def create_outbound_call(number: str, request: Request):
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER]):
        raise HTTPException(status_code=500, detail="Missing Twilio credentials")
    if Client is None:
        raise HTTPException(status_code=500, detail="twilio library not installed")

    to_number = _format_e164_us(number)
    host = request.url.hostname or request.headers.get("host", "localhost")
    twiml_url = f"https://{host}/outbound-twiml"
    if PUBLIC_BASE_URL:
        twiml_url = f"{PUBLIC_BASE_URL.rstrip('/')}/outbound-twiml"

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
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
        "twiml_url": twiml_url,
    })


# ------------------------------------------------------------
# HTTP: TwiML for the outbound call
# ------------------------------------------------------------
@app.api_route("/outbound-twiml", methods=["GET", "POST"])
async def outbound_twiml(request: Request):
    """Handle outbound call and return TwiML response to connect to Media Stream."""
    if VoiceResponse is None:
        raise HTTPException(status_code=500, detail="twilio library not installed")

    response = VoiceResponse()
    # Initial greeting
    response.say(
        "Merhaba, Burak Aktaş ile mi görüşüyorum?",
        voice="Google.en-US-Chirp3-HD-Aoede"
    )

    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
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


@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    print("Outbound: Client connected")
    await websocket.accept()

    async with websockets.connect(
        f"wss://api.openai.com/v1/realtime?model=gpt-realtime&temperature={TEMPERATURE}",
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}"
        }
    ) as openai_ws:
        await send_session_update(openai_ws)
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


async def send_session_update(openai_ws):
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
            "instructions": SYSTEM_MESSAGE,
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
