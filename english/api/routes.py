from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from twilio.twiml.voice_response import VoiceResponse, Connect
from websocket.handler import MediaStreamHandler


def setup_routes(app: FastAPI):
    """Setup all API routes."""

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
        handler = MediaStreamHandler(websocket)
        await handler.handle()
