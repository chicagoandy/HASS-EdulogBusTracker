# HASSEdulogBusTracker

A Home Assistant custom integration that creates a device tracker for each
trip returned for an approved student in an Edulog Parent Portal account. Each
tracker reports the live location of that trip's assigned vehicle.
Entity names combine the student and trip names once (for example,
`Elliana Metcalfe - Trip 1`).
Tracker attributes include `student_first_name`, `student_last_name`,
`trip_type`, `pickup_stop_name`, `pickup_time`, `dropoff_stop_name`, and
`dropoff_time`, along with available vehicle details. Other fields from the
student and trip API responses are not exposed.

## Installation

Copy `custom_components/edulog_bus_tracker` into the Home Assistant
`custom_components` directory, restart Home Assistant, then add **Edulog Bus
Tracker** from **Settings > Devices & services > Add integration**.

Sign in with the Edulog Parent Portal email and password. They are used only to
request an access token and are not saved. Home Assistant stores only the access
token in the config entry. The account ID is read from the token when Home
Assistant starts and is not stored separately. If the access token expires, the
integration asks you to sign in again; replacement credentials are also used
only for that sign-in and are not stored.

The integration checks for active trip windows every 5 seconds. While a trip is
active, it refreshes vehicle locations every 5 seconds. Approved student access
requests refresh daily, and inactive vehicle locations refresh every 60 seconds.
Trip schedules refresh at startup and hourly, regardless of whether a trip is
active. Trip times are interpreted in Home Assistant's configured local
timezone. A student with no returned trips gets a placeholder tracker; trips
without an assigned vehicle remain as trackers with no location.

## API behavior

The integration uses the Parent Portal API at `platform.edulog.com`, including
the portal login, student access request, student trips, and vehicle live event
endpoints. These endpoints are not documented as a public third-party API and
may change without notice. Do not share portal passwords, bearer tokens,
cookies, student identifiers, or live location data in issues or logs. If a
token has been exposed, sign out and revoke the session through Edulog or your
school district if possible.

## Project structure

```text
custom_components/edulog_bus_tracker/
  api.py
  config_flow.py
  coordinator.py
  device_tracker.py
  manifest.json
  strings.json
tests/
```
