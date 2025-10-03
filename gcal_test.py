"""
Test script for Google Calendar integration functions.

This script tests get_availability and set_meeting functions
using credentials from the Supabase database.
"""

import asyncio
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv()

# Supabase configuration
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError('Missing Supabase credentials. Please set SUPABASE_URL and SUPABASE_KEY in the .env file.')

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Test user ID
TEST_USER_ID = 'f8505a11-96cb-4e3f-a326-5ab5e511cdb2'


async def test_get_availability_all_days():
    """Test get_availability without specific_day (shows all available days)"""
    print("\n" + "="*80)
    print("TEST 1: Get availability for all days (default 60 days)")
    print("="*80)

    from gcal import get_availability

    try:
        result = await get_availability(
            supabase=supabase,
            user_id=TEST_USER_ID,
            days_ahead=60
        )
        print("*"*100)
        print(result)
        print("\n✅ Test 1 passed!")
    except Exception as e:
        print(f"\n❌ Test 1 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_get_availability_today():
    """Test get_availability for today only"""
    print("\n" + "="*80)
    print("TEST 2: Get availability for today only")
    print("="*80)

    from gcal import get_availability

    try:
        result = await get_availability(
            supabase=supabase,
            user_id=TEST_USER_ID,
            days_ahead=60,
            specific_day='today'
        )
        print("*"*100)

        print(result)
        print("\n✅ Test 2 passed!")
    except Exception as e:
        print(f"\n❌ Test 2 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_get_availability_tomorrow():
    """Test get_availability for tomorrow only"""
    print("\n" + "="*80)
    print("TEST 3: Get availability for tomorrow only")
    print("="*80)

    from gcal import get_availability

    try:
        result = await get_availability(
            supabase=supabase,
            user_id=TEST_USER_ID,
            days_ahead=60,
            specific_day='tomorrow'
        )
        print(result)
        print("\n✅ Test 3 passed!")
    except Exception as e:
        print(f"\n❌ Test 3 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_get_availability_specific_date():
    """Test get_availability for a specific date"""
    print("\n" + "="*80)
    print("TEST 4: Get availability for a specific date (7 days from now)")
    print("="*80)

    from gcal import get_availability
    import pytz

    try:
        # Get date 7 days from now in NY timezone
        ny_tz = pytz.timezone('America/New_York')
        target_date = (datetime.now(ny_tz) + timedelta(days=7)).strftime('%Y-%m-%d')

        print(f"Checking availability for: {target_date}")

        result = await get_availability(
            supabase=supabase,
            user_id=TEST_USER_ID,
            days_ahead=60,
            specific_day=target_date
        )
        print(result)
        print("\n✅ Test 4 passed!")
    except Exception as e:
        print(f"\n❌ Test 4 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_get_availability_day_of_week():
    """Test get_availability for next Monday"""
    print("\n" + "="*80)
    print("TEST 5: Get availability for next Monday")
    print("="*80)

    from gcal import get_availability

    try:
        result = await get_availability(
            supabase=supabase,
            user_id=TEST_USER_ID,
            days_ahead=60,
            specific_day='Monday'
        )
        print(result)
        print("\n✅ Test 5 passed!")
    except Exception as e:
        print(f"\n❌ Test 5 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_set_meeting():
    """Test set_meeting to create a new meeting"""
    print("\n" + "="*80)
    print("TEST 6: Create a test meeting (2 hours, default duration)")
    print("="*80)

    from gcal import set_meeting
    import pytz

    try:
        # Schedule a meeting for tomorrow at 2 PM
        ny_tz = pytz.timezone('America/New_York')
        tomorrow = datetime.now(ny_tz) + timedelta(days=1)
        meeting_time = tomorrow.replace(hour=14, minute=0, second=0, microsecond=0)
        meeting_time_str = meeting_time.strftime('%Y-%m-%d %H:%M')

        print(f"Scheduling meeting for: {meeting_time_str} NY time")

        result = await set_meeting(
            supabase=supabase,
            user_id=TEST_USER_ID,
            meeting_name='Test Meeting - Calendar Integration Test',
            meeting_time=meeting_time_str,
            duration_minutes=120,  # Default 2 hours
            description='This is a test meeting created by gcal_test.py',
            location='Virtual Meeting Room'
        )
        print(result)
        print("\n✅ Test 6 passed!")
    except Exception as e:
        print(f"\n❌ Test 6 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_set_meeting_natural_language():
    """Test set_meeting with natural language time input"""
    print("\n" + "="*80)
    print("TEST 7: Create a meeting using natural language ('tomorrow at 10am')")
    print("="*80)

    from gcal import set_meeting

    try:
        result = await set_meeting(
            supabase=supabase,
            user_id=TEST_USER_ID,
            meeting_name='Test Meeting - Natural Language',
            meeting_time='tomorrow at 10am',
            duration_minutes=120,
            description='This meeting was created using natural language time input',
            location='Conference Room A'
        )
        print(result)
        print("\n✅ Test 7 passed!")
    except Exception as e:
        print(f"\n❌ Test 7 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_business_hours():
    """Verify business hours are 6 AM to 11 PM"""
    print("\n" + "="*80)
    print("TEST 8: Verify business hours are 6 AM to 11 PM NY time")
    print("="*80)

    from gcal import get_availability

    try:
        # Get availability for today
        result = await get_availability(
            supabase=supabase,
            user_id=TEST_USER_ID,
            days_ahead=1,
            specific_day='today'
        )

        # Check if result contains slots starting at 6 AM
        if '06:00 AM' in result or '6:00 AM' in result:
            print("✓ Found slots starting at 6 AM")
        else:
            print("✗ No slots found starting at 6 AM")

        # Check if result contains slots up to 11 PM
        if '11:00 PM' in result or '10:30 PM' in result:
            print("✓ Found slots up to/near 11 PM")
        else:
            print("✗ No slots found up to 11 PM")

        print("\n" + result)
        print("\n✅ Test 8 completed!")
    except Exception as e:
        print(f"\n❌ Test 8 failed: {e}")
        import traceback
        traceback.print_exc()


async def run_all_tests():
    """Run all tests sequentially"""
    print("\n" + "🧪"*40)
    print("GOOGLE CALENDAR INTEGRATION TEST SUITE")
    print("🧪"*40)
    print(f"\nTest User ID: {TEST_USER_ID}")
    print(f"Timezone: America/New_York (NY Time)")
    print(f"Business Hours: 6:00 AM - 11:00 PM")
    print(f"Default Meeting Duration: 2 hours (120 minutes)")
    print(f"Availability Window: 60 days")

    # Run tests
    # await test_get_availability_all_days()
    # await asyncio.sleep(1)  # Brief pause between tests

    await test_get_availability_today()
    await asyncio.sleep(1)

    await test_get_availability_tomorrow()
    await asyncio.sleep(1)

    await test_get_availability_specific_date()
    # await asyncio.sleep(1)

    await test_get_availability_day_of_week()
    await asyncio.sleep(1)

    #await test_business_hours()
    # await asyncio.sleep(1)

    #await test_set_meeting()
    # await asyncio.sleep(1)

    # await test_set_meeting_natural_language()

    print("\n" + "="*80)
    print("ALL TESTS COMPLETED!")
    print("="*80)


if __name__ == '__main__':
    asyncio.run(run_all_tests())
