import json
import asyncio
import websockets
from config import (
    DEEPGRAM_API_KEY,
    DEEPGRAM_MODEL,
    DEEPGRAM_ENCODING,
    DEEPGRAM_SAMPLE_RATE,
    DEEPGRAM_INTERIM_RESULTS,
    DEEPGRAM_UTTERANCE_END_MS,
    DEEPGRAM_ENDPOINTING
)
from models import ConversationState, TranscriptBuffer


class DeepgramHandler:
    """Handles Deepgram STT processing."""

    def __init__(self, state: ConversationState):
        self.state = state
        self.buffer = TranscriptBuffer()
        self.ws = None

    def get_url(self) -> str:
        """Build Deepgram WebSocket URL."""
        return (
            f"wss://api.deepgram.com/v1/listen?"
            f"model={DEEPGRAM_MODEL}&"
            f"encoding={DEEPGRAM_ENCODING}&"
            f"sample_rate={DEEPGRAM_SAMPLE_RATE}&"
            f"channels=1&"
            f"interim_results={'true' if DEEPGRAM_INTERIM_RESULTS else 'false'}&"
            f"utterance_end_ms={DEEPGRAM_UTTERANCE_END_MS}&"
            f"vad_events=true&"
            f"endpointing={DEEPGRAM_ENDPOINTING}"
        )

    async def connect(self):
        """Connect to Deepgram WebSocket."""
        self.ws = await websockets.connect(
            self.get_url(),
            additional_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"}
        )
        print("Connected to Deepgram")

    async def send_keepalive(self):
        """Send keepalive messages to maintain connection."""
        try:
            while True:
                await asyncio.sleep(5)
                if self.ws and self.ws.state.name == 'OPEN':
                    await self.ws.send(json.dumps({"type": "KeepAlive"}))
        except Exception as e:
            print(f"Error in Deepgram keepalive: {e}")

    async def send_audio(self, audio_payload: bytes):
        """Send audio data to Deepgram."""
        if self.ws and self.ws.state.name == 'OPEN':
            await self.ws.send(audio_payload)

    async def process_transcripts(self, websocket):
        """Process transcripts from Deepgram and handle barge-in."""
        try:
            async for message in self.ws:
                data = json.loads(message)

                if data.get('type') == 'Results':
                    await self._handle_transcript(data, websocket)
                elif data.get('type') == 'UtteranceEnd':
                    await self._handle_utterance_end()

        except Exception as e:
            print(f"Error processing Deepgram transcripts: {e}")

    async def _handle_transcript(self, data: dict, websocket):
        """Handle individual transcript results."""
        transcript = data.get('channel', {}).get('alternatives', [{}])[0].get('transcript', '')
        is_final = data.get('is_final', False)
        speech_final = data.get('speech_final', False)

        if not transcript or not transcript.strip():
            return

        # User is speaking
        if not self.state.is_user_speaking:
            self.state.is_user_speaking = True

            # Check for barge-in
            if self.state.is_ai_speaking:
                await self._handle_barge_in(websocket)

        print(f"Deepgram transcript (final={is_final}, speech_final={speech_final}): {transcript}")

        # Handle interim transcripts
        if not is_final:
            if self.buffer.add_interim(transcript):
                # Force process repeated interim
                print(f"⚡ Force-processing repeated interim transcript: '{transcript}'")
                await self._queue_transcript(self.buffer.get_complete_transcript())
                self.buffer.reset()
                self.state.is_user_speaking = False
            return

        # Accumulate final transcripts
        self.buffer.add_final(transcript)

        if not speech_final:
            print(f"📝 Accumulated final transcript: '{self.buffer.final}'")
            return

        # Process complete utterance
        if speech_final and is_final:
            await self._queue_transcript(self.buffer.get_complete_transcript())
            self.buffer.reset()
            self.state.is_user_speaking = False

    async def _handle_utterance_end(self):
        """Handle utterance end event."""
        print("Utterance ended")
        self.state.is_user_speaking = False

        transcript = self.buffer.get_best_transcript()
        if transcript and transcript.strip():
            print(f"📤 Processing transcript on UtteranceEnd: '{transcript}'")
            await self._queue_transcript(transcript)

        self.buffer.reset()

    async def _queue_transcript(self, transcript: str):
        """Queue transcript for LLM processing."""
        if transcript and transcript.strip():
            await self.state.transcript_queue.put(transcript)

    async def _handle_barge_in(self, websocket):
        """Handle barge-in detection."""
        print(f"🔴 BARGE-IN DETECTED: User speaking while AI is outputting audio")

        # Increment generation
        generation = self.state.increment_generation()
        print(f"🔄 Generation incremented to {generation}")

        # Trigger barge-in
        self.state.trigger_barge_in()

        # Clear Twilio buffer
        if self.state.stream_sid:
            await websocket.send_json({
                "event": "clear",
                "streamSid": self.state.stream_sid
            })

        print("✅ AI response cancelled, listening to user")

    async def close(self):
        """Close Deepgram connection."""
        if self.ws:
            await self.ws.close()
