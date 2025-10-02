import os
import json
import base64
import asyncio
import re
import time
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
DEEPGRAM_API_KEY = os.getenv('DEEPGRAM_API_KEY')
ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY')
PORT = int(os.getenv('PORT', 8000))

SYSTEM_MESSAGE = "You are the front desk for the kunst vc. Be helpful and concise in your responses."
ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel voice, change as needed

app = FastAPI()

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')
if not DEEPGRAM_API_KEY:
    raise ValueError('Missing the Deepgram API key. Please set it in the .env file.')
if not ELEVENLABS_API_KEY:
    raise ValueError('Missing the ElevenLabs API key. Please set it in the .env file.')

# Initialize OpenAI client
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server with Custom Pipeline is running!"}


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()
    response.say(
        "Hey, welcome bro!",
        voice="Google.en-US-Chirp3-HD-Aoede"
    )
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")


@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and custom STT+LLM+TTS pipeline."""
    print("Client connected")
    await websocket.accept()

    stream_sid = None
    conversation_history = [{"role": "system", "content": SYSTEM_MESSAGE}]

    # Barge-in state management
    is_ai_speaking = False
    is_user_speaking = False
    cancel_ai_response = asyncio.Event()
    last_ai_audio_time = 0

    # Connect to Deepgram STT
    deepgram_url = f"wss://api.deepgram.com/v1/listen?model=nova-3&encoding=mulaw&sample_rate=8000&channels=1&interim_results=true&utterance_end_ms=1000&vad_events=true&endpointing=300"

    try:
        async with websockets.connect(
            deepgram_url,
            additional_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"}
        ) as deepgram_ws:
            print("Connected to Deepgram")

            # Queue for LLM processing
            transcript_queue = asyncio.Queue()
            # Queue for TTS processing
            tts_queue = asyncio.Queue()

            async def send_deepgram_keepalive():
                """Send keepalive messages to Deepgram to maintain connection."""
                try:
                    while True:
                        await asyncio.sleep(5)  # Send keepalive every 5 seconds
                        if deepgram_ws.state.name == 'OPEN':
                            await deepgram_ws.send(json.dumps({"type": "KeepAlive"}))
                except Exception as e:
                    print(f"Error in send_deepgram_keepalive: {e}")

            async def receive_from_twilio():
                """Receive audio data from Twilio and send it to Deepgram."""
                nonlocal stream_sid
                try:
                    async for message in websocket.iter_text():
                        data = json.loads(message)
                        if data['event'] == 'media':
                            # Forward mulaw audio to Deepgram
                            audio_payload = base64.b64decode(data['media']['payload'])
                            await deepgram_ws.send(audio_payload)
                        elif data['event'] == 'start':
                            stream_sid = data['start']['streamSid']
                            print(f"Incoming stream has started {stream_sid}")
                        elif data['event'] == 'stop':
                            print("Twilio stream stopped")
                            break
                except WebSocketDisconnect:
                    print("Client disconnected from Twilio")
                except Exception as e:
                    print(f"Error in receive_from_twilio: {e}")

            async def process_deepgram_transcripts():
                """Receive transcripts from Deepgram and queue them for LLM."""
                nonlocal is_ai_speaking, is_user_speaking, last_ai_audio_time
                try:
                    async for message in deepgram_ws:
                        data = json.loads(message)

                        # Check for transcript
                        if data.get('type') == 'Results':
                            transcript = data.get('channel', {}).get('alternatives', [{}])[0].get('transcript', '')
                            is_final = data.get('is_final', False)
                            speech_final = data.get('speech_final', False)

                            if transcript:
                                # User is speaking if we have interim or final transcript
                                if not is_user_speaking and len(transcript.strip()) > 0:
                                    is_user_speaking = True

                                    # Check if AI is currently playing audio (within last 2 seconds)
                                    time_since_last_audio = time.time() - last_ai_audio_time

                                    if is_ai_speaking and time_since_last_audio < 2.0:
                                        print(f"🔴 BARGE-IN DETECTED: User speaking while AI is playing audio")
                                        # Clear Twilio's audio buffer
                                        if stream_sid:
                                            clear_message = {
                                                "event": "clear",
                                                "streamSid": stream_sid
                                            }
                                            await websocket.send_json(clear_message)

                                        # Cancel AI response
                                        cancel_ai_response.set()
                                        is_ai_speaking = False

                                        # Clear TTS queue
                                        while not tts_queue.empty():
                                            try:
                                                tts_queue.get_nowait()
                                            except asyncio.QueueEmpty:
                                                break

                                        print("✅ AI response cancelled, listening to user")

                                print(f"Deepgram transcript (final={is_final}, speech_final={speech_final}): {transcript}")

                                # Only process final utterances
                                if speech_final and is_final:
                                    await transcript_queue.put(transcript)
                                    is_user_speaking = False  # User finished speaking

                        elif data.get('type') == 'UtteranceEnd':
                            print("Utterance ended")
                            is_user_speaking = False

                except Exception as e:
                    print(f"Error in process_deepgram_transcripts: {e}")

            async def process_llm():
                """Process transcripts with GPT-4o and queue responses for TTS."""
                while True:
                    try:
                        # Wait for transcript
                        transcript = await transcript_queue.get()
                        print(f"Processing with GPT-4o: {transcript}")

                        # Clear cancellation flag before starting
                        cancel_ai_response.clear()

                        # Add user message to conversation
                        conversation_history.append({"role": "user", "content": transcript})

                        # Stream response from GPT-4o
                        sentence_buffer = ""
                        full_response = ""
                        was_cancelled = False

                        stream = await openai_client.chat.completions.create(
                            model="gpt-4o",
                            messages=conversation_history,
                            stream=True,
                            temperature=0.8
                        )

                        async for chunk in stream:
                            # Check for cancellation
                            if cancel_ai_response.is_set():
                                print("🛑 LLM generation cancelled due to barge-in")
                                was_cancelled = True
                                break

                            if chunk.choices[0].delta.content:
                                content = chunk.choices[0].delta.content
                                sentence_buffer += content
                                full_response += content

                                # Check if we have a complete sentence
                                # Look for sentence-ending punctuation followed by space or end
                                sentences = re.split(r'([.!?]+(?:\s+|$))', sentence_buffer)

                                # If we have complete sentences, send them to TTS
                                if len(sentences) > 2:
                                    # Join sentence with its punctuation
                                    for i in range(0, len(sentences) - 2, 2):
                                        complete_sentence = sentences[i] + sentences[i + 1]
                                        if complete_sentence.strip():
                                            await tts_queue.put(complete_sentence.strip())

                                    # Keep the incomplete part
                                    sentence_buffer = sentences[-1]

                        if not was_cancelled:
                            # Send any remaining text
                            if sentence_buffer.strip():
                                await tts_queue.put(sentence_buffer.strip())

                            # Add complete response to conversation history
                            if full_response.strip():
                                conversation_history.append({"role": "assistant", "content": full_response})

                            # Signal end of response
                            await tts_queue.put(None)

                    except Exception as e:
                        print(f"Error in process_llm: {e}")

            async def process_tts():
                """Process LLM responses with ElevenLabs TTS and send audio to Twilio."""
                # Connect to ElevenLabs WebSocket once and reuse
                elevenlabs_url = f"wss://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream-input?model_id=eleven_turbo_v2_5&output_format=ulaw_8000"

                try:
                    async with websockets.connect(elevenlabs_url) as elevenlabs_ws:
                        print("Connected to ElevenLabs")

                        # Send initial configuration message once
                        await elevenlabs_ws.send(json.dumps({
                            "text": " ",
                            "voice_settings": {
                                "stability": 0.5,
                                "similarity_boost": 0.75
                            },
                            "xi_api_key": ELEVENLABS_API_KEY
                        }))

                        async def send_elevenlabs_keepalive():
                            """Send periodic keepalive to ElevenLabs to prevent timeout."""
                            try:
                                while True:
                                    await asyncio.sleep(10)  # Send keepalive every 10 seconds
                                    # Send empty space to keep connection alive
                                    await elevenlabs_ws.send(json.dumps({
                                        "text": " "
                                    }))
                            except Exception as e:
                                print(f"Error in ElevenLabs keepalive: {e}")

                        async def send_text_to_elevenlabs():
                            """Send text chunks to ElevenLabs as they arrive."""
                            try:
                                while True:
                                    text_chunk = await tts_queue.get()

                                    if text_chunk is None:
                                        # End of response marker - flush the stream
                                        await elevenlabs_ws.send(json.dumps({
                                            "text": "",
                                            "flush": True
                                        }))
                                        continue

                                    # Check if cancelled
                                    if cancel_ai_response.is_set():
                                        print("🛑 Skipping text due to barge-in")
                                        continue

                                    print(f"Converting to speech: {text_chunk}")

                                    # Send text chunk (ensure it ends with space)
                                    text_to_send = text_chunk if text_chunk.endswith(" ") else text_chunk + " "
                                    await elevenlabs_ws.send(json.dumps({
                                        "text": text_to_send,
                                        "try_trigger_generation": True
                                    }))
                            except Exception as e:
                                print(f"Error sending to ElevenLabs: {e}")

                        async def receive_audio_from_elevenlabs():
                            """Receive audio from ElevenLabs and forward to Twilio."""
                            nonlocal is_ai_speaking, last_ai_audio_time
                            try:
                                async for message in elevenlabs_ws:
                                    data = json.loads(message)

                                    if data.get('audio'):
                                        # Check if response was cancelled (barge-in)
                                        if cancel_ai_response.is_set():
                                            print("🛑 Dropping audio due to barge-in")
                                            continue

                                        # Mark AI as speaking when first audio arrives
                                        if not is_ai_speaking:
                                            is_ai_speaking = True
                                            print("🎤 AI started speaking")

                                        # Update last audio time
                                        last_ai_audio_time = time.time()

                                        # ElevenLabs returns base64 encoded audio
                                        audio_data = data['audio']

                                        # Send to Twilio only if not cancelled
                                        if stream_sid and not cancel_ai_response.is_set():
                                            audio_delta = {
                                                "event": "media",
                                                "streamSid": stream_sid,
                                                "media": {
                                                    "payload": audio_data
                                                }
                                            }
                                            await websocket.send_json(audio_delta)

                                    if data.get('isFinal'):
                                        print("TTS chunk complete")
                                        # Clear speaking flag after a short delay to account for audio buffer
                                        await asyncio.sleep(0.3)
                                        is_ai_speaking = False
                                        print("✅ AI finished speaking")
                            except Exception as e:
                                print(f"Error receiving from ElevenLabs: {e}")
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
    finally:
        print("Media stream handler finished")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
