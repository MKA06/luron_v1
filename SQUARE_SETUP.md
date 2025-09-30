# Square Developer Portal Setup Guide

This guide walks you through setting up Square OAuth and Bookings API integration for your Luron application.

## Prerequisites

- A Square account (create one at https://squareup.com if you don't have one)
- Access to the Square Developer Portal (https://developer.squareup.com)

## Step 1: Create a Square Application

1. Go to the **Square Developer Portal**: https://developer.squareup.com/apps
2. Click **"Create an app"** or **"+ New Application"**
3. Enter your application name (e.g., "Luron Voice AI")
4. Select the appropriate Square account to link this application to
5. Click **"Save"**

## Step 2: Configure OAuth Settings
application id = sandbox-sq0idb-Or7bUXwwtp4RB8ZeNr9j1g
access token = EAAAlw-Pry00Qhavt1_o9EPGKq8s74ZPZ0DCqd7OS-JCuUj6DqlUErWKNTvK3QlN

1. In your newly created application, navigate to the **"OAuth"** section in the left sidebar
2. Under **"Redirect URL"**, add your callback URL:
   - For local development: `http://localhost:5173/auth/square/callback`
   - For production: `https://yourdomain.com/auth/square/callback`
3. Click **"Save"** to save your redirect URL

## Step 3: Get Your Credentials

1. Still in the OAuth section, locate your **Application ID** and **Application Secret**
2. Copy both values - you'll need them for your environment variables

### For Sandbox (Testing)
- Use the **Sandbox** tab to get sandbox credentials
- Sandbox credentials are for testing only and don't affect real Square data

### For Production
- Use the **Production** tab to get production credentials
- Production credentials will work with real Square data and bookings

## Step 4: Enable Required Permissions

1. Navigate to the **"OAuth"** section
2. Ensure the following permissions are enabled (they should be requested via scopes in the OAuth flow):
   - `APPOINTMENTS_READ` - Read booking/appointment information
   - `APPOINTMENTS_WRITE` - Create, update, and cancel bookings
   - `APPOINTMENTS_BUSINESS_SETTINGS_READ` - Read booking settings
   - `MERCHANT_PROFILE_READ` - Read merchant information

**Note**: Square automatically handles scope permissions during the OAuth flow. The user will be prompted to grant these permissions when they connect their Square account.

## Step 5: Set Up Environment Variables

### Backend (luron_v1)

Add the following to your `.env` file:

```bash
# Square OAuth Configuration
SQUARE_OAUTH_CLIENT_ID=your_application_id_here
SQUARE_OAUTH_CLIENT_SECRET=your_application_secret_here
```

### Frontend (luron-core-ai)

Add the following to your `.env` file:

```bash
# Square OAuth Configuration
VITE_SQUARE_OAUTH_CLIENT_ID=your_application_id_here
VITE_SQUARE_OAUTH_CLIENT_SECRET=your_application_secret_here
VITE_SQUARE_REDIRECT_URI=http://localhost:5173/auth/square/callback
VITE_SQUARE_TOKEN_URI=https://connect.squareup.com/oauth2/token

# Backend API Configuration (if not already set)
VITE_EXTERNAL_API_BASE_URL=http://localhost:8000
VITE_EXTERNAL_API_SQUARE_AUTH_PATH=/square/auth
```

**For Production**: Update the redirect URI to your production domain:
```bash
VITE_SQUARE_REDIRECT_URI=https://app.luron.ai/auth/square/callback
```

## Step 6: Set Up Supabase Database Table

You need to create a table to store Square credentials. Run this SQL in your Supabase SQL Editor:

```sql
-- Create square_credentials table
CREATE TABLE IF NOT EXISTS square_credentials (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    token_uri TEXT DEFAULT 'https://connect.squareup.com/oauth2/token',
    client_id TEXT,
    client_secret TEXT,
    scopes TEXT[],
    expires_at TIMESTAMPTZ,
    merchant_id TEXT,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_square_credentials_user_id ON square_credentials(user_id);

-- Enable Row Level Security
ALTER TABLE square_credentials ENABLE ROW LEVEL SECURITY;

-- Create RLS policies
CREATE POLICY "Users can view their own Square credentials"
    ON square_credentials FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own Square credentials"
    ON square_credentials FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own Square credentials"
    ON square_credentials FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "Users can delete their own Square credentials"
    ON square_credentials FOR DELETE
    USING (auth.uid() = user_id);

-- Create function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_square_credentials_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to automatically update updated_at
CREATE TRIGGER update_square_credentials_updated_at_trigger
    BEFORE UPDATE ON square_credentials
    FOR EACH ROW
    EXECUTE FUNCTION update_square_credentials_updated_at();
```

## Step 7: Test the Integration

### Testing in Sandbox Mode

1. Make sure you're using **Sandbox** credentials in your `.env` files
2. Start your backend server:
   ```bash
   cd luron_v1
   python main.py
   ```
3. Start your frontend:
   ```bash
   cd luron-core-ai
   npm run dev
   ```
4. Navigate to the Integrations page in your app
5. Click **"Connect"** on the Square card
6. You'll be redirected to Square's authorization page
7. Log in with your Square sandbox account
8. Grant the requested permissions
9. You'll be redirected back to your app with a success message

### Verifying the Connection

Check your backend logs for:
- ✅ Successfully exchanged authorization code for tokens
- ✅ Square verification successful
- ✅ Stored Square credentials for user

## Step 8: Configure Booking Settings in Square

For the Bookings API to work properly, you need to configure your Square account:

1. Log in to your **Square Dashboard**: https://squareup.com/dashboard
2. Navigate to **"Appointments"** in the left sidebar
3. Complete the **initial setup** if you haven't already:
   - Set your business hours
   - Add services (these will be available for booking)
   - Add team members who can provide services
   - Configure booking settings

## API Endpoints Available

Once configured, your voice agents can use these functions:

### 1. Check Availability
```python
get_square_availability(user_id, days_ahead=7, location_id=None)
```
Returns available booking slots for the next N days.

### 2. Create Booking
```python
create_square_booking(user_id, booking_time, customer_note=None, location_id=None)
```
Creates a new booking at the specified time.

### 3. Reschedule Booking
```python
reschedule_square_booking(user_id, booking_id, new_time)
```
Reschedules an existing booking to a new time.

### 4. Cancel Booking
```python
cancel_square_booking(user_id, booking_id, reason=None)
```
Cancels an existing booking.

## Troubleshooting

### "Invalid client credentials" error
- Double-check that your `SQUARE_OAUTH_CLIENT_ID` and `SQUARE_OAUTH_CLIENT_SECRET` are correct
- Make sure you're using the correct credentials for your environment (Sandbox vs Production)

### "Redirect URI mismatch" error
- Ensure the redirect URI in your Square app matches exactly what's in your frontend `.env` file
- Include the full path: `http://localhost:5173/auth/square/callback`

### "No locations found" error
- Make sure you have at least one location set up in your Square account
- Go to Square Dashboard → Account & Settings → Business → Locations

### "No available slots" error
- Ensure you've configured your Appointments settings in Square Dashboard
- Check that you have services and team members set up
- Verify your business hours are configured

### Token expiration issues
- Square access tokens expire after 30 days
- The system automatically refreshes tokens using the refresh token
- If refresh fails, users need to re-authenticate

## Production Deployment

When deploying to production:

1. **Update environment variables** to use production Square credentials
2. **Update redirect URI** in both:
   - Square Developer Portal OAuth settings
   - Frontend `.env` file (`VITE_SQUARE_REDIRECT_URI`)
3. **Test the OAuth flow** thoroughly in production
4. **Monitor token refresh** to ensure credentials stay valid
5. **Set up error logging** to catch any API issues

## Security Best Practices

1. **Never commit** `.env` files to version control
2. **Store credentials securely** in environment variables only
3. **Use HTTPS** in production for all OAuth redirects
4. **Implement proper RLS policies** in Supabase for credential access
5. **Rotate credentials** periodically in the Square Developer Portal
6. **Monitor API usage** to detect any unusual activity

## Resources

- Square Developer Documentation: https://developer.squareup.com/docs
- Square Bookings API Reference: https://developer.squareup.com/reference/square/bookings-api
- Square OAuth Guide: https://developer.squareup.com/docs/oauth-api/overview
- Square Developer Dashboard: https://developer.squareup.com/apps

## Support

If you encounter issues:
1. Check the Square Developer Portal for API status
2. Review backend logs for detailed error messages
3. Verify all environment variables are set correctly
4. Ensure Supabase tables and policies are properly configured