"""Debug script to list all calendars the API can access."""
import os
from dotenv import load_dotenv
from supabase import create_client
from gcal import build_credentials, GoogleOAuthPayload, get_calendar_service
from datetime import datetime, timezone

load_dotenv()

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

TEST_USER_ID = 'f8505a11-96cb-4e3f-a326-5ab5e511cdb2'

# Fetch credentials
result = supabase.table('google_credentials').select('*').eq('user_id', TEST_USER_ID).single().execute()
creds_data = result.data

expiry = None
if creds_data.get('expiry'):
    expiry = datetime.fromisoformat(creds_data['expiry'].replace('Z', '+00:00'))

payload = GoogleOAuthPayload(
    user_id=creds_data['user_id'],
    access_token=creds_data['access_token'],
    refresh_token=creds_data.get('refresh_token'),
    token_uri=creds_data.get('token_uri', 'https://oauth2.googleapis.com/token'),
    client_id=creds_data.get('client_id'),
    client_secret=creds_data.get('client_secret'),
    scopes=creds_data.get('scopes'),
    expiry=expiry
)

creds = build_credentials(payload)
service = get_calendar_service(creds)

print("\n" + "="*80)
print("AVAILABLE CALENDARS")
print("="*80)

try:
    calendar_list = service.calendarList().list().execute()
    calendars = calendar_list.get('items', [])

    print(f"\nFound {len(calendars)} calendars:\n")

    for cal in calendars:
        cal_id = cal['id']
        summary = cal.get('summary', 'No name')
        primary = ' (PRIMARY)' if cal.get('primary') else ''
        access_role = cal.get('accessRole', 'unknown')

        print(f"📅 {summary}{primary}")
        print(f"   ID: {cal_id}")
        print(f"   Access: {access_role}")

        # Try to fetch events from this calendar
        try:
            from datetime import timedelta
            time_min = datetime.now(timezone.utc)
            time_max = time_min + timedelta(days=30)

            events_result = service.events().list(
                calendarId=cal_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                maxResults=10,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])
            print(f"   Events found: {len(events)}")

            if events:
                print(f"   Sample events:")
                for event in events[:3]:
                    print(f"      - {event.get('summary', 'No title')}")
        except Exception as e:
            print(f"   Error fetching events: {e}")

        print()

except Exception as e:
    print(f"Error listing calendars: {e}")
    import traceback
    traceback.print_exc()
