"""
hevy_client.py
================
A small, dependency-light Python client for the Hevy public API
(https://api.hevyapp.com/docs/). Requires a Hevy Pro account and an API key
from https://hevy.com/settings?developer.

This module is meant to be imported by other tools (e.g. a coaching
assistant) OR run directly as a CLI for quick manual pulls/pushes.

SECURITY
--------
Never hardcode your API key in this file. The key is read from the
HEVY_API_KEY environment variable, which you should set via a local,
gitignored `.env` file (see .env.example) or your shell environment.

QUICK START
-----------
    pip install -r requirements.txt
    cp .env.example .env   # then paste your key into .env
    python hevy_client.py --pull-recent
    python hevy_client.py --pull-range 2026-06-01 2026-07-13
    python hevy_client.py --list-routines
    python hevy_client.py --list-exercise-templates --query "bench"

See README.md for the full command list and for how to use this as a
library (HevyClient class) from other Python code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import requests

BASE_URL = "https://api.hevyapp.com/v1"

# The API currently caps page size at 10 for /workouts and /routines list
# endpoints. Confirmed from the published OpenAPI spec.
MAX_PAGE_SIZE = 10

# Simple client-side throttle between paginated requests to be a good
# citizen of the API (Hevy has not published a formal rate limit as of
# this writing, but this avoids hammering the API during bulk pulls).
REQUEST_PAUSE_SECONDS = 0.25


class HevyAPIError(RuntimeError):
    """Raised when the Hevy API returns a non-2xx response."""

    def __init__(self, status_code: int, url: str, body: Any):
        self.status_code = status_code
        self.url = url
        self.body = body
        super().__init__(f"Hevy API error {status_code} on {url}: {body}")


@dataclass
class HevyClient:
    """Thin wrapper around the Hevy REST API.

    Parameters
    ----------
    api_key:
        Your Hevy Pro API key. If not given, it is read from the
        HEVY_API_KEY environment variable.
    session:
        Optional pre-configured `requests.Session` (mainly for testing).
    """

    api_key: Optional[str] = None
    session: Optional[requests.Session] = None

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.environ.get("HEVY_API_KEY")
        if not self.api_key:
            raise ValueError(
                "No Hevy API key found. Set the HEVY_API_KEY environment "
                "variable (e.g. via a local .env file) or pass api_key= "
                "explicitly. Get your key at "
                "https://hevy.com/settings?developer (Hevy Pro required)."
            )
        if self.session is None:
            self.session = requests.Session()
        self.session.headers.update(
            {
                "api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # low-level request helper
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> Any:
        url = f"{BASE_URL}{path}"
        resp = self.session.request(method, url, params=params, json=json_body, timeout=30)
        if resp.status_code == 429:
            raise HevyAPIError(
                429,
                url,
                "Rate limited by Hevy API. Back off and retry later. "
                f"Body: {resp.text}",
            )
        if not resp.ok:
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise HevyAPIError(resp.status_code, url, body)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ------------------------------------------------------------------
    # Workouts
    # ------------------------------------------------------------------
    def get_workouts_page(self, page: int = 1, page_size: int = MAX_PAGE_SIZE) -> dict:
        """Fetch a single page of workouts (newest first).

        page_size is capped at 10 by the API.
        """
        page_size = min(page_size, MAX_PAGE_SIZE)
        return self._request(
            "GET", "/workouts", params={"page": page, "pageSize": page_size}
        )

    def get_recent_workouts(self, count: int = 10) -> list[dict]:
        """Fetch the most recent `count` workouts (newest first)."""
        workouts: list[dict] = []
        page = 1
        while len(workouts) < count:
            data = self.get_workouts_page(page=page, page_size=MAX_PAGE_SIZE)
            batch = data.get("workouts", [])
            if not batch:
                break
            workouts.extend(batch)
            if page >= data.get("page_count", page):
                break
            page += 1
            time.sleep(REQUEST_PAUSE_SECONDS)
        return workouts[:count]

    def iter_all_workouts(self) -> Iterable[dict]:
        """Generator that yields every workout on the account, newest first,
        paginating automatically. Use with caution on very large histories.
        """
        page = 1
        while True:
            data = self.get_workouts_page(page=page, page_size=MAX_PAGE_SIZE)
            batch = data.get("workouts", [])
            if not batch:
                return
            yield from batch
            page_count = data.get("page_count", page)
            if page >= page_count:
                return
            page += 1
            time.sleep(REQUEST_PAUSE_SECONDS)

    def get_workouts_in_range(
        self, start_date: datetime, end_date: datetime
    ) -> list[dict]:
        """Fetch all workouts whose start time falls within [start_date, end_date].

        The Hevy API has no native date-range filter for /v1/workouts, so this
        paginates newest-first and stops early once workouts fall before
        start_date (safe because the API returns workouts newest to oldest).

        Dates should be timezone-aware; naive datetimes are assumed UTC.
        """
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        matched: list[dict] = []
        for workout in self.iter_all_workouts():
            started_at = _parse_hevy_datetime(workout.get("start_time"))
            if started_at is None:
                continue
            if started_at > end_date:
                # newer than our window, keep going (older ones are further pages)
                continue
            if started_at < start_date:
                # We've paged past the start of the window; since results are
                # newest-first, everything after this is even older. Stop.
                break
            matched.append(workout)
        return matched

    def get_workout(self, workout_id: str) -> dict:
        return self._request("GET", f"/workouts/{workout_id}")

    def get_workout_events(
        self, page: int = 1, page_size: int = MAX_PAGE_SIZE, since: Optional[str] = None
    ) -> dict:
        """Fetch the sync-event feed (updates/deletes), newest first.

        `since` is an optional ISO8601 timestamp to only return events after
        that time. The response envelope is
        ``{"page": n, "page_count": n, "workouts": [...]}`` (events live under
        the `workouts` key). Verified against the live API 2026-07-14.
        """
        params: dict = {"page": page, "pageSize": min(page_size, MAX_PAGE_SIZE)}
        if since:
            params["since"] = since
        return self._request("GET", "/workouts/events", params=params)

    def get_workout_count(self) -> int:
        data = self._request("GET", "/workouts/count")
        return data.get("workout_count", 0)

    def create_workout(self, workout: dict) -> dict:
        """Create a new logged workout. `workout` should match the Hevy
        workout schema, e.g.:

            {
              "title": "Push Day",
              "start_time": "2026-07-13T22:00:00Z",
              "end_time": "2026-07-13T23:00:00Z",
              "exercises": [
                {
                  "exercise_template_id": "D04AC939",
                  "sets": [
                    {"type": "normal", "weight_kg": 60, "reps": 8}
                  ]
                }
              ]
            }
        """
        return self._request("POST", "/workouts", json_body={"workout": workout})

    # ------------------------------------------------------------------
    # Routines
    # ------------------------------------------------------------------
    def get_routines_page(self, page: int = 1, page_size: int = MAX_PAGE_SIZE) -> dict:
        page_size = min(page_size, MAX_PAGE_SIZE)
        return self._request(
            "GET", "/routines", params={"page": page, "pageSize": page_size}
        )

    def get_all_routines(self) -> list[dict]:
        routines: list[dict] = []
        page = 1
        while True:
            data = self.get_routines_page(page=page, page_size=MAX_PAGE_SIZE)
            batch = data.get("routines", [])
            if not batch:
                break
            routines.extend(batch)
            if page >= data.get("page_count", page):
                break
            page += 1
            time.sleep(REQUEST_PAUSE_SECONDS)
        return routines

    def get_routine(self, routine_id: str) -> dict:
        return self._request("GET", f"/routines/{routine_id}")

    def create_routine(
        self,
        title: str,
        exercises: list[dict],
        *,
        notes: str = "",
        folder_id: Optional[int] = None,
    ) -> dict:
        """Create a new routine.

        `exercises` is a list of dicts shaped like:
            {
              "exercise_template_id": "D04AC939",
              "superset_id": None,
              "rest_seconds": 90,
              "notes": "",
              "sets": [
                {"type": "normal", "weight_kg": 60, "reps": 8},
                {"type": "warmup", "weight_kg": 20, "reps": 12},
              ],
            }
        Set "type" must be one of: normal, warmup, failure, dropset.
        """
        body = {
            "routine": {
                "title": title,
                "notes": notes,
                "folder_id": folder_id,
                "exercises": _sanitize_exercises(exercises),
            }
        }
        return self._request("POST", "/routines", json_body=body)

    def update_routine(
        self,
        routine_id: str,
        *,
        title: Optional[str] = None,
        notes: Optional[str] = None,
        exercises: Optional[list[dict]] = None,
    ) -> dict:
        """Update an existing routine. Only include the fields you want to
        change; None values are omitted. Note: the Hevy API generally expects
        the *full* exercises list on update (it replaces, not merges), so if
        you're changing one exercise, pass the complete exercises array.
        """
        routine_body: dict = {}
        if title is not None:
            routine_body["title"] = title
        if notes is not None:
            routine_body["notes"] = notes
        if exercises is not None:
            routine_body["exercises"] = _sanitize_exercises(exercises)
        return self._request(
            "PUT", f"/routines/{routine_id}", json_body={"routine": routine_body}
        )

    # ------------------------------------------------------------------
    # Routine folders
    # ------------------------------------------------------------------
    def get_routine_folders(self, page: int = 1, page_size: int = MAX_PAGE_SIZE) -> dict:
        return self._request(
            "GET", "/routine_folders", params={"page": page, "pageSize": page_size}
        )

    def get_all_routine_folders(self) -> list[dict]:
        """Return every routine folder.

        NOTE: the Hevy API returns folders under a ``routines`` key (not
        ``routine_folders``) in the list envelope — a known API
        inconsistency. Verified against the live API 2026-07-14.
        """
        folders: list[dict] = []
        page = 1
        while True:
            data = self.get_routine_folders(page=page, page_size=MAX_PAGE_SIZE)
            batch = data.get("routines", [])
            if not batch:
                break
            folders.extend(batch)
            if page >= data.get("page_count", page):
                break
            page += 1
            time.sleep(REQUEST_PAUSE_SECONDS)
        return folders

    def create_routine_folder(self, title: str) -> dict:
        return self._request(
            "POST", "/routine_folders", json_body={"routine_folder": {"title": title}}
        )

    # ------------------------------------------------------------------
    # Body measurements (read-only helpers)
    # ------------------------------------------------------------------
    def get_body_measurements_page(
        self, page: int = 1, page_size: int = MAX_PAGE_SIZE
    ) -> dict:
        return self._request(
            "GET",
            "/body_measurements",
            params={"page": page, "pageSize": min(page_size, MAX_PAGE_SIZE)},
        )

    def get_all_body_measurements(self) -> list[dict]:
        """Return every body-measurement entry (weight, etc.), newest first.

        Envelope is ``{"page": n, "page_count": n, "body_measurements": [...]}``.
        Verified against the live API 2026-07-14.
        """
        measurements: list[dict] = []
        page = 1
        while True:
            data = self.get_body_measurements_page(page=page, page_size=MAX_PAGE_SIZE)
            batch = data.get("body_measurements", [])
            if not batch:
                break
            measurements.extend(batch)
            if page >= data.get("page_count", page):
                break
            page += 1
            time.sleep(REQUEST_PAUSE_SECONDS)
        return measurements

    # ------------------------------------------------------------------
    # Exercise templates
    # ------------------------------------------------------------------
    def get_exercise_templates_page(
        self, page: int = 1, page_size: int = MAX_PAGE_SIZE
    ) -> dict:
        return self._request(
            "GET",
            "/exercise_templates",
            params={"page": page, "pageSize": page_size},
        )

    def get_all_exercise_templates(self) -> list[dict]:
        templates: list[dict] = []
        page = 1
        while True:
            data = self.get_exercise_templates_page(page=page, page_size=MAX_PAGE_SIZE)
            batch = data.get("exercise_templates", [])
            if not batch:
                break
            templates.extend(batch)
            if page >= data.get("page_count", page):
                break
            page += 1
            time.sleep(REQUEST_PAUSE_SECONDS)
        return templates

    def find_exercise_template(self, query: str) -> list[dict]:
        """Client-side search by title substring (case-insensitive) across
        all exercise templates. Handy for looking up an exercise_template_id
        to use in create_routine/create_workout.
        """
        query_lower = query.lower()
        return [
            t
            for t in self.get_all_exercise_templates()
            if query_lower in (t.get("title") or "").lower()
        ]

    def get_exercise_template(self, exercise_template_id: str) -> dict:
        return self._request("GET", f"/exercise_templates/{exercise_template_id}")

    def get_exercise_history(self, exercise_template_id: str) -> list[dict]:
        """Return the full logged history for a single exercise, newest first.

        Returns a flat list where each entry is ONE logged set, carrying its
        parent workout context (workout_id/title/start/end) plus the set data
        (weight_kg, reps, distance_meters, duration_seconds, rpe,
        custom_metric, set_type). This endpoint is NOT paginated and returns
        the whole history in one call under an ``{"exercise_history": [...]}``
        envelope. Verified against the live API 2026-07-14.
        """
        data = self._request(
            "GET", f"/exercise_history/{exercise_template_id}"
        )
        return data.get("exercise_history", [])

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def get_user_info(self) -> dict:
        return self._request("GET", "/user/info")


def _sanitize_exercises(exercises: list[dict]) -> list[dict]:
    """Return a copy of `exercises` with empty-string `notes` removed.

    The Hevy API rejects an empty `notes` string on an exercise with a 400
    ("...notes" is not allowed to be empty) on PUT /routines/{id}, even though
    POST /routines accepts it. Dropping the key entirely is equivalent to
    "no note" and keeps create/update behavior consistent. Verified against
    the live API 2026-07-14.
    """
    cleaned: list[dict] = []
    for ex in exercises:
        ex = dict(ex)
        if ex.get("notes") == "":
            ex.pop("notes", None)
        cleaned.append(ex)
    return cleaned


def _parse_hevy_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        # Hevy returns ISO8601 with a trailing Z
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ==========================================================================
# CLI
# ==========================================================================
def _load_dotenv_if_present() -> None:
    """Minimal .env loader so we don't need python-dotenv as a hard dep.
    Only sets vars that aren't already in the environment.
    """
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


# Hevy's API exposes only weight in its body-measurement records. Height and
# other measurements (body fat, etc.) are NOT available via the public API as
# of 2026-07-14, so exports can carry the weight log but not height.
_MEASUREMENTS_NOTE = (
    "Hevy's public API exposes only weight_kg in body measurements; height "
    "and other measurements are not available via the API."
)


def _build_export_payload(client: "HevyClient", data: Any) -> dict:
    """Wrap a command result in a self-describing export envelope that also
    carries the account's measurement context (weight log + profile), so every
    exported file is portable on its own. Best-effort: if the measurement
    fetch fails, the export still succeeds with the error recorded inline.
    """
    measurements: dict = {"note": _MEASUREMENTS_NOTE}
    try:
        measurements["body_measurements"] = client.get_all_body_measurements()
    except HevyAPIError as e:
        measurements["body_measurements"] = None
        measurements["body_measurements_error"] = f"{e.status_code}: {e.body}"
    try:
        measurements["profile"] = client.get_user_info().get("data")
    except HevyAPIError as e:
        measurements["profile"] = None
        measurements["profile_error"] = f"{e.status_code}: {e.body}"

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "measurements": measurements,
        "data": data,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hevy API client CLI (recent workouts, date-range pulls, routine create/update).",
    )
    parser.add_argument(
        "--pull-recent",
        type=int,
        nargs="?",
        const=10,
        metavar="N",
        help="Fetch the N most recent workouts (default 10).",
    )
    parser.add_argument(
        "--pull-range",
        nargs=2,
        metavar=("START_DATE", "END_DATE"),
        help="Fetch workouts between START_DATE and END_DATE (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--workout", metavar="WORKOUT_ID", help="Fetch a single workout by id."
    )
    parser.add_argument(
        "--workout-count", action="store_true", help="Print total workout count."
    )
    parser.add_argument(
        "--list-routines", action="store_true", help="List all routines."
    )
    parser.add_argument(
        "--routine", metavar="ROUTINE_ID", help="Fetch a single routine by id."
    )
    parser.add_argument(
        "--create-routine",
        metavar="JSON_FILE",
        help="Create a routine from a JSON file (see README for schema).",
    )
    parser.add_argument(
        "--update-routine",
        nargs=2,
        metavar=("ROUTINE_ID", "JSON_FILE"),
        help="Update an existing routine from a JSON file.",
    )
    parser.add_argument(
        "--list-exercise-templates",
        action="store_true",
        help="List all exercise templates (paginated fetch of full catalog).",
    )
    parser.add_argument(
        "--query",
        metavar="TEXT",
        help="Used with --list-exercise-templates to filter by title substring.",
    )
    parser.add_argument(
        "--exercise-history",
        metavar="TEMPLATE_ID",
        help="Fetch full logged history for one exercise template id.",
    )
    parser.add_argument(
        "--body-measurements",
        action="store_true",
        help="Fetch all body-measurement entries (weight, etc.).",
    )
    parser.add_argument(
        "--list-routine-folders",
        action="store_true",
        help="List all routine folders.",
    )
    parser.add_argument(
        "--user-info", action="store_true", help="Print authenticated user info."
    )
    parser.add_argument(
        "--out",
        metavar="FILE",
        help="Write JSON output to FILE instead of (or in addition to) stdout.",
    )
    parser.add_argument(
        "--no-measurements",
        action="store_true",
        help="Exclude the body-measurement/profile context from --out exports "
        "(by default every export file includes it).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    _load_dotenv_if_present()
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        client = HevyClient()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    result: Any = None

    try:
        if args.pull_recent is not None:
            result = client.get_recent_workouts(count=args.pull_recent)
        elif args.pull_range:
            start_str, end_str = args.pull_range
            start = datetime.fromisoformat(start_str)
            end = datetime.fromisoformat(end_str)
            # A date-only end (e.g. "2026-07-14") parses to midnight, which
            # would exclude workouts logged later that same day. Treat a
            # date-only end as inclusive through end-of-day.
            if "T" not in end_str and end.time() == datetime.min.time():
                end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
            result = client.get_workouts_in_range(start, end)
        elif args.workout:
            result = client.get_workout(args.workout)
        elif args.workout_count:
            result = {"workout_count": client.get_workout_count()}
        elif args.list_routines:
            result = client.get_all_routines()
        elif args.routine:
            result = client.get_routine(args.routine)
        elif args.create_routine:
            with open(args.create_routine) as f:
                spec = json.load(f)
            result = client.create_routine(
                title=spec["title"],
                exercises=spec["exercises"],
                notes=spec.get("notes", ""),
                folder_id=spec.get("folder_id"),
            )
        elif args.update_routine:
            routine_id, json_file = args.update_routine
            with open(json_file) as f:
                spec = json.load(f)
            result = client.update_routine(
                routine_id,
                title=spec.get("title"),
                notes=spec.get("notes"),
                exercises=spec.get("exercises"),
            )
        elif args.list_exercise_templates:
            if args.query:
                result = client.find_exercise_template(args.query)
            else:
                result = client.get_all_exercise_templates()
        elif args.exercise_history:
            result = client.get_exercise_history(args.exercise_history)
        elif args.body_measurements:
            result = client.get_all_body_measurements()
        elif args.list_routine_folders:
            result = client.get_all_routine_folders()
        elif args.user_info:
            result = client.get_user_info()
        else:
            parser.print_help()
            return 0
    except HevyAPIError as e:
        print(f"Hevy API error ({e.status_code}): {e.body}", file=sys.stderr)
        return 1

    if args.out:
        # Every export file bundles measurement context by default so it's
        # self-contained; --no-measurements writes just the raw result.
        # (Skip wrapping when the command already IS the measurements, to
        # avoid fetching/embedding them twice.)
        if args.no_measurements or args.body_measurements or args.user_info:
            payload = result
        else:
            payload = _build_export_payload(client, result)
        with open(args.out, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        print(f"Wrote output to {args.out}")
    else:
        _print_json(result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
