"""
Test script for Square integration functions.

This script tests Square location discovery, booking retrieval,
and multi-location scenarios for the barber shop use case.
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

# Test user ID - Barber shop client
TEST_USER_ID = '8170aa8b-0fa4-42e6-800f-9a0f1516d284'


async def test_list_all_locations():
    """Test 1: List all Square locations for the user"""
    print("\n" + "="*80)
    print("TEST 1: List all Square locations")
    print("="*80)
    print("Purpose: Identify all locations in the Square account")
    print("This helps understand if the barber shop has multiple sub-locations")
    print("-"*80)

    try:
        # Fetch credentials from Supabase
        result = supabase.table('square_credentials').select('*').eq('user_id', TEST_USER_ID).single().execute()

        if not result.data:
            print(f"❌ No Square credentials found for user {TEST_USER_ID}")
            return None

        creds_data = result.data
        access_token = creds_data['access_token']

        # Import Square functions
        from square_bookings import list_locations, get_square_headers, get_square_api_base_url

        # Get all locations
        locations_response = list_locations(access_token)
        locations = locations_response.get('locations', [])

        print(f"\n📍 Found {len(locations)} location(s):\n")

        location_ids = []
        for i, loc in enumerate(locations, 1):
            print(f"{i}. Location Name: {loc.get('name', 'Unnamed')}")
            print(f"   Location ID: {loc.get('id')}")
            print(f"   Status: {loc.get('status', 'N/A')}")
            print(f"   Address: {loc.get('address', {}).get('address_line_1', 'N/A')}, "
                  f"{loc.get('address', {}).get('locality', 'N/A')}, "
                  f"{loc.get('address', {}).get('administrative_district_level_1', 'N/A')}")
            print(f"   Business Name: {loc.get('business_name', 'N/A')}")
            print(f"   Capabilities: {', '.join(loc.get('capabilities', []))}")
            print(f"   Timezone: {loc.get('timezone', 'N/A')}")
            print()
            location_ids.append(loc.get('id'))

        if len(locations) > 1:
            print("⚠️  MULTIPLE LOCATIONS DETECTED!")
            print("   This could be causing the calendar mix-up issue.")
            print("   Each location should be treated as a separate calendar.\n")

        print("✅ Test 1 passed!")
        return location_ids

    except Exception as e:
        print(f"\n❌ Test 1 failed: {e}")
        import traceback
        traceback.print_exc()
        return None


async def test_list_bookings_per_location(location_ids):
    """Test 2: List bookings for each location separately"""
    print("\n" + "="*80)
    print("TEST 2: List bookings for each location")
    print("="*80)
    print("Purpose: See how bookings are organized across locations")
    print("This shows if bookings are mixed together or properly separated")
    print("-"*80)

    if not location_ids:
        print("❌ No location IDs provided. Run Test 1 first.")
        return

    try:
        # Fetch credentials
        result = supabase.table('square_credentials').select('*').eq('user_id', TEST_USER_ID).single().execute()
        creds_data = result.data
        access_token = creds_data['access_token']

        from square_bookings import list_bookings, list_locations, list_catalog_services

        # Get location names for reference
        locations_response = list_locations(access_token)
        locations = locations_response.get('locations', [])
        location_map = {loc['id']: loc.get('name', loc['id']) for loc in locations}

        # Get service names for reference
        try:
            services = list_catalog_services(access_token)
            service_map = {svc['id']: svc['full_name'] for svc in services}
        except:
            service_map = {}

        user_tz = pytz.timezone('America/New_York')

        # Get ALL bookings (Square API doesn't support location_id filtering in list endpoint)
        print("\n📋 Fetching all bookings (Square API limitation: can't filter by location)...")
        try:
            bookings_response = list_bookings(access_token, limit=100)
            all_bookings_raw = bookings_response.get('bookings', [])
            print(f"   Total bookings retrieved: {len(all_bookings_raw)}")
        except Exception as e:
            print(f"   ❌ Error fetching bookings: {e}")
            all_bookings_raw = []

        # Group by location manually
        from collections import defaultdict
        bookings_by_location = defaultdict(list)

        for booking in all_bookings_raw:
            loc_id = booking.get('location_id')
            if loc_id:
                bookings_by_location[loc_id].append(booking)

        all_bookings = []

        for loc_id in location_ids:
            print(f"\n📅 Bookings for Location: {location_map.get(loc_id, loc_id)}")
            print(f"   Location ID: {loc_id}")
            print("-"*40)

            bookings = bookings_by_location.get(loc_id, [])

            print(f"   Found {len(bookings)} booking(s)")

            if bookings:
                for i, booking in enumerate(bookings, 1):
                    start_at = booking.get('start_at')
                    if start_at:
                        start_dt = datetime.fromisoformat(start_at.replace('Z', '+00:00'))
                        start_local = start_dt.astimezone(user_tz)
                        time_str = start_local.strftime('%A, %B %d, %Y at %I:%M %p')
                    else:
                        time_str = 'N/A'

                    customer_id = booking.get('customer_id', 'N/A')
                    booking_id = booking.get('id', 'N/A')
                    status = booking.get('status', 'N/A')

                    print(f"\n   {i}. Booking ID: {booking_id}")
                    print(f"      Status: {status}")
                    print(f"      Time: {time_str}")
                    print(f"      Customer ID: {customer_id}")
                    print(f"      Location: {location_map.get(loc_id, loc_id)}")

                    if booking.get('appointment_segments'):
                        segments = booking['appointment_segments']
                        print(f"      Services: {len(segments)} segment(s)")
                        for seg in segments:
                            service_id = seg.get('service_variation_id', 'N/A')
                            service_name = service_map.get(service_id, 'Unknown Service')
                            print(f"         - {service_name}")
                            print(f"           Service ID: {service_id}")
                            if seg.get('team_member_id'):
                                print(f"           Team Member ID: {seg.get('team_member_id')}")

                    all_bookings.append({
                        'location_id': loc_id,
                        'location_name': location_map.get(loc_id, loc_id),
                        'booking': booking
                    })
            else:
                print("   No bookings found for this location")

        print("\n" + "="*80)
        print(f"📊 SUMMARY: Found {len(all_bookings)} total booking(s) across {len(location_ids)} location(s)")

        if len(location_ids) > 1 and all_bookings:
            print("\n⚠️  DIAGNOSIS: Multi-location booking detected!")
            print("   Issue: If the AI only checks the first location, it will miss")
            print("   bookings from other locations, causing scheduling conflicts.")
            print("\n   Solution: The AI needs to:")
            print("   1. Ask which location the customer wants")
            print("   2. Check availability for THAT specific location only")
            print("   3. Create bookings at the correct location")

        print("\n✅ Test 2 passed!")

    except Exception as e:
        print(f"\n❌ Test 2 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_list_services_per_location(location_ids):
    """Test 3: List which services are available at each location"""
    print("\n" + "="*80)
    print("TEST 3: List services per location")
    print("="*80)
    print("Purpose: Identify which services belong to which location")
    print("This shows why using wrong service causes 404 errors")
    print("-"*80)

    if not location_ids:
        print("❌ No location IDs provided. Run Test 1 first.")
        return

    try:
        # Fetch credentials
        result = supabase.table('square_credentials').select('*').eq('user_id', TEST_USER_ID).single().execute()
        creds_data = result.data
        access_token = creds_data['access_token']

        from square_bookings import (list_locations, search_availability, list_catalog_services)

        # Get location names
        locations_response = list_locations(access_token)
        locations = locations_response.get('locations', [])
        location_map = {loc['id']: loc.get('name', loc['id']) for loc in locations}

        # Get all services
        try:
            services = list_catalog_services(access_token)
            print(f"\n🛠️  Total services in catalog: {len(services)}")
        except Exception as e:
            print(f"⚠️  Could not retrieve services: {e}")
            services = []
            return

        # Test each service at each location
        from datetime import datetime, timedelta, timezone
        start_at_min = datetime.now(timezone.utc)
        start_at_max = start_at_min + timedelta(hours=24)  # Just check 24 hours for speed

        services_by_location = {}

        for loc_id in location_ids:
            print(f"\n📍 Testing services at: {location_map.get(loc_id, loc_id)}")
            print(f"   Location ID: {loc_id}")
            print("-"*40)

            location_services = []

            for service in services:
                try:
                    segment_filter = {"service_variation_id": service['id']}
                    availability_response = search_availability(
                        access_token=access_token,
                        location_id=loc_id,
                        start_at_min=start_at_min,
                        start_at_max=start_at_max,
                        segment_filters=segment_filter
                    )

                    # If no error, this service works at this location
                    availabilities = availability_response.get('availabilities', [])
                    location_services.append({
                        'name': service['full_name'],
                        'id': service['id'],
                        'duration_ms': service.get('duration_ms'),
                        'slots_available': len(availabilities)
                    })

                except Exception as e:
                    # Service not available at this location
                    continue

            services_by_location[loc_id] = location_services

            print(f"   ✓ Found {len(location_services)} service(s) at this location:\n")
            for i, svc in enumerate(location_services, 1):
                duration_min = svc['duration_ms'] // 60000 if svc['duration_ms'] else 'N/A'
                print(f"   {i}. {svc['name']}")
                print(f"      Service ID: {svc['id']}")
                print(f"      Duration: {duration_min} minutes")
                print(f"      Available slots (next 24h): {svc['slots_available']}")
                print()

        # Summary comparison
        print("\n" + "="*80)
        print("📊 SERVICES COMPARISON")
        print("="*80)

        all_service_names = set()
        for services_list in services_by_location.values():
            for svc in services_list:
                all_service_names.add(svc['name'])

        for service_name in sorted(all_service_names):
            locations_with_service = []
            for loc_id, services_list in services_by_location.items():
                if any(s['name'] == service_name for s in services_list):
                    locations_with_service.append(location_map.get(loc_id, loc_id))

            if len(locations_with_service) == 1:
                print(f"   • {service_name}")
                print(f"     → ONLY at: {locations_with_service[0]}")
            else:
                print(f"   • {service_name}")
                print(f"     → At: {', '.join(locations_with_service)}")

        print("\n⚠️  KEY INSIGHT:")
        print("   Services are location-specific in Square!")
        print("   The AI must use the correct service_id for each location.")
        print("   Using a Brookline service at Harvard SQ causes 404 errors.\n")

        print("✅ Test 3 passed!")
        return services_by_location

    except Exception as e:
        print(f"\n❌ Test 3 failed: {e}")
        import traceback
        traceback.print_exc()
        return None


async def test_check_availability_per_location(location_ids):
    """Test 4: Check availability for each location separately"""
    print("\n" + "="*80)
    print("TEST 4: Check availability for each location")
    print("="*80)
    print("Purpose: See if different locations have different availability")
    print("This demonstrates why location-specific queries are necessary")
    print("-"*80)

    if not location_ids:
        print("❌ No location IDs provided. Run Test 1 first.")
        return

    try:
        # Fetch credentials
        result = supabase.table('square_credentials').select('*').eq('user_id', TEST_USER_ID).single().execute()
        creds_data = result.data
        access_token = creds_data['access_token']

        from square_bookings import (list_locations, search_availability, list_catalog_services)

        # Get location names
        locations_response = list_locations(access_token)
        locations = locations_response.get('locations', [])
        location_map = {loc['id']: loc.get('name', loc['id']) for loc in locations}

        # Get all services
        try:
            services = list_catalog_services(access_token)
            print(f"\n🛠️  Found {len(services)} service(s) in catalog:")
            for svc in services[:5]:  # Show first 5
                print(f"   - {svc['full_name']} (ID: {svc['id']}, Duration: {svc.get('duration_ms', 'N/A')}ms)")
            if len(services) > 5:
                print(f"   ... and {len(services) - 5} more")
        except Exception as e:
            print(f"⚠️  Could not retrieve services: {e}")
            services = []

        print("\n")

        # Check availability for next 3 days for each location
        start_at_min = datetime.now(timezone.utc)
        start_at_max = start_at_min + timedelta(days=3)

        user_tz = pytz.timezone('America/New_York')

        for loc_id in location_ids:
            print(f"\n📅 Availability for Location: {location_map.get(loc_id, loc_id)}")
            print(f"   Location ID: {loc_id}")
            print(f"   Checking next 3 days...")
            print("-"*40)

            # Square API requires segment_filters, so we need to try services until we find one that works
            # Services are location-specific, so we try each service until one works
            found_availability = False

            for service in services[:15]:  # Try first 15 services
                try:
                    segment_filter = {"service_variation_id": service['id']}
                    availability_response = search_availability(
                        access_token=access_token,
                        location_id=loc_id,
                        start_at_min=start_at_min,
                        start_at_max=start_at_max,
                        segment_filters=segment_filter
                    )

                    availabilities = availability_response.get('availabilities', [])

                    if availabilities:
                        found_availability = True
                        print(f"   ✓ Found availability using service: {service['full_name']}")
                        print(f"   Found {len(availabilities)} available slot(s)")

                        # Group by day
                        from collections import defaultdict
                        slots_by_day = defaultdict(list)

                        for slot in availabilities[:20]:  # Show first 20
                            start_at = datetime.fromisoformat(slot['start_at'].replace('Z', '+00:00'))
                            start_local = start_at.astimezone(user_tz)
                            day_key = start_local.strftime('%A, %B %d')
                            time_str = start_local.strftime('%I:%M %p')
                            slots_by_day[day_key].append(time_str)

                        for day, times in sorted(slots_by_day.items()):
                            print(f"\n   {day}:")
                            print(f"      {', '.join(times[:10])}")
                            if len(times) > 10:
                                print(f"      ... and {len(times) - 10} more slots")

                        break  # Found availability, stop trying other services

                except Exception as e:
                    # This service doesn't work for this location, try next one
                    continue

            if not found_availability:
                print("   ⚠️  No available slots found (tried multiple services)")

        print("\n✅ Test 4 passed!")

    except Exception as e:
        print(f"\n❌ Test 4 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_merchant_info():
    """Test 5: Get merchant information"""
    print("\n" + "="*80)
    print("TEST 5: Get merchant information")
    print("="*80)
    print("Purpose: Retrieve merchant details and understand account structure")
    print("-"*80)

    try:
        # Fetch credentials
        result = supabase.table('square_credentials').select('*').eq('user_id', TEST_USER_ID).single().execute()
        creds_data = result.data
        access_token = creds_data['access_token']

        from square_bookings import get_merchant_info

        merchant_response = get_merchant_info(access_token)
        merchant = merchant_response.get('merchant', [])

        if merchant:
            merchant = merchant[0] if isinstance(merchant, list) else merchant
            print(f"\n🏢 Merchant Information:")
            print(f"   Merchant ID: {merchant.get('id', 'N/A')}")
            print(f"   Business Name: {merchant.get('business_name', 'N/A')}")
            print(f"   Country: {merchant.get('country', 'N/A')}")
            print(f"   Language: {merchant.get('language_code', 'N/A')}")
            print(f"   Currency: {merchant.get('currency', 'N/A')}")
            print(f"   Status: {merchant.get('status', 'N/A')}")

            if merchant.get('main_location_id'):
                print(f"   Main Location ID: {merchant.get('main_location_id')}")

        print("\n✅ Test 5 passed!")

    except Exception as e:
        print(f"\n❌ Test 5 failed: {e}")
        import traceback
        traceback.print_exc()


async def test_diagnose_calendar_issue():
    """Test 6: Diagnose the calendar mix-up issue"""
    print("\n" + "="*80)
    print("TEST 6: DIAGNOSE CALENDAR MIX-UP ISSUE")
    print("="*80)
    print("Purpose: Identify why bookings might be getting mixed up")
    print("-"*80)

    try:
        # Fetch credentials
        result = supabase.table('square_credentials').select('*').eq('user_id', TEST_USER_ID).single().execute()
        creds_data = result.data
        access_token = creds_data['access_token']

        from square_bookings import list_locations, list_bookings

        # Get all locations
        locations_response = list_locations(access_token)
        locations = locations_response.get('locations', [])

        print(f"\n🔍 ANALYSIS:")
        print(f"   Total locations: {len(locations)}")

        if len(locations) == 1:
            print("\n   ✅ Single location detected.")
            print("      This is NOT a multi-location issue.")
            print("\n   Possible causes for mix-up:")
            print("      1. Multiple team members sharing one calendar")
            print("      2. Different service types in same location")
            print("      3. Customer confusion about which service they booked")

        elif len(locations) > 1:
            print("\n   ⚠️  MULTIPLE LOCATIONS DETECTED!")
            print("      This is LIKELY causing the calendar mix-up.")
            print("\n   Current AI behavior:")
            print("      - AI defaults to FIRST location only (line 672 in square_bookings.py)")
            print("      - Other locations are IGNORED")
            print("\n   What's happening:")

            # Get first location name
            first_loc = locations[0].get('name', locations[0].get('id'))
            print(f"      1. AI always checks availability for: '{first_loc}'")
            print(f"      2. Customer might want different location")
            print(f"      3. Bookings at other locations don't show as 'busy'")
            print(f"      4. This causes double-bookings and confusion")

            print("\n   🔧 RECOMMENDED FIXES:")
            print("\n   Option 1: Ask customer for location preference (RECOMMENDED)")
            print("      - Modify AI prompt to ask: 'Which location would you prefer?'")
            print("      - List available locations with addresses")
            print("      - Use customer's chosen location_id for availability/booking")
            print("      - Code change: Pass location_id to get_square_availability()")

            print("\n   Option 2: Use a specific default location per agent")
            print("      - Create separate agent configs for each location")
            print("      - Brookline agent → location_id: L94253Q6Y3K70")
            print("      - Harvard SQ agent → location_id: LWMF3BP75AW93")
            print("      - Each agent only shows its location's availability")

            print("\n   Option 3: Show availability across all locations")
            print("      - Query availability for ALL locations")
            print("      - Label each time slot with its location")
            print("      - Let customer choose location + time together")
            print("      - Example: '10:00 AM at Brookline' or '10:30 AM at Harvard SQ'")

            print("\n   Option 4: Location-based phone numbers")
            print("      - Use different phone numbers for each location")
            print("      - Automatically set location based on which number was called")
            print("      - Requires phone system integration")

            # Show which location is currently being used by default
            print(f"\n   📍 Current default location: '{first_loc}' (location_id: {locations[0].get('id')})")
            print(f"   📍 Other locations being IGNORED:")
            for loc in locations[1:]:
                print(f"      - {loc.get('name', loc.get('id'))} (location_id: {loc.get('id')})")

        print("\n✅ Test 6 passed!")

    except Exception as e:
        print(f"\n❌ Test 6 failed: {e}")
        import traceback
        traceback.print_exc()


async def run_all_tests():
    """Run all tests sequentially"""
    print("\n" + "🔬"*40)
    print("SQUARE INTEGRATION TEST SUITE - MULTI-LOCATION DIAGNOSTIC")
    print("🔬"*40)
    print(f"\nTest User ID: {TEST_USER_ID}")
    print(f"Client: Barber Shop (multi-location issue)")
    print(f"Timezone: America/New_York (NY Time)")
    print("\nThis test suite will help identify:")
    print("  • How many locations exist in the Square account")
    print("  • How bookings are organized across locations")
    print("  • Why the AI might be mixing up calendars")
    print("  • Recommended fixes for the issue")

    # Test 1: List all locations
    location_ids = await test_list_all_locations()
    await asyncio.sleep(1)

    if location_ids:
        # Test 2: List bookings per location
        await test_list_bookings_per_location(location_ids)
        await asyncio.sleep(1)

        # Test 3: List services per location
        await test_list_services_per_location(location_ids)
        await asyncio.sleep(1)

        # Test 4: Check availability per location
        await test_check_availability_per_location(location_ids)
        await asyncio.sleep(1)

    # Test 5: Merchant info
    await test_merchant_info()
    await asyncio.sleep(1)

    # Test 6: Diagnose the issue
    await test_diagnose_calendar_issue()

    print("\n" + "="*80)
    print("ALL TESTS COMPLETED!")
    print("="*80)
    print("\n📋 KEY FINDINGS SUMMARY:")
    print("   Review the test results above to understand:")
    print("   1. How many locations exist")
    print("   2. How bookings are distributed")
    print("   3. Which location the AI defaults to")
    print("   4. What fixes are recommended")
    print("\n💡 Next Steps:")
    print("   - Review Test 5 recommendations")
    print("   - Decide on a location handling strategy")
    print("   - Update AI prompts or code accordingly")
    print("="*80 + "\n")


if __name__ == '__main__':
    asyncio.run(run_all_tests())
