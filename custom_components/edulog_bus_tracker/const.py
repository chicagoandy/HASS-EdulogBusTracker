from datetime import timedelta

DOMAIN = "edulog_bus_tracker"
REAUTH_ISSUE_ID_PREFIX = "reauthenticate"
API_BASE_URL = "https://platform.edulog.com"
SCAN_INTERVAL = timedelta(seconds=5)
LOCATION_POLL_LEAD_TIME = timedelta(minutes=30)
LOCATION_POLL_BACKOFF_INITIAL = timedelta(seconds=10)
LOCATION_POLL_BACKOFF_MAX = timedelta(minutes=5)
STUDENT_LIST_REFRESH_INTERVAL = timedelta(days=1)
TRIP_SCHEDULE_REFRESH_INTERVAL = timedelta(hours=1)
