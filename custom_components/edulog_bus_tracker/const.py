"""Constants for the Edulog Bus Tracker integration."""

from datetime import timedelta

DOMAIN = "edulog_bus_tracker"
REAUTH_ISSUE_ID_PREFIX = "reauthenticate"
API_BASE_URL = "https://platform.edulog.com"
SCAN_INTERVAL = timedelta(seconds=5)
LOCATION_POLL_LEAD_TIME = timedelta(minutes=30)
LOCATION_POLL_BACKOFF_INITIAL = timedelta(seconds=10)
LOCATION_POLL_BACKOFF_MAX = timedelta(minutes=5)
API_ERROR_BACKOFF_INITIAL = timedelta(seconds=30)
API_ERROR_BACKOFF_MAX = timedelta(minutes=5)
STUDENT_LIST_REFRESH_INTERVAL = timedelta(days=1)
TRIP_SCHEDULE_REFRESH_INTERVAL = timedelta(hours=1)

# Geofences, centred on each trip's pickup or drop-off stop. Inside the
# polling fence an active trip's vehicle is polled every SCAN_INTERVAL;
# outside it, every OUTSIDE_POLLING_FENCE_INTERVAL. Outside the display fence
# the tracker reports no coordinates, so it is not drawn on the map.
CONF_POLLING_FENCE_RADIUS = "polling_fence_radius"
CONF_DISPLAY_FENCE_RADIUS = "display_fence_radius"
CONF_FENCE_CENTERS = "fence_centers"
DEFAULT_POLLING_FENCE_RADIUS = 1.0
DEFAULT_DISPLAY_FENCE_RADIUS = 3.0
FENCE_RADIUS_MIN = 0.25
FENCE_RADIUS_MAX = 10.0
FENCE_RADIUS_STEP = 0.25
FENCE_CENTER_PICKUP = "pickup"
FENCE_CENTER_DROPOFF = "dropoff"
FENCE_CENTER_OPTIONS = [FENCE_CENTER_PICKUP, FENCE_CENTER_DROPOFF]
DEFAULT_FENCE_CENTER = FENCE_CENTER_PICKUP
FROM_SCHOOL_TRIP_TYPES = {"fromschool", "from_school", "from school", "pm"}
OUTSIDE_POLLING_FENCE_INTERVAL = timedelta(seconds=30)
METERS_PER_MILE = 1609.344
