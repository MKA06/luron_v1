-- Create table for storing Google OAuth credentials
-- This table stores encrypted Google credentials for each user

CREATE TABLE IF NOT EXISTS google_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT UNIQUE NOT NULL, -- Links to your existing users/agents

    -- OAuth tokens (should be encrypted in production)
    access_token TEXT NOT NULL,
    refresh_token TEXT,

    -- OAuth client info for token refresh
    token_uri TEXT DEFAULT 'https://oauth2.googleapis.com/token',
    client_id TEXT,
    client_secret TEXT,

    -- Scopes and expiry
    scopes JSONB, -- Array of scope strings
    expiry TIMESTAMPTZ,

    -- Service type for multi-service support
    service TEXT DEFAULT 'calendar', -- 'calendar', 'gmail', or both

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,

    -- Add index for faster lookups
    CONSTRAINT unique_user_id UNIQUE(user_id)
);

-- Create index for faster queries
CREATE INDEX idx_google_credentials_user_id ON google_credentials(user_id);
CREATE INDEX idx_google_credentials_updated_at ON google_credentials(updated_at DESC);

-- Enable Row Level Security
ALTER TABLE google_credentials ENABLE ROW LEVEL SECURITY;

-- Create RLS policies (adjust based on your auth setup)
-- This example assumes service role access only (backend-only access)
CREATE POLICY "Service role can manage all credentials" ON google_credentials
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- Function to automatically update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger for auto-updating updated_at
CREATE TRIGGER update_google_credentials_updated_at
    BEFORE UPDATE ON google_credentials
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Optional: Add a function to clean up expired tokens periodically
CREATE OR REPLACE FUNCTION cleanup_expired_google_tokens()
RETURNS void AS $$
BEGIN
    DELETE FROM google_credentials
    WHERE expiry < NOW() - INTERVAL '30 days'
    AND refresh_token IS NULL;
END;
$$ LANGUAGE plpgsql;

-- Comments for documentation
COMMENT ON TABLE google_credentials IS 'Stores Google OAuth credentials for users to access Google Calendar and Gmail';
COMMENT ON COLUMN google_credentials.user_id IS 'Unique identifier linking to the user/agent who owns these credentials';
COMMENT ON COLUMN google_credentials.access_token IS 'Google OAuth access token - should be encrypted in production';
COMMENT ON COLUMN google_credentials.refresh_token IS 'Google OAuth refresh token for obtaining new access tokens';
COMMENT ON COLUMN google_credentials.scopes IS 'JSON array of OAuth scopes granted by the user';
COMMENT ON COLUMN google_credentials.service IS 'Which Google service(s) these credentials are for: calendar, gmail, or both';