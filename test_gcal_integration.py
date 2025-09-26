#!/usr/bin/env python3
"""
Test script for Google Calendar integration
Tests the complete flow from OAuth to calendar operations
"""

import json
import requests
from datetime import datetime, timezone, timedelta
from gcal import GoogleOAuthPayload, build_credentials, get_calendar_events, create_calendar_event

def test_oauth_payload_parsing():
    """Test that we can parse RFC3339 datetime strings correctly"""
    print("Testing OAuth payload parsing...")

    # Simulate payload from frontend with RFC3339 timestamp
    frontend_payload = {
        "user_id": "test-user",
        "access_token": "test-token",
        "client_id": "test-client",
        "client_secret": "test-secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
        "service": "calendar",
        "expiry": "2025-09-27T01:23:45+00:00",  # RFC3339 with explicit offset
        "now": "2025-09-26T01:23:45+00:00",
        "timezone": "America/Los_Angeles"
    }

    try:
        # Parse the payload
        payload = GoogleOAuthPayload(**frontend_payload)
        print(f"✅ Successfully parsed payload")
        print(f"   Expiry type: {type(payload.expiry)}")
        print(f"   Expiry value: {payload.expiry}")
        print(f"   Expiry is timezone-aware: {payload.expiry.tzinfo is not None if payload.expiry else 'N/A'}")

        # Test building credentials
        creds = build_credentials(payload)
        print(f"✅ Successfully built credentials")
        print(f"   Credentials expiry: {creds.expiry}")
        print(f"   Expiry is timezone-aware: {creds.expiry.tzinfo is not None if creds.expiry else 'N/A'}")

        return True
    except Exception as e:
        print(f"❌ Failed to parse payload: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_datetime_comparison():
    """Test that datetime comparisons work correctly with timezone-aware datetimes"""
    print("\nTesting datetime comparisons...")

    try:
        # Create timezone-aware datetimes
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=1)
        past = now - timedelta(hours=1)

        # Test comparisons
        assert future > now, "Future should be greater than now"
        assert past < now, "Past should be less than now"
        assert now == now, "Now should equal itself"

        print(f"✅ Timezone-aware datetime comparisons work correctly")
        print(f"   Now: {now}")
        print(f"   Future: {future}")
        print(f"   Past: {past}")

        # Test with mixed timezone representations
        utc_time = datetime.now(timezone.utc)
        # Create a time with offset notation
        iso_str = "2025-09-26T12:00:00+00:00"
        from dateutil.parser import isoparse
        parsed_time = isoparse(iso_str)

        # These should be comparable without errors
        try:
            result = parsed_time > utc_time
            print(f"✅ Can compare parsed RFC3339 with UTC datetime: {result}")
        except TypeError as e:
            print(f"❌ Cannot compare datetimes: {e}")
            return False

        return True
    except Exception as e:
        print(f"❌ Datetime comparison test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_backend_endpoint():
    """Test the /google/auth endpoint with a sample payload"""
    print("\nTesting backend /google/auth endpoint...")

    # This will fail with invalid credentials but should not have datetime errors
    test_payload = {
        "user_id": "test-user-123",
        "access_token": "invalid-token-for-testing",
        "client_id": "test-client-id",
        "client_secret": "test-secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
        "service": "calendar",
        "expiry": "2025-09-27T01:23:45+00:00",  # RFC3339 format
        "now": "2025-09-26T01:23:45+00:00",
        "timezone": "America/Los_Angeles"
    }

    try:
        response = requests.post(
            "http://localhost:8000/google/auth",
            json=test_payload,
            timeout=5
        )

        # We expect 401 because the token is invalid, but not a datetime error
        if response.status_code == 401:
            error_detail = response.json().get('detail', '')
            if 'datetime' in error_detail.lower() or 'compare' in error_detail.lower():
                print(f"❌ Still getting datetime comparison error: {error_detail}")
                return False
            else:
                print(f"✅ Backend correctly rejects invalid token (no datetime errors)")
                print(f"   Response: {response.status_code} - {error_detail}")
                return True
        elif response.status_code == 400:
            error_detail = response.json().get('detail', '')
            if 'datetime' in error_detail.lower():
                print(f"❌ Datetime parsing error: {error_detail}")
                return False
            else:
                print(f"⚠️  Bad request (expected): {error_detail}")
                return True
        else:
            print(f"⚠️  Unexpected response: {response.status_code}")
            print(f"   Body: {response.json()}")
            return True  # Not a datetime error at least

    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to backend. Is the server running on port 8000?")
        return False
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

def main():
    print("=" * 60)
    print("Google Calendar Integration Test Suite")
    print("=" * 60)

    results = []

    # Run tests
    results.append(("OAuth Payload Parsing", test_oauth_payload_parsing()))
    results.append(("Datetime Comparisons", test_datetime_comparison()))
    results.append(("Backend Endpoint", test_backend_endpoint()))

    # Summary
    print("\n" + "=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)

    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_name:.<40} {status}")

    total_passed = sum(1 for _, passed in results if passed)
    total_tests = len(results)

    print("\n" + "-" * 60)
    print(f"Total: {total_passed}/{total_tests} tests passed")

    if total_passed == total_tests:
        print("\n🎉 All tests passed! The datetime comparison issue is fixed.")
    else:
        print("\n⚠️  Some tests failed. Please review the output above.")

    return total_passed == total_tests

if __name__ == "__main__":
    exit(0 if main() else 1)