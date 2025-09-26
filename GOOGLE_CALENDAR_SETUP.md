# Google Calendar Integration Setup

## Critical Frontend Requirements

### 1. OAuth Scopes (MOST IMPORTANT)

For the calendar integration to work properly, your frontend MUST request the correct OAuth scopes:

#### Recommended Scope (Full Access):
```javascript
const SCOPES = ['https://www.googleapis.com/auth/calendar'];
```

#### Alternative Scopes:
- **Read-only** (can ONLY view availability, NOT schedule meetings):
  ```javascript
  const SCOPES = ['https://www.googleapis.com/auth/calendar.readonly'];
  ```

- **Events only** (can view AND create events):
  ```javascript
  const SCOPES = ['https://www.googleapis.com/auth/calendar.events'];
  ```

### 2. Required OAuth Token Data

When sending the OAuth payload to `/google/auth`, include ALL of these fields:

```javascript
const payload = {
  user_id: "unique_user_identifier",  // Required: Unique ID for the user
  access_token: "ya29.xxx...",        // Required: The OAuth access token
  refresh_token: "1//xxx...",         // Highly recommended for long-term access
  token_uri: "https://oauth2.googleapis.com/token",  // Default value
  client_id: "xxx.apps.googleusercontent.com",       // Required for refresh
  client_secret: "GOCSPX-xxx",                       // Required for refresh
  scopes: [                                          // Required: Must match requested scopes
    "https://www.googleapis.com/auth/calendar"
  ],
  expiry: "2024-01-01T00:00:00Z",    // Optional: Token expiration time
  service: "calendar"                  // Optional: Specify service type
};
```

### 3. Google OAuth Configuration

Configure your Google OAuth consent screen and API:

1. **Enable Google Calendar API** in Google Cloud Console
2. **Configure OAuth Consent Screen**:
   - Add your app's domain
   - Add authorized redirect URIs
   - Add required scopes

3. **Create OAuth 2.0 Client ID**:
   - Application type: Web application
   - Authorized JavaScript origins: Your frontend URL
   - Authorized redirect URIs: Your callback URL

### 4. Frontend OAuth Flow Example

```javascript
// Using Google's OAuth2 library
const client = google.accounts.oauth2.initTokenClient({
  client_id: 'YOUR_CLIENT_ID.apps.googleusercontent.com',
  scope: 'https://www.googleapis.com/auth/calendar',
  callback: async (response) => {
    // Send the complete token data to your backend
    const payload = {
      user_id: getCurrentUserId(),
      access_token: response.access_token,
      // For refresh token, you need to use the authorization code flow
      // refresh_token: response.refresh_token,
      client_id: 'YOUR_CLIENT_ID.apps.googleusercontent.com',
      client_secret: 'YOUR_CLIENT_SECRET',  // Keep this secure!
      scopes: ['https://www.googleapis.com/auth/calendar'],
      service: 'calendar'
    };

    await fetch('YOUR_BACKEND/google/auth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
  }
});
```

### 5. Getting Refresh Token

To get a refresh token (for persistent access), use the authorization code flow:

```javascript
// Step 1: Get authorization code
const authUrl = new URL('https://accounts.google.com/o/oauth2/v2/auth');
authUrl.searchParams.append('client_id', CLIENT_ID);
authUrl.searchParams.append('redirect_uri', REDIRECT_URI);
authUrl.searchParams.append('response_type', 'code');
authUrl.searchParams.append('scope', 'https://www.googleapis.com/auth/calendar');
authUrl.searchParams.append('access_type', 'offline');  // Important for refresh token
authUrl.searchParams.append('prompt', 'consent');       // Force consent to get refresh token

// Step 2: Exchange code for tokens
const tokenResponse = await fetch('https://oauth2.googleapis.com/token', {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  body: new URLSearchParams({
    code: authorizationCode,
    client_id: CLIENT_ID,
    client_secret: CLIENT_SECRET,
    redirect_uri: REDIRECT_URI,
    grant_type: 'authorization_code'
  })
});

const tokens = await tokenResponse.json();
// tokens will contain both access_token and refresh_token
```

## Troubleshooting Common Issues

### 1. "No events found" / Calendar appears empty when it's not

**Possible Causes:**
- **Wrong calendar**: The API might be looking at a different calendar. Check the debug output for which calendars are accessible.
- **Timezone issues**: Events might be outside the queried time range due to timezone differences.
- **Insufficient permissions**: Read-only scopes might not show all events.

**Solution:**
- Use the full `calendar` scope
- Check debug logs to see which calendar is being accessed
- Verify the calendar timezone matches your expectation

### 2. "Authorization error" when scheduling meetings

**Cause**: The OAuth token only has read-only access
**Solution**: Re-authenticate with write scopes (`calendar.events` or `calendar`)

### 3. "No credentials found" error

**Cause**: User hasn't authenticated yet or credentials not stored properly
**Solution**: Ensure the user completes OAuth flow and all required fields are sent

### 4. Events not showing in availability

**Debug Steps:**
1. Check console output when authenticating - it will show:
   - All accessible calendars
   - Calendar timezone
   - Number of events found
   - Event details

2. Verify the primary calendar is correct
3. Check if events are in the queried time range (next 7 days by default)

## Backend Functions

1. **`get_availability()`**: Check calendar availability (requires read access)
2. **`set_meeting()`**: Schedule new meetings (requires write access)

Both functions automatically use the agent owner's credentials when called by an agent during a phone conversation.

## Debug Information

When you authenticate, the backend will print:
1. List of all calendars (with IDs and access roles)
2. Calendar timezone
3. Number of events found
4. First 3 events with their times
5. Calculated availability

Use this information to diagnose why events might not be showing up.

2. **Frontend OAuth Configuration Example**:
   ```javascript
   const SCOPES = [
     'https://www.googleapis.com/auth/calendar.events',  // For full calendar access
     // OR
     'https://www.googleapis.com/auth/calendar'  // For all calendar operations
   ];
   ```

3. **Testing the Integration**:
   - First authenticate with proper scopes
   - The backend will print the granted scopes to console
   - Check that the scopes include write access if you need to create meetings

## Troubleshooting

### "Authorization Error" when scheduling meetings:
- **Cause**: The OAuth token only has read-only access
- **Solution**: Re-authenticate with write scopes (`calendar.events` or `calendar`)

### "No credentials found" error:
- **Cause**: User hasn't authenticated yet
- **Solution**: Ensure the user goes through the OAuth flow first

## Backend Functions

1. **`get_availability()`**: Check calendar availability (requires read access)
2. **`set_meeting()`**: Schedule new meetings (requires write access)

Both functions automatically use the agent owner's credentials when called by an agent during a phone conversation.