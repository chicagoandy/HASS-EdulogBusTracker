# HASSEdulogBusTracker

A Home Assistant custom integration that creates one Home Assistant device for
each approved student in an Edulog Parent Portal account. The student's device
contains one device tracker for each distinct assigned vehicle, reporting its
live location, and diagnostic polling entities. When multiple trips use the
same vehicle, they share one tracker; it lists the associated trips in its
`trip` and `trips` attributes. Trackers use generic `Vehicle 1`, `Vehicle 2`,
etc. names and account/student-scoped entity IDs, rather than exposing the
Edulog vehicle ID in their names or entity IDs. Entity IDs follow the
`device_tracker.edulog_<account>_<student>_vehicle_<number>` pattern when a
tracker is first created. Vehicles keep the number they were registered with,
new vehicles receive the lowest free number, and the integration never changes
an entity ID or name you have customized. A vehicle that is no longer assigned
keeps its entity, history and settings and becomes unavailable.
Vehicle diagnostic entities expose their `vehicle_id` attribute.
Tracker attributes include `student_first_name`, `student_last_name`,
`trip_type`, `pickup_stop_name`, `pickup_time`, `dropoff_stop_name`, and
`dropoff_time`, along with available vehicle details. For shared vehicles,
these top-level trip details reflect the first associated trip; `trips` contains
selected details for every associated trip. Other fields from the student and
trip API responses are not exposed.

## Installation

Install **Edulog Bus Tracker** through HACS, or copy
`custom_components/edulog_bus_tracker` into the Home Assistant
`custom_components` directory manually. Restart Home Assistant, then add
**Edulog Bus Tracker** from **Settings > Devices & services > Add integration**.

To install through HACS before it is listed in the default store, go to
**HACS > Integrations > menu > Custom repositories**, add
`https://github.com/chicagoandy/HASS-EdulogBusTracker`, and choose **Integration**.

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
a vehicle, it is polled once per vehicle while any associated trip is in its
polling window. Vehicle-location API errors trigger per-vehicle exponential
backoff, starting at 10 seconds and capped at 5 minutes; a successful request
resets the backoff. While a vehicle's requests fail, its tracker keeps the last
known position and its telemetry health sensor reports `stale`. If the student
or trip schedule endpoints fail, the whole update backs off, starting at 30
seconds and capped at 5 minutes, and returns to the normal cadence after the
next successful update. Authentication failures still trigger reauthentication
immediately. Students in different school districts (Edulog tenants) are
supported; trip schedules are requested separately for each district.
The approved student list is fetched at startup and refreshed daily. Trip
schedules are fetched at startup and refreshed hourly, or sooner if the
approved student list changes. Thus, student and trip endpoints are not called
every 5 seconds. Trip times are evaluated in the
timezone returned with each trip schedule; if the API omits or provides an
unknown timezone, Home Assistant's configured timezone is used. Students without an
assigned vehicle do not get a location tracker.
Each student also has an **Any trip active** diagnostic binary sensor. It is on
when at least one of that student's trips has an assigned vehicle and is
within its location polling window.
Each distinct vehicle assigned to a student has one **Location polling**
diagnostic binary sensor on that student's device. Its `vehicle_id` attribute
identifies the vehicle, and it is on whenever any trip using that vehicle is
in its polling window, starting 30 minutes before the first route stop and
ending at its scheduled drop-off. If route-stop data is unavailable, the
pickup time is used as the start reference. Trips sharing a vehicle therefore
share one sensor per student, and it reflects the vehicle's actual polling
state.
Each distinct vehicle assigned to a student has one **Location polling
interval** diagnostic sensor, which always reports 5 seconds, and one **Next
eligible location poll** timestamp sensor. Their `vehicle_id` attributes
identify the vehicle. For a shared vehicle, the timestamp reports the earliest
next poll across all trips using the vehicle: the upcoming polling-window
start while inactive, or the next refresh time while active. The request runs
on the first coordinator update due after the displayed time.

## Troubleshooting logs

The integration logs warnings when the student API returns no approved
requests, approved requests are missing required IDs, trip schedule data is
missing or does not match approved students, or no assigned trips are returned.
Failed updates are reported by Home Assistant's update coordinator, once when
updates start failing and once when they recover. Fetch counts, tracker
preparation summaries, polling decisions and vehicle location results are logged at DEBUG; that
includes a summary on each coordinator update while debug logging is enabled,
so use it temporarily when troubleshooting. Individual trip diagnostics show
the pickup/dropoff values, whether a vehicle is assigned, and the computed
active state; they are emitted at startup, schedule refresh, or when the
active state changes. To see them, set the integration logger to `debug` in
Home Assistant's `configuration.yaml`:

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

If the Edulog access token expires and cannot be refreshed, Home Assistant
asks you to reauthenticate the integration from **Settings > Devices &
services**. Sign in with the same Edulog account to restore bus updates.

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
  translations/en.json
hacs.json
LICENSE
.github/workflows/validate.yml
tests/
```
