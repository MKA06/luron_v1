import json
import base64
import asyncio
import websockets
from config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_VOICE_ID,
    ELEVENLABS_MODEL,
    ELEVENLABS_OUTPUT_FORMAT,
    ELEVENLABS_LATENCY,
    VOICE_STABILITY,
    VOICE_SIMILARITY_BOOST,
    VOICE_STYLE,
    VOICE_SPEAKER_BOOST
)
from models import ConversationState


class ElevenLabsHandler:
    """Handles ElevenLabs TTS processing."""

    def __init__(self, state: ConversationState):
        self.state = state
        self.ws = None

    def get_url(self) -> str:
        """Build ElevenLabs WebSocket URL."""
        return (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream-input?"
            f"model_id={ELEVENLABS_MODEL}&"
            f"output_format={ELEVENLABS_OUTPUT_FORMAT}&"
            f"optimize_streaming_latency={ELEVENLABS_LATENCY}"
        )

    async def connect(self):
        """Connect to ElevenLabs WebSocket and send initial config."""
        self.ws = await websockets.connect(self.get_url())
        print("Connected to ElevenLabs")

        # Send initial configuration
        await self.ws.send(json.dumps({
            "text": " ",
            "voice_settings": {
                "stability": VOICE_STABILITY,
                "similarity_boost": VOICE_SIMILARITY_BOOST,
                "style": VOICE_STYLE,
                "use_speaker_boost": VOICE_SPEAKER_BOOST
            },
            "generation_config": {
                "chunk_length_schedule": [120, 160, 250, 290]
            },
            "xi_api_key": ELEVENLABS_API_KEY
        }))

    async def send_keepalive(self):
        """Send keepalive to prevent timeout."""
        try:
            while True:
                await asyncio.sleep(10)
                if self.ws and self.ws.state.name == 'OPEN':
                    await self.ws.send(json.dumps({"text": " "}))
        except Exception as e:
            print(f"Error in ElevenLabs keepalive: {e}")

    async def send_text(self, websocket):
        """Send text chunks to ElevenLabs from TTS queue."""
        try:
            while True:
                # Wait for queue item or flush signal
                get_task = asyncio.create_task(self.state.tts_queue.get())
                flush_task = asyncio.create_task(self.state.flush_elevenlabs.wait())

                done, pending = await asyncio.wait(
                    [get_task, flush_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                # Handle flush
                if flush_task in done:
                    await self._flush()
                    if get_task in pending:
                        get_task.cancel()
                    continue

                # Cancel flush task if pending
                if flush_task in pending:
                    flush_task.cancel()

                # Process queue item
                generation_id, text_chunk = get_task.result()

                # Skip old generations
                if generation_id < self.state.current_generation:
                    print(f"🚫 Skipping old generation {generation_id} text (current: {self.state.current_generation})")
                    continue

                # Handle end of response
                if text_chunk is None:
                    await self._flush()
                    continue

                # Skip if cancelled
                if self.state.cancel_ai_response.is_set():
                    print("🛑 Skipping text due to barge-in")
                    continue

                # Mark AI as speaking
                if not self.state.is_ai_speaking:
                    self.state.is_ai_speaking = True
                    print("🎤 AI started speaking")

                print(f"Converting to speech: {text_chunk}")

                # Update audio generation
                self.state.audio_generation = generation_id

                # Send text chunk
                text_to_send = text_chunk if text_chunk.endswith(" ") else text_chunk + " "
                await self.ws.send(json.dumps({
                    "text": text_to_send,
                    "try_trigger_generation": True
                }))

        except Exception as e:
            print(f"Error sending to ElevenLabs: {e}")

    async def receive_audio(self, websocket):
        """Receive audio from ElevenLabs and forward to Twilio."""
        try:
            async for message in self.ws:
                data = json.loads(message)

                if data.get('audio'):
                    # Check generation
                    if self.state.audio_generation < self.state.current_generation:
                        print(f"🚫 Dropping audio from old generation {self.state.audio_generation}")
                        continue

                    # Check if cancelled
                    if self.state.cancel_ai_response.is_set():
                        print("🛑 Dropping audio due to barge-in")
                        continue

                    # Forward to Twilio
                    if self.state.stream_sid and not self.state.cancel_ai_response.is_set():
                        await websocket.send_json({
                            "event": "media",
                            "streamSid": self.state.stream_sid,
                            "media": {
                                "payload": data['audio']
                            }
                        })

                        # Pacing delay
                        audio_bytes = len(base64.b64decode(data['audio']))
                        audio_duration_ms = (audio_bytes / 8000.0) * 1000
                        pacing_delay = max(0.005, min((audio_duration_ms * 0.7) / 1000.0, 0.1))
                        await asyncio.sleep(pacing_delay)

                if data.get('isFinal'):
                    print("TTS chunk complete")
                    await asyncio.sleep(0.3)
                    if self.state.is_ai_speaking:
                        self.state.is_ai_speaking = False
                        print("✅ AI finished speaking (isFinal)")

        except Exception as e:
            print(f"Error receiving from ElevenLabs: {e}")
            self.state.is_ai_speaking = False

    async def _flush(self):
        """Flush ElevenLabs buffer."""
        print("🧹 Flushing ElevenLabs buffer")
        await self.ws.send(json.dumps({
            "text": "",
            "flush": True
        }))
        self.state.flush_elevenlabs.clear()

    async def close(self):
        """Close ElevenLabs connection."""
        if self.ws:
            await self.ws.close()
