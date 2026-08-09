"""
F1 championship calculation engine — reference implementation of Section 4
of the design doc. Works identically for drivers and constructors: just pass
in the right current_points dict (driver totals or team totals).

This is meant as a clear, testable reference for the Swift port, not
necessarily the final production code.
"""

from dataclasses import dataclass


@dataclass
class PointsTable:
    race_points: list[int]     # e.g. [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
    sprint_points: list[int]   # e.g. [8, 7, 6, 5, 4, 3, 2, 1]
    fastest_lap_point: bool = False


def max_points_remaining(
    remaining_full_races: int,
    remaining_sprints: int,
    points_table: PointsTable,
    is_constructor: bool = False,
) -> int:
    """Section 4.1 / 4.6 — max points an entity could still score.
    For constructors, both cars can occupy the top two positions in the
    same race, so the ceiling is doubled — except the fastest-lap point,
    which is only awarded once per race regardless of team size."""
    per_race = max(points_table.race_points)
    per_sprint = max(points_table.sprint_points)

    if is_constructor:
        top_two_race = sorted(points_table.race_points, reverse=True)[:2]
        top_two_sprint = sorted(points_table.sprint_points, reverse=True)[:2]
        per_race = sum(top_two_race)
        per_sprint = sum(top_two_sprint)

    total = remaining_full_races * per_race + remaining_sprints * per_sprint
    if points_table.fastest_lap_point:
        total += remaining_full_races * 1  # never doubled, even for constructors
    return total


def forced_above(x_ceiling: int, rival_current_points: dict[str, int]) -> int:
    """Section 4.7 Step 2 — how many rivals are guaranteed to finish above X
    no matter what X does, given X's ceiling."""
    return sum(1 for pts in rival_current_points.values() if pts > x_ceiling)


def rival_projected_totals(
    rival_current_points: dict[str, int],
    events_completed: int,
    remaining_events: int,
) -> dict[str, float]:
    """Section 4.4 (strict/practical mode) — projects each rival forward
    using their own season-average scoring pace, instead of freezing them
    at their current points. Used as a drop-in replacement for
    rival_current_points in forced_above() to get the 'practically
    eliminated' check rather than the 'mathematically eliminated' one.
    Must always be shown alongside the standard check, clearly labeled as
    a projection, never as a replacement for it."""
    if events_completed <= 0:
        return dict(rival_current_points)
    return {
        rid: pts + (remaining_events * (pts / events_completed))
        for rid, pts in rival_current_points.items()
    }


def can_still_reach_top_p(x_ceiling: int, rival_current_points: dict[str, int], p: int) -> bool:
    """Section 4.7 Step 3a — is top-P still mathematically possible for X?"""
    return forced_above(x_ceiling, rival_current_points) < p


def already_guaranteed_top_p(x_current_points: int, rival_ceilings: dict[str, int], p: int) -> bool:
    """Section 4.7 Step 3b — has X already clinched at least Pth, even if X
    scores zero more points from here?"""
    rivals_that_could_still_pass = sum(1 for c in rival_ceilings.values() if c > x_current_points)
    return rivals_that_could_still_pass < p


def points_needed_to_guarantee(
    x_current_points: int, rival_ceilings: dict[str, int], p: int
) -> int | None:
    """Section 4.7 Step 4 — points X needs to score (on top of current) to
    guarantee at least Pth regardless of what every rival does.
    Returns None if X has already clinched top-P (needed = 0 in that case,
    but caller should check already_guaranteed_top_p separately for clarity)."""
    ceilings_desc = sorted(rival_ceilings.values(), reverse=True)
    if p > len(ceilings_desc):
        return 0  # fewer rivals than P — trivially guaranteed
    threshold = ceilings_desc[p - 1] + 1
    return max(0, threshold - x_current_points)


def is_guarantee_achievable(points_needed: int, max_remaining_for_x: int) -> bool:
    """Section 10 takeaway — flags the 'not yet mathematically decidable'
    state. If this is False, the UI must NOT show points_needed as a normal
    number — show a distinct 'not yet decidable' state instead."""
    return points_needed <= max_remaining_for_x


def slack_races(
    x_projected_max_points: int,
    threshold_for_p: int,
    max_points_per_race: int,
) -> int:
    """Section 4.9 — optimistic cushion: how many more scoreless races could
    X afford before elimination from P becomes locked in, assuming every
    rival ahead scores zero more from here. Always label this in the UI as
    a best case, not a forecast."""
    if max_points_per_race <= 0:
        return 0
    return max(0, (x_projected_max_points - threshold_for_p) // max_points_per_race)


def elimination_status(
    x_id: str,
    x_current_points: int,
    x_ceiling: int,
    rival_current_points: dict[str, int],
    rival_ceilings: dict[str, int],
    p: int,
) -> dict:
    """Convenience wrapper bundling the 4.7 checks for one entity/position
    pair, in the shape the app's UI needs for a single Position Ladder row."""
    still_possible = can_still_reach_top_p(x_ceiling, rival_current_points, p)
    guaranteed = already_guaranteed_top_p(x_current_points, rival_ceilings, p)
    needed = points_needed_to_guarantee(x_current_points, rival_ceilings, p)
    achievable_now = is_guarantee_achievable(needed, x_ceiling - x_current_points)

    if guaranteed:
        state = 'guaranteed'
    elif not still_possible:
        state = 'eliminated'
    elif not achievable_now:
        state = 'not_yet_decidable'
    else:
        state = 'contested'

    return {
        'entity_id': x_id,
        'position': p,
        'state': state,             # guaranteed | eliminated | not_yet_decidable | contested
        'points_needed': needed if achievable_now else None,
    }


def elimination_status_both_modes(
    x_id: str,
    x_current_points: int,
    x_ceiling: int,
    rival_current_points: dict[str, int],
    rival_ceilings: dict[str, int],
    p: int,
    events_completed: int,
    remaining_events: int,
) -> dict:
    """Section 4.4 — bundles the media-standard ('mathematically alive') and
    strict/practical ('realistically alive') checks for one row, exactly
    as the UI must show them: both together, never just one."""
    mathematical = elimination_status(
        x_id, x_current_points, x_ceiling, rival_current_points, rival_ceilings, p
    )
    projected_rivals = rival_projected_totals(rival_current_points, events_completed, remaining_events)
    practical_forced_above = sum(1 for pts in projected_rivals.values() if pts > x_ceiling)
    practical_state = 'eliminated' if practical_forced_above >= p else mathematical['state']

    return {
        **mathematical,
        'practical_state': practical_state,  # eliminated | (falls back to mathematical state otherwise)
    }


def update_elimination_log(
    existing_log: dict[str, dict[int, int]],
    x_id: str,
    p: int,
    current_race_index: int,
    is_eliminated_now: bool,
) -> dict[str, dict[int, int]]:
    """Section 4.8 — records the first race index at which entity X becomes
    eliminated from position P. Once set, never overwritten (monotonic)."""
    existing_log.setdefault(x_id, {})
    if is_eliminated_now and p not in existing_log[x_id]:
        existing_log[x_id][p] = current_race_index
    return existing_log


def countback_wins(a: dict, b: dict) -> int:
    """Section 4.5 / 12.4 — F1's actual tiebreaker rule: most wins, then
    most 2nd places, then most 3rd places, etc. Each argument is a
    DriverStanding/ConstructorStanding-shaped dict (Section 3) with
    'wins', 'podiums', 'secondPlaces', 'thirdPlaces'. Returns positive if
    a ranks above b, negative if b ranks above a, 0 if still fully tied.
    Only called when points are equal — points always take priority."""
    for key in ('wins', 'secondPlaces', 'thirdPlaces'):
        diff = a.get(key, 0) - b.get(key, 0)
        if diff != 0:
            return diff
    return 0

