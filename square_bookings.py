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


async def get_square_availability(supabase, user_id: str, days_ahead: int = 7, location: Optional[str] = None, specific_day: Optional[str] = None):
    """Get Square booking availability for agents to use.

    Args:
        supabase: Supabase client instance
        user_id: The user ID to fetch availability for
        days_ahead: Number of days to check ahead (default 7)
        location: Optional location name (e.g., 'harvard', 'brookline') - if not provided, lists all locations
        specific_day: Optional specific day to check (e.g., 'today', 'tomorrow', '2025-10-10', 'Monday')

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

        # Get all locations
        locations_response = list_locations(access_token)
        locations = locations_response.get('locations', [])
        if not locations:
            return "Error: No Square locations found for this merchant"

        # If no location specified, return list of locations
        if not location:
            response = "📍 Available Locations:\n\n"
            for i, loc in enumerate(locations, 1):
                loc_name = loc.get('name', 'Unnamed')
                loc_address = loc.get('address', {})
                address_line = loc_address.get('address_line_1', '')
                city = loc_address.get('locality', '')
                response += f"{i}. {loc_name}\n"
                if address_line or city:
                    response += f"   Address: {address_line}, {city}\n"
                response += "\n"
            response += "Please specify which location you'd like to check availability for (e.g., 'harvard' or 'brookline')."
            return response

        # Find location by matching name (case-insensitive partial match)
        location_id = None
        location_name = None
        location_lower = location.lower()
        for loc in locations:
            loc_name = loc.get('name', '')
            if location_lower in loc_name.lower():
                location_id = loc['id']
                location_name = loc_name
                break

        if not location_id:
            return f"Error: Location '{location}' not found. Available locations: {', '.join([loc.get('name', 'Unnamed') for loc in locations])}"

        print(f"Using location: {location_name} (ID: {location_id})")

        # Get all services for this location (by testing each service)
        all_services = list_catalog_services(access_token)
        location_services = []

        # Test each service at this location to see which ones work
        test_start = datetime.now(timezone.utc)
        test_end = test_start + timedelta(hours=1)

        for service in all_services:
            try:
                segment_filter = {"service_variation_id": service['id']}
                test_response = search_availability(
                    access_token=access_token,
                    location_id=location_id,
                    start_at_min=test_start,
                    start_at_max=test_end,
                    segment_filters=segment_filter
                )
                # If no error, this service works at this location
                location_services.append(service)
            except Exception:
                # Service not available at this location
                continue

        if not location_services:
            return f"Error: No bookable services found for location '{location_name}'. Please create services in your Square Dashboard under Appointments > Services"

        print(f"Found {len(location_services)} service(s) at {location_name}")

        # Use first available service for availability search
        service_id = location_services[0]['id']

        # Build segment filter for the service
        segment_filter = {
            "service_variation_id": service_id
        }

        # Use New York Time as default
        user_timezone = 'America/New_York'
        user_tz = pytz.timezone(user_timezone)

        # Parse specific_day if provided
        start_at_min = datetime.now(timezone.utc)
        if specific_day:
            now_local = datetime.now(user_tz)
            specific_day_lower = specific_day.lower()

            if specific_day_lower == 'today':
                target_date = now_local.date()
            elif specific_day_lower == 'tomorrow':
                target_date = (now_local + timedelta(days=1)).date()
            else:
                # Try to parse as date or day of week
                try:
                    # Try ISO format date
                    target_date = parser.parse(specific_day).date()
                except:
                    # Try day of week (e.g., 'Monday', 'Tuesday')
                    try:
                        days_of_week = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
                        if specific_day_lower in days_of_week:
                            target_weekday = days_of_week.index(specific_day_lower)
                            current_weekday = now_local.weekday()
                            days_until = (target_weekday - current_weekday) % 7
                            if days_until == 0:
                                days_until = 7  # Next week if today is the same day
                            target_date = (now_local + timedelta(days=days_until)).date()
                        else:
                            return f"Error: Could not parse specific_day '{specific_day}'. Use 'today', 'tomorrow', a date like '2025-10-10', or a day like 'Monday'."
                    except:
                        return f"Error: Could not parse specific_day '{specific_day}'. Use 'today', 'tomorrow', a date like '2025-10-10', or a day like 'Monday'."

            # Set start and end times to cover the target date
            start_at_min = user_tz.localize(datetime.combine(target_date, datetime.min.time())).astimezone(pytz.UTC)
            start_at_max = start_at_min + timedelta(days=1)
        else:
            # No specific day, check days_ahead
            start_at_max = start_at_min + timedelta(days=days_ahead)

        availability_response = search_availability(
            access_token=access_token,
            location_id=location_id,
            start_at_min=start_at_min,
            start_at_max=start_at_max,
            segment_filters=segment_filter
        )

        availabilities = availability_response.get('availabilities', [])

        # CRITICAL: Fetch existing bookings and filter out occupied time slots
        print(f"Fetching existing bookings for location {location_id}...")
        try:
            # Get all bookings (Square API doesn't support location_id filter in list endpoint)
            all_bookings = []
            cursor = None
            max_pages = 10  # Safety limit

            for _ in range(max_pages):
                bookings_response = list_bookings(access_token, limit=100, cursor=cursor)
                bookings = bookings_response.get('bookings', [])
                all_bookings.extend(bookings)

                cursor = bookings_response.get('cursor')
                if not cursor:
                    break

            print(f"Found {len(all_bookings)} total bookings across all locations")

            # Filter bookings to only those in our time range and at this location
            booked_times = set()
            for booking in all_bookings:
                booking_location = booking.get('location_id')
                booking_start = booking.get('start_at')
                booking_status = booking.get('status', '')

                # Only consider active bookings (not cancelled)
                if booking_location == location_id and booking_start and booking_status in ['PENDING', 'ACCEPTED']:
                    try:
                        booking_time = datetime.fromisoformat(booking_start.replace('Z', '+00:00'))
                        # Check if booking is in our time range
                        if start_at_min <= booking_time <= start_at_max:
                            booked_times.add(booking_time.isoformat())
                            print(f"  Found booking at: {booking_time.astimezone(user_tz).strftime('%A, %B %d at %I:%M %p')}")
                    except Exception as e:
                        print(f"  Error parsing booking time: {e}")
                        continue

            print(f"Total booked slots in time range: {len(booked_times)}")

            # Filter out booked time slots from availabilities
            filtered_availabilities = []
            for slot in availabilities:
                slot_time = datetime.fromisoformat(slot['start_at'].replace('Z', '+00:00'))
                if slot_time.isoformat() not in booked_times:
                    filtered_availabilities.append(slot)

            print(f"Available slots after filtering Square bookings: {len(filtered_availabilities)} (was {len(availabilities)})")
            availabilities = filtered_availabilities

        except Exception as e:
            print(f"Warning: Could not fetch Square bookings to filter availability: {e}")
            # Continue with unfiltered availability if booking fetch fails

        # Format response - group by day first
        # Group slots by day
        from collections import defaultdict
        slots_by_day = defaultdict(list)

        for slot in availabilities:
            start_at = datetime.fromisoformat(slot['start_at'].replace('Z', '+00:00'))
            start_local = start_at.astimezone(user_tz)
            day_key = start_local.strftime('%A, %B %d, %Y')  # Full date for clarity
            time_str = start_local.strftime('%I:%M %p')
            slots_by_day[day_key].append(time_str)

        # Build response with clear headers
        if specific_day:
            response = f"📅 Availability for {location_name} on {specific_day}:\n\n"
        else:
            response = f"📅 Availability for {location_name} (next {days_ahead} days):\n\n"

        # List available services at this location
        response += f"🛠️  Available Services at {location_name}:\n"
        for i, svc in enumerate(location_services, 1):
            duration_min = svc.get('duration_ms', 0) // 60000 if svc.get('duration_ms') else 'N/A'
            response += f"  {i}. {svc['full_name']}"
            if duration_min != 'N/A':
                response += f" ({duration_min} min)"
            response += f"\n     Service ID: {svc['id']}\n"
        response += "\n"

        if slots_by_day:
            response += f"📍 Total: {len(availabilities)} available slots\n\n"

            # Sort by date and display
            sorted_days = sorted(slots_by_day.keys(), key=lambda x: datetime.strptime(x, '%A, %B %d, %Y'))

            for day in sorted_days:
                times = slots_by_day[day]
                response += f"{day} ({len(times)} slots):\n"
                for time in times:
                    response += f"  • {time}\n"
                response += "\n"
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


def parse_natural_date(booking_time: str, user_tz: pytz.tzinfo) -> datetime:
    """Parse natural language dates like 'tomorrow at 2pm', 'next Thursday at 3pm', etc.

    Args:
        booking_time: Natural language time string
        user_tz: User's timezone

    Returns:
        Parsed datetime in user's timezone
    """
    import re

    now_local = datetime.now(user_tz)
    booking_time_lower = booking_time.lower().strip()

    # Handle "tomorrow"
    if booking_time_lower.startswith('tomorrow'):
        # Extract time portion (e.g., "at 2pm" or "2pm")
        time_match = re.search(r'(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm))', booking_time_lower)
        if time_match:
            time_str = time_match.group(1)
            # Parse just the time
            time_obj = parser.parse(time_str)
            # Add one day to current date
            tomorrow = now_local + timedelta(days=1)
            parsed_time = tomorrow.replace(hour=time_obj.hour, minute=time_obj.minute, second=0, microsecond=0)
        else:
            # Just "tomorrow" without time
            parsed_time = now_local + timedelta(days=1)

    # Handle "today"
    elif booking_time_lower.startswith('today'):
        time_match = re.search(r'(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm))', booking_time_lower)
        if time_match:
            time_str = time_match.group(1)
            time_obj = parser.parse(time_str)
            parsed_time = now_local.replace(hour=time_obj.hour, minute=time_obj.minute, second=0, microsecond=0)
        else:
            parsed_time = now_local

    # Handle "next [weekday]"
    elif booking_time_lower.startswith('next'):
        # Try to parse the whole thing with dateutil
        try:
            parsed_time = parser.parse(booking_time, default=now_local, fuzzy=True)
            # If the parsed time is in the past, add 7 days
            if parsed_time < now_local:
                parsed_time = parsed_time + timedelta(days=7)
        except:
            parsed_time = parser.parse(booking_time, default=now_local)

    # Try regular parsing for other formats
    else:
        parsed_time = parser.parse(booking_time, default=now_local, fuzzy=True)

    # Ensure timezone-aware
    if parsed_time.tzinfo is None:
        parsed_time = user_tz.localize(parsed_time)

    return parsed_time


async def create_square_booking(supabase, user_id: str, booking_time: str,
                               customer_name: Optional[str] = None,
                               customer_phone: Optional[str] = None,
                               customer_email: Optional[str] = None,
                               customer_note: Optional[str] = None,
                               location: Optional[str] = None,
                               service_id: Optional[str] = None):
    """Create a Square booking for agents to use.

    Args:
        supabase: Supabase client instance
        user_id: The user ID whose Square account to use
        booking_time: ISO format datetime string or natural language time
        customer_name: Optional customer name (e.g., "John Smith")
        customer_phone: Optional customer phone number
        customer_email: Optional customer email address
        customer_note: Optional note for the booking
        location: Optional location name (e.g., 'harvard', 'brookline') - if not provided, uses first location
        service_id: Optional specific service ID to book - if not provided, uses first available service

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

        # Get all locations
        locations_response = list_locations(access_token)
        locations = locations_response.get('locations', [])
        if not locations:
            return "Error: No Square locations found"

        # Find location by name if provided
        location_id = None
        location_name = None
        if location:
            location_lower = location.lower()
            for loc in locations:
                loc_name = loc.get('name', '')
                if location_lower in loc_name.lower():
                    location_id = loc['id']
                    location_name = loc_name
                    break
            if not location_id:
                return f"Error: Location '{location}' not found. Available locations: {', '.join([loc.get('name', 'Unnamed') for loc in locations])}"
        else:
            # Use first location
            location_id = locations[0]['id']
            location_name = locations[0].get('name', location_id)

        print(f"Using location: {location_name} (ID: {location_id})")

        # Get service - if not provided, get first available service for this location
        if not service_id:
            # Get all services and test at this location
            all_services = list_catalog_services(access_token)
            location_services = []

            test_start = datetime.now(timezone.utc)
            test_end = test_start + timedelta(hours=1)

            for service in all_services:
                try:
                    segment_filter = {"service_variation_id": service['id']}
                    test_response = search_availability(
                        access_token=access_token,
                        location_id=location_id,
                        start_at_min=test_start,
                        start_at_max=test_end,
                        segment_filters=segment_filter
                    )
                    location_services.append(service)
                except Exception:
                    continue

            if not location_services:
                return f"Error: No bookable services found at location '{location_name}'. Please create services in your Square Dashboard."

            service_id = location_services[0]['id']
            service_name = location_services[0]['full_name']
            print(f"Using service: {service_name} (ID: {service_id})")
        else:
            # Verify the service exists
            try:
                all_services = list_catalog_services(access_token)
                service_info = next((s for s in all_services if s['id'] == service_id), None)
                if service_info:
                    service_name = service_info['full_name']
                    print(f"Using specified service: {service_name} (ID: {service_id})")
                else:
                    return f"Error: Service ID '{service_id}' not found in catalog."
            except Exception as e:
                print(f"Warning: Could not verify service: {e}")
                service_name = "Unknown Service"

        # Parse booking time using improved natural language parser
        # Use Boston/Eastern timezone
        user_timezone = 'America/New_York'
        user_tz = pytz.timezone(user_timezone)

        parsed_time = parse_natural_date(booking_time, user_tz)
        start_at = parsed_time.astimezone(pytz.UTC)

        print(f"Parsed booking time: {parsed_time} (local) -> {start_at} (UTC)")

        # Search for availability at the requested time to get proper appointment segments
        # Search in a 2-hour window around the requested time
        search_start = start_at - timedelta(minutes=30)
        search_end = start_at + timedelta(hours=1, minutes=30)

        segment_filter = {
            "service_variation_id": service_id
        }

        print(f"Searching availability from {search_start} to {search_end}")

        availability_response = search_availability(
            access_token=access_token,
            location_id=location_id,
            start_at_min=search_start,
            start_at_max=search_end,
            segment_filters=segment_filter
        )

        availabilities = availability_response.get('availabilities', [])
        print(f"Found {len(availabilities)} available slots")

        # Find the slot that matches our requested time (within 5 minutes)
        matching_slot = None
        for slot in availabilities:
            slot_time = datetime.fromisoformat(slot['start_at'].replace('Z', '+00:00'))
            time_diff = abs((slot_time - start_at).total_seconds())
            if time_diff < 300:  # Within 5 minutes
                matching_slot = slot
                print(f"Found matching slot at {slot_time}")
                break

        if not matching_slot:
            return f"Error: The requested time {parsed_time.strftime('%A, %B %d at %I:%M %p')} is not available. Please check availability first and choose an available time slot."

        # Use appointment segments from the availability response
        appointment_segments = matching_slot.get('appointment_segments', [])
        if not appointment_segments:
            # Fallback if no segments in response
            appointment_segments = [{
                "service_variation_id": service_id,
                "service_variation_version": 1
            }]

        # Use the exact time from the availability slot
        start_at = datetime.fromisoformat(matching_slot['start_at'].replace('Z', '+00:00'))

        # Parse customer name into first and last name
        given_name = 'Guest'
        family_name = 'Customer'
        if customer_name:
            name_parts = customer_name.strip().split(None, 1)  # Split on first space
            if len(name_parts) == 1:
                given_name = name_parts[0]
            elif len(name_parts) >= 2:
                given_name = name_parts[0]
                family_name = name_parts[1]

        # Create customer info for auto-creation (will be used if no customer_id)
        customer_info = {
            'given_name': given_name,
            'family_name': family_name,
            'note': 'Auto-created via phone booking system'
        }

        # Add optional contact info
        if customer_email:
            customer_info['email_address'] = customer_email
        if customer_phone:
            customer_info['phone_number'] = customer_phone

        print(f"Creating booking with segments: {appointment_segments}")

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
        if customer_name:
            response += f"👤 Customer: {customer_name}\n"
        response += f"📍 Location: {location_name}\n"
        response += f"🛠️  Service: {service_name}\n"
        response += f"🕐 Time: {parsed_time.strftime('%A, %B %d at %I:%M %p')}\n"
        response += f"🆔 Booking ID: {booking.get('id', 'N/A')}\n"
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

        # Parse new time using improved natural language parser
        # Use Boston/Eastern timezone
        user_timezone = 'America/New_York'
        user_tz = pytz.timezone(user_timezone)

        parsed_time = parse_natural_date(new_time, user_tz)
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