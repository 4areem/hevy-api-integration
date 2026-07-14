# Handoff: Hevy API Integration - finish testing + polish

## Context (read this first)

I (a Cowork agent, no direct shell network access to `api.hevyapp.com`) built
a Python client for Kareem's Hevy Pro account so a separate coaching-assistant
chat can pull his logged workouts and create/update routines automatically,
instead of manual CSV exports.

**I could not test the code against the real API.** My sandbox's outbound
network is allowlisted and `api.hevyapp.com` (and `raw.githubusercontent.com`,
`api.github.com`, `cdn.jsdelivr.net`) are all blocked (confirmed via `curl` -
`pypi.org` works, those don't). Claude Code, running locally on Kareem's
machine, should have normal internet access and can actually run and verify
this against his live account.

**Your job:** run the test plan below against the real Hevy API, fix
anything that's wrong (endpoint paths, field names, response shapes - I
derived the schema from the vendored OpenAPI spec, not from a live call, so
there is real risk of small mismatches), and finish the two checklist items
in README.md that are still unchecked.

## The user

Kareem (karoomy04@gmail.com). Has Hevy Pro. Wants this to be reusable both by
him directly (CLI) and by another Claude chat acting as his coaching
assistant (importing `hevy_client.py` as a library).

## The API key

```
HEVY_API_KEY=<your-key-here — see hevy.com/settings?developer>
```

**Do not commit this anywhere.** Put it in a local `.env` file (already
gitignored via `.gitignore` in this repo) as:

```
HEVY_API_KEY=<your-key-here — see hevy.com/settings?developer>
```

If for any reason this key stops working (401s), it needs to be regenerated
by Kareem at https://hevy.com/settings?developer and re-entered by him - don't
guess at a replacement.

## Files already written (in the project's `hevy-api-integration` folder)

- `hevy_client.py` - the full client + CLI. This is the main deliverable.
- `README.md` - usage docs. Two checklist items at the top are unchecked
  ("Tested against a real Hevy account", "README finalized after testing") -
  check them off once verified, and correct any usage examples that turn out
  to be wrong after real testing.
- `requirements.txt` - just `requests>=2.31`.
- `.env.example` - template for the real `.env` (which you'll create locally,
  not commit).
- `.gitignore` - ignores `.env`, `__pycache__/`, `*.pyc`, `.venv/`.

## How I derived the API schema (so you can sanity-check it)

`https://api.hevyapp.com/docs/` is a Swagger UI SPA with no fetchable raw
JSON endpoint (tried `/docs/swagger.json`, `/docs/json`, `/openapi.json`,
`/docs-json`, `/api/docs-json` - all empty/blocked). I found the spec
vendored in a community project instead:

```
https://raw.githubusercontent.com/chrisdoc/hevy-mcp/main/openapi-spec.json
```

That community repo (`chrisdoc/hevy-mcp`, an MCP server wrapping this same
API) is worth glancing at if something doesn't match - it's a working
reference implementation. There's also `remuzel/hevy-api` (Python client) and
a `hevy-api-client` package on PyPI as cross-references if needed.

### Endpoints confirmed present in the spec (base URL `https://api.hevyapp.com/v1`)

| Method | Path | Notes |
|---|---|---|
| GET | `/workouts` | Paginated, `page`/`pageSize` (max 10/page, default 5) |
| POST | `/workouts` | Create a workout (body: `{"workout": {...}}`) |
| GET | `/workouts/count` | Total workout count |
| GET | `/workouts/events` | Paged sync events (updates/deletes) since a given date, newest-to-oldest |
| GET | `/workouts/{workoutId}` | Single workout, full detail |
| PUT | `/workouts/{workoutId}` | Update an existing workout |
| GET | `/user/info` | Authenticated user info |
| GET | `/routines` | Paginated |
| POST | `/routines` | Create (body: `{"routine": {...}}`) |
| GET | `/routines/{routineId}` | Single routine |
| PUT | `/routines/{routineId}` | Update - **replaces exercises array, doesn't merge** |
| GET | `/exercise_templates` | Paginated catalog |
| POST | `/exercise_templates` | Create a custom exercise template |
| GET | `/exercise_templates/{exerciseTemplateId}` | Single template |
| GET | `/routine_folders` | Paginated |
| POST | `/routine_folders` | Create (goes to index 0, shifts others) |
| GET | `/routine_folders/{folderId}` | Single folder |
| GET | `/exercise_history/{exerciseTemplateId}` | History for one exercise |
| GET | `/body_measurements` | Paginated |
| POST | `/body_measurements` | Create for a date (409 if exists) |
| GET/PUT | `/body_measurements/{date}` | Single entry |

All endpoints require header `api-key: <uuid>`.

### Set/exercise schema (confirmed from spec text, used in `hevy_client.py`)

Routine/workout `exercises[]` entries:
```json
{
  "exercise_template_id": "D04AC939",
  "superset_id": null,
  "rest_seconds": 90,
  "notes": "",
  "sets": [
    {"type": "normal", "weight_kg": 60, "reps": 8}
  ]
}
```
`sets[].type` enum confirmed as: `warmup`, `normal`, `failure`, `dropset`.
`weight_kg` is nullable number; there's likely also `distance_meters`,
`duration_seconds`, `rpe` fields for cardio/RPE-based exercises that I did
NOT fully verify field-by-field - check the vendored spec or a real
`GET /workouts/{id}` response for exact optional fields if Kareem logs
cardio or RPE.

**What I have NOT verified against a live call, and you should check first:**
1. The exact response envelope shape for `GET /workouts` (I assumed
   `{"workouts": [...], "page": n, "page_count": n}` based on typical
   Hevy API docs/community client conventions - confirm actual key names).
2. Whether `POST /workouts` and `POST/PUT /routines` really want the payload
   wrapped in `{"workout": {...}}` / `{"routine": {...}}` vs. flat - this
   matched the pattern in the spec's parameter/description text but I did
   not see a full literal request-body example.
3. Whether `create_routine`'s `folder_id` param name is correct (vs. e.g.
   `routine_folder_id`).
4. Real behavior/response of `PUT /routines/{id}` when only partial fields
   are sent (does it actually require full `exercises[]`, or does the
   client's cautious "pass everything" README note turn out to be unnecessary).

## Test plan (do this against Kareem's real account)

Run these roughly in order, fixing code as you go:

1. `pip install -r requirements.txt`
2. Create `.env` with the key above.
3. `python hevy_client.py --user-info` - sanity check auth works at all.
4. `python hevy_client.py --workout-count`
5. `python hevy_client.py --pull-recent 5` - inspect the actual JSON shape,
   fix `get_workouts_page`/`get_recent_workouts` parsing if the envelope
   differs from what's assumed (see "not verified" #1 above).
6. `python hevy_client.py --pull-range <30 days ago> <today>` - confirm the
   early-break-on-date-order logic actually short-circuits sensibly (won't
   error either way, just confirm results look right and it doesn't loop
   forever on an empty account).
7. `python hevy_client.py --list-exercise-templates --query "bench"` - grab
   a real `exercise_template_id` to use in step 8.
8. Create a throwaway test routine JSON (use the schema in README.md) with
   the real exercise_template_id from step 7, low stakes title like "API
   Test - safe to delete", and run
   `python hevy_client.py --create-routine test_routine.json`. Confirm it
   shows up in the Hevy app.
9. Take the routine_id returned from step 8 and test
   `python hevy_client.py --update-routine <id> test_routine_update.json`
   (e.g. change the title or add a set). Confirm the change reflects in the
   Hevy app.
10. Fix any field/shape mismatches found in steps 5-9 directly in
    `hevy_client.py`.
11. **Clean up:** delete/rename the test routine created in step 8 if Hevy's
    API supports delete (check the spec - I don't recall seeing a DELETE
    /routines endpoint in what I extracted; if there isn't one, tell Kareem
    he'll need to delete it manually in the app).
12. Update `README.md`: check off the two pending checklist items, and fix
    any usage examples/JSON schema shown there if reality differed from my
    draft.
13. Report back to Kareem: what worked as-is, what you had to fix, and
    anything from the "not verified" list above that turned out to matter
    (especially if the request/response envelope names were wrong - that's
    the highest-risk unknown).

## Where the files live

Everything above is already sitting in Kareem's connected project folder,
which Claude Code should treat as the repo root:

```
/Users/kareem/hevy-api-integration/
├── hevy_client.py
├── README.md
├── HANDOFF.md          <- this file, delete or keep once done, your call
├── requirements.txt
├── .env.example
└── .gitignore
```

(`.env` does not exist yet - create it locally per step 2 above, it's
gitignored so it's safe.)

## One more thing

Kareem's separate "coaching assistant" chat is meant to eventually call into
`hevy_client.py` as a library (`from hevy_client import HevyClient`) to pull
his logs and write routines. Keep the public method names/signatures stable
once you've verified them, since that's the contract the other assistant
will code against.
