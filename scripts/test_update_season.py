"""
Tests for update_season.validate_season — the pre-commit schema gate that
keeps the pipeline from publishing season JSON the app can't decode.

    python3 -m unittest discover -s scripts
"""

import copy
import json
import unittest
from pathlib import Path

from update_season import validate_season


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


if __name__ == '__main__':
    unittest.main()
