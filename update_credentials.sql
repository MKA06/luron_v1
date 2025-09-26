-- Update the stored credentials with client_id and client_secret
-- Replace these values with your actual OAuth client credentials

UPDATE google_credentials
SET
    client_id = 'YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com',
    client_secret = 'YOUR_GOOGLE_CLIENT_SECRET',
    token_uri = 'https://oauth2.googleapis.com/token'
WHERE user_id = '827c7c35-4b1e-41c4-85a2-bacbd48d70b1';

-- Note: refresh_token can only be obtained through authorization code flow
-- The current frontend uses implicit flow which doesn't provide refresh_token