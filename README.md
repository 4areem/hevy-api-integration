# Hevy API Integration

A small Python client + CLI for the Hevy public API (https://api.hevyapp.com/docs/),
built for pulling workout history and creating/updating routines programmatically
(e.g. so a coaching assistant can read your logs and write routines back).

Requires **Hevy Pro** and an API key from https://hevy.com/settings?developer.

## Status

- [x] Client library written (`hevy_client.py`)
- [x] Tested against a real Hevy account (verified 2026-07-14 against live API)
- [x] README finalized after testing

Current focus is the **non-invasive / read-only** surface (pulling workouts,
history, body measurements, routines, exercise templates). The write path
(`create_routine` / `update_routine` / `create_workout`) is implemented and
was smoke-tested once, but is intentionally not the focus yet — see
"Verified against live API" below for exactly what was checked.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env and paste your real HEVY_API_KEY
```

`.env` is gitignored - never commit your real key. `hevy_client.py` loads it
automatically (no `python-dotenv` dependency needed).

## CLI usage

```bash
# Recent workouts
python hevy_client.py --pull-recent 10

# Workouts in a date range
python hevy_client.py --pull-range 2026-06-01 2026-07-13

# Single workout by id
python hevy_client.py --workout <workout_id>

# Total workout count
python hevy_client.py --workout-count

# Routines
python hevy_client.py --list-routines
python hevy_client.py --routine <routine_id>
python hevy_client.py --create-routine my_routine.json
python hevy_client.py --update-routine <routine_id> my_routine_update.json

# Exercise templates (needed to build routine/workout exercise entries)
python hevy_client.py --list-exercise-templates
python hevy_client.py --list-exercise-templates --query "bench"

# Full logged history for one exercise (one row per set, newest first)
python hevy_client.py --exercise-history 79D0BB3A

# Body-measurement log (weight, etc.)
python hevy_client.py --body-measurements

# Routine folders
python hevy_client.py --list-routine-folders

# Authenticated user info
python hevy_client.py --user-info

# Any command can be written to a file instead of stdout.
# Export files are wrapped in an envelope that also bundles your measurement
# context (weight log + profile) by default, so each file stands on its own:
#   {"exported_at": ..., "measurements": {...}, "data": <command result>}
python hevy_client.py --pull-recent 10 --out recent.json

# Opt out of the bundled measurements to get just the raw result:
python hevy_client.py --pull-recent 10 --no-measurements --out recent.json
```

## Using it as a library

```python
from hevy_client import HevyClient

client = HevyClient()  # reads HEVY_API_KEY from environment/.env

recent = client.get_recent_workouts(count=5)
history = client.get_workouts_in_range(
    datetime(2026, 6, 1), datetime(2026, 7, 13)
)

routine = client.create_routine(
    title="Push Day A",
    notes="Chest/shoulders/triceps",
    exercises=[
        {
            "exercise_template_id": "D04AC939",  # look up via find_exercise_template
            "superset_id": None,
            "rest_seconds": 90,
            "notes": "",
            "sets": [
                {"type": "warmup", "weight_kg": 20, "reps": 12},
                {"type": "normal", "weight_kg": 60, "reps": 8},
                {"type": "normal", "weight_kg": 60, "reps": 8},
            ],
        }
    ],
)
```

### JSON file schema for `--create-routine` / `--update-routine`

```json
{
  "title": "Push Day A",
  "notes": "Chest/shoulders/triceps",
  "folder_id": null,
  "exercises": [
    {
      "exercise_template_id": "D04AC939",
      "superset_id": null,
      "rest_seconds": 90,
      "notes": "",
      "sets": [
        {"type": "warmup", "weight_kg": 20, "reps": 12},
        {"type": "normal", "weight_kg": 60, "reps": 8}
      ]
    }
  ]
}
```

Set `type` must be one of: `normal`, `warmup`, `failure`, `dropset`.

## Notes / gotchas

- **Hevy Pro required.** The API key endpoint (https://hevy.com/settings?developer)
  only appears for Pro accounts.
- **Pagination cap:** `/v1/workouts`, `/v1/routines`, and `/v1/exercise_templates`
  cap `pageSize` at 10 per page. Bulk pulls (e.g. `--pull-range` over months)
  make multiple requests; the client throttles itself slightly (0.25s) between
  pages to be a good API citizen.
- **No native date-range filter.** `get_workouts_in_range` paginates newest-first
  and stops early once it pages past your start date. This assumes the API
  keeps returning workouts newest-to-oldest (true as of the current spec).
- **Routine updates replace, not merge, the exercises list.** If you're editing
  one exercise in a routine, pass the *full* exercises array in your update
  JSON, not just the one exercise you changed.
- **Rate limits:** not formally published by Hevy as of this writing. Client
  raises `HevyAPIError` with status 429 if you get throttled - back off and retry.
- **This API is explicitly beta/unstable** per Hevy's own docs ("we make no
  guarantees that we won't completely change the structure or abandon the
  project entirely").

## Verified against live API (2026-07-14)

Confirmed by real calls against a live Pro account. Findings folded into the
client:

- **Read (non-invasive) — all working:** `--user-info` (wrapped in a `data`
  envelope), `--workout-count`, `--pull-recent`, `--pull-range`, `--workout`,
  `--list-routines`, `--routine`, `--list-exercise-templates [--query]`,
  `--exercise-history`, `--body-measurements`, `--list-routine-folders`.
- **`GET /workouts` envelope confirmed** as `{page, page_count, workouts}`.
- **`--pull-range` end date is now inclusive of that whole day.** A date-only
  end (`2026-07-14`) parses to midnight; the CLI now bumps it to end-of-day so
  workouts logged later that day aren't silently dropped.
- **`/routine_folders` returns folders under a `routines` key** (not
  `routine_folders`) — a Hevy API inconsistency, handled in
  `get_all_routine_folders`.
- **`/exercise_history/{id}` is one row per set**, unpaginated, newest first.
- **Body measurements are weight-only.** `/body_measurements` returns just
  `weight_kg` (with a date); `/user/info` has no height. **Height and other
  measurements are not available via the public API** — exports bundle the
  weight log and note this gap.
- **`--out` exports bundle measurements by default** in an
  `{exported_at, measurements, data}` envelope (`--no-measurements` opts out).
  `--body-measurements`/`--user-info` exports are written raw (they already
  are the measurements).
- **Write path (smoke-tested):** `POST /routines` and `PUT /routines/{id}` do
  wrap the body in `{"routine": {...}}`, and `folder_id` is the correct field
  name. `PUT` **requires the full `exercises` array** (400 otherwise) and
  **rejects an empty `notes: ""` on an exercise** even though `POST` accepts
  it — the client now strips empty exercise notes automatically on both.
- **No delete/archive for routines.** `DELETE /routines/{id}` and
  `POST /routines/{id}/archive` both 404 — routines can only be removed
  manually in the app. (A throwaway routine "API Test - UPDATED (safe to
  delete)" was left on the account by testing; delete it in the app.)
