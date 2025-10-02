import json
import base64
import asyncio
from fastapi import WebSocket
from fastapi.websockets import WebSocketDisconnect
from models import ConversationState
from audio.deepgram import DeepgramHandler
from audio.elevenlabs import ElevenLabsHandler
from llm.processor import LLMProcessor


class MediaStreamHandler:
    """Orchestrates WebSocket connections between Twilio, Deepgram, LLM, and ElevenLabs."""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.state = ConversationState()
        self.deepgram = DeepgramHandler(self.state)
        self.elevenlabs = ElevenLabsHandler(self.state)
        self.llm = LLMProcessor(self.state)

    async def handle(self):
        """Main handler for media stream WebSocket."""
        print("Client connected")
        await self.websocket.accept()

        try:
            # Connect to services
            await self.deepgram.connect()
            await self.elevenlabs.connect()

            # Run all tasks concurrently
            await asyncio.gather(
                self._receive_from_twilio(),
                self.deepgram.send_keepalive(),
                self.deepgram.process_transcripts(self.websocket),
                self.llm.process_loop(),
                self._process_tts()
            )

        except Exception as e:
            print(f"Error in media stream handler: {e}")
        finally:
            await self._cleanup()
            print("Media stream handler finished")

    async def _receive_from_twilio(self):
        """Receive audio data from Twilio and forward to Deepgram."""
        try:
            async for message in self.websocket.iter_text():
                data = json.loads(message)

                if data['event'] == 'media':
                    # Forward audio to Deepgram
                    audio_payload = base64.b64decode(data['media']['payload'])
                    await self.deepgram.send_audio(audio_payload)

                elif data['event'] == 'start':
                    self.state.stream_sid = data['start']['streamSid']
                    print(f"Incoming stream has started {self.state.stream_sid}")

                elif data['event'] == 'stop':
                    print("Twilio stream stopped")
                    break

        except WebSocketDisconnect:
            print("Client disconnected from Twilio")
        except Exception as e:
            print(f"Error receiving from Twilio: {e}")

    async def _process_tts(self):
        """Process TTS with ElevenLabs."""
        try:
            await asyncio.gather(
                self.elevenlabs.send_keepalive(),
                self.elevenlabs.send_text(self.websocket),
                self.elevenlabs.receive_audio(self.websocket)
            )
        except Exception as e:
            print(f"Error in TTS processing: {e}")

    async def _cleanup(self):
        """Clean up connections."""
        await self.deepgram.close()
        await self.elevenlabs.close()
