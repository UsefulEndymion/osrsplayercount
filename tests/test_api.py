"""API tests for osrs_api, against a synthetic database.

These pin the response shapes the frontend depends on: the two branches of
/api/history (global `players` vs. filtered `world_data`), the imported-era
splice, and the aligned-series contract of /api/history/grouped. They exist so
those endpoints can be refactored without guessing at what changed.

The fixture DB is tiny and deterministic — 6 scrapes, 4 worlds — so expected
values are written out by hand rather than computed the same way the code does.
"""
import os
import sqlite3
import tempfile
import unittest

import database


# 6 scrapes, 10 minutes apart. Kept well in the past so no test depends on `now`.
SCRAPE_TIMES = [
    '2026-01-05T00:00:00Z',
    '2026-01-05T00:10:00Z',
    '2026-01-05T00:20:00Z',
    '2026-01-05T01:00:00Z',
    '2026-01-05T01:10:00Z',
    '2026-01-06T00:00:00Z',
]

# Two of these are one family, so a group selection sends both ids and has to
# sum them; `-` stays the no-activity bucket.
ACTIVITIES = {
    1: '-',
    2: 'Castle Wars 1',
    3: 'Castle Wars - Free',
    4: 'Guardians of the Rift',
}

# world -> (location_id, is_f2p, activity_id, player counts across the 6 scrapes)
WORLDS = {
    301: (1, 0, 2, [100, 110, 120, 130, 140, 150]),
    302: (1, 1, 3, [10, 20, 30, 40, 50, 60]),
    303: (2, 0, 4, [200, 210, 220, 230, 240, 250]),
    304: (2, 1, 1, [5, 5, 5, 5, 5, 5]),
}

# Global `players` samples — deliberately NOT the sum of the world counts, so a
# test that reads the wrong table fails loudly instead of coincidentally passing.
PLAYER_COUNTS = [1000, 1100, 1200, 1300, 1400, 1500]


def build_db(path):
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE players (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME, count INTEGER);
        CREATE TABLE locations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE);
        CREATE TABLE activities (id INTEGER PRIMARY KEY AUTOINCREMENT, description TEXT UNIQUE);
        CREATE TABLE world_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT, location_id INTEGER, is_f2p BOOLEAN,
            activity_id INTEGER, UNIQUE(location_id, is_f2p, activity_id));
        CREATE TABLE scrape_events (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME UNIQUE);
        CREATE TABLE world_data (
            scrape_id INTEGER, world_number INTEGER, player_count INTEGER, detail_id INTEGER,
            PRIMARY KEY (scrape_id, world_number)) WITHOUT ROWID;
        CREATE TABLE history_import (
            period_start DATETIME, period_end DATETIME, stat TEXT, count INTEGER, source TEXT,
            PRIMARY KEY (period_start, stat, source));
    ''')

    conn.executemany('INSERT INTO locations (id, name) VALUES (?, ?)',
                     [(1, 'United States'), (2, 'Germany')])
    conn.executemany('INSERT INTO activities (id, description) VALUES (?, ?)',
                     sorted(ACTIVITIES.items()))

    # One world_details row per (location, f2p, activity) triple the worlds use.
    detail_ids = {}
    for world, (loc, f2p, activity, _) in WORLDS.items():
        key = (loc, f2p, activity)
        if key not in detail_ids:
            cur = conn.execute(
                'INSERT INTO world_details (location_id, is_f2p, activity_id) '
                'VALUES (?, ?, ?)', key)
            detail_ids[key] = cur.lastrowid

    for i, ts in enumerate(SCRAPE_TIMES, start=1):
        conn.execute('INSERT INTO scrape_events (id, timestamp) VALUES (?, ?)', (i, ts))
        conn.execute('INSERT INTO players (timestamp, count) VALUES (?, ?)',
                     (ts, PLAYER_COUNTS[i - 1]))
        for world, (loc, f2p, activity, counts) in WORLDS.items():
            conn.execute(
                'INSERT INTO world_data (scrape_id, world_number, player_count, detail_id) '
                'VALUES (?, ?, ?, ?)', (i, world, counts[i - 1], detail_ids[(loc, f2p, activity)]))

    conn.commit()
    conn.close()


class ApiTestCase(unittest.TestCase):
    """Points osrs_api at a temp DB and gives each test a Flask test client."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls._tmp.name, 'test.db')
        build_db(cls.db_path)

        # get_db_connection reads database.DB_PATH at call time, and osrs_api
        # calls through to it, so patching the one global covers both.
        cls._real_db_path = database.DB_PATH
        database.DB_PATH = cls.db_path

        import osrs_api
        cls.osrs_api = osrs_api
        osrs_api.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        database.DB_PATH = cls._real_db_path
        cls._tmp.cleanup()

    def setUp(self):
        self.client = self.osrs_api.app.test_client()

    def get(self, path):
        resp = self.client.get(path)
        return resp.status_code, resp.get_json()

    def clear_imports(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute('DELETE FROM history_import')
        conn.commit()
        conn.close()


class LatestTest(ApiTestCase):
    """/api/latest and its F2P/members breakdown."""

    def test_latest_is_the_final_scrape(self):
        status, body = self.get('/api/latest')
        self.assertEqual(status, 200)
        self.assertEqual(body['timestamp'], SCRAPE_TIMES[-1])
        self.assertEqual(body['count'], 1500)

    def test_breakdown_splits_by_f2p(self):
        _, body = self.get('/api/latest')
        # Final scrape: f2p worlds 302 + 304 = 60 + 5; members 301 + 303 = 150 + 250.
        self.assertEqual(body['f2p_count'], 65)
        self.assertEqual(body['members_count'], 400)
        self.assertEqual(body['breakdown_timestamp'], SCRAPE_TIMES[-1])


class HistoryGlobalTest(ApiTestCase):
    """/api/history against the `players` table — the no-filter branch."""

    def setUp(self):
        super().setUp()
        self.clear_imports()

    def test_raw_rows_are_ascending(self):
        status, body = self.get('/api/history?start=2026-01-05T00:00:00Z&end=2026-01-06T00:00:00Z')
        self.assertEqual(status, 200)
        self.assertEqual([r['timestamp'] for r in body], SCRAPE_TIMES)
        self.assertEqual([r['count'] for r in body], PLAYER_COUNTS)

    def test_limit_returns_the_most_recent_n_still_ascending(self):
        _, body = self.get('/api/history?limit=2')
        self.assertEqual([r['count'] for r in body], [1400, 1500])

    def test_hourly_max(self):
        _, body = self.get('/api/history?unit=hour&start=2026-01-05T00:00:00Z&end=2026-01-05T23:59:59Z')
        self.assertEqual(body, [
            {'timestamp': '2026-01-05T00:00:00Z', 'count': 1200},
            {'timestamp': '2026-01-05T01:00:00Z', 'count': 1400},
        ])

    def test_hourly_avg_rounds(self):
        _, body = self.get(
            '/api/history?unit=hour&agg=avg&start=2026-01-05T00:00:00Z&end=2026-01-05T23:59:59Z')
        # (1000+1100+1200)/3 = 1100, (1300+1400)/2 = 1350
        self.assertEqual([r['count'] for r in body], [1100, 1350])

    def test_range_filters_are_inclusive(self):
        _, body = self.get('/api/history?start=2026-01-05T00:10:00Z&end=2026-01-05T01:00:00Z')
        self.assertEqual([r['count'] for r in body], [1100, 1200, 1300])

    def test_minute_span_over_30_days_is_rejected(self):
        status, body = self.get(
            '/api/history?unit=minute&step=5&start=2026-01-01T00:00:00Z&end=2026-06-01T00:00:00Z')
        self.assertEqual(status, 400)
        self.assertIn('30 days', body['error'])


class HistoryImportedEraTest(ApiTestCase):
    """The imported weekly series is prepended, and the native query is floored."""

    def setUp(self):
        super().setUp()
        conn = sqlite3.connect(self.db_path)
        conn.execute('DELETE FROM history_import')
        conn.executemany(
            'INSERT INTO history_import (period_start, period_end, stat, count, source) '
            'VALUES (?, ?, ?, ?, ?)',
            [('2025-12-15T00:00:00Z', '2025-12-22T00:00:00Z', 'peak', 900, 'mi'),
             ('2025-12-15T00:00:00Z', '2025-12-22T00:00:00Z', 'avg', 800, 'mi'),
             ('2025-12-22T00:00:00Z', '2025-12-29T00:00:00Z', 'peak', 950, 'mi'),
             ('2025-12-22T00:00:00Z', '2025-12-29T00:00:00Z', 'avg', 850, 'mi')])
        conn.commit()
        conn.close()

    def tearDown(self):
        self.clear_imports()

    def test_imported_rows_precede_native_rows(self):
        _, body = self.get('/api/history?start=2025-12-01T00:00:00Z&end=2026-01-06T00:00:00Z')
        self.assertEqual([r['count'] for r in body], [900, 950] + PLAYER_COUNTS)

    def test_agg_avg_selects_the_avg_statistic(self):
        _, body = self.get('/api/history?agg=avg&start=2025-12-01T00:00:00Z&end=2026-01-06T00:00:00Z')
        self.assertEqual([r['count'] for r in body][:2], [800, 850])

    def test_bare_limit_call_skips_the_imported_era(self):
        _, body = self.get('/api/history?limit=2')
        self.assertEqual([r['count'] for r in body], [1400, 1500])


class HistoryWorldDataTest(ApiTestCase):
    """/api/history with a filter — the world_data branch."""

    def test_single_world_returns_its_own_counts(self):
        _, body = self.get(
            '/api/history?world_id=301&start=2026-01-05T00:00:00Z&end=2026-01-06T00:00:00Z')
        self.assertEqual([r['count'] for r in body], WORLDS[301][3])

    def test_location_sums_within_each_scrape(self):
        _, body = self.get(
            '/api/history?location_id=1&start=2026-01-05T00:00:00Z&end=2026-01-06T00:00:00Z')
        # Worlds 301 + 302 at each scrape.
        self.assertEqual([r['count'] for r in body], [110, 130, 150, 170, 190, 210])

    def test_f2p_filter_sums_only_f2p_worlds(self):
        _, body = self.get(
            '/api/history?is_f2p=1&start=2026-01-05T00:00:00Z&end=2026-01-06T00:00:00Z')
        # Worlds 302 + 304.
        self.assertEqual([r['count'] for r in body], [15, 25, 35, 45, 55, 65])

    def test_location_and_f2p_combine(self):
        _, body = self.get(
            '/api/history?location_id=2&is_f2p=0&start=2026-01-05T00:00:00Z&end=2026-01-06T00:00:00Z')
        self.assertEqual([r['count'] for r in body], WORLDS[303][3])

    def test_bucketing_aggregates_across_time_not_within_it(self):
        # Hourly max of a summed series: the sum happens per scrape first, then
        # MAX across the scrapes in the hour. Collapsing the two would give 450.
        _, body = self.get(
            '/api/history?location_id=1&unit=hour'
            '&start=2026-01-05T00:00:00Z&end=2026-01-05T23:59:59Z')
        self.assertEqual([r['count'] for r in body], [150, 190])

    def test_range_outside_all_scrapes_is_empty(self):
        status, body = self.get(
            '/api/history?world_id=301&start=2020-01-01T00:00:00Z&end=2020-01-02T00:00:00Z')
        self.assertEqual(status, 200)
        self.assertEqual(body, [])

    def test_single_activity_sums_only_its_worlds(self):
        _, body = self.get(
            '/api/history?activity_id=4&start=2026-01-05T00:00:00Z&end=2026-01-06T00:00:00Z')
        self.assertEqual([r['count'] for r in body], WORLDS[303][3])

    def test_grouped_activity_sums_every_member(self):
        # The Castle Wars family: worlds 301 + 302, sent as one comma-separated
        # value the way the dropdown does it.
        _, body = self.get(
            '/api/history?activity_id=2,3&start=2026-01-05T00:00:00Z&end=2026-01-06T00:00:00Z')
        self.assertEqual([r['count'] for r in body], [110, 130, 150, 170, 190, 210])

    def test_repeated_activity_id_params_are_equivalent_to_a_list(self):
        _, body = self.get(
            '/api/history?activity_id=2&activity_id=3'
            '&start=2026-01-05T00:00:00Z&end=2026-01-06T00:00:00Z')
        self.assertEqual([r['count'] for r in body], [110, 130, 150, 170, 190, 210])

    def test_activity_and_f2p_combine(self):
        # Castle Wars is two worlds, only one of which is F2P.
        _, body = self.get(
            '/api/history?activity_id=2,3&is_f2p=1'
            '&start=2026-01-05T00:00:00Z&end=2026-01-06T00:00:00Z')
        self.assertEqual([r['count'] for r in body], WORLDS[302][3])

    def test_unmatched_activity_is_empty_not_an_error(self):
        # What a retired seasonal activity looks like: a valid id, no rows.
        status, body = self.get(
            '/api/history?activity_id=999&start=2026-01-05T00:00:00Z&end=2026-01-06T00:00:00Z')
        self.assertEqual(status, 200)
        self.assertEqual(body, [])

    def test_unparseable_activity_id_drops_the_filter(self):
        # Matches how `type=int` treats location_id: a bad value is no filter.
        _, body = self.get(
            '/api/history?activity_id=abc&start=2026-01-05T00:00:00Z&end=2026-01-06T00:00:00Z')
        self.assertEqual([r['count'] for r in body], PLAYER_COUNTS)

    def test_too_many_activity_ids_is_rejected(self):
        ids = ','.join(str(n) for n in range(500))
        status, body = self.get(f'/api/history?activity_id={ids}')
        self.assertEqual(status, 400)
        self.assertIn('activity_id', body['error'])


class HistoryGroupedTest(ApiTestCase):
    """/api/history/grouped — the aligned timestamps/series contract."""

    RANGE = 'start=2026-01-05T00:00:00Z&end=2026-01-05T23:59:59Z'

    def test_unit_is_required(self):
        status, body = self.get(f'/api/history/grouped?{self.RANGE}')
        self.assertEqual(status, 400)
        self.assertIn('unit', body['error'])

    def test_unknown_group_by_is_rejected(self):
        status, body = self.get(f'/api/history/grouped?group_by=nope&unit=hour&{self.RANGE}')
        self.assertEqual(status, 400)
        self.assertIn('group_by', body['error'])

    def test_start_after_end_is_rejected(self):
        status, _ = self.get(
            '/api/history/grouped?unit=hour&start=2026-02-01T00:00:00Z&end=2026-01-01T00:00:00Z')
        self.assertEqual(status, 400)

    def test_group_by_world_gives_one_series_per_world(self):
        status, body = self.get(f'/api/history/grouped?group_by=world&unit=hour&{self.RANGE}')
        self.assertEqual(status, 200)
        self.assertEqual(body['timestamps'],
                         ['2026-01-05T00:00:00Z', '2026-01-05T01:00:00Z'])
        self.assertEqual([s['key'] for s in body['series']], [301, 302, 303, 304])
        by_key = {s['key']: s['counts'] for s in body['series']}
        self.assertEqual(by_key[301], [120, 140])
        self.assertEqual(by_key[304], [5, 5])

    def test_group_by_location_sums_within_each_scrape(self):
        _, body = self.get(f'/api/history/grouped?group_by=location&unit=hour&{self.RANGE}')
        by_key = {s['key']: s['counts'] for s in body['series']}
        self.assertEqual(by_key[1], [150, 190])   # worlds 301 + 302
        self.assertEqual(by_key[2], [225, 245])   # worlds 303 + 304

    def test_group_by_f2p_sums_within_each_scrape(self):
        _, body = self.get(f'/api/history/grouped?group_by=f2p&unit=hour&{self.RANGE}')
        by_key = {s['key']: s['counts'] for s in body['series']}
        self.assertEqual(by_key[0], [340, 380])   # worlds 301 + 303
        self.assertEqual(by_key[1], [35, 55])     # worlds 302 + 304

    def test_every_series_is_aligned_to_timestamps(self):
        _, body = self.get(f'/api/history/grouped?group_by=world&unit=hour&{self.RANGE}')
        for s in body['series']:
            self.assertEqual(len(s['counts']), len(body['timestamps']), s['key'])

    def test_filters_narrow_the_series_set(self):
        _, body = self.get(
            f'/api/history/grouped?group_by=world&unit=hour&is_f2p=1&{self.RANGE}')
        self.assertEqual([s['key'] for s in body['series']], [302, 304])

    def test_activity_filter_narrows_the_series_set(self):
        _, body = self.get(
            f'/api/history/grouped?group_by=world&unit=hour&activity_id=2,3&{self.RANGE}')
        self.assertEqual([s['key'] for s in body['series']], [301, 302])

    def test_activity_filter_applies_to_a_summed_grouping(self):
        # group_by=location already joins world_details; the activity clause has
        # to land on that same join rather than a second one.
        _, body = self.get(
            f'/api/history/grouped?group_by=location&unit=hour&activity_id=2,3&{self.RANGE}')
        by_key = {s['key']: s['counts'] for s in body['series']}
        self.assertEqual(list(by_key), [1])
        self.assertEqual(by_key[1], [150, 190])

    def test_too_many_activity_ids_is_rejected(self):
        ids = ','.join(str(n) for n in range(500))
        status, body = self.get(
            f'/api/history/grouped?group_by=world&unit=hour&activity_id={ids}&{self.RANGE}')
        self.assertEqual(status, 400)
        self.assertIn('activity_id', body['error'])

    def test_range_outside_all_scrapes_is_empty(self):
        status, body = self.get(
            '/api/history/grouped?group_by=world&unit=hour'
            '&start=2020-01-01T00:00:00Z&end=2020-01-02T00:00:00Z')
        self.assertEqual(status, 200)
        self.assertEqual(body, {'timestamps': [], 'series': []})

    def test_over_the_point_cap_is_rejected(self):
        real = self.osrs_api.MAX_GROUPED_POINTS
        self.osrs_api.MAX_GROUPED_POINTS = 1
        try:
            status, body = self.get(f'/api/history/grouped?group_by=world&unit=hour&{self.RANGE}')
            self.assertEqual(status, 400)
            self.assertIn('Too many points', body['error'])
        finally:
            self.osrs_api.MAX_GROUPED_POINTS = real


class MetadataTest(ApiTestCase):
    def test_metadata_lists_locations_and_worlds(self):
        status, body = self.get('/api/metadata')
        self.assertEqual(status, 200)
        self.assertEqual(sorted(loc['name'] for loc in body['locations']),
                         ['Germany', 'United States'])
        self.assertEqual(sorted(body['worlds']), [301, 302, 303, 304])

    def test_world_data_start_is_the_first_scrape(self):
        # The floor the UI warns about: filtered queries cannot reach earlier.
        _, body = self.get('/api/metadata')
        self.assertEqual(body['world_data_start'], SCRAPE_TIMES[0])

    def test_activities_stay_raw(self):
        _, body = self.get('/api/metadata')
        self.assertEqual(sorted(a['description'] for a in body['activities']),
                         sorted(ACTIVITIES.values()))

    def test_activity_groups_collapse_families_and_drop_no_activity(self):
        _, body = self.get('/api/metadata')
        self.assertEqual(body['activity_groups'], [
            {'name': 'Castle Wars', 'ids': [2, 3]},
            {'name': 'Guardians of the Rift', 'ids': [4]},
        ])


if __name__ == '__main__':
    unittest.main()
