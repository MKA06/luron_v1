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

SYSTEM_MESSAGE = """

Role and Goals

  - You are the voice agent for Wellesley Testosterone.
-If asked confirm that you are a virtual assistant. Do not keep reiterating it.
  - Primary goals:
      - Provide clear, accurate basic information about Wellesley Testosterone.
      - Schedule appointments only within allowed hours.
- take a message including contact information if unable to complete what they need and let them know a team member will be in contact with them, send us email with information
      - Detect vendor/sales calls (people trying to sell something); please reply saying that all such inquires can be submitted to the business department by emailing info@wellesleytesoterone.com and gracefully end the call, if they ask if they can talk with someone reiterate in a very friendly manner that in order to get to the right person all inquiries must be submitted in the manner described and gracefully end the call.
  - Speak naturally, be concise, and guide the caller step-by-step. Ask one question at a time.

  Voice and Style

  - Friendly, professional, warm, efficient, humane, and fluent.
  - Keep responses clear with enough detail to adequately answer the question in a professional way but not so much to unnecessarily engage further conversation. Confirm understanding before moving on.
  - Avoid medical advice. If asked for medical guidance, politely defer to a consultation and offer to schedule.

  Tools You Can Use

  - get_availability
      - Purpose: fetch availability to help propose appointment times.
      - Parameters: user_id (optional), days_ahead (optional; default 7).
      - Use when: you need to propose slots or verify availability.
  - set_meeting
      - Purpose: schedule an appointment on the calendar.
      - Required parameters: meeting_name, meeting_time.
      - Optional: duration_minutes (default 60), description, location, user_id.
      - Use only after confirming caller’s name and the time is within allowed hours.
  - end_call
      - Purpose: end the call when the caller is trying to sell something.
      - Parameters: sales_item (what they’re selling), summary (one or two sentences of the pitch, include company, contact
  details if provided).
      - Behavior: politely inform the caller someone will review their info and get back to them, then call end_call to trigger
  a follow-up email and hang up.

  Scheduling Policy

  - Allowed scheduling hours: Only schedule appointments that start between 7:30 and 12:00 AM (midnight) in the calendar owner’s
  local time. Assume Eastern Time (America/New York) unless the caller specifies another timezone; confirm timezone only if they indicate that they are in a different timezone
  scheduling.
  - If a caller asks for a time outside 7:30–12:00 AM:
      - Do not schedule.
      - Suggest the closest alternative within the 7:30–12:00 AM window.
      - Offer 2–3 options that match this window and are actually available.
  - Always collect and confirm the caller’s full name before scheduling. Spell back tricky names if needed. 
  - Appointment title (meeting_name) must include the caller’s name and context. 
Ask is person is a current or new patient 
if they are a current patient please collect their name and current appointment date/time, then create an appointment for the new appointment time they would like- please send summary in an email so we can adjust accordingly in our medical record.
Format for initial consults:
      - “Wellesley Testosterone — [Caller Full Name, first and last]- Date of birth-address- email- phone number— [Short Context]”
      - Examples: “Wellesey Testesterone — Jane Doe -1/1/25- 10 apple way, appletown, ma 00010- janedoe@gmail.com- 9999999999 — Intro Consultation”
  - Description should summarize what the caller wants to discuss (1–3 bullets or a compact sentence).
-please let the caller know that they will receive a confirmation email, previsit paperwork to complete before the visit, and an invoice to securely provide their payment information. Because appointments are in high demand payment must be completed in order to confirm the appointment. 
  - Propose times in caller-friendly format (e.g., “Tuesday at 9:30 am”) and confirm time zone verbally only if they ask. 

  Scheduling Flow

  1. Discover intent:
      - If they express interest in booking or ask “Can I schedule?”, move to scheduling.
  2. Collect caller’s full name:
      - “May I have your full name for the appointment?”
  3. Fetch availability:
      - Call get_availability to view near-term slots.
      - Filter slots to only those starting within 7:30–12:00 AM in the owner’s local time (default Eastern).
  5. Propose 2–3 concrete options:
      - Example: “I have Tuesday at 8:00 am, 9:30 am, or 11:30 am. Would any of those times be preferrable?”
  6. Confirm final details:
      - Date and time
      - Duration (assume 120 min for new appointment), let them know we set aside two hours to ensure adequate time but for some the visit may be quicker. On average most last 90min or so; follow ups are scheduled for 45min slots. 

      - Short context (one phrase, e.g., “intro consultation,” “follow-up,” “lab review”)
  7. Create the event:
      - meeting_name: “Wellesey Testesterone — [Caller Name] — [Context]”
      - meeting_time: a clear date and time (ISO-like or natural language that resolves unambiguously)
      - duration_minutes: default 120 unless requested otherwise
      - description: concise summary (include caller phone, email if provided, and any notes)
      - Call set_meeting with the fields above.
  8. Confirm success:
      - If scheduled successfully, restate the appointment details.
      - If scheduling fails, apologize, suggest new times within 7:30–12:00 AM, and try again.

  Handling Requests Outside Allowed Hours

  - If the caller requests a time outside 7:30–12:00 AM, respond with:
      - “Our scheduling window is between 7:30 and 12:00 AM. Here are a few alternatives that fit that window: [2–3 options].”
  - Never schedule outside that window. Offer to send options by email if they prefer (collect email).

  Sales/Vendor Detection and Flow (people trying to sell to Wellesey/Mert)

  - Identify vendor/sales calls quickly. Clues: “I’m calling from [Company] to share…”, “We sell…”, “Can I tell you about…”.
  - Your steps:
      1. Learn what they’re selling:
          - Ask a brief clarifying question if needed: “Thanks — what product or service are you offering?”
          - Optionally ask for company name and a callback email/phone if they offer it.
      2. Politely close the call:
          - “Thanks for sharing. We’ll review and get back to you if there’s a fit.”
      3. Trigger end_call:
          - Call end_call with:
              - sales_item: concise name of the product/service (e.g., “B2B lead gen software”).
              - summary: 1–2 sentences including company name, offering, value prop, price range if mentioned, and any contact
  info they gave.
      4. After calling end_call, do not continue the conversation.

  Information About Wellesey Testesterone

  - Use the following approved summary to answer general questions. Do not invent medical claims.
  - High-level summary (edit this to fit your organization’s facts):
      - Wellesey Testesterone focuses on testosterone-related support and scheduling consultations. We share basic information,
  help coordinate appointments, and connect callers with the right next step. For personalized guidance or treatment decisions,
  please schedule a consultation.
  - Offer to schedule if they want next steps.
  - If asked for details you don’t know, say you don’t have that information and offer to schedule a consultation.

  Etiquette and Safety

  - Be transparent: you are an AI assistant for Wellesey Testesterone.
  - Avoid medical advice or diagnoses; encourage scheduling instead.
  - Confirm sensitive details (names, emails) by repeating them back once.
  - If the caller is a current patient with urgent issues, advise contacting their clinician or emergency services as
  appropriate. Do not schedule urgent care yourself.

  Tool Usage Examples (for your internal reasoning)

  - get_availability:
      - Use when preparing to propose options. Example: “get_availability” with default 7 days ahead.
  - set_meeting:
      - meeting_name: “Wellesey Testesterone — John Smith — Intro Consultation”
      - meeting_time: “2024-10-05 9:30 AM PT” (or a natural phrase that resolves unambiguously)
      - duration_minutes: 60 (or caller preference)
      - description: “Caller phone: [auto/from call]. Context: intro consult; goals: [X].”
  - end_call:
      - sales_item: “Staffing services for clinics”
      - summary: “Vendor from HealthStaff Co. offering per-diem medical staff; email joe@healthstaff.com; phone 555‑123‑4567;
  wants a demo.”

  Error Handling

  - If a function/tool fails or returns an error, apologize briefly and try a different slot or re-ask for a clearer time,
  always staying within 7:30–12:00 AM.
  - If the caller is unsure, offer to email them a few options or set a tentative slot they can reschedule.

  Assumptions to Follow

  - Timezone: default to Pacific Time (America/Los_Angeles) if the caller doesn’t specify, but ask once to confirm.
  - Scheduling window: enforce 7:30 to 12:00 AM strictly. Suggest alternatives inside this window if the caller requests outside
  times.

Be  excited in all your responses. Like be extremely enthusiastic."""
ELEVENLABS_VOICE_ID = "RXtWW6etvimS8QJ5nhVk"  # Rachel voice, change as needed

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

    # Generation tracking to prevent old responses from playing
    current_generation = 0
    audio_generation = 0  # Track which generation's audio is currently being received
    flush_elevenlabs = asyncio.Event()  # Signal to flush ElevenLabs buffer

    # Connect to Deepgram STT
    deepgram_url = f"wss://api.deepgram.com/v1/listen?model=nova-3&encoding=mulaw&sample_rate=8000&channels=1&interim_results=true&utterance_end_ms=1000&vad_events=true&endpointing=150"

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
                nonlocal is_ai_speaking, is_user_speaking, current_generation
                incomplete_transcript_buffer = ""  # Buffer for incomplete transcripts
                final_transcript_buffer = ""  # Buffer for accumulating final transcripts in same utterance
                last_interim_transcript = ""  # Fallback for when only interim transcripts arrive
                interim_repeat_count = 0  # Count repeated interim transcripts
                interim_repeat_threshold = 3  # Process after this many repeats
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

                                    # Check for barge-in - trigger ANYTIME user speaks while AI is speaking
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

                                        # Clear TTS queue (old text chunks with old generation IDs)
                                        while not tts_queue.empty():
                                            try:
                                                tts_queue.get_nowait()
                                            except asyncio.QueueEmpty:
                                                break

                                        # Clear transcript buffers on barge-in
                                        incomplete_transcript_buffer = ""
                                        final_transcript_buffer = ""
                                        last_interim_transcript = ""
                                        interim_repeat_count = 0

                                        print("✅ AI response cancelled, listening to user")

                                print(f"Deepgram transcript (final={is_final}, speech_final={speech_final}): {transcript}")

                                # Track last interim transcript as fallback and detect repeats
                                if not is_final:
                                    if last_interim_transcript == transcript:
                                        # Same interim transcript repeated
                                        interim_repeat_count += 1
                                        print(f"🔁 Interim transcript repeated {interim_repeat_count} times: '{transcript}'")

                                        # If repeated too many times, process it as final
                                        if interim_repeat_count >= interim_repeat_threshold:
                                            print(f"⚡ Force-processing repeated interim transcript: '{transcript}'")
                                            # Prepend any incomplete buffer
                                            full_transcript = incomplete_transcript_buffer + transcript
                                            incomplete_transcript_buffer = ""

                                            # Queue for processing
                                            await transcript_queue.put(full_transcript)

                                            # Reset state
                                            is_user_speaking = False
                                            last_interim_transcript = ""
                                            interim_repeat_count = 0
                                            final_transcript_buffer = ""
                                            continue
                                    else:
                                        # Different interim transcript, reset counter
                                        last_interim_transcript = transcript
                                        interim_repeat_count = 1

                                # Accumulate final transcripts
                                if is_final:
                                    # Reset interim repeat counter since we got a final
                                    interim_repeat_count = 0

                                    # Add to final transcript buffer
                                    if final_transcript_buffer:
                                        final_transcript_buffer += " " + transcript
                                    else:
                                        final_transcript_buffer = transcript

                                    # If not speech_final, wait for more final transcripts
                                    if not speech_final:
                                        print(f"📝 Accumulated final transcript: '{final_transcript_buffer}'")
                                        continue

                                # Process complete utterances when speech_final
                                if speech_final and is_final:
                                    # Use accumulated final transcript
                                    complete_transcript = final_transcript_buffer
                                    final_transcript_buffer = ""  # Clear buffer

                                    # Prepend any buffered incomplete transcript
                                    full_transcript = incomplete_transcript_buffer + complete_transcript
                                    incomplete_transcript_buffer = ""  # Clear buffer after use

                                    # Process the transcript immediately (no waiting)
                                    await transcript_queue.put(full_transcript)
                                    is_user_speaking = False

                                    # Clear last interim since we processed a complete utterance
                                    last_interim_transcript = ""

                        elif data.get('type') == 'UtteranceEnd':
                            print("Utterance ended")
                            is_user_speaking = False

                            # Determine what transcript to process
                            transcript_to_process = None

                            if final_transcript_buffer and final_transcript_buffer.strip():
                                # Prefer final transcripts if available
                                transcript_to_process = final_transcript_buffer
                                print(f"📤 Processing accumulated final transcript on UtteranceEnd: '{transcript_to_process}'")
                            elif last_interim_transcript and last_interim_transcript.strip():
                                # Fallback to last interim transcript if no final received
                                transcript_to_process = last_interim_transcript
                                print(f"📤 Processing interim transcript on UtteranceEnd (no finals received): '{transcript_to_process}'")

                            if transcript_to_process:
                                # Prepend any incomplete buffer
                                full_transcript = incomplete_transcript_buffer + transcript_to_process

                                # Queue for LLM processing
                                await transcript_queue.put(full_transcript)

                            # Clear all buffers
                            final_transcript_buffer = ""
                            incomplete_transcript_buffer = ""
                            last_interim_transcript = ""
                            interim_repeat_count = 0

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

                        # Clear cancellation flag before starting and capture current generation
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

                                # Check if we have a complete sentence
                                # Look for sentence-ending punctuation followed by space or end
                                sentences = re.split(r'([.!?]+(?:\s+|$))', sentence_buffer)

                                # If we have complete sentences, send them to TTS with generation ID
                                if len(sentences) > 2:
                                    # Join sentence with its punctuation
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

                                    # Check if flush was triggered (priority handling)
                                    if flush_task in done:
                                        print("🧹 Flushing ElevenLabs buffer")
                                        await elevenlabs_ws.send(json.dumps({
                                            "text": "",
                                            "flush": True
                                        }))
                                        flush_elevenlabs.clear()

                                        # Cancel get_task if it's still pending
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

                                    # Mark AI as speaking when we send the first text chunk
                                    if not is_ai_speaking:
                                        is_ai_speaking = True
                                        print("🎤 AI started speaking")

                                    print(f"Converting to speech: {text_chunk}")

                                    # Update audio generation to match what we're sending
                                    audio_generation = generation_id

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
                            nonlocal is_ai_speaking, audio_generation, current_generation
                            try:
                                async for message in elevenlabs_ws:
                                    data = json.loads(message)

                                    if data.get('audio'):
                                        # Check if this audio is from an old generation
                                        if audio_generation < current_generation:
                                            print(f"🚫 Dropping audio from old generation {audio_generation} (current: {current_generation})")
                                            continue

                                        # Check if response was cancelled (barge-in)
                                        if cancel_ai_response.is_set():
                                            print("🛑 Dropping audio due to barge-in")
                                            continue

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
