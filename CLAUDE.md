# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Python client + CLI (`hevy_client.py`) for the Hevy public API
(`https://api.hevyapp.com/v1`). It exists so both Kareem (via CLI) and a
separate coaching-assistant chat (via `from hevy_client import HevyClient`)
can pull workout history and read/write routines instead of manual CSV
exports. Requires a Hevy **Pro** account + API key.

## Commands

```bash
pip install -r requirements.txt      # only dep: requests
cp .env.example .env                 # then paste real HEVY_API_KEY

# CLI (see README for the full flag list)
python hevy_client.py --user-info
python hevy_client.py --pull-recent 10
python hevy_client.py --pull-range 2026-06-01 2026-07-14
python hevy_client.py --exercise-history <template_id>
python hevy_client.py <any-command> --out result.json   # write JSON to file
```

There is **no build step, linter, or test suite** — don't invent commands for
them. "Testing" here means running CLI commands against the live API and
inspecting the JSON.

## Architecture

- **`HevyClient` dataclass** — the whole client. `_request()` is the single
  choke point for every HTTP call (auth header, timeout, error raising).
  All public methods route through it. Non-2xx raises `HevyAPIError`
  (carries `status_code`, `url`, `body`); 429 is called out explicitly.
- **Pagination pattern** — list endpoints cap `pageSize` at `MAX_PAGE_SIZE`
  (10). Bulk helpers (`get_all_*`, `iter_all_workouts`) loop pages until
  `page >= page_count` or an empty batch, sleeping `REQUEST_PAUSE_SECONDS`
  (0.25s) between requests. Copy this exact loop shape for any new
  paginated endpoint.
- **CLI** — `build_arg_parser()` + `main()`. Each flag maps to one client
  method; results print as JSON (or `--out` to a file). To add a command:
  add the client method, add the argparse flag, add one `elif` branch in
  `main()`.
- **Config loading** — `_load_dotenv_if_present()` runs **only in the CLI
  path** (`main()`), not on import. Library callers must set `HEVY_API_KEY`
  in their own environment; `HevyClient()` reads it in `__post_init__` and
  raises `ValueError` if missing.

## API contract facts (verified live 2026-07-14 — do not re-derive from guesswork)

These are real Hevy quirks confirmed against a live account; the code depends
on them:

- `GET /workouts` envelope is `{page, page_count, workouts}`. `/routines`,
  `/exercise_templates`, `/body_measurements` follow the same shape under
  their own key.
- **`/routine_folders` returns folders under a `routines` key**, not
  `routine_folders` — a Hevy inconsistency handled in
  `get_all_routine_folders`.
- **`/exercise_history/{id}` is unpaginated and returns one row per logged
  set** (not per workout), under `{exercise_history: [...]}`.
- Write bodies wrap in `{"routine": {...}}` / `{"workout": {...}}`.
  `folder_id` (not `routine_folder_id`) is correct.
- **`PUT /routines/{id}` requires the full `exercises` array** (400 if
  omitted) and **rejects an empty `notes: ""` on an exercise** even though
  `POST` accepts it. `_sanitize_exercises()` strips empty notes on both
  paths — keep calling it from any new create/update method.
- **No delete or archive for routines** (`DELETE` and `POST .../archive`
  both 404). Routines can only be removed in the app.
- **Body measurements are weight-only.** `/body_measurements` records carry
  just `weight_kg` + date; `/user/info` has no height. Height/other
  measurements are NOT exposed by the API — don't add code paths that assume
  they exist.

## Export envelope

`--out` writes an `{exported_at, measurements, data}` envelope, bundling the
weight log + profile so each file is self-contained (`_build_export_payload`).
`--no-measurements` opts out; `--body-measurements`/`--user-info` are written
raw to avoid embedding measurements twice. If you add a command whose result
IS measurement data, add it to that skip-list in `main()`.

## Guardrails

- The public method names/signatures are a contract the coaching-assistant
  chat codes against — keep them stable once verified.
- Current scope is the **read-only / non-invasive** surface (pulling data).
  The write path (`create_routine`/`update_routine`/`create_workout`) exists
  and is smoke-tested but is a deliberately later phase — don't exercise it
  against the live account without explicit instruction, since it mutates
  the app and there's no programmatic cleanup.
