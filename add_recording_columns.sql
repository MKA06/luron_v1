-- Migration: Add recording columns to existing calls table
-- Run this in Supabase SQL Editor

ALTER TABLE calls
ADD COLUMN recording_url TEXT;

ALTER TABLE calls
ADD COLUMN recording_duration FLOAT;

-- Add index for faster queries on recording URLs
CREATE INDEX idx_calls_recording_url ON calls(recording_url) WHERE recording_url IS NOT NULL;

-- Optional: Add comment for documentation
COMMENT ON COLUMN calls.recording_url IS 'URL to the stereo WAV recording in Supabase storage (left: user, right: assistant)';
COMMENT ON COLUMN calls.recording_duration IS 'Duration of the call recording in seconds';