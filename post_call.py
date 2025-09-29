import os
import io
import wave
import time
import math
from typing import Any, Dict, Optional, List

from dotenv import load_dotenv
from supabase import create_client, Client
from openai import OpenAI
import stripe
from compat_audioop import ulaw2lin


load_dotenv()

# OpenAI client
openai_client = OpenAI()

# Supabase client
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Stripe client
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY')
if STRIPE_SECRET_KEY:
    try:
        stripe.api_key = STRIPE_SECRET_KEY
    except Exception as e:
        print(f"Failed to init Stripe client: {e}")


async def transcribe_ulaw_to_text(ulaw_bytes: bytes) -> str:
    if not ulaw_bytes:
        return ""
    try:
        pcm16 = ulaw2lin(ulaw_bytes, 2)
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(8000)
            wav_file.writeframes(pcm16)
        wav_buffer.seek(0)
        setattr(wav_buffer, 'name', 'utterance.wav')
        result = openai_client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=wav_buffer,
        )
        text = getattr(result, 'text', None) or (result.get('text') if isinstance(result, dict) else None)
        return text or ""
    except Exception as e:
        print(f"Utterance transcription failed: {e}")
        return ""


async def process_post_call(
    *,
    conversation: List[dict],
    agent_id: str,
    db_call_id: Optional[str],
    twilio_call_sid: Optional[str],
    call_started_monotonic: Optional[float],
    call_ended_monotonic: Optional[float],
) -> None:
    try:
        duration_sec: Optional[float] = None
        if call_started_monotonic is not None:
            end_val = call_ended_monotonic if call_ended_monotonic is not None else time.monotonic()
            duration_sec = max(0.0, end_val - call_started_monotonic)

        # Build transcript text, transcribing user audio segments as needed
        lines: list[str] = []
        for item in conversation:
            role = item.get("role")
            if role == "assistant":
                text = item.get("text") or ""
                if text:
                    lines.append(f"Assistant: {text}")
            elif role == "user":
                if "text" not in item:
                    text_val = await transcribe_ulaw_to_text(item.get("audio_bytes", b""))
                    item["text"] = text_val
                text = item.get("text") or ""
                if text:
                    lines.append(f"User: {text}")
        transcript_text = "\n".join(lines)

        # Derive intent (brief summary) and disposition using gpt-4o
        intent_text: Optional[str] = None
        disposition_text: Optional[str] = None
        try:
            # Intent: very short summary of the entire interaction
            intent_msg = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a concise assistant. Summarize the entire phone call in a very short phrase (<= 8 words). Return only the summary."},
                    {"role": "user", "content": transcript_text or ""},
                ],
                temperature=0.2,
                max_tokens=32,
            )
            intent_text = (intent_msg.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"Intent generation failed: {e}")

        try:
            # Disposition: success if there was back-and-forth; otherwise failed
            disp_msg = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You classify call outcome. If both the user and the assistant spoke at least once (a back-and-forth), output exactly: success. Otherwise output exactly: failed. Return only one word: success or failed."},
                    {"role": "user", "content": transcript_text or ""},
                ],
                temperature=0.0,
                max_tokens=10,
            )
            disposition_text = (disp_msg.choices[0].message.content or "").strip().lower()
            if disposition_text not in ("success", "failed"):
                # Fallback simple heuristic if model deviates
                disposition_text = "success" if ("User:" in transcript_text and "Assistant:" in transcript_text) else "failed"
        except Exception as e:
            print(f"Disposition classification failed: {e}")
            disposition_text = "success" if ("User:" in transcript_text and "Assistant:" in transcript_text) else "failed"

        update_payload: Dict[str, Any] = {
            'call_status': 'answered',
            'transcript': transcript_text,
        }
        if intent_text:
            update_payload['intent'] = intent_text
        if disposition_text:
            update_payload['disposition'] = disposition_text
        if duration_sec is not None:
            try:
                update_payload['duration_sec'] = int(round(duration_sec))
            except Exception:
                pass

        try:
            if db_call_id:
                supabase.table('calls').update(update_payload).eq('id', db_call_id).execute()
            elif twilio_call_sid:
                supabase.table('calls').update(update_payload).eq('twilio_call_sid', twilio_call_sid).eq('agent_id', agent_id).execute()
        except Exception as e:
            print(f"Failed to update call record: {e}")

        # Update the user's monthly_duration and subscription_status if needed
        try:
            if duration_sec is not None:
                duration_increment = int(round(duration_sec))
                user_id_value: Optional[str] = None

                # Resolve user_id from the calls table
                if db_call_id:
                    try:
                        call_row = supabase.table('calls').select('user_id').eq('id', db_call_id).single().execute()
                        if getattr(call_row, 'data', None):
                            user_id_value = call_row.data.get('user_id')
                    except Exception as e:
                        print(f"Failed to fetch user_id by db_call_id: {e}")

                if not user_id_value and twilio_call_sid:
                    try:
                        lookup = supabase.table('calls').select('user_id').eq('twilio_call_sid', twilio_call_sid).eq('agent_id', agent_id).order('created_at', desc=True).limit(1).execute()
                        if getattr(lookup, 'data', None):
                            if isinstance(lookup.data, list) and lookup.data:
                                user_id_value = lookup.data[0].get('user_id')
                            elif isinstance(lookup.data, dict):
                                user_id_value = lookup.data.get('user_id')
                    except Exception as e:
                        print(f"Failed to fetch user_id by twilio_call_sid: {e}")

                if user_id_value:
                    try:
                        profile_resp = supabase.table('profiles').select('monthly_duration, subscription_tier, subscription_status, stripe_minutes_item_id').eq('user_id', user_id_value).single().execute()
                        profile_data = getattr(profile_resp, 'data', None) or {}
                        current_monthly = profile_data.get('monthly_duration') or 0
                        try:
                            current_monthly = int(current_monthly)
                        except Exception:
                            current_monthly = 0
                        new_monthly = current_monthly + duration_increment
                        profile_update: Dict[str, Any] = {'monthly_duration': new_monthly}
                        subscription_tier = profile_data.get('subscription_tier')
                        if (subscription_tier or '').lower() == 'free' and new_monthly > 10:
                            profile_update['subscription_status'] = 'overdue'
                        supabase.table('profiles').update(profile_update).eq('user_id', user_id_value).execute()
                        ####=====================================
                        ####=====================================
                        ####=====================================
                        ####=====================================
                        # Also post usage to Stripe (rounded-up minutes)
                        try:
                            minutes_item_id = profile_data.get('stripe_minutes_item_id')
                            if minutes_item_id and STRIPE_SECRET_KEY and duration_sec is not None:
                                minutes_used = int(math.ceil(max(0.0, duration_sec) / 60.0))
                                if minutes_used > 0:
                                    idem_key = None
                                    if twilio_call_sid:
                                        idem_key = f"usage:{twilio_call_sid}"
                                    elif db_call_id:
                                        idem_key = f"usage-db:{db_call_id}"
                                    create_kwargs: Dict[str, Any] = {
                                        'quantity': minutes_used,
                                        'timestamp': int(time.time()),
                                        'action': 'increment',
                                    }
                                    if idem_key:
                                        create_kwargs['idempotency_key'] = idem_key
                                    try:
                                        stripe.SubscriptionItem.create_usage_record(
                                            minutes_item_id,
                                            **create_kwargs,
                                        )
                                    except Exception as e:
                                        print(f"Stripe usage record failed: {e}")
                        except Exception as e:
                            print(f"Stripe usage posting error: {e}")
                        ####=====================================
                        ####=====================================
                        ####=====================================
                    except Exception as e:
                        print(f"Failed to update profile monthly duration: {e}")
        except Exception as e:
            print(f"Monthly duration update error: {e}")
    except Exception as e:
        print(f"Post-call transcript assembly error: {e}")


