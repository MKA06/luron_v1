# Voice Server Architecture

Refactored voice server with clean modular structure for maintainability and extensibility.

## Structure

```
english/
├── main.py                 # Entry point (23 lines)
├── config.py              # Configuration and environment variables (221 lines)
├── models.py              # Data structures and state management (80 lines)
├── api/
│   ├── __init__.py
│   └── routes.py          # FastAPI endpoints (30 lines)
├── audio/
│   ├── __init__.py
│   ├── deepgram.py        # Speech-to-Text handling (157 lines)
│   └── elevenlabs.py      # Text-to-Speech handling (156 lines)
├── llm/
│   ├── __init__.py
│   └── processor.py       # GPT-4o processing (78 lines)
└── websocket/
    ├── __init__.py
    └── handler.py         # WebSocket orchestration (71 lines)
```

## Module Overview

### `main.py`
- Application entry point
- Initializes FastAPI app
- Validates configuration
- ~20 lines vs original 692 lines

### `config.py`
- All environment variables and API keys
- System message/prompt
- Model configurations (Deepgram, ElevenLabs, GPT-4o)
- Configuration validation

### `models.py`
- **ConversationState**: Manages session state, barge-in, generation tracking
- **TranscriptBuffer**: Handles Deepgram transcript buffering and processing

### `api/routes.py`
- FastAPI route definitions
- `/` - Health check endpoint
- `/incoming-call` - Twilio TwiML response
- `/media-stream` - WebSocket handler

### `audio/deepgram.py`
- **DeepgramHandler**:
  - WebSocket connection management
  - STT transcript processing
  - Barge-in detection
  - Keepalive handling

### `audio/elevenlabs.py`
- **ElevenLabsHandler**:
  - WebSocket connection management
  - TTS audio generation
  - Audio streaming to Twilio
  - Generation tracking for barge-in

### `llm/processor.py`
- **LLMProcessor**:
  - GPT-4o streaming integration
  - Sentence segmentation
  - Conversation history management
  - Cancellation handling

### `websocket/handler.py`
- **MediaStreamHandler**:
  - Orchestrates all components
  - Manages Twilio WebSocket
  - Coordinates Deepgram, LLM, and ElevenLabs
  - Lifecycle management

## Benefits

1. **Modularity**: Each component has a single responsibility
2. **Testability**: Easy to unit test individual modules
3. **Extensibility**: Simple to add features like:
   - Tool calling (add to `llm/tools.py`)
   - Custom endpoints (add to `api/routes.py`)
   - Alternative TTS/STT providers (add to `audio/`)
4. **Maintainability**: Clear separation of concerns
5. **Reusability**: Components can be used independently

## Running

```bash
cd english
python3 main.py
```

## Adding Features

### Tool Calls ✅ IMPLEMENTED

Tool calling architecture is fully implemented with async background execution.

**How it works:**
1. LLM detects need for tool call and responds with acknowledgment (e.g., "Let me check that!")
2. Tool executes in background queue (non-blocking)
3. Assistant can continue answering questions while tool runs
4. When tool completes, result is injected back into conversation
5. LLM automatically processes tool result and responds

**Example tool structure:**
```python
# In llm/tools.py
@registry.register(
    name="your_tool_name",
    description="What your tool does",
    parameters={
        "type": "object",
        "properties": {
            "param1": {
                "type": "string",
                "description": "Parameter description"
            }
        },
        "required": ["param1"]
    }
)
async def your_tool_name(param1: str) -> str:
    # Your async tool logic here
    result = await some_async_operation(param1)
    return result
```

**Adding new tools:**
1. Define function in `llm/tools.py` with `@registry.register()` decorator
2. Add tool description to system message in `config.py`
3. Tool automatically available to LLM

**Architecture benefits:**
- Non-blocking: User can ask questions while tool runs
- Queued: Multiple tools can run concurrently
- Automatic: Tool results automatically processed by LLM

### Custom Endpoints
Add new routes to `api/routes.py` via `setup_routes()`

### Alternative Providers
Create new handlers in `audio/` (e.g., `azure_tts.py`)
