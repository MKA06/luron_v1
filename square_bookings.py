from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Literal, Dict, Tuple, Union
from dateutil.parser import isoparse
from dateutil import parser

from pydantic import BaseModel, Field, field_validator
import pytz
import traceback
import requests


class SquareOAuthPayload(BaseModel):
    """Payload sent from the frontend containing user's Square OAuth authorization code or tokens.

    Notes:
    - Can contain either authorization_code (for initial auth) or access_token (for existing auth)
    - If authorization_code is provided, backend will exchange it for tokens
    - Square tokens expire after 30 days and need to be refreshed
    """

    user_id: str
    # Either authorization code or access token
    authorization_code: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    redirect_uri: Optional[str] = None  # Required for OAuth code exchange
    token_uri: str = "https://connect.squareup.com/oauth2/token"
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    scopes: Optional[List[str]] = None
    expires_at: Optional[Union[datetime, str]] = None  # Square uses expires_at (ISO string)
    merchant_id: Optional[str] = None  # Square merchant/seller ID
    # Optional context fields
    agent_id: Optional[str] = None
    # Additional fields from frontend
    now: Optional[str] = None  # RFC3339 timestamp
    timezone: Optional[str] = None  # IANA timezone name

    @field_validator('expires_at', mode='before')
    @classmethod
    def parse_expires_at(cls, v):
        """Parse expires_at from RFC3339 string to datetime if needed."""
        if v is None:
            return None
        if isinstance(v, datetime):
            # Already a datetime, ensure it's timezone-aware
            if v.tzinfo is None:
                return v.replace(tzinfo=timezone.utc)
            return v
        if isinstance(v, str):
            try:
                # Parse RFC3339 string with explicit offset (e.g., +00:00)
                parsed = isoparse(v)
                # Ensure timezone-aware
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
            except Exception as e:
                print(f"Error parsing expires_at datetime: {v} - {e}")
                return None
        return v


def exchange_authorization_code(code: str, client_id: str, client_secret: str,
                                token_uri: str = "https://connect.squareup.com/oauth2/token",
                                redirect_uri: Optional[str] = None) -> Dict:
    """Exchange authorization code for access and refresh tokens."""

    # Use sandbox token URI for sandbox credentials
    if client_id.startswith('sandbox-') and token_uri == "https://connect.squareup.com/oauth2/token":
        token_uri = "https://connect.squareupsandbox.com/oauth2/token"

    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'code': code,
        'grant_type': 'authorization_code'
    }

    # Add redirect_uri if provided (required by Square OAuth)
    if redirect_uri:
        data['redirect_uri'] = redirect_uri

    response = requests.post(token_uri, json=data)
    response.raise_for_status()
    return response.json()


def refresh_access_token(refresh_token: str, client_id: str, client_secret: str,
                         token_uri: str = "https://connect.squareup.com/oauth2/token") -> Dict:
    """Refresh an access token using a refresh token."""

    # Use sandbox token URI for sandbox credentials
    if client_id.startswith('sandbox-') and token_uri == "https://connect.squareup.com/oauth2/token":
        token_uri = "https://connect.squareupsandbox.com/oauth2/token"

    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token'
    }

    response = requests.post(token_uri, json=data)
    response.raise_for_status()
    return response.json()


def get_square_api_base_url(access_token: str) -> str:
    """Get the base URL for Square API (sandbox or production)."""
    # Sandbox access tokens typically start with 'EAAA'
    # This is a heuristic - you can also pass a flag if needed
    # For now, we'll use an environment variable or default to production
    import os
    if os.getenv('SQUARE_ENVIRONMENT') == 'sandbox':
        return 'https://connect.squareupsandbox.com'
    return 'https://connect.squareup.com'


def get_square_headers(access_token: str) -> Dict[str, str]:
    """Get headers for Square API requests."""
    return {
        'Square-Version': '2024-12-18',  # Use latest stable API version
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }


def search_availability(access_token: str,
                       location_id: str,
                       start_at_min: datetime,
                       start_at_max: datetime,
                       segment_filters: Optional[Dict] = None) -> Dict:
    """Search for available booking slots.

    Args:
        access_token: Square OAuth access token
        location_id: Square location ID
        start_at_min: Earliest time to search from
        start_at_max: Latest time to search until
        segment_filters: Optional filters for service variations, team members, etc.

    Returns:
        Dictionary containing available slots
    """
    base_url = get_square_api_base_url(access_token)
    url = f"{base_url}/v2/bookings/availability/search"

    # Ensure timezone-aware datetimes
    if start_at_min.tzinfo is None:
        start_at_min = start_at_min.replace(tzinfo=timezone.utc)
    if start_at_max.tzinfo is None:
        start_at_max = start_at_max.replace(tzinfo=timezone.utc)

    body = {
        "query": {
            "filter": {
                "location_id": location_id,
                "start_at_range": {
                    "start_at": start_at_min.isoformat(),
                    "end_at": start_at_max.isoformat()
                }
            }
        }
    }

    # Add segment filters if provided
    if segment_filters:
        body["query"]["filter"]["segment_filters"] = [segment_filters]

    headers = get_square_headers(access_token)

    try:
        response = requests.post(url, json=body, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error searching availability: {e}")
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            print(f"Response body: {e.response.text}")
        print(f"Request body: {body}")
        raise


def create_booking(access_token: str,
                  location_id: str,
                  start_at: datetime,
                  customer_id: Optional[str] = None,
                  customer_note: Optional[str] = None,
                  seller_note: Optional[str] = None,
                  appointment_segments: Optional[List[Dict]] = None,
                  customer_info: Optional[Dict[str, str]] = None) -> Dict:
    """Create a new booking. If no customer_id is provided, creates a new customer first.

    Args:
        access_token: Square OAuth access token
        location_id: Square location ID
        start_at: Booking start time
        customer_id: Optional Square customer ID (if not provided, creates new customer)
        customer_note: Optional note from customer
        seller_note: Optional note for seller
        appointment_segments: List of appointment segments (services, team members)
        customer_info: Optional dict with customer details (given_name, family_name, email_address, phone_number)
                      Used to create a new customer if customer_id is not provided

    Returns:
        Dictionary containing created booking details
    """
    base_url = get_square_api_base_url(access_token)
    url = f"{base_url}/v2/bookings"

    # Ensure timezone-aware datetime
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)

    # If no customer_id is provided, create a new customer
    if not customer_id:
        print("No customer_id provided, creating new customer...")
        try:
            customer_data = customer_info or {}
            customer_response = create_customer(
                access_token=access_token,
                given_name=customer_data.get('given_name', 'Guest'),
                family_name=customer_data.get('family_name', 'Customer'),
                email_address=customer_data.get('email_address'),
                phone_number=customer_data.get('phone_number'),
                note=customer_data.get('note', 'Auto-created for booking')
            )
            customer_id = customer_response.get('customer', {}).get('id')
            print(f"Created new customer with ID: {customer_id}")
        except Exception as e:
            print(f"Failed to create customer: {e}")
            raise Exception(f"Cannot create booking without customer. Customer creation failed: {e}")

    booking = {
        "location_id": location_id,
        "start_at": start_at.isoformat(),
        "customer_id": customer_id
    }

    if customer_note:
        booking["customer_note"] = customer_note
    if seller_note:
        booking["seller_note"] = seller_note
    if appointment_segments:
        booking["appointment_segments"] = appointment_segments

    body = {"booking": booking}

    headers = get_square_headers(access_token)

    try:
        response = requests.post(url, json=body, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error creating booking: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response: {e.response.text}")
        raise


def update_booking(access_token: str,
                  booking_id: str,
                  version: int,
                  start_at: Optional[datetime] = None,
                  location_id: Optional[str] = None,
                  customer_note: Optional[str] = None,
                  seller_note: Optional[str] = None) -> Dict:
    """Update an existing booking (e.g., reschedule).

    Args:
        access_token: Square OAuth access token
        booking_id: ID of the booking to update
        version: Current version of the booking (for optimistic concurrency)
        start_at: New start time (for rescheduling)
        location_id: New location ID
        customer_note: Updated customer note
        seller_note: Updated seller note

    Returns:
        Dictionary containing updated booking details
    """
    base_url = get_square_api_base_url(access_token)
    url = f"{base_url}/v2/bookings/{booking_id}"

    booking = {
        "version": version
    }

    if start_at:
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=timezone.utc)
        booking["start_at"] = start_at.isoformat()

    if location_id:
        booking["location_id"] = location_id
    if customer_note:
        booking["customer_note"] = customer_note
    if seller_note:
        booking["seller_note"] = seller_note

    body = {"booking": booking}

    headers = get_square_headers(access_token)

    try:
        response = requests.put(url, json=body, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error updating booking: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response: {e.response.text}")
        raise


def cancel_booking(access_token: str,
                  booking_id: str,
                  version: int,
                  cancellation_reason: Optional[str] = None) -> Dict:
    """Cancel an existing booking.

    Args:
        access_token: Square OAuth access token
        booking_id: ID of the booking to cancel
        version: Current version of the booking (for optimistic concurrency)
        cancellation_reason: Optional reason for cancellation

    Returns:
        Dictionary containing cancelled booking details
    """
    base_url = get_square_api_base_url(access_token)
    url = f"{base_url}/v2/bookings/{booking_id}/cancel"

    body = {
        "booking_version": version
    }

    if cancellation_reason:
        body["cancellation_reason"] = cancellation_reason

    headers = get_square_headers(access_token)

    try:
        response = requests.post(url, json=body, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error cancelling booking: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response: {e.response.text}")
        raise


def retrieve_booking(access_token: str, booking_id: str) -> Dict:
    """Retrieve a specific booking by ID.

    Args:
        access_token: Square OAuth access token
        booking_id: ID of the booking to retrieve

    Returns:
        Dictionary containing booking details
    """
    base_url = get_square_api_base_url(access_token)
    url = f"{base_url}/v2/bookings/{booking_id}"
    headers = get_square_headers(access_token)

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error retrieving booking: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response: {e.response.text}")
        raise


def list_bookings(access_token: str,
                 location_id: Optional[str] = None,
                 limit: int = 10,
                 cursor: Optional[str] = None) -> Dict:
    """List bookings with optional filtering.

    Args:
        access_token: Square OAuth access token
        location_id: Optional location ID filter
        limit: Maximum number of results
        cursor: Pagination cursor

    Returns:
        Dictionary containing bookings list
    """
    base_url = get_square_api_base_url(access_token)
    url = f"{base_url}/v2/bookings"
    headers = get_square_headers(access_token)

    params = {"limit": limit}
    if location_id:
        params["location_id"] = location_id
    if cursor:
        params["cursor"] = cursor

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error listing bookings: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response: {e.response.text}")
        raise


def get_merchant_info(access_token: str) -> Dict:
    """Get merchant information to retrieve merchant_id and locations.

    Args:
        access_token: Square OAuth access token

    Returns:
        Dictionary containing merchant details
    """
    base_url = get_square_api_base_url(access_token)
    url = f"{base_url}/v2/merchants"
    headers = get_square_headers(access_token)

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data
    except Exception as e:
        print(f"Error getting merchant info: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response: {e.response.text}")
        raise


def list_locations(access_token: str) -> Dict:
    """List all locations for the merchant.

    Args:
        access_token: Square OAuth access token

    Returns:
        Dictionary containing locations list
    """
    base_url = get_square_api_base_url(access_token)
    url = f"{base_url}/v2/locations"
    headers = get_square_headers(access_token)

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error listing locations: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response: {e.response.text}")
        raise


def create_customer(access_token: str,
                   given_name: Optional[str] = None,
                   family_name: Optional[str] = None,
                   email_address: Optional[str] = None,
                   phone_number: Optional[str] = None,
                   note: Optional[str] = None) -> Dict:
    """Create a new customer in Square.

    Args:
        access_token: Square OAuth access token
        given_name: Customer's first name
        family_name: Customer's last name
        email_address: Customer's email address
        phone_number: Customer's phone number
        note: Optional note about the customer

    Returns:
        Dictionary containing created customer details including customer_id
    """
    base_url = get_square_api_base_url(access_token)
    url = f"{base_url}/v2/customers"
    headers = get_square_headers(access_token)

    # Build customer object with provided fields
    # Note: Square requires at least one field to be provided
    customer = {}
    if given_name:
        customer["given_name"] = given_name
    if family_name:
        customer["family_name"] = family_name
    if email_address:
        customer["email_address"] = email_address
    if phone_number:
        customer["phone_number"] = phone_number
    if note:
        customer["note"] = note

    # If no fields provided, at least set given_name
    if not customer:
        customer["given_name"] = "Guest"

    body = customer  # Square expects the customer object directly, not wrapped

    try:
        response = requests.post(url, json=body, headers=headers)
        response.raise_for_status()
        result = response.json()
        customer_data = result.get('customer', {})
        print(f"✅ Created customer: {customer_data.get('id')}")
        return result
    except Exception as e:
        print(f"Error creating customer: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response: {e.response.text}")
            print(f"Request body: {body}")
        raise


def list_catalog_services(access_token: str) -> List[Dict]:
    """List all catalog services (bookable items) for the merchant.

    Args:
        access_token: Square OAuth access token

    Returns:
        List of service variations with their details
    """
    base_url = get_square_api_base_url(access_token)
    url = f"{base_url}/v2/catalog/list"
    headers = get_square_headers(access_token)
    params = {"types": "ITEM"}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        catalog_data = response.json()

        services = []
        catalog_objects = catalog_data.get('objects', [])

        for obj in catalog_objects:
            if obj.get('type') == 'ITEM':
                item_data = obj.get('item_data', {})
                item_name = item_data.get('name', 'Unnamed')

                # Get variations (service types within each item)
                variations = item_data.get('variations', [])

                for var in variations:
                    var_id = var.get('id')
                    var_data = var.get('item_variation_data', {})
                    var_name = var_data.get('name', 'Regular')
                    service_duration = var_data.get('service_duration')

                    if var_id:
                        services.append({
                            'id': var_id,
                            'item_name': item_name,
                            'variation_name': var_name,
                            'duration_ms': service_duration,
                            'full_name': f"{item_name} - {var_name}" if var_name != 'Regular' else item_name
                        })

        return services
    except Exception as e:
        print(f"Error listing catalog services: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response: {e.response.text}")
        raise


def get_first_available_service(access_token: str) -> Optional[str]:
    """Get the first available service variation ID.

    Args:
        access_token: Square OAuth access token

    Returns:
        Service variation ID or None if no services found
    """
    try:
        services = list_catalog_services(access_token)
        if services:
            first_service = services[0]
            print(f"Auto-selected service: {first_service['full_name']} (ID: {first_service['id']})")
            return first_service['id']
        return None
    except Exception as e:
        print(f"Error getting first available service: {e}")
        return None


async def get_square_availability(supabase, user_id: str, days_ahead: int = 7, location_id: Optional[str] = None):
    """Get Square booking availability for agents to use.

    Args:
        supabase: Supabase client instance
        user_id: The user ID to fetch availability for
        days_ahead: Number of days to check ahead (default 7)
        location_id: Optional specific location ID (uses first location if not provided)

    Returns:
        A formatted string with availability information
    """
    print("CALLED THE GET_SQUARE_AVAILABILITY FUNCTION")

    if not user_id:
        return "Error: No user_id provided for availability check"

    try:
        # Fetch credentials from Supabase
        result = supabase.table('square_credentials').select('*').eq('user_id', user_id).single().execute()

        if not result.data:
            return f"No Square credentials found for user {user_id}. User needs to authenticate first."

        creds_data = result.data

        # Check if token needs refresh
        needs_refresh = False
        if creds_data.get('expires_at'):
            expires_at = datetime.fromisoformat(creds_data['expires_at'].replace('Z', '+00:00'))
            if expires_at <= datetime.now(timezone.utc) + timedelta(minutes=5):
                needs_refresh = True
                print(f"Token expired or expiring soon, refreshing...")

        # Refresh token if needed
        if needs_refresh and creds_data.get('refresh_token'):
            try:
                token_response = refresh_access_token(
                    refresh_token=creds_data['refresh_token'],
                    client_id=creds_data['client_id'],
                    client_secret=creds_data['client_secret'],
                    token_uri=creds_data.get('token_uri', 'https://connect.squareup.com/oauth2/token')
                )

                # Update credentials in database
                new_expires_at = token_response.get('expires_at')

                update_data = {
                    'access_token': token_response['access_token'],
                    'expires_at': new_expires_at,
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }

                if 'refresh_token' in token_response:
                    update_data['refresh_token'] = token_response['refresh_token']

                supabase.table('square_credentials').update(update_data).eq('user_id', user_id).execute()
                creds_data['access_token'] = token_response['access_token']
                creds_data['expires_at'] = new_expires_at
                print("Successfully refreshed access token")
            except Exception as e:
                print(f"Error refreshing token: {e}")
                return f"Error: Token expired and could not be refreshed. User needs to re-authenticate."

        access_token = creds_data['access_token']

        # Get location if not provided
        if not location_id:
            locations_response = list_locations(access_token)
            locations = locations_response.get('locations', [])
            if not locations:
                return "Error: No Square locations found for this merchant"
            location_id = locations[0]['id']
            print(f"Using location: {locations[0].get('name', location_id)}")

        # Get first available service for availability search
        service_id = get_first_available_service(access_token)
        if not service_id:
            return "Error: No bookable services found. Please create services in your Square Dashboard under Appointments > Services"

        # Build segment filter for the service
        segment_filter = {
            "service_variation_id": service_id
        }

        # Search availability
        start_at_min = datetime.now(timezone.utc)
        start_at_max = start_at_min + timedelta(days=days_ahead)

        availability_response = search_availability(
            access_token=access_token,
            location_id=location_id,
            start_at_min=start_at_min,
            start_at_max=start_at_max,
            segment_filters=segment_filter
        )

        availabilities = availability_response.get('availabilities', [])

        # Format response
        response = f"📅 Square Booking Availability for next {days_ahead} days:\n\n"

        if availabilities:
            response += f"Found {len(availabilities)} available time slots:\n\n"

            # Group by date
            user_timezone = 'America/Los_Angeles'  # Default, can be customized
            user_tz = pytz.timezone(user_timezone)

            current_day = None
            for slot in availabilities[:20]:  # Limit to first 20
                start_at = datetime.fromisoformat(slot['start_at'].replace('Z', '+00:00'))
                start_local = start_at.astimezone(user_tz)

                day_str = start_local.strftime('%A, %B %d')
                if day_str != current_day:
                    current_day = day_str
                    response += f"\n{day_str}:\n"

                response += f"  - {start_local.strftime('%I:%M %p')}\n"
        else:
            response += "No available slots found in the specified time range.\n"

        # Update last_used_at
        supabase.table('square_credentials').update({
            'last_used_at': datetime.now(timezone.utc).isoformat()
        }).eq('user_id', user_id).execute()

        return response

    except Exception as e:
        error_msg = f"Error fetching Square availability: {str(e)}"
        print(f"Exception: {error_msg}")
        print(f"Traceback: {traceback.format_exc()}")
        return error_msg


async def create_square_booking(supabase, user_id: str, booking_time: str,
                               customer_note: Optional[str] = None,
                               location_id: Optional[str] = None):
    """Create a Square booking for agents to use.

    Args:
        supabase: Supabase client instance
        user_id: The user ID whose Square account to use
        booking_time: ISO format datetime string or natural language time
        customer_note: Optional note for the booking
        location_id: Optional specific location ID

    Returns:
        A formatted string with booking creation status
    """
    print("CALLED THE CREATE_SQUARE_BOOKING FUNCTION")

    if not user_id:
        return "Error: No user_id provided"

    if not booking_time:
        return "Error: No booking time provided"

    try:
        # Fetch credentials from Supabase
        result = supabase.table('square_credentials').select('*').eq('user_id', user_id).single().execute()

        if not result.data:
            return f"No Square credentials found for user {user_id}. User needs to authenticate first."

        creds_data = result.data
        access_token = creds_data['access_token']

        # Get location if not provided
        if not location_id:
            locations_response = list_locations(access_token)
            locations = locations_response.get('locations', [])
            if not locations:
                return "Error: No Square locations found"
            location_id = locations[0]['id']

        # Get first available service
        service_id = get_first_available_service(access_token)
        if not service_id:
            return "Error: No bookable services found. Please create services in your Square Dashboard under Appointments > Services"

        # Parse booking time
        user_timezone = 'America/Los_Angeles'
        user_tz = pytz.timezone(user_timezone)
        now_local = datetime.now(user_tz)

        parsed_time = parser.parse(booking_time, default=now_local)
        if parsed_time.tzinfo is None:
            parsed_time = user_tz.localize(parsed_time)

        start_at = parsed_time.astimezone(pytz.UTC)

        # Build appointment segment for the service
        appointment_segments = [{
            "service_variation_id": service_id,
            "service_variation_version": 1  # Square requires version number
        }]

        # Create customer info for auto-creation (will be used if no customer_id)
        customer_info = {
            'given_name': 'Guest',
            'family_name': 'Customer',
            'note': 'Auto-created by Luron AI booking system'
        }

        # Create booking (will auto-create customer if needed)
        booking_response = create_booking(
            access_token=access_token,
            location_id=location_id,
            start_at=start_at,
            customer_note=customer_note,
            appointment_segments=appointment_segments,
            customer_info=customer_info
        )

        booking = booking_response.get('booking', {})

        # Update last_used_at
        supabase.table('square_credentials').update({
            'last_used_at': datetime.now(timezone.utc).isoformat()
        }).eq('user_id', user_id).execute()

        response = f"✅ Square booking created successfully!\n\n"
        response += f"🆔 Booking ID: {booking.get('id', 'N/A')}\n"
        response += f"🕐 Time: {parsed_time.strftime('%A, %B %d at %I:%M %p')}\n"
        if customer_note:
            response += f"📝 Note: {customer_note}\n"

        return response

    except Exception as e:
        error_msg = f"Error creating Square booking: {str(e)}"
        print(f"Exception: {error_msg}")
        print(f"Traceback: {traceback.format_exc()}")
        return error_msg


async def reschedule_square_booking(supabase, user_id: str, booking_id: str, new_time: str):
    """Reschedule an existing Square booking.

    Args:
        supabase: Supabase client instance
        user_id: The user ID
        booking_id: ID of the booking to reschedule
        new_time: New datetime for the booking

    Returns:
        A formatted string with reschedule status
    """
    print("CALLED THE RESCHEDULE_SQUARE_BOOKING FUNCTION")

    if not user_id:
        return "Error: No user_id provided"

    if not booking_id:
        return "Error: No booking_id provided"

    if not new_time:
        return "Error: No new_time provided"

    try:
        # Fetch credentials
        result = supabase.table('square_credentials').select('*').eq('user_id', user_id).single().execute()

        if not result.data:
            return f"No Square credentials found for user {user_id}."

        access_token = result.data['access_token']

        # Get current booking to get version
        booking_response = retrieve_booking(access_token, booking_id)
        booking = booking_response.get('booking', {})
        version = booking.get('version')

        if not version:
            return "Error: Could not retrieve booking version"

        # Parse new time
        user_timezone = 'America/Los_Angeles'
        user_tz = pytz.timezone(user_timezone)
        now_local = datetime.now(user_tz)

        parsed_time = parser.parse(new_time, default=now_local)
        if parsed_time.tzinfo is None:
            parsed_time = user_tz.localize(parsed_time)

        start_at = parsed_time.astimezone(pytz.UTC)

        # Update booking
        update_response = update_booking(
            access_token=access_token,
            booking_id=booking_id,
            version=version,
            start_at=start_at
        )

        updated_booking = update_response.get('booking', {})

        # Update last_used_at
        supabase.table('square_credentials').update({
            'last_used_at': datetime.now(timezone.utc).isoformat()
        }).eq('user_id', user_id).execute()

        response = f"✅ Square booking rescheduled successfully!\n\n"
        response += f"🆔 Booking ID: {booking_id}\n"
        response += f"🕐 New time: {parsed_time.strftime('%A, %B %d at %I:%M %p')}\n"

        return response

    except Exception as e:
        error_msg = f"Error rescheduling Square booking: {str(e)}"
        print(f"Exception: {error_msg}")
        print(f"Traceback: {traceback.format_exc()}")
        return error_msg


async def cancel_square_booking(supabase, user_id: str, booking_id: str, reason: Optional[str] = None):
    """Cancel an existing Square booking.

    Args:
        supabase: Supabase client instance
        user_id: The user ID
        booking_id: ID of the booking to cancel
        reason: Optional cancellation reason

    Returns:
        A formatted string with cancellation status
    """
    print("CALLED THE CANCEL_SQUARE_BOOKING FUNCTION")

    if not user_id:
        return "Error: No user_id provided"

    if not booking_id:
        return "Error: No booking_id provided"

    try:
        # Fetch credentials
        result = supabase.table('square_credentials').select('*').eq('user_id', user_id).single().execute()

        if not result.data:
            return f"No Square credentials found for user {user_id}."

        access_token = result.data['access_token']

        # Get current booking to get version
        booking_response = retrieve_booking(access_token, booking_id)
        booking = booking_response.get('booking', {})
        version = booking.get('version')

        if not version:
            return "Error: Could not retrieve booking version"

        # Cancel booking
        cancel_response = cancel_booking(
            access_token=access_token,
            booking_id=booking_id,
            version=version,
            cancellation_reason=reason
        )

        # Update last_used_at
        supabase.table('square_credentials').update({
            'last_used_at': datetime.now(timezone.utc).isoformat()
        }).eq('user_id', user_id).execute()

        response = f"✅ Square booking cancelled successfully!\n\n"
        response += f"🆔 Booking ID: {booking_id}\n"
        if reason:
            response += f"📝 Reason: {reason}\n"

        return response

    except Exception as e:
        error_msg = f"Error cancelling Square booking: {str(e)}"
        print(f"Exception: {error_msg}")
        print(f"Traceback: {traceback.format_exc()}")
        return error_msg