import os
import json
import base64
import asyncio
import websockets
import contextlib
import io
import wave
import struct
from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime, timezone
from uuid import UUID
from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream, Record
from twilio.rest import Client as TwilioClient
from supabase import create_client, Client as SupabaseClient
from openai import AsyncOpenAI
from dotenv import load_dotenv
load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')  # requires OpenAI Realtime API Access
PORT = int(os.getenv('PORT', 8000))
VOICE = 'shimmer'

# Supabase configuration
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Twilio configuration
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL')

# Initialize clients
supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN else None
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Store active call sessions
call_sessions = {}

async def fetch_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    """Fetch agent data from Supabase."""
    if not supabase:
        return None
    try:
        response = supabase.table('agents').select('*').eq('id', agent_id).single().execute()
        return response.data
    except Exception as e:
        print(f"Error fetching agent: {e}")
        return None

async def create_call_record(agent_id: str, twilio_call_sid: str, from_number: str, to_number: str) -> Optional[str]:
    """Create initial call record in database."""
    if not supabase:
        return None
    try:
        # Get agent's user_id
        agent_response = supabase.table('agents').select('user_id').eq('id', agent_id).single().execute()
        if not agent_response.data:
            return None

        user_id = agent_response.data['user_id']

        call_data = {
            'agent_id': agent_id,
            'user_id': user_id,
            'twilio_call_sid': twilio_call_sid,
            'from_number': from_number,
            'to_number': to_number,
            'caller_number': from_number,
            'channel': 'inbound',
            'call_status': 'initiated',
            'date_iso': datetime.now(timezone.utc).isoformat()
        }
        response = supabase.table('calls').insert(call_data).execute()
        return response.data[0]['id']
    except Exception as e:
        print(f"Error creating call record: {e}")
        return None

async def update_call_record(call_id: str, update_data: Dict[str, Any]):
    """Update call record with transcript and analysis."""
    if not supabase or not call_id:
        return
    try:
        supabase.table('calls').update(update_data).eq('id', call_id).execute()
    except Exception as e:
        print(f"Error updating call record: {e}")

async def transcribe_recording(recording_url: str) -> Optional[str]:
    """Transcribe call recording using OpenAI (SOTA) models."""
    try:
        import requests
        import tempfile

        resp = requests.get(recording_url, timeout=60)
        if resp.status_code != 200:
            print(f"Failed to download recording: HTTP {resp.status_code}")
            return None

        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
            tmp_file.write(resp.content)
            tmp_path = tmp_file.name

        # Prefer the newest transcribe-capable model; fallback to whisper-1
        model = os.getenv('OPENAI_TRANSCRIBE_MODEL', 'gpt-4o-mini-transcribe')
        try:
            with open(tmp_path, 'rb') as audio_file:
                transcript_response = await openai_client.audio.transcriptions.create(
                    model=model,
                    file=audio_file,
                    response_format="text"
                )
        except Exception as inner:
            print(f"Primary transcription model failed ({model}), falling back to whisper-1: {inner}")
            with open(tmp_path, 'rb') as audio_file:
                transcript_response = await openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text"
                )

        os.remove(tmp_path)
        return transcript_response
    except Exception as e:
        print(f"Error transcribing recording: {e}")
        return None

async def analyze_call(transcript: str, agent_prompt: str) -> Dict[str, Any]:
    """Analyze call transcript to extract intent and disposition."""
    try:
        analysis_prompt = f"""
        Analyze this phone call transcript and extract:
        1. The main intent/purpose of the call (1-3 sentences describing what the caller wanted)
        2. The disposition (must be one of: success, no_answer, voicemail, or failed)
           - success: call completed with meaningful conversation
           - no_answer: no one spoke or answered
           - voicemail: left a voicemail
           - failed: technical issues or incomplete call

        Context: The agent's role was: {agent_prompt[:500]}...

        Transcript:
        {transcript[:3000]}

        Return JSON with keys: intent, disposition
        """

        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",  # Fast and efficient for analysis
            messages=[
                {"role": "system", "content": "You are a call analysis assistant. Return only valid JSON with 'intent' and 'disposition' keys."},
                {"role": "user", "content": analysis_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.3
        )

        result = json.loads(response.choices[0].message.content)
        print(f"Analysis result: {result}")
        return result
    except Exception as e:
        print(f"Error analyzing call: {e}")
        import traceback
        traceback.print_exc()
        return {"intent": "Error analyzing call", "disposition": "failed"}

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

async def start_twilio_recording(call_sid: str, callback_url: str):
    """Start Twilio call recording via REST API (dual-channel, both tracks)."""
    if not twilio_client:
        return
    try:
        # Run the blocking Twilio SDK call in a thread
        def _start():
            try:
                rec = twilio_client.calls(call_sid).recordings.create(
                    recording_channels='dual',
                    recording_track='both',
                    recording_status_callback=callback_url,
                    recording_status_callback_event=['completed']
                )
                print(f"Twilio recording started: {rec.sid}")
            except Exception as e:
                print(f"Failed to start Twilio recording: {e}")
        await asyncio.to_thread(_start)
    except Exception as e:
        print(f"Error in start_twilio_recording: {e}")


@app.api_route("/twilio/recording-status", methods=["GET", "POST"])
async def twilio_recording_status(request: Request):
    """Handle Twilio RecordingStatusCallback: download, upload to Supabase, transcribe, analyze, and update DB."""
    try:
        form = await request.form()
        call_sid = form.get('CallSid') or form.get('CallSid'.lower())
        recording_sid = form.get('RecordingSid')
        recording_url_base = form.get('RecordingUrl')
        recording_status = form.get('RecordingStatus')
        recording_duration = form.get('RecordingDuration')
        channels = form.get('RecordingChannels')

        print(f"Recording callback: call_sid={call_sid}, recording_sid={recording_sid}, status={recording_status}, duration={recording_duration}, channels={channels}")

        if not call_sid or not recording_url_base:
            return JSONResponse({"ok": False, "error": "missing callSid or recordingUrl"}, status_code=400)

        # Download recording (WAV)
        import requests
        download_url = recording_url_base + '.wav'
        auth = None
        if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
            auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        r = requests.get(download_url, auth=auth, timeout=120)
        if r.status_code != 200:
            return JSONResponse({"ok": False, "error": f"download failed {r.status_code}"}, status_code=502)
        wav_bytes = r.content

        # Upload to Supabase bucket
        public_url = await save_audio_to_supabase(call_sid, wav_bytes)

        # Attempt to split channels if stereo for role-based STT
        left_wav, right_wav, _, _ = split_stereo_wav(wav_bytes)
        transcript = ''
        try:
            if left_wav and right_wav:
                caller_text = await transcribe_bytes(left_wav)
                agent_text = await transcribe_bytes(right_wav)
                if caller_text:
                    transcript += f"Caller: {caller_text}\n"
                if agent_text:
                    transcript += f"Agent: {agent_text}\n"
            else:
                transcript = await transcribe_bytes(wav_bytes)
        except Exception as te:
            print(f"transcription error: {te}")

        # Analyze intent/disposition
        agent_prompt = call_sessions.get(call_sid, {}).get('agent', {}).get('prompt', '')
        analysis = await analyze_call(transcript or '', agent_prompt or '') if transcript else {"intent": None, "disposition": None}

        # Update calls row by twilio_call_sid
        update_data: Dict[str, Any] = {
            'recording_url': public_url,
            'transcript': transcript or None,
            'call_status': 'completed' if (recording_status or '').lower() == 'completed' else None,
        }
        if analysis.get('intent'):
            update_data['intent'] = analysis['intent']
        if analysis.get('disposition') in {"success", "no_answer", "voicemail", "failed"}:
            update_data['disposition'] = analysis['disposition']
        if recording_duration and str(recording_duration).isdigit():
            update_data['duration_sec'] = int(recording_duration)

        # Clean None values (Supabase update should not include Nones)
        update_data = {k: v for k, v in update_data.items() if v is not None}

        try:
            if supabase:
                supabase.table('calls').update(update_data).eq('twilio_call_sid', call_sid).execute()
        except Exception as ue:
            print(f"error updating calls by twilio sid: {ue}")

        return JSONResponse({"ok": True, "public_url": public_url, "updated": list(update_data.keys())})
    except Exception as e:
        print(f"twilio_recording_status error: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}
@app.api_route("/twilio/agents/{agent_id}", methods=["GET", "POST"])
async def handle_agent_call(agent_id: str, request: Request):
    """Handle incoming call for a specific agent."""
    # Fetch agent data
    agent = await fetch_agent(agent_id)
    if not agent:
        response = VoiceResponse()
        response.say("Sorry, this agent is not available.", voice="Polly.Joanna")
        response.hangup()
        return HTMLResponse(content=str(response), media_type="application/xml")

    # Get call details from Twilio request
    form_data = await request.form()
    call_sid = form_data.get('CallSid', '')
    from_number = form_data.get('From', '')
    to_number = form_data.get('To', '')

    # Create initial call record
    call_record_id = await create_call_record(agent_id, call_sid, from_number, to_number)

    # Store session data
    call_sessions[call_sid] = {
        'agent_id': agent_id,
        'agent': agent,
        'call_record_id': call_record_id,
        'start_time': datetime.now(timezone.utc)
    }

    # Build TwiML response
    response = VoiceResponse()

    # Say welcome message
    welcome_message = agent.get('welcome_message')
    response.say(welcome_message, voice="Polly.Filiz")

    # Connect to WebSocket for real-time conversation
    host = request.url.hostname
    connect = Connect()
    # Prefer public base URL if provided
    base_url = PUBLIC_BASE_URL.rstrip('/') if PUBLIC_BASE_URL else f"https://{host}"
    ws_base_url = base_url.replace('https://', 'wss://').replace('http://', 'ws://')
    connect.stream(url=f'{ws_base_url}/media-stream/{call_sid}')
    response.append(connect)

    # Kick off Twilio REST recording in background (dual channel)
    if twilio_client:
        try:
            callback_url = f"{base_url}/twilio/recording-status"
            asyncio.create_task(start_twilio_recording(call_sid, callback_url))
        except Exception as e:
            print(f"Error starting Twilio recording: {e}")

    return HTMLResponse(content=str(response), media_type="application/xml")

def pcmu_to_pcm(pcmu_data: bytes) -> bytes:
    """Convert μ-law (PCMU) audio to PCM."""
    # μ-law decoding table
    ULAW_TABLE = [
        -32124, -31100, -30076, -29052, -28028, -27004, -25980, -24956,
        -23932, -22908, -21884, -20860, -19836, -18812, -17788, -16764,
        -15996, -15484, -14972, -14460, -13948, -13436, -12924, -12412,
        -11900, -11388, -10876, -10364, -9852, -9340, -8828, -8316,
        -7932, -7676, -7420, -7164, -6908, -6652, -6396, -6140,
        -5884, -5628, -5372, -5116, -4860, -4604, -4348, -4092,
        -3900, -3772, -3644, -3516, -3388, -3260, -3132, -3004,
        -2876, -2748, -2620, -2492, -2364, -2236, -2108, -1980,
        -1884, -1820, -1756, -1692, -1628, -1564, -1500, -1436,
        -1372, -1308, -1244, -1180, -1116, -1052, -988, -924,
        -876, -844, -812, -780, -748, -716, -684, -652,
        -620, -588, -556, -524, -492, -460, -428, -396,
        -372, -356, -340, -324, -308, -292, -276, -260,
        -244, -228, -212, -196, -180, -164, -148, -132,
        -120, -112, -104, -96, -88, -80, -72, -64,
        -56, -48, -40, -32, -24, -16, -8, 0,
        32124, 31100, 30076, 29052, 28028, 27004, 25980, 24956,
        23932, 22908, 21884, 20860, 19836, 18812, 17788, 16764,
        15996, 15484, 14972, 14460, 13948, 13436, 12924, 12412,
        11900, 11388, 10876, 10364, 9852, 9340, 8828, 8316,
        7932, 7676, 7420, 7164, 6908, 6652, 6396, 6140,
        5884, 5628, 5372, 5116, 4860, 4604, 4348, 4092,
        3900, 3772, 3644, 3516, 3388, 3260, 3132, 3004,
        2876, 2748, 2620, 2492, 2364, 2236, 2108, 1980,
        1884, 1820, 1756, 1692, 1628, 1564, 1500, 1436,
        1372, 1308, 1244, 1180, 1116, 1052, 988, 924,
        876, 844, 812, 780, 748, 716, 684, 652,
        620, 588, 556, 524, 492, 460, 428, 396,
        372, 356, 340, 324, 308, 292, 276, 260,
        244, 228, 212, 196, 180, 164, 148, 132,
        120, 112, 104, 96, 88, 80, 72, 64,
        56, 48, 40, 32, 24, 16, 8, 0
    ]

    pcm_data = bytearray()
    for byte in pcmu_data:
        pcm_value = ULAW_TABLE[byte]
        # Convert to 16-bit little-endian
        pcm_data.extend(struct.pack('<h', pcm_value))
    return bytes(pcm_data)

def create_wav_from_buffers(audio_buffers: List[bytes], sample_rate: int = 8000) -> bytes:
    """Create a WAV file from audio buffers."""
    # Combine all audio buffers
    combined_pcmu = b''.join(audio_buffers)

    # Convert μ-law to PCM
    pcm_data = pcmu_to_pcm(combined_pcmu)

    # Create WAV file in memory
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)

    wav_buffer.seek(0)
    return wav_buffer.read()

def split_stereo_wav(wav_bytes: bytes) -> Tuple[Optional[bytes], Optional[bytes], int, int]:
    """Split a stereo WAV into two mono WAV byte blobs. Returns (left, right, sample_rate, sampwidth)."""
    try:
        bio = io.BytesIO(wav_bytes)
        with wave.open(bio, 'rb') as w:
            nch = w.getnchannels()
            fr = w.getframerate()
            sw = w.getsampwidth()
            nf = w.getnframes()
            if nch != 2:
                return None, None, fr, sw
            frames = w.readframes(nf)

        frame_size = 2 * sw  # stereo
        left_bytes = bytearray()
        right_bytes = bytearray()
        for i in range(nf):
            base = i * frame_size
            left_bytes.extend(frames[base:base+sw])
            right_bytes.extend(frames[base+sw:base+2*sw])

        # Write each as mono WAV with same sample rate/width
        def _to_wav(mono: bytes) -> bytes:
            out = io.BytesIO()
            with wave.open(out, 'wb') as ww:
                ww.setnchannels(1)
                ww.setsampwidth(sw)
                ww.setframerate(fr)
                ww.writeframes(mono)
            out.seek(0)
            return out.read()

        return _to_wav(bytes(left_bytes)), _to_wav(bytes(right_bytes)), fr, sw
    except Exception as e:
        print(f"split_stereo_wav error: {e}")
        return None, None, 8000, 2

def create_stereo_wav_from_buffers(
    left_ulaw_buffers: List[bytes], right_ulaw_buffers: List[bytes], sample_rate: int = 8000
) -> bytes:
    """Create a stereo WAV (L=caller, R=agent) from u-law buffers.

    - left_ulaw_buffers: inbound/caller PCMU chunks
    - right_ulaw_buffers: outbound/agent PCMU chunks
    """
    # Concatenate and convert each side
    left_pcm = pcmu_to_pcm(b"".join(left_ulaw_buffers)) if left_ulaw_buffers else b""
    right_pcm = pcmu_to_pcm(b"".join(right_ulaw_buffers)) if right_ulaw_buffers else b""

    # Ensure both channels have same length in samples (16-bit per sample)
    def pcm_len_samples(p: bytes) -> int:
        return len(p) // 2

    l_samples = pcm_len_samples(left_pcm)
    r_samples = pcm_len_samples(right_pcm)
    max_samples = max(l_samples, r_samples)

    # Pad the shorter channel with silence
    if l_samples < max_samples:
        left_pcm += b"\x00\x00" * (max_samples - l_samples)
    if r_samples < max_samples:
        right_pcm += b"\x00\x00" * (max_samples - r_samples)

    # Interleave L/R samples
    stereo = bytearray()
    for i in range(max_samples):
        # Each sample is 2 bytes little-endian
        l = left_pcm[i*2:(i+1)*2]
        r = right_pcm[i*2:(i+1)*2]
        stereo.extend(l)
        stereo.extend(r)

    # Write WAV
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, 'wb') as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(stereo))

    wav_buffer.seek(0)
    return wav_buffer.read()

async def save_audio_to_supabase(call_sid: str, audio_data: bytes) -> Optional[str]:
    """Save audio recording to Supabase storage bucket."""
    if not supabase:
        return None

    try:
        # Create filename with timestamp
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"calls/{call_sid}_{timestamp}.wav"

        # Upload to Supabase storage
        response = supabase.storage.from_('recordings').upload(
            path=filename,
            file=audio_data,
            file_options={"content-type": "audio/wav", "upsert": "true"}
        )

        # Get public URL
        url = supabase.storage.from_('recordings').get_public_url(filename)
        return url
    except Exception as e:
        print(f"Error saving audio to Supabase: {e}")
        return None

async def transcribe_bytes(audio_wav_bytes: bytes, model: Optional[str] = None) -> str:
    """Transcribe in-memory WAV bytes using OpenAI; returns plain text string."""
    try:
        import tempfile
        model = model or os.getenv('OPENAI_TRANSCRIBE_MODEL', 'gpt-4o-mini-transcribe')
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp.write(audio_wav_bytes)
            tmp_path = tmp.name
        try:
            with open(tmp_path, 'rb') as f:
                resp = await openai_client.audio.transcriptions.create(
                    model=model,
                    file=f,
                    response_format='text'
                )
        except Exception as inner:
            with open(tmp_path, 'rb') as f:
                resp = await openai_client.audio.transcriptions.create(
                    model='whisper-1',
                    file=f,
                    response_format='text'
                )
        finally:
            os.remove(tmp_path)
        return resp or ""
    except Exception as e:
        print(f"Error in transcribe_bytes: {e}")
        return ""

async def process_call_end(call_sid: str):
    """On call end: compute duration, upload recording, transcribe, analyze, update DB."""
    if call_sid not in call_sessions:
        return

    session = call_sessions[call_sid]
    call_record_id = session.get('call_record_id')
    start_time = session.get('start_time')
    inbound_ulaw: List[bytes] = session.get('inbound_ulaw', []) or []
    outbound_ulaw: List[bytes] = session.get('outbound_ulaw', []) or []
    agent_prompt: str = session.get('agent', {}).get('prompt', '')

    try:
        # 1) Duration
        duration = int((datetime.now(timezone.utc) - start_time).total_seconds()) if start_time else 0
        print(f"Call ended for {call_sid}, duration: {duration} seconds")

        # 2) Build recording (stereo preferred)
        recording_url: Optional[str] = None
        if inbound_ulaw or outbound_ulaw:
            try:
                # Prefer stereo (caller=left, agent=right). Fallback to mono caller.
                if inbound_ulaw and outbound_ulaw:
                    wav_bytes = create_stereo_wav_from_buffers(inbound_ulaw, outbound_ulaw, sample_rate=8000)
                else:
                    wav_bytes = create_wav_from_buffers(inbound_ulaw or outbound_ulaw, sample_rate=8000)

                recording_url = await save_audio_to_supabase(call_sid, wav_bytes)
                print(f"Uploaded recording for {call_sid}: {recording_url}")
            except Exception as e:
                print(f"Failed to build/upload recording: {e}")

        # 3) Transcribe (with roles). Transcribe both channels separately when available.
        caller_text = ""
        agent_text = ""
        try:
            if inbound_ulaw:
                caller_wav = create_wav_from_buffers(inbound_ulaw, sample_rate=8000)
                caller_text = await transcribe_bytes(caller_wav)
            if outbound_ulaw:
                agent_wav = create_wav_from_buffers(outbound_ulaw, sample_rate=8000)
                agent_text = await transcribe_bytes(agent_wav)
        except Exception as e:
            print(f"Error creating channel transcripts: {e}")

        # Fallback to single-file transcription if both are empty and we have a URL
        full_transcript = ""
        if caller_text or agent_text:
            # Simple role-labeled concat; further structuring can be added later
            if caller_text:
                full_transcript += f"Caller: {caller_text}\n"
            if agent_text:
                full_transcript += f"Agent: {agent_text}\n"
        elif recording_url:
            t = await transcribe_recording(recording_url)
            full_transcript = t or ""

        # 4) Analyze intent/disposition using GPT
        analysis: Dict[str, Any] = {"intent": None, "disposition": None}
        if full_transcript:
            analysis = await analyze_call(full_transcript, agent_prompt or "")

        # 5) Update DB record
        update_data = {
            'duration_sec': duration,
            'call_status': 'completed',
            'recording_url': recording_url,
        }
        if full_transcript:
            update_data['transcript'] = full_transcript
        if analysis.get('intent'):
            update_data['intent'] = analysis.get('intent')
        if analysis.get('disposition') in {"success", "no_answer", "voicemail", "failed"}:
            update_data['disposition'] = analysis.get('disposition')
        else:
            update_data['disposition'] = 'success'

        await update_call_record(call_record_id, update_data)
        print(f"Call record updated for {call_sid}")

    except Exception as e:
        print(f"Error processing call end: {e}")
        await update_call_record(call_record_id, {
            'call_status': 'failed',
            'disposition': 'failed'
        })

@app.websocket("/media-stream/{call_sid}")
async def handle_media_stream(call_sid: str, websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI for a specific call."""
    print(f"Client connected for call {call_sid}")
    await websocket.accept()

    # Get session data for this call
    session = call_sessions.get(call_sid, {})
    agent = session.get('agent', {})
    system_prompt = agent.get('prompt', 'You are a helpful assistant.')

    async with websockets.connect(
        f"wss://api.openai.com/v1/realtime?model=gpt-realtime&temperature={TEMPERATURE}",
        additional_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}"
        }
    ) as openai_ws:
        # Per-connection tool queue and worker
        tool_queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()

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

                    # Ask the model to respond using the new tool result
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

        await send_session_update(openai_ws, system_prompt)
        stream_sid = None
        # Accumulate inbound (caller) and outbound (agent) u-law audio
        session.setdefault('inbound_ulaw', [])
        session.setdefault('outbound_ulaw', [])

        # Background queue to decouple recording from live streaming
        recording_queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()

        async def recording_worker():
            while True:
                job = await recording_queue.get()
                if job is None:
                    break
                try:
                    direction = job.get('dir')
                    data_bytes: bytes = job.get('data') or b''
                    if direction == 'inbound':
                        session['inbound_ulaw'].append(data_bytes)
                    elif direction == 'outbound':
                        session['outbound_ulaw'].append(data_bytes)
                except Exception:
                    pass
                finally:
                    recording_queue.task_done()
        recording_task = asyncio.create_task(recording_worker())

        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.state.name == 'OPEN':
                        # Send to OpenAI first, then queue inbound for recording
                        payload_b64 = data['media']['payload']
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": payload_b64
                        }
                        await openai_ws.send(json.dumps(audio_append))
                        try:
                            recording_queue.put_nowait({"dir": "inbound", "data": base64.b64decode(payload_b64)})
                        except Exception as qe:
                            print(f"Error queueing inbound audio: {qe}")
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {stream_sid}")
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
                    if response['type'] == 'session.updated':
                        print("Session updated successfully:", response)

                    # Handle barge-in when user starts speaking 
                    
                    if response['type'] == 'input_audio_buffer.speech_started':
                        # Clear Twilio's audio buffer
                        clear_message = {"event": "clear", "streamSid": stream_sid}
                        await websocket.send_json(clear_message)
                        # Cancel OpenAI's response
                        cancel_message = {"type": "response.cancel"}
                        await openai_ws.send(json.dumps(cancel_message)) 

                    if response['type'] == 'response.output_audio.delta' and response.get('delta'):
                        # Audio from OpenAI
                        try:
                            raw_ulaw = base64.b64decode(response['delta'])
                            audio_payload = base64.b64encode(raw_ulaw).decode('utf-8')
                            audio_delta = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": audio_payload}
                            }
                            await websocket.send_json(audio_delta)
                            # Queue outbound audio for recording
                            try:
                                recording_queue.put_nowait({"dir": "outbound", "data": raw_ulaw})
                            except Exception as qe:
                                print(f"Error queueing outbound audio: {qe}")
                        except Exception as e:
                            print(f"Error processing audio data: {e}")
                    # Detect function calling and queue tools
                    if response.get('type') == 'response.done':
                        try:
                            out = response.get('response', {}).get('output', [])
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
            # Stop workers gracefully
            try:
                await tool_queue.put(None)
            except Exception:
                pass
            worker_task.cancel()
            with contextlib.suppress(Exception):
                await worker_task
            try:
                await recording_queue.put(None)
            except Exception:
                pass
            with contextlib.suppress(Exception):
                await recording_task

            # Process call end and clean up
            if call_sid in call_sessions:
                await process_call_end(call_sid)
                del call_sessions[call_sid]



async def send_session_update(openai_ws, system_prompt: str):
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
            "instructions": system_prompt,
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
