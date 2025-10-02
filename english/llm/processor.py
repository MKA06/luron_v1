import re
import json
import asyncio
from openai import AsyncOpenAI
from config import OPENAI_API_KEY, LLM_MODEL, LLM_TEMPERATURE, SYSTEM_MESSAGE
from models import ConversationState
from llm.tools import registry


class LLMProcessor:
    """Handles LLM processing with GPT-4o and tool calling."""

    def __init__(self, state: ConversationState):
        self.state = state
        self.client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        self.conversation_history = [{"role": "system", "content": SYSTEM_MESSAGE}]
        self.tool_registry = registry

    async def process_loop(self):
        """Main processing loop for transcripts and tool results."""
        # Start tool result processor
        asyncio.create_task(self._process_tool_results())

        while True:
            try:
                # Wait for transcript
                transcript = await self.state.transcript_queue.get()
                print(f"Processing with GPT-4o: {transcript}")

                # Process transcript
                await self._process_transcript(transcript)

            except Exception as e:
                print(f"Error in LLM processing loop: {e}")

    async def _process_tool_results(self):
        """Process completed tool results and inject back into conversation."""
        while True:
            try:
                # Wait for tool result
                tool_call_id, tool_name, result = await self.state.tool_result_queue.get()
                print(f"🔧 Tool result received: {tool_name} = {result}")

                # Add tool result to conversation history
                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "content": result
                })

                # Process the tool result (trigger new LLM response)
                await self._generate_response_with_tools()

            except Exception as e:
                print(f"Error processing tool result: {e}")

    async def _process_transcript(self, transcript: str):
        """Process a single transcript with GPT-4o."""
        # Add user message
        self.conversation_history.append({"role": "user", "content": transcript})

        # Generate response
        await self._generate_response_with_tools()

    async def _generate_response_with_tools(self):
        """Generate LLM response with tool calling support."""
        # Clear cancellation flag and capture generation
        self.state.cancel_ai_response.clear()
        my_generation = self.state.current_generation
        print(f"🆔 LLM starting with generation {my_generation}")

        sentence_buffer = ""
        full_response = ""
        tool_calls = []
        current_tool_call = None
        was_cancelled = False

        try:
            stream = await self.client.chat.completions.create(
                model=LLM_MODEL,
                messages=self.conversation_history,
                tools=self.tool_registry.get_definitions(),
                stream=True,
                temperature=LLM_TEMPERATURE
            )

            async for chunk in stream:
                # Check for cancellation
                if self.state.cancel_ai_response.is_set():
                    print("🛑 LLM generation cancelled due to barge-in")
                    was_cancelled = True
                    break

                choice = chunk.choices[0]

                # Handle tool calls
                if choice.delta.tool_calls:
                    for tool_call_delta in choice.delta.tool_calls:
                        # Start new tool call
                        if tool_call_delta.index is not None:
                            if current_tool_call is not None:
                                tool_calls.append(current_tool_call)

                            current_tool_call = {
                                "id": tool_call_delta.id or "",
                                "type": "function",
                                "function": {
                                    "name": tool_call_delta.function.name or "",
                                    "arguments": ""
                                }
                            }

                        # Accumulate function arguments
                        if tool_call_delta.function and tool_call_delta.function.arguments:
                            current_tool_call["function"]["arguments"] += tool_call_delta.function.arguments

                # Handle regular content
                if choice.delta.content:
                    content = choice.delta.content
                    sentence_buffer += content
                    full_response += content

                    # Check for complete sentences
                    sentences = re.split(r'([.!?]+(?:\s+|$))', sentence_buffer)

                    # Send complete sentences to TTS
                    if len(sentences) > 2:
                        for i in range(0, len(sentences) - 2, 2):
                            complete_sentence = sentences[i] + sentences[i + 1]
                            if complete_sentence.strip():
                                await self.state.tts_queue.put((my_generation, complete_sentence.strip()))

                        # Keep incomplete part
                        sentence_buffer = sentences[-1]

            # Add last tool call if exists
            if current_tool_call is not None:
                tool_calls.append(current_tool_call)

            if not was_cancelled:
                # Send remaining text
                if sentence_buffer.strip():
                    await self.state.tts_queue.put((my_generation, sentence_buffer.strip()))

                # Handle tool calls
                if tool_calls:
                    print(f"🔧 Tool calls detected: {[tc['function']['name'] for tc in tool_calls]}")

                    # Add assistant message with tool calls to history
                    self.conversation_history.append({
                        "role": "assistant",
                        "content": full_response if full_response else None,
                        "tool_calls": tool_calls
                    })

                    # Execute tools in background
                    for tool_call in tool_calls:
                        await self._execute_tool_async(tool_call)

                # Add regular response to history if no tool calls
                elif full_response.strip():
                    self.conversation_history.append({"role": "assistant", "content": full_response})

                # Signal end of response
                await self.state.tts_queue.put((my_generation, None))

        except Exception as e:
            print(f"Error in LLM stream processing: {e}")
            import traceback
            traceback.print_exc()

    async def _execute_tool_async(self, tool_call: dict):
        """Execute a tool call asynchronously in the background."""
        tool_name = tool_call["function"]["name"]
        tool_call_id = tool_call["id"]

        try:
            arguments = json.loads(tool_call["function"]["arguments"])
        except json.JSONDecodeError:
            arguments = {}

        print(f"🔧 Executing tool: {tool_name} with args: {arguments}")

        # Create background task
        async def execute_and_queue():
            result = await self.tool_registry.execute(tool_name, arguments)
            await self.state.tool_result_queue.put((tool_call_id, tool_name, result))

        task = asyncio.create_task(execute_and_queue())
        self.state.pending_tools[tool_call_id] = task
