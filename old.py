import os
import json
import base64
import asyncio
import websockets
import contextlib
import audioop
import wave
import io
import time
from typing import Any, Dict, Optional
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
from supabase import create_client, Client
from openai import OpenAI

load_dotenv()
# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY') # requires OpenAI Realtime API Access
PORT = int(os.getenv('PORT', 8000))
VOICE = 'shimmer'
openai_client = OpenAI()

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
if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

# Include outbound call routes if available
try:
    from outbound import router as outbound_router
    app.include_router(outbound_router)
except Exception:
    pass


async def get_weather():
    print("started")
    await asyncio.sleep(10)
    print("HEY HEY HEY WHAT'S HAPPENING YOUTUBE")
    return "The weather right now is sunny"


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

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

    response.say(
        agent_welcome,
        voice="Polly.Filiz"
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

    # Fetch agent prompt from Supabase
    result = supabase.table('agents').select('prompt').eq('id', agent_id).single().execute()
    agent_data = result.data
    agent_prompt = agent_data.get('prompt')
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

        async def tool_worker():
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
            nonlocal stream_sid, is_user_speaking, current_user_buffer, call_started_monotonic, call_ended_monotonic, twilio_call_sid, db_call_id
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
                                    lookup = supabase.table('calls').select('id').eq('twilio_call_sid', twilio_call_sid).eq('agent_id', agent_id).order('created_at', desc=True).limit(1).execute()
                                    if lookup.data:
                                        db_call_id = lookup.data[0].get('id')
                                except Exception as e:
                                    print(f"Failed to lookup call record: {e}")
                        except Exception:
                            pass
                        if call_started_monotonic is None:
                            call_started_monotonic = time.monotonic()
                    elif data['event'] == 'stop':
                        # Twilio indicates call is ending; close OpenAI WS and exit loop
                        print(f"Stream stopped {stream_sid}")
                        # Finalize any in-progress user utterance
                        if is_user_speaking and current_user_buffer:
                            conversation.append({
                                "role": "user",
                                "audio_bytes": bytes(current_user_buffer)
                            })
                            current_user_buffer = bytearray()
                            is_user_speaking = False
                        call_ended_monotonic = time.monotonic()
                        try:
                            if openai_ws.state.name == 'OPEN':
                                await openai_ws.close()
                        except Exception:
                            pass
                        # Exit receive loop to allow gather to finish and trigger cleanup
                        return
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.state.name == 'OPEN':
                    await openai_ws.close()
        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, is_user_speaking, current_user_buffer
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)
                    if response['type'] == 'session.updated':
                        print("Session updated successfully:", response)

                    # Handle barge-in when user starts speaking 
                    
                    if response['type'] == 'input_audio_buffer.speech_started':
                        # Begin capturing a user utterance
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
                    # Detect function calling and queue tools; also capture assistant text
                    if response.get('type') == 'response.done':
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

                                    # 1) Immediately ask the model to tell user to wait
                                    wait_event = {
                                        "type": "response.create",
                                        "response": {
                                            # Remove prior context to ensure a short hold message
                                            "input": [],
                                            "instructions": "Say exactly: 'Wait here while I check.' Keep it short.",
                                        }
                                    }
                                    await openai_ws.send(json.dumps(wait_event))

                                    # 2) Queue the tool execution
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
            await asyncio.gather(receive_from_twilio(), send_to_twilio())
        finally:
            # Stop worker gracefully
            try:
                await tool_queue.put(None)
            except Exception:
                pass
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
            # After call ends, assemble transcript and update DB
            try:
                duration_sec: Optional[float] = None
                if call_started_monotonic is not None:
                    end_val = call_ended_monotonic if call_ended_monotonic is not None else time.monotonic()
                    duration_sec = max(0.0, end_val - call_started_monotonic)

                # Build transcript text, transcribing user audio segments as needed
                lines: list[str] = []
                for item in conversation:
                    role = item.get("role")
                    if role == "assistant":
                        text = item.get("text") or ""
                        if text:
                            lines.append(f"Assistant: {text}")
                    elif role == "user":
                        if "text" not in item:
                            text_val = await transcribe_ulaw_to_text(item.get("audio_bytes", b""))
                            item["text"] = text_val
                        text = item.get("text") or ""
                        if text:
                            lines.append(f"User: {text}")
                transcript_text = "\n".join(lines)

                # Derive intent (brief summary) and disposition using gpt-4o
                intent_text: Optional[str] = None
                disposition_text: Optional[str] = None
                try:
                    # Intent: very short summary of the entire interaction
                    intent_msg = openai_client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": "You are a concise assistant. Summarize the entire phone call in a very short phrase (<= 8 words). Return only the summary."},
                            {"role": "user", "content": transcript_text or ""},
                        ],
                        temperature=0.2,
                        max_tokens=32,
                    )
                    intent_text = (intent_msg.choices[0].message.content or "").strip()
                except Exception as e:
                    print(f"Intent generation failed: {e}")

                try:
                    # Disposition: success if there was back-and-forth; otherwise failed
                    disp_msg = openai_client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": "You classify call outcome. If both the user and the assistant spoke at least once (a back-and-forth), output exactly: success. Otherwise output exactly: failed. Return only one word: success or failed."},
                            {"role": "user", "content": transcript_text or ""},
                        ],
                        temperature=0.0,
                        max_tokens=4,
                    )
                    disposition_text = (disp_msg.choices[0].message.content or "").strip().lower()
                    if disposition_text not in ("success", "failed"):
                        # Fallback simple heuristic if model deviates
                        disposition_text = "success" if ("User:" in transcript_text and "Assistant:" in transcript_text) else "failed"
                except Exception as e:
                    print(f"Disposition classification failed: {e}")
                    disposition_text = "success" if ("User:" in transcript_text and "Assistant:" in transcript_text) else "failed"

                update_payload: Dict[str, Any] = {
                    'call_status': 'answered',
                    'transcript': transcript_text,
                }
                if intent_text:
                    update_payload['intent'] = intent_text
                if disposition_text:
                    update_payload['disposition'] = disposition_text
                if duration_sec is not None:
                    try:
                        update_payload['duration_sec'] = int(round(duration_sec))
                    except Exception:
                        pass

                try:
                    if db_call_id:
                        supabase.table('calls').update(update_payload).eq('id', db_call_id).execute()
                    elif twilio_call_sid:
                        supabase.table('calls').update(update_payload).eq('twilio_call_sid', twilio_call_sid).eq('agent_id', agent_id).execute()
                except Exception as e:
                    print(f"Failed to update call record: {e}")
            except Exception as e:
                print(f"Post-call transcript assembly error: {e}")



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
                }
            ],
            "tool_choice": "auto",
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))


async def transcribe_and_print_call(ulaw_bytes: bytes) -> None:
    """Convert u-law bytes to WAV, send to OpenAI STT, and print transcript."""
    if not ulaw_bytes:
        print("No audio captured; nothing to transcribe.")
        return
    try:
        # Convert 8kHz u-law to 16-bit PCM
        pcm16 = audioop.ulaw2lin(ulaw_bytes, 2)
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(8000)
            wav_file.writeframes(pcm16)
        wav_buffer.seek(0)
        # Give the buffer a name so the SDK sets filename in multipart form
        setattr(wav_buffer, 'name', 'call.wav')
    except Exception as e:
        print(f"Failed to prepare audio for transcription: {e}")
        return

    try:
        result = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=wav_buffer,
        )
        text = getattr(result, 'text', None) or (result.get('text') if isinstance(result, dict) else None)
        if text:
            print("===== CALL TRANSCRIPT =====")
            print(text)
            print("===========================")
        else:
            print("Transcription completed but no text returned.")
    except Exception as e:
        print(f"Transcription failed: {e}")


async def transcribe_ulaw_to_text(ulaw_bytes: bytes) -> str:
    if not ulaw_bytes:
        return ""
    try:
        pcm16 = audioop.ulaw2lin(ulaw_bytes, 2)
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(8000)
            wav_file.writeframes(pcm16)
        wav_buffer.seek(0)
        setattr(wav_buffer, 'name', 'utterance.wav')
        result = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=wav_buffer,
        )
        text = getattr(result, 'text', None) or (result.get('text') if isinstance(result, dict) else None)
        return text or ""
    except Exception as e:
        print(f"Utterance transcription failed: {e}")
        return ""


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


async def print_conversation_transcript(conversation: list[dict], *, duration_seconds: Optional[float] = None) -> None:
    print("CALL TRANSCRIPT")
    if duration_seconds is not None:
        print(f"Duration: {_format_duration(duration_seconds)}")
    for item in conversation:
        role = item.get("role")
        if role == "assistant":
            text = item.get("text") or ""
            if text:
                print(f"Assistant: {text}")
        elif role == "user":
            # Transcribe if needed
            if "text" not in item:
                text = await transcribe_ulaw_to_text(item.get("audio_bytes", b""))
                item["text"] = text
            text = item.get("text") or ""
            if text:
                print(f"User: {text}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
