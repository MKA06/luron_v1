import os
import json
import base64
import asyncio
import re
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

SYSTEM_MESSAGE = """Sen Türkçe konuşan yardımcı bir asistansın. Kısa ve net cevaplar ver. ÇOK HIZLI VE ENERJETIK KONŞ. """

# Turkish voice from ElevenLabs
ELEVENLABS_VOICE_ID = "yM93hbw8Qtvdma2wCnJG"  # Antoni - works well for Turkish

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
    return {"message": "Turkish Voice Pipeline with Deepgram (Improved) is running!"}


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()
    response.say(
        "Merhaba!",
        voice="Google.tr-TR-Standard-A"
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

    # Generation tracking to prevent old responses from playing
    current_generation = 0
    audio_generation = 0
    flush_elevenlabs = asyncio.Event()

    # Connect to Deepgram STT with Turkish settings
    # Using nova-3 which has better multilingual support
    deepgram_url = f"wss://api.deepgram.com/v1/listen?model=nova-3&language=tr&encoding=mulaw&sample_rate=8000&channels=1&interim_results=true"

    try:
        async with websockets.connect(
            deepgram_url,
            additional_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"}
        ) as deepgram_ws:
            print("Connected to Deepgram (Turkish - Nova-3)")

            # Queue for LLM processing
            transcript_queue = asyncio.Queue()
            # Queue for TTS processing
            tts_queue = asyncio.Queue()

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
                nonlocal is_ai_speaking, is_user_speaking, current_generation
                final_transcript_buffer = ""  # Buffer for accumulating final transcripts
                last_interim_transcript = ""

                try:
                    async for message in deepgram_ws:
                        data = json.loads(message)

                        # Check for transcript
                        if data.get('type') == 'Results':
                            transcript = data.get('channel', {}).get('alternatives', [{}])[0].get('transcript', '')
                            is_final = data.get('is_final', False)
                            speech_final = data.get('speech_final', False)

                            if transcript and len(transcript.strip()) > 0:
                                # User is speaking if we have interim or final transcript
                                if not is_user_speaking:
                                    is_user_speaking = True

                                    # Check for barge-in
                                    if is_ai_speaking:
                                        print(f"🔴 BARGE-IN DETECTED: User speaking while AI is outputting audio")

                                        # Increment generation to invalidate all old responses
                                        current_generation += 1
                                        print(f"🔄 Generation incremented to {current_generation}")

                                        # Signal to flush ElevenLabs buffer
                                        flush_elevenlabs.set()

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

                                        # Clear transcript buffers on barge-in
                                        final_transcript_buffer = ""
                                        last_interim_transcript = ""

                                        print("✅ AI response cancelled, listening to user")

                                print(f"Deepgram transcript (final={is_final}, speech_final={speech_final}): {transcript}")

                                # Track interim transcripts but don't process them
                                if not is_final:
                                    last_interim_transcript = transcript
                                else:
                                    # Accumulate final transcripts
                                    if final_transcript_buffer:
                                        final_transcript_buffer += " " + transcript
                                    else:
                                        final_transcript_buffer = transcript

                                    print(f"📝 Accumulated final: '{final_transcript_buffer}'")

                                    # Process when speech is final
                                    if speech_final:
                                        print(f"📤 Processing complete utterance: '{final_transcript_buffer}'")
                                        await transcript_queue.put(final_transcript_buffer)
                                        is_user_speaking = False
                                        final_transcript_buffer = ""
                                        last_interim_transcript = ""

                        elif data.get('type') == 'UtteranceEnd':
                            print("Utterance ended")
                            is_user_speaking = False

                            # Process whatever we have
                            transcript_to_process = final_transcript_buffer or last_interim_transcript

                            if transcript_to_process and transcript_to_process.strip():
                                print(f"📤 Processing on UtteranceEnd: '{transcript_to_process}'")
                                await transcript_queue.put(transcript_to_process)

                            # Clear buffers
                            final_transcript_buffer = ""
                            last_interim_transcript = ""

                except Exception as e:
                    print(f"Error in process_deepgram_transcripts: {e}")

            async def process_llm():
                """Process transcripts with GPT-4o and queue responses for TTS."""
                nonlocal current_generation
                while True:
                    try:
                        # Wait for transcript
                        transcript = await transcript_queue.get()
                        print(f"Processing with GPT-4o: {transcript}")

                        # Clear cancellation flag before starting
                        cancel_ai_response.clear()
                        my_generation = current_generation
                        print(f"🆔 LLM starting with generation {my_generation}")

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

                                # Split on sentence boundaries
                                sentences = re.split(r'([.!?]+(?:\s+|$))', sentence_buffer)

                                # Send complete sentences to TTS
                                if len(sentences) > 2:
                                    for i in range(0, len(sentences) - 2, 2):
                                        complete_sentence = sentences[i] + sentences[i + 1]
                                        if complete_sentence.strip():
                                            await tts_queue.put((my_generation, complete_sentence.strip()))

                                    # Keep the incomplete part
                                    sentence_buffer = sentences[-1]

                        if not was_cancelled:
                            # Send any remaining text
                            if sentence_buffer.strip():
                                await tts_queue.put((my_generation, sentence_buffer.strip()))

                            # Add complete response to conversation history
                            if full_response.strip():
                                conversation_history.append({"role": "assistant", "content": full_response})

                            # Signal end of response
                            await tts_queue.put((my_generation, None))

                    except Exception as e:
                        print(f"Error in process_llm: {e}")

            async def process_tts():
                """Process LLM responses with ElevenLabs TTS and send audio to Twilio."""
                # Connect to ElevenLabs WebSocket for Turkish TTS
                elevenlabs_url = f"wss://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream-input?model_id=eleven_turbo_v2_5&output_format=ulaw_8000&optimize_streaming_latency=4"

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
                            nonlocal is_ai_speaking, audio_generation, current_generation
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
                                            audio_bytes = len(base64.b64decode(audio_data))
                                            audio_duration_ms = (audio_bytes / 8000.0) * 1000
                                            pacing_delay = (audio_duration_ms * 0.7) / 1000.0
                                            pacing_delay = max(0.005, min(pacing_delay, 0.1))
                                            await asyncio.sleep(pacing_delay)

                                    if data.get('isFinal'):
                                        print("TTS chunk complete")
                                        await asyncio.sleep(0.3)
                                        if is_ai_speaking:
                                            is_ai_speaking = False
                                            print("✅ AI finished speaking (isFinal)")
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
