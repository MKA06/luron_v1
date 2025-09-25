from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Literal

from pydantic import BaseModel
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


class GoogleOAuthPayload(BaseModel):
    """Payload sent from the frontend containing user's Google OAuth tokens.

    Notes
    - access_token is sufficient for short-lived use. If refresh_token, client_id,
      and client_secret are provided, the backend can refresh when needed.
    - expiry is optional; if provided it will be applied to Credentials.
    """

    user_id: str
    access_token: str
    refresh_token: Optional[str] = None
    token_uri: str = "https://oauth2.googleapis.com/token"
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    scopes: Optional[List[str]] = None
    expiry: Optional[datetime] = None
    # Optional context fields
    agent_id: Optional[str] = None
    # Explicit service hint to gate verification (calendar or gmail)
    service: Optional[Literal['calendar', 'gmail']] = None


def build_credentials(payload: GoogleOAuthPayload) -> Credentials:
    """Construct google.oauth2.credentials.Credentials from payload."""
    kwargs = {
        "token": payload.access_token,
        "refresh_token": payload.refresh_token,
        "token_uri": payload.token_uri,
        "client_id": payload.client_id,
        "client_secret": payload.client_secret,
        "scopes": payload.scopes,
    }
    # Remove None values to avoid type issues in the underlying library
    filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
    creds = Credentials(**filtered_kwargs)
    if payload.expiry is not None:
        creds.expiry = payload.expiry
    return creds


def get_calendar_service(credentials: Credentials):
    """Create a Google Calendar API client (v3)."""
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def get_gmail_service(credentials: Credentials):
    """Create a Gmail API client (v1)."""
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)

