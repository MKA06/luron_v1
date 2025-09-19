import os
import json
import base64
import asyncio
import websockets
import contextlib
from typing import Any, Dict, Optional
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
load_dotenv()
# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY') # requires OpenAI Realtime API Access
PORT = int(os.getenv('PORT', 8000))
VOICE = 'shimmer'

async def weather():
    # Legacy stub; unused. Keeping for backward-compatibility.
    await asyncio.sleep(10)
    return "the weather is sunny"


SYSTEM_MESSAGE = (
    """ROL
İrmet Hospital (Çerkezköy, Tekirdağ) sanal karşılama ve çağrı yönlendirme asistanısın. Amacın; arayanları güler yüzlü ve profesyonel bir dille karşılamak, doğru birime/uzmana yönlendirmek, randevu/teklif sürecini başlatmak, uluslararası hastalara destek sağlamak ve iletişim bilgilerini eksiksiz toplamaktır. Türkçe başla fakat gerekirse İngilizce, Azerbaycan dili, ya da başka herhangi bir yabancı dilde de konuşabilirsin eğer konuştuğun kişi farklı bir dilde konuşuyorsa (kısacası konuştuğun kişinin dilinde konuş).

KİMLİK & TON
- Doğal, kısa ve net konuş: 1–2 cümle + gerekiyorsa çok kısa maddeler. Hızlı konuş
- Empatik, sakin, güven veren; tıbbi tavsiye verme, teşhis/tedavi önermeden bilgilendir.
- Acil durum tespiti: "Acil bir durumsa lütfen 112’yi arayın."

KURUM BİLGİLERİ (Sabit kullan)
- Adres: G.O.P. Mah. Namık Kemal Bulv. No:17/21 Çerkezköy / Tekirdağ
- Santral/Çağrı Merkezi: +90 282 725 44 44
- E-posta: info@irmethospital.com (genel), patient@irmethospital.com (uluslararası)
- WhatsApp danışma (uluslararası): +90 530 955 38 88
- Web aksiyonları: "Get a Free Quote" (ücretsiz ön teklif), "Online Randevu"
- Muayene ücret bilgisi: Çağrı Merkezi verir.
- KVKK ve Hasta Hakları: Kişisel verileri asgari düzeyde al, güvenli aktar; ayrıntılı talepleri ilgili birime yönlendir.

ALT YAPI & KAPASİTE (Bilgilendirme amaçlı, soru gelirse kısa söyle)
- 18.000 m² hastane, toplam ~200 yatak; 24 yetişkin, 24 yenidoğan, 5 koroner, 5 KVC yoğun bakım.
- 8 ameliyathane; Anjiyo, KVC ve Nükleer Tıp üniteleri.
- Görüntüleme: MR, 128 dilimli BT, dijital röntgen/mamografi, 4D USG.
- Uluslararası sağlık turizmi: 2012’den beri; çok dilli ekip ve VIP transfer.

AKREDİTASYON/ÖDÜLLER (soru gelirse tek cümleyle)
- SRC (Surgical Review Corporation) "Center of Excellence" ve AACI akreditasyonları.

BÖLÜMLER (özetle say ve yönlendir)
Aşağıdaki başlıklara kısaca bilgi ver ve randevu/iletme yap:
- Obezite ve Bariatrik Cerrahi, Genel Cerrahi, Estetik/Plastik-Rekonstrüktif Cerrahi, Diş Tedavileri
- Kardiyoloji/Kalp Damar Cerrahisi
- Ortopedi ve Travmatoloji, Nöroloji, Beyin ve Sinir Cerrahisi
- Göz Hastalıkları, KBB, Dermatoloji
- Dahiliye, Gastroenteroloji, Enfeksiyon, Radyoloji, Girişimsel Radyoloji, Biyokimya
- Kadın Doğum, Yenidoğan, Çocuk Sağlığı ve Hastalıkları, Çocuk Cerrahisi
- Fizik Tedavi ve Rehabilitasyon, Psikiyatri, Psikoloji, Beslenme ve Diyet

CHECK-UP (sorulursa kısa bilgi)
- Yaşa ve cinsiyete göre paketli "periodik sağlık taraması"; içerik ve ücret için çağrı merkezi/randevu.

ULUSLARARASI HASTA AKIŞI
- Diller: Türkçe, İngilizce, Arapça, Bulgarca, Arnavutça, İtalyanca, Kürtçe (diğer diller için ön bildirim).
- Süreç (özetle): Hasta koordinatörü atanır → ön değerlendirme/teklif → uçuş sonrası 7/24 VIP havaalanı transferi → danışman eşliğinde yatış/işlemler.
- Evraklar: Kısa öykü, önceki tetkikler (rapor, görüntülemeler), düzenli ilaçlar.
- İletişim: patient@irmethospital.com veya WhatsApp +90 530 955 38 88.

VERİ TOPLAMA (her aramada gerekliyse sırayla iste)
- Ad Soyad, telefon, e-posta
- Yaş/Doğum yılı (gerekirse)
- Şikâyet/İhtiyaç: (örn. bariatrik cerrahi, estetik, diş, kardiyoloji vb.)
- Tercih edilen tarih/saat, doktor/bölüm tercihi
- Uluslararası çağrıda: uyruğu/ülke, geliş planı (tahmini tarih)
- İzinli iletişim kanalı (SMS/e-posta) ve KVKK onayı metnine yönlendirme

ANA AKIŞ
1) Karşılama:
"İrmet Hospital'a hoş geldiniz, nasıl yardımcı olabilirim?"
2) Triage:
- "Randevu mu oluşturmak istersiniz yoksa bilgi mi almak istiyorsunuz?"
- "Hangi bölüm veya işlem için aramıştınız?"
3) Randevu/Ön-Teklif:
- Türkiye içi: "Uygun gün/saat paylaşabilir misiniz? Ücret bilgisini çağrı merkezimiz netleştirir."
- Uluslararası: "Size bir hasta koordinatörü atayalım. Kısa tıbbi öykünüz ve mevcut raporlarınızla ön teklif hazırlayalım."
4) Onay & Özet:
- "Özetliyorum: [Bölüm/İşlem], [tarih/saat/tercih], iletişim: [telefon/e-posta]."
- "Detayları SMS/e-posta ile paylaşacağım."
5) Kapatma:
"Aramanız için teşekkür ederiz. Acil durumlar için lütfen 112’yi arayın. İyi günler dilerim."

KISA YANIT STİLİ (örnek kalıplar)
- "Randevu için ad-soyad ve tercih ettiğiniz tarihi alabilirim."
- "Uluslararası süreçte size hasta koordinatörü atıyoruz; WhatsApp +90 530 955 38 88 üzerinden de yazabilirsiniz."
- "Muayene ücret bilgisi çağrı merkezimizden paylaşılır."
- "Tıbbi tavsiye veremem; ilgili uzmanımıza randevu oluşturalım."

YÖNLENDİRME & ESCALATION
- Spesifik hekim/birim talebi → ilgili poliklinik sekreterliği.
- Tıbbi sonuç/ilaç/kompleks vaka → hekim/hasta koordinatörü.
- Veri erişim/silme talepleri → KVKK birimi ve resmi başvuru kanalı.
- Şikâyet/öneri → Hasta Hakları birimi.

GÜVENLİK & GİZLİLİK
- Gereksiz sağlık verisi alma. Sadece randevu ve ön değerlendirme için minimum bilgi.
- KVKK metinlerine yönlendir; paylaşım izinlerini sor ve kaydet.
"""
)

TEMPERATURE = float(os.getenv('TEMPERATURE', 0.8))
LOG_EVENT_TYPES = [
    'response.content.done', 'rate_limits.updated', 'response.done',
    'input_audio_buffer.committed', 'input_audio_buffer.speech_stopped',
    'input_audio_buffer.speech_started', 'session.created'
]
app = FastAPI()
if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

# Include outbound call routes if available
try:
    from outbound import router as outbound_router
    app.include_router(outbound_router)
except Exception:
    pass


async def get_weather():
    print("started")
    await asyncio.sleep(10)
    print("HEY HEY HEY WHAT'S HAPPENING YOUTUBE")
    return "The weather right now is sunny"


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}
@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()

    # response.say(
    #     "O.K. you can start talking!",
    #     voice="Google.en-US-Chirp3-HD-Aoede"
    # )
    # response.say(
    #     "O.K. you can start talking!",
    #     voice="alice",
    #     language="tr-TR"
    # )
    response.say(
        "O.K. you can start talking!",
        voice="Polly.Filiz"
    )
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    print("Client connected")
    await websocket.accept()
    async with websockets.connect(
        # f"wss://api.openai.com/v1/realtime?model=gpt-realtime&temperature={TEMPERATURE}",
        f"wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview&temperature={TEMPERATURE}",
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}"
        }
    ) as openai_ws:
        # Per-connection tool queue and worker
        tool_queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()

        async def tool_worker():
            while True:
                job = await tool_queue.get()
                if job is None:  # shutdown signal
                    break
                name: str = job.get("name", "")
                call_id: str = job.get("call_id", "")
                args: Dict[str, Any] = job.get("arguments") or {}

                try:
                    # Execute the tool
                    if name == "get_weather":
                        result = await get_weather()
                        output_obj = {"weather": result}
                    else:
                        output_obj = {"error": f"Unknown tool: {name}"}

                    # Send function_call_output back to the conversation
                    item_event = {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            # API expects a JSON-encoded string
                            "output": json.dumps(output_obj),
                        },
                    }
                    await openai_ws.send(json.dumps(item_event))

                    # Ask the model to respond using the new tool result
                    await openai_ws.send(json.dumps({"type": "response.create"}))
                except Exception as e:
                    # On error, still inform the model so it can recover
                    error_item = {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps({"error": str(e)}),
                        },
                    }
                    try:
                        await openai_ws.send(json.dumps(error_item))
                        await openai_ws.send(json.dumps({"type": "response.create"}))
                    except Exception:
                        pass
                finally:
                    tool_queue.task_done()

        worker_task = asyncio.create_task(tool_worker())

        await send_session_update(openai_ws)
        stream_sid = None
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.state.name == 'OPEN':
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {stream_sid}")
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.state.name == 'OPEN':
                    await openai_ws.close()
        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)
                    if response['type'] == 'session.updated':
                        print("Session updated successfully:", response)

                    # Handle barge-in when user starts speaking 
                    
                    if response['type'] == 'input_audio_buffer.speech_started':
                        # Clear Twilio's audio buffer
                        clear_message = {
                            "event": "clear",
                            "streamSid": stream_sid
                        }
                        await websocket.send_json(clear_message)
                        # Cancel OpenAI's response
                        cancel_message = {
                            "type": "response.cancel"
                        }
                        await openai_ws.send(json.dumps(cancel_message)) 

                    if response['type'] == 'response.output_audio.delta' and response.get('delta'):
                        # Audio from OpenAI
                        try:
                            audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                            audio_delta = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": audio_payload
                                }
                            }
                            await websocket.send_json(audio_delta)
                        except Exception as e:
                            print(f"Error processing audio data: {e}")
                    # Detect function calling and queue tools
                    if response.get('type') == 'response.done':
                        try:
                            out = response.get('response', {}).get('output', [])
                            for item in out:
                                if item.get('type') == 'function_call':
                                    name = item.get('name')
                                    call_id = item.get('call_id')
                                    args_json = item.get('arguments') or '{}'
                                    try:
                                        args = json.loads(args_json)
                                    except Exception:
                                        args = {}

                                    # 1) Immediately ask the model to tell user to wait
                                    wait_event = {
                                        "type": "response.create",
                                        "response": {
                                            # Remove prior context to ensure a short hold message
                                            "input": [],
                                            "instructions": "Say exactly: 'Wait here while I check.' Keep it short.",
                                        }
                                    }
                                    await openai_ws.send(json.dumps(wait_event))

                                    # 2) Queue the tool execution
                                    await tool_queue.put({
                                        "name": name,
                                        "call_id": call_id,
                                        "arguments": args,
                                    })
                        except Exception as e:
                            print(f"Error handling function call: {e}")
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")
        try:
            await asyncio.gather(receive_from_twilio(), send_to_twilio())
        finally:
            # Stop worker gracefully
            try:
                await tool_queue.put(None)
            except Exception:
                pass
            worker_task.cancel()
            with contextlib.suppress(Exception):
                await worker_task



async def send_session_update(openai_ws):
    """Send session update to OpenAI WebSocket."""
    session_update = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            # "model": "gpt-realtime",
            "model": "gpt-4o-realtime-preview",
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcmu"},
                    "turn_detection": {"type": "server_vad"}
                },
                "output": {
                    "format": {"type": "audio/pcmu"},
                    "voice": VOICE
                }
            },
            "instructions": SYSTEM_MESSAGE,
            # Configure function calling tools at the session level
            "tools": [
                {
                    "type": "function",
                    "name": "get_weather",
                    "description": "Get the current weather conditions.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            ],
            "tool_choice": "auto",
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
