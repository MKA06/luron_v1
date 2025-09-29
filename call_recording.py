import io
import wave
import base64
import asyncio
import time
from datetime import datetime
from typing import List, Optional, Tuple, Dict
import numpy as np
from supabase import Client
import audioop


class CallRecorder:
    def __init__(self, supabase: Client, agent_id: str, call_sid: str):
        self.supabase = supabase
        self.agent_id = agent_id
        self.call_sid = call_sid

        # Store audio segments with exact timestamps
        self.user_segments: List[Tuple[float, float, bytes]] = []  # (start_time, end_time, audio)
        self.assistant_segments: List[Tuple[float, float, bytes]] = []  # (start_time, end_time, audio)

        # Current segment buffers
        self.current_user_segment: Optional[Tuple[float, bytearray]] = None  # (start_time, audio_buffer)
        self.current_assistant_segment: Optional[Tuple[float, bytearray]] = None  # (start_time, audio_buffer)

        self.recording_started = datetime.utcnow()
        self.call_start_time = time.time()
        self.sample_rate = 8000  # PCMU/u-law standard rate
        self.is_recording = True

    def append_user_audio(self, pcmu_data: bytes, timestamp: Optional[float] = None):
        """Append user (caller) audio data."""
        if self.is_recording and pcmu_data:
            if timestamp is None:
                timestamp = time.time() - self.call_start_time

            # Start new segment if needed
            if self.current_user_segment is None:
                self.current_user_segment = (timestamp, bytearray())

            # Append to current segment
            self.current_user_segment[1].extend(pcmu_data)

    def append_assistant_audio(self, pcmu_data: bytes, timestamp: Optional[float] = None):
        """Append assistant (AI) audio data."""
        if self.is_recording and pcmu_data:
            if timestamp is None:
                timestamp = time.time() - self.call_start_time

            # Start new segment if needed
            if self.current_assistant_segment is None:
                self.current_assistant_segment = (timestamp, bytearray())

            # Append to current segment
            self.current_assistant_segment[1].extend(pcmu_data)

    def user_started_speaking(self, timestamp: Optional[float] = None):
        """Called when user starts speaking (for turn detection)."""
        if timestamp is None:
            timestamp = time.time() - self.call_start_time

        # End current user segment if exists
        if self.current_user_segment:
            start_time, audio = self.current_user_segment
            if audio:
                self.user_segments.append((start_time, timestamp, bytes(audio)))

        # Start new user segment
        self.current_user_segment = (timestamp, bytearray())

        # IMPORTANT: End assistant segment when user interrupts (barge-in)
        if self.current_assistant_segment:
            start_time, audio = self.current_assistant_segment
            if audio:
                # Cut off assistant audio at interruption point
                end_time = timestamp  # Assistant stops when user starts
                self.assistant_segments.append((start_time, end_time, bytes(audio)))
                print(f"Barge-in detected: Assistant segment cut at {end_time:.2f}s")
            self.current_assistant_segment = None

    def user_stopped_speaking(self, timestamp: Optional[float] = None):
        """Called when user stops speaking."""
        if timestamp is None:
            timestamp = time.time() - self.call_start_time

        # End current user segment
        if self.current_user_segment:
            start_time, audio = self.current_user_segment
            if audio:
                self.user_segments.append((start_time, timestamp, bytes(audio)))
            self.current_user_segment = None

    def assistant_started_speaking(self, timestamp: Optional[float] = None):
        """Called when assistant starts speaking."""
        if timestamp is None:
            timestamp = time.time() - self.call_start_time

        # End previous assistant segment if exists
        if self.current_assistant_segment:
            start_time, audio = self.current_assistant_segment
            if audio:
                # Use current timestamp as end time
                self.assistant_segments.append((start_time, timestamp, bytes(audio)))

        # Start new assistant segment
        self.current_assistant_segment = (timestamp, bytearray())

    def assistant_stopped_speaking(self, timestamp: Optional[float] = None):
        """Called when assistant stops speaking."""
        if timestamp is None:
            timestamp = time.time() - self.call_start_time

        # End current assistant segment
        if self.current_assistant_segment:
            start_time, audio = self.current_assistant_segment
            if audio:
                self.assistant_segments.append((start_time, timestamp, bytes(audio)))
            self.current_assistant_segment = None

    def _build_timeline_audio(self) -> Tuple[bytes, bytes]:
        """Build properly timed audio tracks from segments."""
        # Finalize any pending segments
        if self.current_user_segment:
            start_time, audio = self.current_user_segment
            if audio:
                end_time = time.time() - self.call_start_time
                self.user_segments.append((start_time, end_time, bytes(audio)))

        if self.current_assistant_segment:
            start_time, audio = self.current_assistant_segment
            if audio:
                end_time = time.time() - self.call_start_time
                self.assistant_segments.append((start_time, end_time, bytes(audio)))

        if not self.user_segments and not self.assistant_segments:
            return b'\xff' * self.sample_rate, b'\xff' * self.sample_rate  # 1 second of silence

        # Find total duration
        max_time = 0.0
        for start_t, end_t, _ in self.user_segments:
            max_time = max(max_time, end_t)
        for start_t, end_t, _ in self.assistant_segments:
            max_time = max(max_time, end_t)

        # Create buffers for the full duration
        total_samples = int(max_time * self.sample_rate) + self.sample_rate  # Add 1 second padding
        user_buffer = bytearray(b'\xff' * total_samples)
        assistant_buffer = bytearray(b'\xff' * total_samples)

        # Place user segments at exact timestamps
        for start_time, end_time, audio in self.user_segments:
            start_sample = int(start_time * self.sample_rate)
            # Only use audio up to the segment duration
            segment_duration = end_time - start_time
            max_samples = int(segment_duration * self.sample_rate)
            audio_to_use = audio[:max_samples] if len(audio) > max_samples else audio

            end_sample = start_sample + len(audio_to_use)
            if end_sample <= len(user_buffer):
                user_buffer[start_sample:end_sample] = audio_to_use

        # Place assistant segments at exact timestamps
        for start_time, end_time, audio in self.assistant_segments:
            start_sample = int(start_time * self.sample_rate)
            # Only use audio up to the segment duration (handles barge-ins)
            segment_duration = end_time - start_time
            max_samples = int(segment_duration * self.sample_rate)
            audio_to_use = audio[:max_samples] if len(audio) > max_samples else audio

            end_sample = start_sample + len(audio_to_use)
            if end_sample <= len(assistant_buffer):
                assistant_buffer[start_sample:end_sample] = audio_to_use
                print(f"Placed assistant segment: {start_time:.2f}s - {end_time:.2f}s ({len(audio_to_use)} bytes)")

        return bytes(user_buffer), bytes(assistant_buffer)

    def _create_stereo_wav(self) -> bytes:
        """Create a stereo WAV file."""
        # Build properly timed audio tracks
        user_pcmu, assistant_pcmu = self._build_timeline_audio()

        # Decode PCMU to PCM (simple, no processing)
        user_pcm = audioop.ulaw2lin(user_pcmu, 2)
        assistant_pcm = audioop.ulaw2lin(assistant_pcmu, 2)

        # Convert to numpy arrays
        user_samples = np.frombuffer(user_pcm, dtype=np.int16)
        assistant_samples = np.frombuffer(assistant_pcm, dtype=np.int16)

        # Very light volume adjustment only if extremely quiet
        user_samples = self._adjust_volume(user_samples)
        assistant_samples = self._adjust_volume(assistant_samples)

        # Create stereo interleaved samples
        stereo_length = len(user_samples)
        stereo_samples = np.empty((stereo_length * 2,), dtype=np.int16)
        stereo_samples[0::2] = user_samples  # Left channel (user)
        stereo_samples[1::2] = assistant_samples  # Right channel (assistant)

        # Create WAV file
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(2)  # Stereo
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(stereo_samples.tobytes())

        wav_buffer.seek(0)
        return wav_buffer.read()

    def _adjust_volume(self, samples: np.ndarray) -> np.ndarray:
        """Very conservative volume adjustment to avoid distortion."""
        if len(samples) == 0:
            return samples

        # Calculate peak amplitude
        peak = np.abs(samples).max()

        if peak == 0:
            return samples

        # Only boost if signal is extremely quiet (less than 5% of max)
        if peak < 1638:  # 5% of 32767
            # Apply very conservative gain (max 3x)
            gain = min(3.0, 4915.0 / peak)  # Target 15% of max
            samples = (samples * gain).astype(np.int16)

        # Never apply any reduction or normalization
        # Just ensure we don't clip
        return np.clip(samples, -32768, 32767).astype(np.int16)

    async def save_recording(self) -> Optional[str]:
        """Save the recording to Supabase storage."""
        # Check if we have any segments to save
        has_audio = bool(self.user_segments or self.assistant_segments or
                        (self.current_user_segment and self.current_user_segment[1]) or
                        (self.current_assistant_segment and self.current_assistant_segment[1]))

        if not has_audio:
            print("No audio to save")
            return None

        try:
            self.is_recording = False

            # Count total audio data
            user_bytes = sum(len(audio) for _, _, audio in self.user_segments)
            assistant_bytes = sum(len(audio) for _, _, audio in self.assistant_segments)
            if self.current_user_segment and self.current_user_segment[1]:
                user_bytes += len(self.current_user_segment[1])
            if self.current_assistant_segment and self.current_assistant_segment[1]:
                assistant_bytes += len(self.current_assistant_segment[1])

            print(f"Saving recording: {user_bytes} user bytes, {assistant_bytes} assistant bytes")
            print(f"User segments: {len(self.user_segments)}, Assistant segments: {len(self.assistant_segments)}")

            # Create WAV file
            wav_data = self._create_stereo_wav()

            # Generate filename
            timestamp = self.recording_started.strftime("%Y%m%d_%H%M%S")
            filename = f"{self.agent_id}/{self.call_sid}_{timestamp}.wav"

            # Upload to Supabase
            response = self.supabase.storage.from_('call-recordings').upload(
                path=filename,
                file=wav_data,
                file_options={"content-type": "audio/wav"}
            )

            # Get public URL
            url_response = self.supabase.storage.from_('call-recordings').get_public_url(filename)

            print(f"✅ Call recording saved: {filename}")
            return url_response

        except Exception as e:
            print(f"❌ Failed to save recording: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_duration_seconds(self) -> float:
        """Get the duration of the recording in seconds."""
        max_time = 0.0
        for _, end_t, _ in self.user_segments:
            max_time = max(max_time, end_t)
        for _, end_t, _ in self.assistant_segments:
            max_time = max(max_time, end_t)

        # Check current segments too
        if self.current_user_segment:
            current_time = time.time() - self.call_start_time
            max_time = max(max_time, current_time)
        if self.current_assistant_segment:
            current_time = time.time() - self.call_start_time
            max_time = max(max_time, current_time)

        return max_time