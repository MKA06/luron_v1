import asyncio
from dataclasses import dataclass, field
from typing import List, Dict

@dataclass
class ConversationState:
    """Manages the state of a conversation session."""
    stream_sid: str = None
    conversation_history: List[Dict] = field(default_factory=list)

    # Barge-in state
    is_ai_speaking: bool = False
    is_user_speaking: bool = False
    cancel_ai_response: asyncio.Event = field(default_factory=asyncio.Event)

    # Generation tracking
    current_generation: int = 0
    audio_generation: int = 0
    flush_elevenlabs: asyncio.Event = field(default_factory=asyncio.Event)

    # Queues
    transcript_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    tts_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    tool_result_queue: asyncio.Queue = field(default_factory=asyncio.Queue)

    # Tool calling state
    pending_tools: Dict[str, asyncio.Task] = field(default_factory=dict)

    def increment_generation(self):
        """Increment generation to invalidate old responses."""
        self.current_generation += 1
        return self.current_generation

    def reset_speaking_state(self):
        """Reset all speaking state flags."""
        self.is_ai_speaking = False
        self.is_user_speaking = False
        self.cancel_ai_response.clear()

    def trigger_barge_in(self):
        """Trigger barge-in sequence."""
        self.increment_generation()
        self.flush_elevenlabs.set()
        self.cancel_ai_response.set()
        self.is_ai_speaking = False

        # Clear TTS queue
        while not self.tts_queue.empty():
            try:
                self.tts_queue.get_nowait()
            except asyncio.QueueEmpty:
                break


@dataclass
class TranscriptBuffer:
    """Manages transcript buffering for Deepgram."""
    incomplete: str = ""
    final: str = ""
    last_interim: str = ""
    interim_repeat_count: int = 0
    interim_repeat_threshold: int = 3

    def add_interim(self, transcript: str) -> bool:
        """
        Add interim transcript and check if it should be force-processed.
        Returns True if threshold exceeded.
        """
        if self.last_interim == transcript:
            self.interim_repeat_count += 1
            return self.interim_repeat_count >= self.interim_repeat_threshold
        else:
            self.last_interim = transcript
            self.interim_repeat_count = 1
            return False

    def add_final(self, transcript: str):
        """Add final transcript to buffer."""
        if self.final:
            self.final += " " + transcript
        else:
            self.final = transcript
        self.interim_repeat_count = 0

    def get_complete_transcript(self) -> str:
        """Get complete transcript from buffers."""
        return self.incomplete + self.final

    def reset(self):
        """Clear all buffers."""
        self.incomplete = ""
        self.final = ""
        self.last_interim = ""
        self.interim_repeat_count = 0

    def get_best_transcript(self) -> str:
        """Get best available transcript (prefer final over interim)."""
        if self.final and self.final.strip():
            return self.incomplete + self.final
        elif self.last_interim and self.last_interim.strip():
            return self.incomplete + self.last_interim
        return ""
