"""
Timezone detection utilities for phone numbers and user preferences.
"""

from typing import Optional, Dict
import pytz
from datetime import datetime

# US area code to timezone mapping
AREA_CODE_TO_TIMEZONE = {
    # Pacific Time (PT)
    '206': 'America/Los_Angeles',  # Seattle, WA
    '213': 'America/Los_Angeles',  # Los Angeles, CA
    '310': 'America/Los_Angeles',  # Los Angeles, CA
    '323': 'America/Los_Angeles',  # Los Angeles, CA
    '408': 'America/Los_Angeles',  # San Jose, CA
    '415': 'America/Los_Angeles',  # San Francisco, CA
    '424': 'America/Los_Angeles',  # Los Angeles, CA
    '425': 'America/Los_Angeles',  # Seattle, WA
    '442': 'America/Los_Angeles',  # California
    '503': 'America/Los_Angeles',  # Portland, OR
    '509': 'America/Los_Angeles',  # Washington
    '510': 'America/Los_Angeles',  # Oakland, CA
    '530': 'America/Los_Angeles',  # California
    '559': 'America/Los_Angeles',  # Fresno, CA
    '562': 'America/Los_Angeles',  # Long Beach, CA
    '619': 'America/Los_Angeles',  # San Diego, CA
    '626': 'America/Los_Angeles',  # Pasadena, CA
    '650': 'America/Los_Angeles',  # San Mateo, CA
    '657': 'America/Los_Angeles',  # Anaheim, CA
    '661': 'America/Los_Angeles',  # Bakersfield, CA
    '669': 'America/Los_Angeles',  # San Jose, CA
    '707': 'America/Los_Angeles',  # California
    '714': 'America/Los_Angeles',  # Orange County, CA
    '747': 'America/Los_Angeles',  # Los Angeles, CA
    '760': 'America/Los_Angeles',  # California
    '775': 'America/Los_Angeles',  # Nevada (Reno)
    '805': 'America/Los_Angeles',  # California
    '818': 'America/Los_Angeles',  # Los Angeles, CA
    '831': 'America/Los_Angeles',  # California
    '858': 'America/Los_Angeles',  # San Diego, CA
    '909': 'America/Los_Angeles',  # California
    '916': 'America/Los_Angeles',  # Sacramento, CA
    '925': 'America/Los_Angeles',  # California
    '949': 'America/Los_Angeles',  # Irvine, CA
    '951': 'America/Los_Angeles',  # Riverside, CA

    # Mountain Time (MT)
    '303': 'America/Denver',  # Denver, CO
    '385': 'America/Denver',  # Utah
    '406': 'America/Denver',  # Montana
    '435': 'America/Denver',  # Utah
    '480': 'America/Phoenix',  # Phoenix, AZ (no DST)
    '505': 'America/Denver',  # New Mexico
    '520': 'America/Phoenix',  # Tucson, AZ (no DST)
    '602': 'America/Phoenix',  # Phoenix, AZ (no DST)
    '623': 'America/Phoenix',  # Phoenix, AZ (no DST)
    '720': 'America/Denver',  # Denver, CO
    '801': 'America/Denver',  # Salt Lake City, UT
    '928': 'America/Phoenix',  # Arizona (no DST)
    '970': 'America/Denver',  # Colorado

    # Central Time (CT)
    '205': 'America/Chicago',  # Alabama
    '210': 'America/Chicago',  # San Antonio, TX
    '214': 'America/Chicago',  # Dallas, TX
    '217': 'America/Chicago',  # Illinois
    '224': 'America/Chicago',  # Illinois
    '225': 'America/Chicago',  # Louisiana
    '228': 'America/Chicago',  # Mississippi
    '251': 'America/Chicago',  # Alabama
    '254': 'America/Chicago',  # Texas
    '256': 'America/Chicago',  # Alabama
    '262': 'America/Chicago',  # Wisconsin
    '270': 'America/Chicago',  # Kentucky
    '281': 'America/Chicago',  # Houston, TX
    '309': 'America/Chicago',  # Illinois
    '312': 'America/Chicago',  # Chicago, IL
    '314': 'America/Chicago',  # St. Louis, MO
    '316': 'America/Chicago',  # Kansas
    '318': 'America/Chicago',  # Louisiana
    '319': 'America/Chicago',  # Iowa
    '320': 'America/Chicago',  # Minnesota
    '331': 'America/Chicago',  # Illinois
    '334': 'America/Chicago',  # Alabama
    '337': 'America/Chicago',  # Louisiana
    '346': 'America/Chicago',  # Houston, TX
    '361': 'America/Chicago',  # Texas
    '402': 'America/Chicago',  # Nebraska
    '405': 'America/Chicago',  # Oklahoma City, OK
    '409': 'America/Chicago',  # Texas
    '414': 'America/Chicago',  # Milwaukee, WI
    '417': 'America/Chicago',  # Missouri
    '430': 'America/Chicago',  # Texas
    '432': 'America/Chicago',  # Texas
    '469': 'America/Chicago',  # Dallas, TX
    '479': 'America/Chicago',  # Arkansas
    '501': 'America/Chicago',  # Arkansas
    '504': 'America/Chicago',  # New Orleans, LA
    '507': 'America/Chicago',  # Minnesota
    '512': 'America/Chicago',  # Austin, TX
    '515': 'America/Chicago',  # Iowa
    '563': 'America/Chicago',  # Iowa
    '573': 'America/Chicago',  # Missouri
    '601': 'America/Chicago',  # Mississippi
    '608': 'America/Chicago',  # Wisconsin
    '612': 'America/Chicago',  # Minneapolis, MN
    '615': 'America/Chicago',  # Nashville, TN
    '618': 'America/Chicago',  # Illinois
    '630': 'America/Chicago',  # Illinois
    '636': 'America/Chicago',  # Missouri
    '641': 'America/Chicago',  # Iowa
    '651': 'America/Chicago',  # Minnesota
    '662': 'America/Chicago',  # Mississippi
    '708': 'America/Chicago',  # Illinois
    '712': 'America/Chicago',  # Iowa
    '713': 'America/Chicago',  # Houston, TX
    '715': 'America/Chicago',  # Wisconsin
    '731': 'America/Chicago',  # Tennessee
    '737': 'America/Chicago',  # Austin, TX
    '763': 'America/Chicago',  # Minnesota
    '769': 'America/Chicago',  # Mississippi
    '773': 'America/Chicago',  # Chicago, IL
    '785': 'America/Chicago',  # Kansas
    '806': 'America/Chicago',  # Texas
    '815': 'America/Chicago',  # Illinois
    '816': 'America/Chicago',  # Kansas City, MO
    '817': 'America/Chicago',  # Fort Worth, TX
    '830': 'America/Chicago',  # Texas
    '832': 'America/Chicago',  # Houston, TX
    '847': 'America/Chicago',  # Illinois
    '870': 'America/Chicago',  # Arkansas
    '901': 'America/Chicago',  # Memphis, TN
    '903': 'America/Chicago',  # Texas
    '913': 'America/Chicago',  # Kansas
    '915': 'America/Chicago',  # El Paso, TX
    '918': 'America/Chicago',  # Oklahoma
    '920': 'America/Chicago',  # Wisconsin
    '936': 'America/Chicago',  # Texas
    '940': 'America/Chicago',  # Texas
    '952': 'America/Chicago',  # Minnesota
    '956': 'America/Chicago',  # Texas
    '972': 'America/Chicago',  # Dallas, TX
    '979': 'America/Chicago',  # Texas
    '985': 'America/Chicago',  # Louisiana

    # Eastern Time (ET)
    '201': 'America/New_York',  # New Jersey
    '202': 'America/New_York',  # Washington, DC
    '203': 'America/New_York',  # Connecticut
    '207': 'America/New_York',  # Maine
    '212': 'America/New_York',  # New York, NY
    '215': 'America/New_York',  # Philadelphia, PA
    '216': 'America/New_York',  # Cleveland, OH
    '229': 'America/New_York',  # Georgia
    '231': 'America/New_York',  # Michigan
    '234': 'America/New_York',  # Ohio
    '239': 'America/New_York',  # Florida
    '240': 'America/New_York',  # Maryland
    '248': 'America/New_York',  # Michigan
    '252': 'America/New_York',  # North Carolina
    '260': 'America/New_York',  # Indiana
    '267': 'America/New_York',  # Philadelphia, PA
    '269': 'America/New_York',  # Michigan
    '276': 'America/New_York',  # Virginia
    '301': 'America/New_York',  # Maryland
    '302': 'America/New_York',  # Delaware
    '304': 'America/New_York',  # West Virginia
    '305': 'America/New_York',  # Miami, FL
    '313': 'America/New_York',  # Detroit, MI
    '315': 'America/New_York',  # New York
    '321': 'America/New_York',  # Florida
    '330': 'America/New_York',  # Ohio
    '336': 'America/New_York',  # North Carolina
    '339': 'America/New_York',  # Massachusetts
    '347': 'America/New_York',  # New York, NY
    '351': 'America/New_York',  # Massachusetts
    '352': 'America/New_York',  # Florida
    '380': 'America/New_York',  # Ohio
    '386': 'America/New_York',  # Florida
    '401': 'America/New_York',  # Rhode Island
    '404': 'America/New_York',  # Atlanta, GA
    '407': 'America/New_York',  # Orlando, FL
    '410': 'America/New_York',  # Maryland
    '412': 'America/New_York',  # Pittsburgh, PA
    '413': 'America/New_York',  # Massachusetts
    '419': 'America/New_York',  # Ohio
    '423': 'America/New_York',  # Tennessee
    '434': 'America/New_York',  # Virginia
    '440': 'America/New_York',  # Ohio
    '443': 'America/New_York',  # Maryland
    '470': 'America/New_York',  # Georgia
    '478': 'America/New_York',  # Georgia
    '484': 'America/New_York',  # Pennsylvania
    '502': 'America/New_York',  # Kentucky
    '508': 'America/New_York',  # Massachusetts
    '513': 'America/New_York',  # Cincinnati, OH
    '516': 'America/New_York',  # New York
    '517': 'America/New_York',  # Michigan
    '518': 'America/New_York',  # New York
    '540': 'America/New_York',  # Virginia
    '551': 'America/New_York',  # New Jersey
    '561': 'America/New_York',  # Florida
    '567': 'America/New_York',  # Ohio
    '570': 'America/New_York',  # Pennsylvania
    '571': 'America/New_York',  # Virginia
    '574': 'America/New_York',  # Indiana
    '585': 'America/New_York',  # New York
    '586': 'America/New_York',  # Michigan
    '603': 'America/New_York',  # New Hampshire
    '607': 'America/New_York',  # New York
    '609': 'America/New_York',  # New Jersey
    '610': 'America/New_York',  # Pennsylvania
    '614': 'America/New_York',  # Columbus, OH
    '616': 'America/New_York',  # Michigan
    '617': 'America/New_York',  # Boston, MA
    '631': 'America/New_York',  # New York
    '646': 'America/New_York',  # New York, NY
    '667': 'America/New_York',  # Maryland
    '678': 'America/New_York',  # Georgia
    '679': 'America/New_York',  # Michigan
    '681': 'America/New_York',  # West Virginia
    '703': 'America/New_York',  # Virginia
    '704': 'America/New_York',  # Charlotte, NC
    '706': 'America/New_York',  # Georgia
    '716': 'America/New_York',  # New York
    '717': 'America/New_York',  # Pennsylvania
    '718': 'America/New_York',  # New York, NY
    '724': 'America/New_York',  # Pennsylvania
    '727': 'America/New_York',  # Florida
    '732': 'America/New_York',  # New Jersey
    '734': 'America/New_York',  # Michigan
    '740': 'America/New_York',  # Ohio
    '754': 'America/New_York',  # Florida
    '757': 'America/New_York',  # Virginia
    '762': 'America/New_York',  # Georgia
    '770': 'America/New_York',  # Georgia
    '772': 'America/New_York',  # Florida
    '774': 'America/New_York',  # Massachusetts
    '781': 'America/New_York',  # Massachusetts
    '786': 'America/New_York',  # Miami, FL
    '802': 'America/New_York',  # Vermont
    '803': 'America/New_York',  # South Carolina
    '804': 'America/New_York',  # Virginia
    '810': 'America/New_York',  # Michigan
    '812': 'America/New_York',  # Indiana
    '813': 'America/New_York',  # Tampa, FL
    '814': 'America/New_York',  # Pennsylvania
    '828': 'America/New_York',  # North Carolina
    '843': 'America/New_York',  # South Carolina
    '845': 'America/New_York',  # New York
    '848': 'America/New_York',  # New Jersey
    '850': 'America/New_York',  # Florida
    '856': 'America/New_York',  # New Jersey
    '857': 'America/New_York',  # Massachusetts
    '859': 'America/New_York',  # Kentucky
    '860': 'America/New_York',  # Connecticut
    '862': 'America/New_York',  # New Jersey
    '863': 'America/New_York',  # Florida
    '864': 'America/New_York',  # South Carolina
    '865': 'America/New_York',  # Tennessee
    '878': 'America/New_York',  # Pennsylvania
    '904': 'America/New_York',  # Jacksonville, FL
    '908': 'America/New_York',  # New Jersey
    '910': 'America/New_York',  # North Carolina
    '914': 'America/New_York',  # New York
    '917': 'America/New_York',  # New York, NY
    '919': 'America/New_York',  # North Carolina
    '929': 'America/New_York',  # New York, NY
    '931': 'America/New_York',  # Tennessee
    '937': 'America/New_York',  # Ohio
    '941': 'America/New_York',  # Florida
    '947': 'America/New_York',  # Michigan
    '954': 'America/New_York',  # Florida
    '959': 'America/New_York',  # Connecticut
    '973': 'America/New_York',  # New Jersey
    '978': 'America/New_York',  # Massachusetts
    '980': 'America/New_York',  # North Carolina
    '984': 'America/New_York',  # North Carolina
    '989': 'America/New_York',  # Michigan
}


def get_timezone_from_phone(phone_number: str) -> Optional[str]:
    """
    Get timezone from US phone number.

    Args:
        phone_number: Phone number in any format

    Returns:
        Timezone string (e.g., 'America/Los_Angeles') or None if not found
    """
    if not phone_number:
        return None

    # Clean the phone number - keep only digits
    digits = ''.join(c for c in phone_number if c.isdigit())

    # Handle different formats
    if len(digits) == 11 and digits[0] == '1':
        # Remove country code
        digits = digits[1:]

    if len(digits) == 10:
        # Extract area code (first 3 digits)
        area_code = digits[:3]
        return AREA_CODE_TO_TIMEZONE.get(area_code)

    return None


def get_user_timezone(phone_number: Optional[str] = None,
                      stored_timezone: Optional[str] = None,
                      default_timezone: str = 'America/Los_Angeles') -> str:
    """
    Get user's timezone from various sources with fallback.

    Priority:
    1. Stored user preference
    2. Phone number area code
    3. Default timezone

    Args:
        phone_number: User's phone number
        stored_timezone: Timezone stored in user preferences
        default_timezone: Fallback timezone if detection fails

    Returns:
        Timezone string
    """
    # First priority: stored user preference
    if stored_timezone:
        try:
            pytz.timezone(stored_timezone)  # Validate timezone
            return stored_timezone
        except:
            pass

    # Second priority: phone number
    if phone_number:
        tz = get_timezone_from_phone(phone_number)
        if tz:
            return tz

    # Fallback to default
    return default_timezone


def convert_to_timezone(dt: datetime, timezone_str: str) -> datetime:
    """
    Convert a datetime to a specific timezone.

    Args:
        dt: Datetime object (can be naive or aware)
        timezone_str: Target timezone string

    Returns:
        Timezone-aware datetime in the target timezone
    """
    tz = pytz.timezone(timezone_str)

    # If naive, assume it's in the target timezone
    if dt.tzinfo is None:
        return tz.localize(dt)

    # If aware, convert to target timezone
    return dt.astimezone(tz)