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

Install **Edulog Bus Tracker** through HACS, or copy
`custom_components/edulog_bus_tracker` into the Home Assistant
`custom_components` directory manually. Restart Home Assistant, then add
**Edulog Bus Tracker** from **Settings > Devices & services > Add integration**.

To install through HACS before it is listed in the default store, go to
**HACS > Integrations > menu > Custom repositories**, add
`https://github.com/chicagoandy/HASSEdulogBusTracker`, and choose **Integration**.

Sign in with the Edulog Parent Portal email and password. They are used only to
request an access token and are not saved. Home Assistant stores only the access
token in the config entry. The account ID is read from the token when Home
Assistant starts and is not stored separately. If the access token expires, the
integration asks you to sign in again; replacement credentials are also used
only for that sign-in and are not stored.

Home Assistant checks the integration for updates every 5 seconds; this is not
the interval for every API request. On each check, it evaluates active trip
windows using the cached trip schedules. Vehicle locations are refreshed every
5 seconds for vehicles tied to an active trip, and every 60 seconds for other
assigned vehicles. The approved student list is fetched at startup and
refreshed daily. Trip schedules are fetched at startup and refreshed hourly,
or sooner if the approved student list changes. Thus, student and trip
endpoints are not called every 5 seconds. Trip times are interpreted in Home
Assistant's configured local timezone. A student with no returned trips gets
a placeholder tracker; trips without an assigned vehicle remain as trackers
with no location.
Each trip also has a **Location polling** binary sensor. It is on when that
trip has an assigned vehicle and is within its scheduled pickup-to-dropoff
window, indicating that its vehicle location is polled every 5 seconds; it is
off outside that window, when that vehicle's location is polled every 60
seconds, or when the trip has no assigned vehicle. Each vehicle's cadence is
independent; if multiple trips share a vehicle, it is polled every 5 seconds
whenever any associated trip is active.

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

This project is distributed under the GNU General Public License v3.0 or later.
See [LICENSE](./LICENSE) for the full text.

## Project structure

```text
custom_components/edulog_bus_tracker/
  api.py
  binary_sensor.py
  config_flow.py
  coordinator.py
  device_tracker.py
  manifest.json
  strings.json
hacs.json
LICENSE
.github/workflows/validate.yml
tests/
```
