#!/usr/bin/env python3
"""
Voice Server for Wellesley Testosterone
Handles Twilio calls with Deepgram STT, GPT-4o LLM, and ElevenLabs TTS
"""
import uvicorn
from fastapi import FastAPI
from config import PORT, validate_config
from api.routes import setup_routes

# Validate configuration
validate_config()

# Initialize FastAPI app
app = FastAPI(title="Voice Server", version="1.0.0")

# Setup routes
setup_routes(app)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
