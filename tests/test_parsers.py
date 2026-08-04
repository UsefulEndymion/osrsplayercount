"""Parser tests for rs_tracker.

The fixture tests pin the parsers against a saved copy of each page (see
tests/fixtures/README.md). The synthetic tests pin the contract itself — the
edge cases that are easy to break and hard to notice, because a broken parser
returns [] and the tracker just logs a warning.
"""
import os
import unittest

from rs_tracker import parse_osrs_count, parse_world_data

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')

# Facts about the 2026-08-04 capture. A fixture refresh will move these; that's
# the point — read the diff before updating them.
EXPECTED_WORLDS = 317
EXPECTED_FREE = 55
EXPECTED_MEMBERS = 262
EXPECTED_LOCATIONS = {
    'United States': 139,
    'United Kingdom': 71,
    'Germany': 63,
    'Australia': 30,
    'Singapore': 5,
    'Brazil': 4,
    'Japan': 3,
    'South Africa': 2,
}
EXPECTED_TOTAL_COUNT = 131884


def fixture(name):
    with open(os.path.join(FIXTURES, name), 'rb') as f:
        return f.read()


class SluFixtureTest(unittest.TestCase):
    """parse_world_data against the saved /slu page."""

    @classmethod
    def setUpClass(cls):
        cls.worlds = parse_world_data(fixture('slu.html'))

    def test_row_count(self):
        self.assertEqual(len(self.worlds), EXPECTED_WORLDS)

    def test_every_world_is_complete(self):
        for w in self.worlds:
            self.assertEqual(
                set(w), {'world_number', 'player_count', 'location', 'is_f2p', 'activity'})
            self.assertIsInstance(w['world_number'], int)
            self.assertIsInstance(w['player_count'], int)
            self.assertIsInstance(w['is_f2p'], bool)
            # Activity is legitimately '-' for most worlds, but location never blanks.
            self.assertTrue(w['location'], f"world {w['world_number']} has no location")

    def test_world_numbers_are_unique(self):
        numbers = [w['world_number'] for w in self.worlds]
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_counts_are_in_range(self):
        for w in self.worlds:
            self.assertGreaterEqual(w['player_count'], 0)
            self.assertLessEqual(w['player_count'], 2000)

    def test_type_split(self):
        free = sum(1 for w in self.worlds if w['is_f2p'])
        self.assertEqual(free, EXPECTED_FREE)
        self.assertEqual(len(self.worlds) - free, EXPECTED_MEMBERS)

    def test_locations(self):
        counts = {}
        for w in self.worlds:
            counts[w['location']] = counts.get(w['location'], 0) + 1
        self.assertEqual(counts, EXPECTED_LOCATIONS)

    def test_known_rows(self):
        by_number = {w['world_number']: w for w in self.worlds}

        self.assertEqual(by_number[93], {
            'world_number': 93, 'player_count': 69, 'location': 'United States',
            'is_f2p': True, 'activity': '750 skill total'})

        # PvP rows carry an extra class on the <tr>; the selector must still match.
        self.assertEqual(by_number[277], {
            'world_number': 277, 'player_count': 90, 'location': 'United States',
            'is_f2p': True, 'activity': 'PvP World - Free'})

        # World 296 has an empty player cell in this capture -> the full-world fallback.
        self.assertEqual(by_number[296]['player_count'], 2000)

    def test_activity_dash_is_preserved(self):
        # '-' is the no-activity bucket and must reach the DB as-is; the data dump
        # depends on it not being normalised away.
        self.assertIn('-', {w['activity'] for w in self.worlds})


class IndexFixtureTest(unittest.TestCase):
    """parse_osrs_count against the saved front page."""

    def test_total_count(self):
        self.assertEqual(
            parse_osrs_count(fixture('index.html').decode('utf-8')), EXPECTED_TOTAL_COUNT)


class WorldParserContractTest(unittest.TestCase):
    """Edge cases, on synthetic markup so they survive a fixture refresh."""

    def row(self, world='Old School 42', players='48 players',
            location='United Kingdom', type_='Members', activity='-'):
        return f"""
        <tr class='server-list__row'>
          <td><a class='server-list__world-link' href='#'>{world}</a></td>
          <td>{players}</td>
          <td>{location}</td>
          <td>{type_}</td>
          <td>{activity}</td>
        </tr>"""

    def parse_one(self, **kw):
        worlds = parse_world_data(f"<table>{self.row(**kw)}</table>")
        self.assertEqual(len(worlds), 1)
        return worlds[0]

    def test_full_world_has_no_number(self):
        # The server list omits the count when a world is full.
        self.assertEqual(self.parse_one(players='')['player_count'], 2000)

    def test_empty_world_reports_zero(self):
        self.assertEqual(self.parse_one(players='0 players')['player_count'], 0)

    def test_thousands_separator(self):
        self.assertEqual(self.parse_one(players='1,337 players')['player_count'], 1337)

    def test_free_detection_is_case_insensitive(self):
        self.assertTrue(self.parse_one(type_='FREE')['is_f2p'])
        self.assertFalse(self.parse_one(type_='Members')['is_f2p'])

    def test_rows_without_a_world_link_are_skipped(self):
        html = "<table><tr class='server-list__row'><td>x</td><td>1</td>" \
               "<td>a</td><td>b</td><td>c</td></tr></table>"
        self.assertEqual(parse_world_data(html), [])

    def test_short_rows_are_skipped(self):
        html = "<table><tr class='server-list__row'><td>x</td><td>1</td></tr></table>"
        self.assertEqual(parse_world_data(html), [])

    def test_unrelated_tables_are_ignored(self):
        html = f"<table><tr><td>not a world</td></tr>{self.row()}</table>"
        self.assertEqual(len(parse_world_data(html)), 1)

    def test_empty_page(self):
        self.assertEqual(parse_world_data(''), [])


class CountParserContractTest(unittest.TestCase):

    def test_both_phrasings(self):
        self.assertEqual(parse_osrs_count('<p>101,655 people playing</p>'), 101655)
        self.assertEqual(parse_osrs_count('<p>101,655 players online</p>'), 101655)

    def test_case_and_spacing(self):
        self.assertEqual(parse_osrs_count('99 PEOPLE PLAYING'), 99)
        self.assertEqual(parse_osrs_count('99people playing'), 99)

    def test_missing_count(self):
        self.assertIsNone(parse_osrs_count('<html>maintenance</html>'))
        self.assertIsNone(parse_osrs_count(''))


@unittest.skipUnless(os.environ.get('RUN_LIVE_SCRAPE_TESTS'),
                     'set RUN_LIVE_SCRAPE_TESTS=1 to hit the real site')
class LiveScrapeTest(unittest.TestCase):
    """Opt-in canary: does the parser still work against the pages as they are now?

    The fixtures can't tell you Jagex changed their markup — only this can. Kept
    out of the default run so tests stay offline and deterministic.
    """

    def test_world_list_still_parses(self):
        from rs_tracker import get_world_data
        worlds = get_world_data()
        self.assertGreater(len(worlds), 150, 'server list parsed to implausibly few worlds')
        self.assertTrue(any(w['is_f2p'] for w in worlds))
        self.assertTrue(any(not w['is_f2p'] for w in worlds))
        self.assertTrue(any(w['player_count'] > 0 for w in worlds))

    def test_total_count_still_parses(self):
        from rs_tracker import get_osrs_count
        count = get_osrs_count()
        self.assertIsNotNone(count, 'front page count no longer matches the regex')
        self.assertGreater(count, 1000)


if __name__ == '__main__':
    unittest.main()
