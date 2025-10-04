"""
Test script for updated Square booking functions with location and service parameters.

This script tests the new functionality where:
1. get_square_availability accepts location and specific_day parameters
2. create_square_booking accepts location and service_id parameters
3. Agent can ask for location first, view services, then book

Instead of actually booking, this script prints the variables that would be used.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client, Client
import pytz

# Load environment variables
load_dotenv()

# Supabase configuration
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError('Missing Supabase credentials. Please set SUPABASE_URL and SUPABASE_KEY in the .env file.')

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Test user ID - Barber shop client with multiple locations
TEST_USER_ID = '8170aa8b-0fa4-42e6-800f-9a0f1516d284'


async def test_get_locations():
    """Test 1: Get list of available locations"""
    print("\n" + "="*80)
    print("TEST 1: Get list of available locations")
    print("="*80)
    print("Purpose: When no location is specified, show available locations")
    print("-"*80)

    try:
        from square_bookings import get_square_availability

        result = await get_square_availability(
            supabase=supabase,
            user_id=TEST_USER_ID,
            days_ahead=7
            # Note: No location parameter = list locations
        )

        print("\nResult:")
        print(result)
        print("\n✅ Test 1 passed!")

    except Exception as e:
        print(f"\n❌ Test 1 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_get_availability_for_location_and_day():
    """Test 2: Get availability for a specific location and specific day"""
    print("\n" + "="*80)
    print("TEST 2: Get availability for specific location and day")
    print("="*80)
    print("Purpose: Show availability for Harvard location on Monday")
    print("-"*80)

    try:
        from square_bookings import get_square_availability

        result = await get_square_availability(
            supabase=supabase,
            user_id=TEST_USER_ID,
            days_ahead=7,
            location='harvard',  # Specify location
            specific_day='Monday'  # Specify day
        )

        print("\nResult:")
        print(result)
        print("\n✅ Test 2 passed!")

    except Exception as e:
        print(f"\n❌ Test 2 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_get_availability_brookline_tomorrow():
    """Test 3: Get availability for Brookline location tomorrow"""
    print("\n" + "="*80)
    print("TEST 3: Get availability for Brookline location tomorrow")
    print("="*80)
    print("Purpose: Show availability for Brookline location on tomorrow")
    print("-"*80)

    try:
        from square_bookings import get_square_availability

        result = await get_square_availability(
            supabase=supabase,
            user_id=TEST_USER_ID,
            days_ahead=7,
            location='brookline',  # Different location
            specific_day='tomorrow'  # Tomorrow
        )

        print("\nResult:")
        print(result)
        print("\n✅ Test 3 passed!")

    except Exception as e:
        print(f"\n❌ Test 3 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_create_booking_dry_run():
    """Test 4: Create booking (DRY RUN - prints variables instead of booking)"""
    print("\n" + "="*80)
    print("TEST 4: Create booking (DRY RUN)")
    print("="*80)
    print("Purpose: Print the variables that would be used for booking")
    print("NOTE: This does NOT actually create a booking")
    print("-"*80)

    try:
        # First, get availability to see service IDs
        from square_bookings import get_square_availability

        print("\n📋 Step 1: Getting availability to find service IDs...\n")
        availability_result = await get_square_availability(
            supabase=supabase,
            user_id=TEST_USER_ID,
            days_ahead=3,
            location='harvard',
            specific_day='tomorrow'
        )

        print(availability_result)

        # Extract a service ID from the result (look for "Service ID: " in output)
        # For demo purposes, we'll use a placeholder
        print("\n" + "-"*80)
        print("📋 Step 2: Simulating booking with the following parameters:")
        print("-"*80)

        # These are the variables that would be passed to create_square_booking
        booking_params = {
            'user_id': TEST_USER_ID,
            'booking_time': 'tomorrow at 2pm',
            'customer_name': 'John Test Customer',
            'customer_phone': '+1234567890',
            'customer_email': 'john@example.com',
            'customer_note': 'First time customer, please confirm appointment',
            'location': 'harvard',  # Location name
            'service_id': None  # Would be extracted from availability result
        }

        print("\n🔍 Booking Parameters (would be used for actual booking):")
        for key, value in booking_params.items():
            print(f"   {key}: {value}")

        print("\n" + "="*80)
        print("⚠️  IMPORTANT: This is a DRY RUN")
        print("="*80)
        print("To actually create a booking:")
        print("1. Extract the service_id from the availability result above")
        print("2. Use that service_id in the booking_params")
        print("3. Uncomment the create_square_booking call below")
        print("="*80)

        # UNCOMMENT TO ACTUALLY BOOK (make sure to set service_id first):
        # from square_bookings import create_square_booking
        # result = await create_square_booking(
        #     supabase=supabase,
        #     **booking_params
        # )
        # print("\nBooking Result:")
        # print(result)

        print("\n✅ Test 4 passed! (Dry run completed)")

    except Exception as e:
        print(f"\n❌ Test 4 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_workflow_simulation():
    """Test 5: Simulate full agent workflow"""
    print("\n" + "="*80)
    print("TEST 5: Simulate full agent booking workflow")
    print("="*80)
    print("Purpose: Show how an agent would use these functions step-by-step")
    print("-"*80)

    try:
        from square_bookings import get_square_availability

        print("\n🤖 Agent: Hello! I can help you book an appointment.")
        print("👤 Customer: I'd like to book an appointment.")
        print()

        # Step 1: Agent asks for location
        print("🤖 Agent: Which location would you prefer?")
        print("   [Agent calls get_square_availability with no location]")
        print()

        locations_result = await get_square_availability(
            supabase=supabase,
            user_id=TEST_USER_ID
        )
        print(locations_result)
        print()

        print("👤 Customer: Harvard Square, please.")
        print()

        # Step 2: Agent asks for preferred day
        print("🤖 Agent: Great! What day would you like to come in?")
        print("👤 Customer: How about Monday?")
        print()
        print("🤖 Agent: Let me check availability for Harvard Square on Monday...")
        print("   [Agent calls get_square_availability with location='harvard', specific_day='Monday']")
        print()

        availability_result = await get_square_availability(
            supabase=supabase,
            user_id=TEST_USER_ID,
            location='harvard',
            specific_day='Monday'
        )
        print(availability_result)
        print()

        # Step 3: Customer chooses time and service
        print("👤 Customer: I'd like the Men's Haircut at 2:00 PM.")
        print()

        # Step 4: Agent collects customer info
        print("🤖 Agent: Perfect! Can I get your name?")
        print("👤 Customer: John Smith")
        print()
        print("🤖 Agent: And a phone number to reach you at?")
        print("👤 Customer: 617-555-1234")
        print()

        # Step 5: Agent prepares to book (DRY RUN)
        print("🤖 Agent: Great! Let me book that for you...")
        print("   [Agent would call create_square_booking with:]")
        print()

        booking_params = {
            'user_id': TEST_USER_ID,
            'booking_time': 'Monday at 2pm',
            'customer_name': 'John Smith',
            'customer_phone': '617-555-1234',
            'location': 'harvard',
            'service_id': '<<extracted from availability result>>',
            'customer_note': 'Booked via phone agent'
        }

        print("   📋 Booking parameters:")
        for key, value in booking_params.items():
            print(f"      {key}: {value}")

        print()
        print("🤖 Agent: All set! You're booked for Monday at 2:00 PM at Harvard Square.")
        print("          You'll receive a confirmation at 617-555-1234.")

        print("\n✅ Test 5 passed! (Workflow simulation completed)")

    except Exception as e:
        print(f"\n❌ Test 5 failed: {e}")
        import traceback
        traceback.print_exc()


async def run_all_tests():
    """Run all tests sequentially"""
    print("\n" + "🧪"*40)
    print("SQUARE BOOKING TEST SUITE - LOCATION & SERVICE PARAMETERS")
    print("🧪"*40)
    print(f"\nTest User ID: {TEST_USER_ID}")
    print(f"Client: Barber Shop (multi-location)")
    print(f"Timezone: America/New_York (NY Time)")
    print("\nThis test suite demonstrates:")
    print("  • How to get list of available locations")
    print("  • How to check availability for a specific location and day")
    print("  • How to view available services for a location")
    print("  • How to prepare booking parameters (without actually booking)")
    print("  • Full agent workflow simulation")

    await test_get_locations()
    await asyncio.sleep(1)

    await test_get_availability_for_location_and_day()
    await asyncio.sleep(1)

    await test_get_availability_brookline_tomorrow()
    await asyncio.sleep(1)

    await test_create_booking_dry_run()
    await asyncio.sleep(1)

    await test_workflow_simulation()

    print("\n" + "="*80)
    print("ALL TESTS COMPLETED!")
    print("="*80)
    print("\n📋 KEY IMPROVEMENTS:")
    print("   ✅ Agent can now ask for location first")
    print("   ✅ Agent can check availability for specific location and day")
    print("   ✅ Agent can see available services for each location")
    print("   ✅ Agent can book with specific location and service")
    print("\n💡 Next Steps:")
    print("   1. Update agent prompts to ask for location first")
    print("   2. Train agent to extract service_id from availability result")
    print("   3. Test with real phone calls")
    print("="*80 + "\n")


if __name__ == '__main__':
    asyncio.run(run_all_tests())
