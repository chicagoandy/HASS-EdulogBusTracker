# HASSEdulogBusTracker

A Home Assistant custom integration that creates one Home Assistant device for
each approved student in an Edulog Parent Portal account. The student's device
contains a device tracker for each returned trip, reporting the live location
of its assigned vehicle, and a **Location polling** binary sensor for each
trip. Entity names combine the student and trip names (for example,
`Elliana Metcalfe - Trip 1`).
Tracker attributes include `student_first_name`, `student_last_name`,
`trip_type`, `pickup_stop_name`, `pickup_time`, `dropoff_stop_name`, and
`dropoff_time`, along with available vehicle details. Other fields from the
student and trip API responses are not exposed.

## Installation

Install **Edulog Bus Tracker** through HACS, or copy
`custom_components/edulog_bus_tracker` into the Home Assistant
`custom_components` directory manually. Restart Home Assistant, then add
**Edulog Bus Tracker** from **Settings > Devices & services > Add integration**.

To install through HACS before it is listed in the default store, go to
**HACS > Integrations > menu > Custom repositories**, add
`https://github.com/chicagoandy/HASSEdulogBusTracker`, and choose **Integration**.

## Setup

Sign in with the Edulog Parent Portal email and password. They are used only to
request access and refresh tokens and are not saved. Home Assistant stores
those tokens in the integration config entry. The integration refreshes the
access token shortly before it expires and retries once with a refreshed token
if an API request returns unauthorized. Any rotated tokens are saved back to
the config entry. The account ID is stored as the config entry's unique ID.
Treat Home Assistant backups and config-entry data as sensitive because they
contain authentication tokens. Older entries without a refresh token will ask
you to reauthenticate when their access token can no longer be used.

## Polling and behavior

Home Assistant checks the integration for updates every 5 seconds; this is not
the interval for every API request. On each check, it evaluates active trip
windows using the cached trip schedules. At startup, it makes one location
request for each assigned vehicle to initialize tracker entities, even if the
trip is not yet in its polling window. After startup, vehicle locations are
refreshed every 5 seconds only for vehicles tied to a trip from 30 minutes
before that route's first stop through the trip's drop-off time. If trips share
a vehicle, it is polled while any associated trip is in its polling window.
The approved student list is fetched at startup and refreshed daily. Trip
schedules are fetched at startup and refreshed hourly, or sooner if the
approved student list changes. Thus, student and trip endpoints are not called
every 5 seconds. Trip times are evaluated in the
timezone returned with each trip schedule; if the API omits or provides an
unknown timezone, Home Assistant's configured timezone is used. A student
with no returned trips gets a placeholder tracker; trips without an assigned
vehicle remain as trackers with no location.
Each distinct vehicle assigned to a student has one **Vehicle [ID] location
polling** diagnostic binary sensor on that student's device. It is on whenever
any trip using that vehicle is in its polling window, starting 30 minutes
before the first route stop and ending at its scheduled drop-off. If route-stop
data is unavailable, the pickup time is used as the start reference. Trips
sharing a vehicle therefore share one sensor per student, and it reflects the
vehicle's actual polling state.
Each trip has a diagnostic sensor for the configured location polling interval,
which always reports 5 seconds, including while the trip is inactive. Each
distinct vehicle assigned to a student has one next eligible location poll
timestamp sensor. For a shared vehicle, that sensor reports the earliest next
poll across all trips using the vehicle: the upcoming polling-window start
while inactive, or the next refresh time while active. The request runs on the
first coordinator update due after the displayed time.

## Troubleshooting logs

The integration logs warnings when the student API returns no approved
requests, approved requests are missing required IDs, trip schedule data is
missing or does not match approved students, or no assigned trips are returned.
Successful setup, fetch counts, and tracker preparation summaries are logged
at INFO. Authentication, API, and connection failures are logged at ERROR.
Polling decisions and vehicle location results are logged at DEBUG; that
includes a summary on each coordinator update while debug logging is enabled,
so use it temporarily when troubleshooting. Individual trip diagnostics show
the pickup/dropoff values, whether a vehicle is assigned, and the computed
active state; they are emitted at startup, schedule refresh, or when the
active state changes. To show INFO and ERROR messages, set the integration
logger to `info` in Home Assistant's `configuration.yaml`. Use `debug` for
polling diagnostics:

```yaml
logger:
  logs:
    custom_components.edulog_bus_tracker: debug
```

If a `logger:` section already exists, add the integration entry under its
`logs:` key. Restart Home Assistant after changing the configuration. The
integration's diagnostic logs use counts and avoid logging student identifiers,
vehicle identifiers, coordinates, or access tokens; still avoid sharing full
Home Assistant logs publicly.

If the Edulog access token expires, Home Assistant creates a repair issue
asking you to reauthenticate the integration. The issue is cleared after a
successful sign-in.

## API behavior

The integration uses the Parent Portal API at `platform.edulog.com`, including
the portal login, student access request, student trips, and vehicle live event
endpoints. These endpoints are not documented as a public third-party API and
may change without notice. Do not share portal passwords, bearer tokens,
cookies, student identifiers, or live location data in issues or logs. If a
token has been exposed, sign out and revoke the session through Edulog or your
school district if possible.

## License

Copyright (c) 2026 chicagoandy. Edulog Bus Tracker is distributed under the
MIT License. See [LICENSE](./LICENSE) for the license text.

## Project structure

```text
custom_components/edulog_bus_tracker/
  api.py
  binary_sensor.py
  config_flow.py
  coordinator.py
  device_tracker.py
  manifest.json
  sensor.py
  strings.json
hacs.json
LICENSE
.github/workflows/validate.yml
tests/
```
