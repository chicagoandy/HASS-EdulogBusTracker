from datetime import timedelta

DOMAIN = "edulog_bus_tracker"
API_BASE_URL = "https://platform.edulog.com"
SCAN_INTERVAL = timedelta(seconds=5)
STUDENT_LIST_REFRESH_INTERVAL = timedelta(days=1)
IDLE_VEHICLE_REFRESH_INTERVAL = timedelta(seconds=60)
TRIP_SCHEDULE_REFRESH_INTERVAL = timedelta(hours=1)
