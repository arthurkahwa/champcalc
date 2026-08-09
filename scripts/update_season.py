"""
Runs on a schedule via GitHub Actions (see .github/workflows/update-season.yml).
No manual intervention required during normal operation:

  1. Fetch current driver + constructor standings (which also carry each
     entity's name/current team — Jolpica hands these back in the same
     call, no extra request needed), the calendar, and the actual
     last-completed round from Jolpica-F1
  2. For every completed round, fetch its real per-round race (and sprint,
     where applicable) results — this is what lets the app recompute
     standings/ladder/scenarios client-side from raw results (Section 3),
     rather than reading a pre-aggregated points total
  3. Load the existing committed season-{year}.json
  4. If nothing changed, exit quietly (workflow will skip the commit step)
  5. If something changed: recompute derived fields (elimination log, ladder
     status per position) using f1_math.py — purely for the push-
     notification bundling below, the app itself recomputes its own ladder
     from the raw results — and write the updated JSON in the app's
     Season schema (Section 3): year, seasonStatus, drivers, teams,
     pointsTable, calendar
  6. Once the season's final round has results, mark the season 'final' and
     stop recomputing — the numbers are locked in permanently (git history
     already preserves every intermediate state, so nothing is lost)
  7. Notify at most once per completed race, regardless of how many times
     this script polls during that race weekend — never more
"""

import json
import hashlib
import os
import sys
import time
from pathlib import Path

import requests

from f1_math import (
    PointsTable,
    max_points_remaining,
    elimination_status,
    update_elimination_log,
)

JOLPICA_BASE = 'https://api.jolpi.ca/ergast/f1'
SEASON_YEAR = os.environ.get('SEASON_YEAR', '2026')
DATA_PATH = Path(__file__).parent.parent / 'data' / f'season-{SEASON_YEAR}.json'
NOTIFICATION_PAYLOAD_PATH = Path(__file__).parent.parent / 'data' / '.notification_payload.json'

# --- Points table lives here, not hardcoded elsewhere, per Section 2 ---
POINTS_TABLE = PointsTable(
    race_points=[25, 18, 15, 12, 10, 8, 6, 4, 2, 1],
    sprint_points=[8, 7, 6, 5, 4, 3, 2, 1],
    fastest_lap_point=False,
)

# --- Section 7: team colors aren't in any API (unlike lineups, the points
# table, or the calendar) — this is the one piece of season data that's
# legitimately hand-maintained, same as the permanent "no real photos/
# logos" rule. Unknown/new constructorIds fall back to DEFAULT_TEAM_COLOR
# rather than crashing the pipeline. ---
TEAM_COLORS: dict[str, str] = {
    'mercedes': '#00D2BE',
    'ferrari': '#DC0000',
    'mclaren': '#FF8000',
    'red_bull': '#1E41FF',
    'rb': '#6692FF',
    'alpine': '#0090FF',
    'haas': '#B6BABD',
    'audi': '#BB0A30',
    'williams': '#005AFF',
    'aston_martin': '#006F62',
    'cadillac': '#B4975A',
}
DEFAULT_TEAM_COLOR = '#8E8E93'


def fetch_json(path: str, retries: int = 3, backoff_seconds: float = 2.0) -> dict:
    """Section 12.6 — retry/backoff so a transient Jolpica outage doesn't
    silently skip an update cycle. Raises after exhausting retries; the
    workflow run fails loudly rather than committing incomplete data."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(f'{JOLPICA_BASE}/{path}', timeout=15)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(backoff_seconds * (2 ** attempt))
    raise RuntimeError(f'Failed to fetch {path} after {retries} attempts') from last_error


def points_hash(driver_points: dict[str, int], constructor_points: dict[str, int]) -> str:
    """Section 12.6 — hash-based change detection instead of a raw dict
    comparison, so key reordering or incidental JSON differences never
    trigger a false 'changed' signal."""
    payload = json.dumps(
        {'drivers': driver_points, 'constructors': constructor_points}, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def fetch_driver_standings() -> tuple[dict[str, int], dict[str, dict]]:
    """Returns (driverId -> points, driverId -> {name, teamId}). Roster
    info comes free in the same response Jolpica already returns for
    points — no extra request needed."""
    data = fetch_json(f'{SEASON_YEAR}/driverStandings.json')
    standings = data['MRData']['StandingsTable']['StandingsLists'][0]['DriverStandings']
    points: dict[str, int] = {}
    roster: dict[str, dict] = {}
    for row in standings:
        driver_id = row['Driver']['driverId']
        points[driver_id] = int(row['points'])
        name = f"{row['Driver'].get('givenName', '')} {row['Driver'].get('familyName', '')}".strip()
        team_id = row['Constructors'][0]['constructorId'] if row.get('Constructors') else ''
        roster[driver_id] = {'name': name, 'teamId': team_id}
    return points, roster


def fetch_constructor_standings() -> tuple[dict[str, int], dict[str, str]]:
    """Returns (constructorId -> points, constructorId -> name)."""
    data = fetch_json(f'{SEASON_YEAR}/constructorStandings.json')
    standings = data['MRData']['StandingsTable']['StandingsLists'][0]['ConstructorStandings']
    points: dict[str, int] = {}
    names: dict[str, str] = {}
    for row in standings:
        constructor_id = row['Constructor']['constructorId']
        points[constructor_id] = int(row['points'])
        names[constructor_id] = row['Constructor']['name']
    return points, names


def fetch_calendar() -> list[dict]:
    data = fetch_json(f'{SEASON_YEAR}.json')
    races = data['MRData']['RaceTable']['Races']
    return [
        {
            'round': int(r['round']),
            'name': r['raceName'],
            'date': r['date'],
            'has_sprint': 'Sprint' in r,
        }
        for r in races
    ]


def fetch_last_completed_round() -> int:
    """Uses Jolpica's 'last' shortcut to find the actual last round with
    results — NOT the same as the highest round number on the calendar,
    which just lists every scheduled race whether it's happened yet or not.
    This is what the once-per-race notification rate limit depends on."""
    data = fetch_json(f'{SEASON_YEAR}/last/results.json')
    races = data['MRData']['RaceTable']['Races']
    if not races:
        return 0
    return int(races[0]['round'])


def _is_dnf(status: str) -> bool:
    # Classified finishers are either "Finished" or lapped-but-classified
    # ("+1 Lap", "+2 Laps", ...); anything else (Retired, Accident,
    # Engine, Did not start, ...) is a DNF for our purposes.
    return not (status == 'Finished' or status.startswith('+'))


def _entries_to_results(entries: list[dict]) -> list[dict]:
    return [
        {
            'driverId': entry['Driver']['driverId'],
            # Team-of-record snapshot (Section 4.6) — the team this
            # specific result was scored for, not the driver's current
            # roster team, so a mid-season swap can't retroactively move
            # already-scored points.
            'teamId': entry['Constructor']['constructorId'],
            'position': int(entry['position']),
            'points': int(float(entry['points'])),
            'dnf': _is_dnf(entry.get('status', 'Finished')),
        }
        for entry in entries
    ]


def fetch_round_race_result(round_number: int) -> list[dict]:
    data = fetch_json(f'{SEASON_YEAR}/{round_number}/results.json')
    races = data['MRData']['RaceTable']['Races']
    if not races:
        return []
    return _entries_to_results(races[0]['Results'])


def fetch_round_sprint_result(round_number: int) -> list[dict]:
    data = fetch_json(f'{SEASON_YEAR}/{round_number}/sprint.json')
    races = data['MRData']['RaceTable']['Races']
    if not races:
        return []
    return _entries_to_results(races[0]['SprintResults'])


def remaining_events(calendar: list[dict], completed_rounds: int) -> tuple[int, int]:
    remaining = [r for r in calendar if r['round'] > completed_rounds]
    full_races = len(remaining)
    sprints = sum(1 for r in remaining if r['has_sprint'])
    return full_races, sprints


def load_existing() -> dict:
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text())
    return {
        'year': int(SEASON_YEAR),
        'seasonStatus': 'live',
        'lastNotifiedRound': 0,
        'eliminationLog': {'drivers': {}, 'constructors': {}},
    }


def compute_ladder(current_points: dict[str, int], remaining_races: int, remaining_sprints: int,
                    is_constructor: bool, elimination_log: dict, completed_rounds: int) -> dict:
    """Section 4.7/4.8, used only to detect newly-eliminated/newly-clinched
    entities for the bundled push-notification payload — the app itself
    recomputes its own Position Ladder client-side from the raw calendar
    results, so this ladder is not persisted into the season JSON."""
    n = len(current_points)
    new_events: list[dict] = []

    ceilings = {
        eid: pts + max_points_remaining(remaining_races, remaining_sprints, POINTS_TABLE, is_constructor)
        for eid, pts in current_points.items()
    }

    for eid, pts in current_points.items():
        rival_current = {k: v for k, v in current_points.items() if k != eid}
        rival_ceilings = {k: v for k, v in ceilings.items() if k != eid}
        for p in range(1, n):
            row = elimination_status(eid, pts, ceilings[eid], rival_current, rival_ceilings, p)
            if row['state'] == 'eliminated' and p not in elimination_log.get(eid, {}):
                new_events.append({'entity_id': eid, 'position': p, 'event': 'eliminated'})
                elimination_log = update_elimination_log(
                    elimination_log, eid, p, completed_rounds, True
                )
            if row['state'] == 'guaranteed' and p == 1:
                # Clinch (P=1) is the only "guaranteed" event worth a push —
                # guaranteeing lower positions happens too often to be newsworthy.
                new_events.append({'entity_id': eid, 'position': p, 'event': 'clinched'})

    return {'eliminationLog': elimination_log, 'new_events': new_events}


def build_calendar(calendar_meta: list[dict], completed_rounds: int) -> list[dict]:
    calendar: list[dict] = []
    for meta in calendar_meta:
        round_number = meta['round']
        is_completed = round_number <= completed_rounds
        race_result = None
        sprint_result = None
        if is_completed:
            race_result = fetch_round_race_result(round_number)
            time.sleep(0.3)  # be a polite API citizen — this is a burst of N calls, not one
            if meta['has_sprint']:
                sprint_result = fetch_round_sprint_result(round_number)
                time.sleep(0.3)
        calendar.append({
            'id': f'r{round_number}',
            'name': meta['name'],
            'round': round_number,
            'date': meta['date'],
            'hasSprint': meta['has_sprint'],
            'completed': is_completed,
            'raceResult': race_result,
            'sprintResult': sprint_result,
        })
    return calendar


def main() -> None:
    driver_points, driver_roster = fetch_driver_standings()
    constructor_points, constructor_names = fetch_constructor_standings()
    calendar_meta = fetch_calendar()
    total_rounds = len(calendar_meta)
    completed_rounds = fetch_last_completed_round()
    remaining_races, remaining_sprints = remaining_events(calendar_meta, completed_rounds)

    existing = load_existing()

    # --- Season finalization: once the last round is in, lock the numbers in
    # permanently. No further recomputation runs against this season after
    # this point — the committed JSON becomes the permanent historical record. ---
    if existing.get('seasonStatus') == 'final':
        print('Season already finalized — nothing to do.', file=sys.stderr)
        _set_github_output(changed=False, notify=False)
        return

    new_hash = points_hash(driver_points, constructor_points)
    changed = existing.get('pointsHash') != new_hash

    # --- Rate limit: notify at most once per completed race, full stop.
    # Gated purely on round advancement, not on how many times we've polled
    # this same race weekend (results can be provisional/revised for hours
    # after a race — we still only ever fire once for that round). ---
    is_new_race_result = completed_rounds > existing.get('lastNotifiedRound', 0)

    notify = False
    bundled_events: list[dict] = []

    if changed:
        driver_result = compute_ladder(
            driver_points, remaining_races, remaining_sprints, False,
            existing.get('eliminationLog', {}).get('drivers', {}), completed_rounds,
        )
        constructor_result = compute_ladder(
            constructor_points, remaining_races, remaining_sprints, True,
            existing.get('eliminationLog', {}).get('constructors', {}), completed_rounds,
        )

        if is_new_race_result:
            bundled_events = driver_result['new_events'] + constructor_result['new_events']
            notify = len(bundled_events) > 0

        season_status = 'final' if completed_rounds >= total_rounds and total_rounds > 0 else 'live'

        drivers = [
            {'id': driver_id, 'name': info['name'], 'teamId': info['teamId']}
            for driver_id, info in driver_roster.items()
        ]
        teams = [
            {'id': team_id, 'name': name, 'colorHex': TEAM_COLORS.get(team_id, DEFAULT_TEAM_COLOR)}
            for team_id, name in constructor_names.items()
        ]
        calendar = build_calendar(calendar_meta, completed_rounds)

        updated = {
            # --- Section 3 schema — what the app's Season decoder expects ---
            'year': int(SEASON_YEAR),
            'seasonStatus': season_status,
            'drivers': drivers,
            'teams': teams,
            'pointsTable': {
                'racePoints': POINTS_TABLE.race_points,
                'sprintPoints': POINTS_TABLE.sprint_points,
                'fastestLapPoint': POINTS_TABLE.fastest_lap_point,
            },
            'calendar': calendar,
            # --- Internal bookkeeping only — the app ignores unknown top-
            # level keys, so these ride along without affecting decoding ---
            'pointsHash': new_hash,
            'lastUpdatedRound': completed_rounds,
            'lastNotifiedRound': completed_rounds if is_new_race_result else existing.get('lastNotifiedRound', 0),
            'eliminationLog': {
                'drivers': driver_result['eliminationLog'],
                'constructors': constructor_result['eliminationLog'],
            },
        }
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_PATH.write_text(json.dumps(updated, indent=2, sort_keys=True))

        if notify:
            NOTIFICATION_PAYLOAD_PATH.write_text(json.dumps({
                'season': SEASON_YEAR,
                'round': completed_rounds,
                'events': bundled_events,
            }, indent=2))

    _set_github_output(changed=changed, notify=notify)
    print(f'changed={changed} notify={notify} round={completed_rounds}', file=sys.stderr)


def _set_github_output(changed: bool, notify: bool) -> None:
    github_output = os.environ.get('GITHUB_OUTPUT')
    if github_output:
        with open(github_output, 'a') as f:
            f.write(f'changed={"true" if changed else "false"}\n')
            f.write(f'notify={"true" if notify else "false"}\n')


if __name__ == '__main__':
    main()
