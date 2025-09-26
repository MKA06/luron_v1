#!/usr/bin/env python3
"""
Manual testing script for Google OAuth flow.

Use this to:
1. Generate an OAuth URL for manual authorization
2. Exchange the authorization code for tokens
3. Test the complete flow
"""

import os
import asyncio
import aiohttp
import webbrowser
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

# Configuration
CLIENT_ID = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
CLIENT_SECRET = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"  # For manual copy-paste flow
BACKEND_URL = "http://localhost:8000"


def generate_oauth_url():
    """Generate the OAuth authorization URL"""
    base_url = "https://accounts.google.com/o/oauth2/v2/auth"

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/calendar",
        "access_type": "offline",  # To get refresh token
        "prompt": "consent"  # Force consent to ensure refresh token
    }

    return f"{base_url}?{urlencode(params)}"


async def exchange_code(auth_code: str, user_id: str):
    """Exchange authorization code for tokens via backend"""

    payload = {
        "user_id": user_id,
        "authorization_code": auth_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
        "service": "calendar"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{BACKEND_URL}/google/auth", json=payload) as resp:
            result = await resp.json()
            return resp.status, result


async def test_availability(user_id: str):
    """Test fetching calendar availability"""

    # Import and call the function directly
    from main import get_availability

    result = await get_availability(user_id=user_id, days_ahead=7)
    return result


async def test_create_meeting(user_id: str):
    """Test creating a calendar event"""

    from main import set_meeting
    from datetime import datetime, timedelta, timezone

    # Schedule for tomorrow at 2 PM
    meeting_time = (datetime.now(timezone.utc) + timedelta(days=1, hours=14)).isoformat()

    result = await set_meeting(
        user_id=user_id,
        meeting_name="Test Meeting - OAuth Integration",
        meeting_time=meeting_time,
        duration_minutes=30,
        description="Test meeting created via OAuth integration",
        location="Virtual"
    )

    return result


async def main():
    """Main test flow"""
    print("Google Calendar OAuth Manual Test")
    print("="*50)

    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ Error: Missing GOOGLE_OAUTH_CLIENT_ID or GOOGLE_OAUTH_CLIENT_SECRET in .env")
        return

    # Step 1: Get user ID
    user_id = input("Enter a user ID for testing (e.g., test-user-001): ").strip()
    if not user_id:
        user_id = "test-user-001"

    print(f"\nUsing user ID: {user_id}")

    # Step 2: Generate OAuth URL
    auth_url = generate_oauth_url()
    print(f"\n1. Open this URL in your browser to authorize:")
    print(f"   {auth_url}")

    # Try to open in browser
    try:
        webbrowser.open(auth_url)
        print("   (Browser should open automatically)")
    except:
        pass

    print("\n2. After authorizing, you'll see an authorization code")
    print("3. Copy and paste that code here:")

    auth_code = input("\nAuthorization code: ").strip()

    if not auth_code:
        print("❌ No authorization code provided")
        return

    # Step 3: Exchange code for tokens
    print("\n4. Exchanging authorization code for tokens...")
    status, result = await exchange_code(auth_code, user_id)

    if status == 200:
        print("✅ Successfully authenticated and stored credentials!")
    else:
        print(f"❌ Authentication failed: {result}")
        return

    # Step 4: Test calendar operations
    print("\n5. Testing calendar operations...")

    # Test availability
    print("\n   Testing availability check...")
    availability = await test_availability(user_id)
    if "Error" not in availability:
        print("   ✅ Availability check successful!")
        print(f"   First few lines: {availability[:200]}...")
    else:
        print(f"   ❌ Availability check failed: {availability}")

    # Test meeting creation
    create_meeting = input("\n   Do you want to create a test meeting? (y/n): ").strip().lower()
    if create_meeting == 'y':
        print("   Creating test meeting...")
        meeting_result = await test_create_meeting(user_id)
        if "Error" not in meeting_result:
            print("   ✅ Meeting created successfully!")
            print(f"   Result: {meeting_result}")
        else:
            print(f"   ❌ Meeting creation failed: {meeting_result}")

    print("\n✅ OAuth flow test complete!")
    print("\nYour agent can now:")
    print("- Check calendar availability with get_availability()")
    print("- Schedule meetings with set_meeting()")
    print("- Tokens will auto-refresh when needed")


if __name__ == "__main__":
    print("\n⚠️  PREREQUISITES:")
    print("1. Backend server must be running (python main.py)")
    print("2. Google OAuth credentials must be in .env file")
    print("3. Google Calendar API must be enabled in Google Cloud Console")
    print("")

    asyncio.run(main())