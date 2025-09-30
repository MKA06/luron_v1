# Adding Square Tools to Voice Agents

This guide shows how to add Square booking tools to your voice agents in `main.py`.

## Step 1: Import Square Functions

At the top of `main.py`, add the Square functions import (this is already done if you followed the implementation):

```python
from square_bookings import (
    get_square_availability,
    create_square_booking,
    reschedule_square_booking,
    cancel_square_booking
)
```

## Step 2: Add Tool Definitions

In the `send_session_update()` function, add Square tools to the tools array. Here's the complete tool definitions:

```python
async def send_session_update(openai_ws, instructions, agent_id=None, welcome_message=None):
    """Send session update to OpenAI WebSocket."""
    tools = []

    # Add tools for specific agent (modify agent_id as needed)
    if agent_id == "your-agent-id-here":
        tools = [
            # Existing tools (weather, google calendar, etc.)

            # Square Booking Tools
            {
                "type": "function",
                "name": "get_square_availability",
                "description": "Check available appointment slots in Square bookings/calendar.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user ID to check availability for (optional, uses agent's owner if not provided)"
                        },
                        "days_ahead": {
                            "type": "integer",
                            "description": "Number of days ahead to check availability (default: 7)"
                        },
                        "location_id": {
                            "type": "string",
                            "description": "Specific Square location ID (optional, uses first location if not provided)"
                        }
                    },
                    "required": []
                }
            },
            {
                "type": "function",
                "name": "create_square_booking",
                "description": "Create/book a new appointment in Square.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user ID whose Square account to use (optional, uses agent's owner if not provided)"
                        },
                        "booking_time": {
                            "type": "string",
                            "description": "The date and time for the booking (e.g., 'tomorrow at 2pm' or '2024-12-25 14:00')"
                        },
                        "customer_note": {
                            "type": "string",
                            "description": "Optional note for the booking"
                        },
                        "location_id": {
                            "type": "string",
                            "description": "Specific Square location ID (optional, uses first location if not provided)"
                        }
                    },
                    "required": ["booking_time"]
                }
            },
            {
                "type": "function",
                "name": "reschedule_square_booking",
                "description": "Reschedule an existing Square booking to a new time.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user ID (optional, uses agent's owner if not provided)"
                        },
                        "booking_id": {
                            "type": "string",
                            "description": "The ID of the booking to reschedule"
                        },
                        "new_time": {
                            "type": "string",
                            "description": "The new date and time for the booking (e.g., 'next Monday at 10am')"
                        }
                    },
                    "required": ["booking_id", "new_time"]
                }
            },
            {
                "type": "function",
                "name": "cancel_square_booking",
                "description": "Cancel an existing Square booking.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user ID (optional, uses agent's owner if not provided)"
                        },
                        "booking_id": {
                            "type": "string",
                            "description": "The ID of the booking to cancel"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Optional reason for cancellation"
                        }
                    },
                    "required": ["booking_id"]
                }
            }
        ]
```

## Step 3: Handle Tool Calls in Worker

In the `tool_worker()` function inside `handle_media_stream_with_agent()`, add handlers for Square tools:

```python
async def tool_worker():
    nonlocal websocket, should_end_call, goodbye_audio_bytes
    while True:
        job = await tool_queue.get()
        if job is None:  # shutdown signal
            break
        name: str = job.get("name", "")
        call_id: str = job.get("call_id", "")
        args: Dict[str, Any] = job.get("arguments") or {}

        try:
            # Existing tool handlers (get_weather, get_availability, set_meeting, end_call)

            # Square tool handlers
            elif name == "get_square_availability":
                user_id = args.get("user_id")
                if not user_id:
                    agent_result = supabase.table('agents').select('user_id').eq('id', agent_id).single().execute()
                    if agent_result.data:
                        user_id = agent_result.data.get('user_id')

                days_ahead = args.get("days_ahead", 7)
                location_id = args.get("location_id")

                if not user_id:
                    output_obj = {"error": "user_id is required for Square availability check"}
                else:
                    result = await get_square_availability(
                        supabase=supabase,
                        user_id=user_id,
                        days_ahead=days_ahead,
                        location_id=location_id
                    )
                    output_obj = {"availability": result}

            elif name == "create_square_booking":
                user_id = args.get("user_id")
                if not user_id:
                    agent_result = supabase.table('agents').select('user_id').eq('id', agent_id).single().execute()
                    if agent_result.data:
                        user_id = agent_result.data.get('user_id')

                booking_time = args.get("booking_time")
                customer_note = args.get("customer_note")
                location_id = args.get("location_id")

                if not user_id:
                    output_obj = {"error": "user_id is required for Square booking"}
                elif not booking_time:
                    output_obj = {"error": "booking_time is required"}
                else:
                    result = await create_square_booking(
                        supabase=supabase,
                        user_id=user_id,
                        booking_time=booking_time,
                        customer_note=customer_note,
                        location_id=location_id
                    )
                    output_obj = {"booking": result}

            elif name == "reschedule_square_booking":
                user_id = args.get("user_id")
                if not user_id:
                    agent_result = supabase.table('agents').select('user_id').eq('id', agent_id).single().execute()
                    if agent_result.data:
                        user_id = agent_result.data.get('user_id')

                booking_id = args.get("booking_id")
                new_time = args.get("new_time")

                if not user_id:
                    output_obj = {"error": "user_id is required"}
                elif not booking_id:
                    output_obj = {"error": "booking_id is required"}
                elif not new_time:
                    output_obj = {"error": "new_time is required"}
                else:
                    result = await reschedule_square_booking(
                        supabase=supabase,
                        user_id=user_id,
                        booking_id=booking_id,
                        new_time=new_time
                    )
                    output_obj = {"result": result}

            elif name == "cancel_square_booking":
                user_id = args.get("user_id")
                if not user_id:
                    agent_result = supabase.table('agents').select('user_id').eq('id', agent_id).single().execute()
                    if agent_result.data:
                        user_id = agent_result.data.get('user_id')

                booking_id = args.get("booking_id")
                reason = args.get("reason")

                if not user_id:
                    output_obj = {"error": "user_id is required"}
                elif not booking_id:
                    output_obj = {"error": "booking_id is required"}
                else:
                    result = await cancel_square_booking(
                        supabase=supabase,
                        user_id=user_id,
                        booking_id=booking_id,
                        reason=reason
                    )
                    output_obj = {"result": result}

            else:
                output_obj = {"error": f"Unknown tool: {name}"}

            # Send function_call_output back to the conversation
            item_event = {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(output_obj),
                },
            }
            await openai_ws.send(json.dumps(item_event))
            await openai_ws.send(json.dumps({"type": "response.create"}))

        except Exception as e:
            # Error handling...
            pass
```

## Step 4: Example Agent Prompts

Here are example prompts for agents that use Square booking:

### Appointment Booking Agent
```
You are a helpful appointment booking assistant for [Business Name].

Your role is to:
1. Check availability when customers want to book appointments
2. Create bookings for customers at their preferred times
3. Reschedule existing appointments if needed
4. Cancel appointments when requested

When booking:
- Always check availability first using get_square_availability
- Confirm the date and time clearly with the customer
- Create the booking using create_square_booking
- Provide the booking confirmation

Be friendly, professional, and efficient. Always confirm details before creating or modifying bookings.
```

### Full-Service Scheduling Agent
```
You are an AI scheduling assistant for [Business Name].

Available functions:
- Check availability in our calendar
- Book new appointments
- Reschedule existing appointments
- Cancel appointments

Process:
1. Greet the caller warmly
2. Ask what they need (new booking, reschedule, cancel)
3. For new bookings: Check availability first, then book
4. For rescheduling: Get booking ID and new time preference
5. For cancellations: Get booking ID and reason if provided
6. Always confirm actions and provide confirmation details

Be conversational, helpful, and make the scheduling process smooth and easy.
```

## Step 5: Testing the Integration

### Test Conversation Flow

1. **Check Availability**:
   - User: "What times do you have available tomorrow?"
   - Agent calls: `get_square_availability(days_ahead=1)`
   - Agent: "We have slots available at 9am, 10am, 2pm, and 3pm tomorrow."

2. **Book Appointment**:
   - User: "I'd like to book 2pm tomorrow"
   - Agent calls: `create_square_booking(booking_time="tomorrow at 2pm")`
   - Agent: "Great! I've booked you for 2pm tomorrow. Your booking ID is XYZ."

3. **Reschedule**:
   - User: "I need to move my appointment to 3pm"
   - Agent calls: `reschedule_square_booking(booking_id="XYZ", new_time="3pm")`
   - Agent: "Done! I've moved your appointment to 3pm."

4. **Cancel**:
   - User: "I need to cancel my appointment"
   - Agent calls: `cancel_square_booking(booking_id="XYZ", reason="Customer requested")`
   - Agent: "Your appointment has been cancelled. Is there anything else I can help with?"

## Important Notes

1. **User ID**: The agent automatically uses the agent owner's user_id if not provided
2. **Location ID**: Uses the first available location if not specified
3. **Time Parsing**: Supports natural language ("tomorrow at 2pm") and ISO format
4. **Error Handling**: All functions return error messages if credentials are missing
5. **Token Refresh**: Automatically refreshes expired tokens before API calls

## Agent ID Configuration

To enable Square tools for a specific agent, update the condition in `send_session_update()`:

```python
if agent_id == "your-actual-agent-id-here":
    tools = [
        # Include Square tools here
    ]
```

You can find your agent ID in the Supabase `agents` table or in the URL when editing an agent in the UI.

## Production Considerations

1. **Rate Limiting**: Square has API rate limits - monitor usage
2. **Webhooks**: Consider setting up Square webhooks for real-time updates
3. **Booking Policies**: Configure cancellation and rescheduling policies in Square
4. **Time Zones**: Ensure timezone handling is correct for your business
5. **Error Messages**: Customize error responses for better user experience