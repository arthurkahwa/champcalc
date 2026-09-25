"""
Tests for update_season.validate_season — the pre-commit schema gate that
keeps the pipeline from publishing season JSON the app can't decode.

    python3 -m unittest discover -s scripts
"""

import copy
import json
import unittest
from pathlib import Path

from update_season import compute_ladder, parse_existing, validate_season


def valid_season() -> dict:
    return {
        'year': 2026,
        'seasonStatus': 'live',
        'drivers': [{'id': 'norris', 'name': 'Lando Norris', 'teamId': 'mclaren'}],
        'teams': [{'id': 'mclaren', 'name': 'McLaren', 'colorHex': '#FF8000'}],
        'pointsTable': {'racePoints': [25, 18], 'sprintPoints': [8, 7], 'fastestLapPoint': False},
        'calendar': [
            {
                'id': 'r1', 'name': 'Australian Grand Prix', 'round': 1, 'date': '2026-03-08',
                'hasSprint': False, 'completed': True, 'country': 'Australia',
                'raceResult': [
                    {'driverId': 'norris', 'teamId': 'mclaren', 'position': 1, 'points': 25, 'dnf': False},
                    {'driverId': 'x', 'teamId': 'mclaren', 'position': None, 'points': 0, 'dnf': True},
                ],
                'sprintResult': None,
            },
        ],
        # Pipeline bookkeeping the app ignores — must not trip validation.
        'pointsHash': 'abc',
        'lastUpdatedRound': 1,
    }


class ValidateSeasonTests(unittest.TestCase):
    def test_valid_season_has_no_problems(self):
        self.assertEqual(validate_season(valid_season()), [])

    def test_committed_season_file_is_valid(self):
        path = Path(__file__).parent.parent / 'data' / 'season-2026.json'
        self.assertEqual(validate_season(json.loads(path.read_text())), [])

    def test_missing_calendar_country_is_reported(self):
        # The real regression: the pipeline once dropped `country`, which
        # made the whole payload undecodable in the app.
        season = valid_season()
        del season['calendar'][0]['country']
        problems = validate_season(season)
        self.assertEqual(len(problems), 1)
        self.assertIn('calendar[0].country', problems[0])

    def test_wrong_types_are_reported(self):
        season = valid_season()
        season['calendar'][0]['round'] = '1'
        season['calendar'][0]['completed'] = 1          # int is not a bool
        season['pointsTable']['racePoints'] = [25, True]  # bool is not an int
        problems = validate_season(season)
        self.assertEqual(len(problems), 3, problems)

    def test_missing_top_level_and_nested_keys_are_reported(self):
        for mutate, where in [
            (lambda s: s.pop('teams'), 'teams'),
            (lambda s: s['drivers'][0].pop('teamId'), 'drivers[0].teamId'),
            (lambda s: s['teams'][0].pop('colorHex'), 'teams[0].colorHex'),
            (lambda s: s['pointsTable'].pop('fastestLapPoint'), 'pointsTable.fastestLapPoint'),
            (lambda s: s['calendar'][0]['raceResult'][0].pop('dnf'), 'calendar[0].raceResult[0].dnf'),
        ]:
            season = copy.deepcopy(valid_season())
            mutate(season)
            problems = validate_season(season)
            self.assertTrue(any(where in p for p in problems), (where, problems))

    def test_unknown_season_status_is_reported(self):
        season = valid_season()
        season['seasonStatus'] = 'paused'
        self.assertTrue(any('seasonStatus' in p for p in validate_season(season)))

    def test_result_position_may_be_absent_or_null(self):
        season = valid_season()
        del season['calendar'][0]['raceResult'][0]['position']
        self.assertEqual(validate_season(season), [])

    def test_empty_rosters_are_reported(self):
        season = valid_season()
        season['drivers'] = []
        self.assertTrue(any('drivers' in p for p in validate_season(season)))


class EliminationLogRoundTripTests(unittest.TestCase):
    """The committed JSON turns the log's int position keys into strings.
    Reading them back as strings made every later run re-report old
    eliminations and then crash on json.dumps(sort_keys=True) with mixed
    int/str keys — the pipeline silently stopped publishing after Round 13."""

    POINTS = {'a': 100, 'b': 90, 'c': 0}

    def _first_run_season(self) -> dict:
        result = compute_ladder(self.POINTS, 0, 0, False, {}, 13)
        self.assertTrue(result['new_events'])  # 'b' is out of P1, 'c' of P1/P2
        committed = json.dumps({'eliminationLog': {'drivers': result['eliminationLog']}}, sort_keys=True)
        return parse_existing(json.loads(committed))

    def test_positions_are_ints_after_loading(self):
        log = self._first_run_season()['eliminationLog']['drivers']
        self.assertEqual(log, {'b': {1: 13}, 'c': {1: 13, 2: 13}})

    def test_known_eliminations_are_not_reported_again(self):
        log = self._first_run_season()['eliminationLog']['drivers']
        result = compute_ladder(self.POINTS, 0, 0, False, log, 14)
        self.assertEqual([e for e in result['new_events'] if e['event'] == 'eliminated'], [])
        self.assertEqual(result['eliminationLog'], {'b': {1: 13}, 'c': {1: 13, 2: 13}})

    def test_updated_log_can_be_written_with_sorted_keys(self):
        log = self._first_run_season()['eliminationLog']['drivers']
        result = compute_ladder(self.POINTS, 0, 0, False, log, 14)
        json.dumps(result['eliminationLog'], sort_keys=True)


if __name__ == '__main__':
    unittest.main()
