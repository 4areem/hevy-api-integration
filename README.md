# Hevy API Integration — giving an AI coach direct access to my training data

A Python client + CLI for the [Hevy public API](https://api.hevyapp.com/docs/) that
gives an AI assistant structured, programmatic access to my workout logs — so it can
**read** every set I've ever logged plus my body-weight history, and **write** new
routines straight back into the app.

The idea: instead of copy-pasting screenshots of my training into a chat, I point an
LLM (Claude) at this tool and let it work the data directly — pull my full history,
find where a lift has stalled or a muscle group is under-volumed, and generate the next
block as a routine that shows up in Hevy on my phone. It closes the loop between
"analyze my training" and "program my next mesocycle."

Requires **Hevy Pro** and an API key from https://hevy.com/settings?developer.

## How I used it

1. **Give the AI read access.** `--pull-range` / `--exercise-history` / `--body-measurements`
   dump my real training history as clean JSON the model can reason over — one row per
   set, newest first, with body-weight context bundled in.
2. **Let it analyze.** The model reads the exported JSON directly: progression per lift,
   stalls, volume imbalances between muscle groups, bodyweight trend against the training.
3. **Write the program back.** It emits a routine JSON, and `--create-routine` /
   `--update-routine` push it into Hevy — no manual entry, the workout is waiting in the
   app next session.

This is the small, boring integration layer that makes "AI as a coach" actually
actionable instead of a chat transcript I have to re-type by hand.

## Privacy — no personal data in this repo

This repository is **only the access tooling**. My actual workout exports
(`recent_workouts.json`, `bench_history.json`, `workouts_full.json`, etc.) and my API
key are gitignored and never committed. Clone it, add your own Hevy Pro key, and it
reads *your* data locally.

## Status

- [x] Client library written (`hevy_client.py`)
- [x] Tested against a real Hevy account (verified 2026-07-14 against live API)
- [x] Read path (history, measurements, routines, templates) fully working
- [x] Write path (`create_routine` / `update_routine`) implemented + smoke-tested

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env and paste your real HEVY_API_KEY
```

`.env` is gitignored — never commit your real key. `hevy_client.py` loads it
automatically (no `python-dotenv` dependency needed).

## CLI usage

```bash
# --- read (what the AI consumes) ---
python hevy_client.py --pull-recent 10
python hevy_client.py --pull-range 2026-06-01 2026-07-13
python hevy_client.py --workout <workout_id>
python hevy_client.py --workout-count
python hevy_client.py --exercise-history 79D0BB3A     # one row per set, newest first
python hevy_client.py --body-measurements             # weight log
python hevy_client.py --user-info

# --- exercise templates (needed to build routine entries) ---
python hevy_client.py --list-exercise-templates
python hevy_client.py --list-exercise-templates --query "bench"

# --- write (what the AI produces) ---
python hevy_client.py --list-routines
python hevy_client.py --routine <routine_id>
python hevy_client.py --create-routine my_routine.json
python hevy_client.py --update-routine <routine_id> my_routine_update.json
python hevy_client.py --list-routine-folders

# Any command can be written to a file for the model to read. Exports are wrapped
# in an envelope that bundles body-measurement context so each file stands alone:
#   {"exported_at": ..., "measurements": {...}, "data": <command result>}
python hevy_client.py --pull-recent 10 --out recent.json
python hevy_client.py --pull-recent 10 --no-measurements --out recent.json   # raw only
```

## Using it as a library

```python
from hevy_client import HevyClient
from datetime import datetime

client = HevyClient()  # reads HEVY_API_KEY from environment/.env

recent  = client.get_recent_workouts(count=5)
history = client.get_workouts_in_range(datetime(2026, 6, 1), datetime(2026, 7, 13))

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

### JSON schema for `--create-routine` / `--update-routine`

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

- **Hevy Pro required.** The API-key page only appears for Pro accounts.
- **Pagination cap:** `/workouts`, `/routines`, and `/exercise_templates` cap `pageSize`
  at 10. Bulk pulls make multiple requests; the client throttles ~0.25s between pages.
- **No native date-range filter.** `get_workouts_in_range` paginates newest-first and
  stops once it pages past the start date.
- **Routine updates replace, not merge.** Pass the *full* exercises array on update.
- **Rate limits** aren't published; the client raises `HevyAPIError` on 429 — back off.
- **Beta API.** Hevy's own docs warn the structure may change or be abandoned.

## Verified against live API (2026-07-14)

Confirmed by real calls against a live Pro account, findings folded into the client:

- **Read — all working:** `--user-info`, `--workout-count`, `--pull-recent`,
  `--pull-range`, `--workout`, `--list-routines`, `--routine`,
  `--list-exercise-templates [--query]`, `--exercise-history`, `--body-measurements`,
  `--list-routine-folders`.
- `GET /workouts` envelope is `{page, page_count, workouts}`.
- `--pull-range` end date is inclusive of the whole day (parses to end-of-day).
- `/routine_folders` returns folders under a `routines` key (Hevy inconsistency, handled).
- `/exercise_history/{id}` is one row per set, unpaginated, newest first.
- **Body measurements are weight-only** — `/body_measurements` returns `weight_kg`;
  height/other measurements aren't exposed by the public API.
- **Write path:** `POST /routines` and `PUT /routines/{id}` wrap the body in
  `{"routine": {...}}`; `PUT` requires the full `exercises` array and rejects an empty
  exercise `notes: ""` (the client strips those automatically).
- **No delete/archive** — `DELETE`/`archive` both 404; routines are removed only in-app.
