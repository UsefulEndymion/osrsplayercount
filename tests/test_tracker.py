"""Tests for the tracker's database write path.

parse_world_data is covered in test_parsers; this covers what happens to the
rows afterwards — the interning of locations, activities and world_details, and
the world_data rows they end up pointing at.
"""
import sqlite3
import unittest

from rs_tracker import _intern, _save_world_data


def world(number, count, location, f2p, activity):
    return {'world_number': number, 'player_count': count,
            'location': location, 'is_f2p': f2p, 'activity': activity}


class SaveWorldDataTest(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.executescript('''
            CREATE TABLE locations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE);
            CREATE TABLE activities (id INTEGER PRIMARY KEY AUTOINCREMENT, description TEXT UNIQUE);
            CREATE TABLE world_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT, location_id INTEGER, is_f2p BOOLEAN,
                activity_id INTEGER, UNIQUE(location_id, is_f2p, activity_id));
            CREATE TABLE scrape_events (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME UNIQUE);
            CREATE TABLE world_data (
                scrape_id INTEGER, world_number INTEGER, player_count INTEGER, detail_id INTEGER,
                PRIMARY KEY (scrape_id, world_number)) WITHOUT ROWID;
        ''')

    def tearDown(self):
        self.conn.close()

    def rows(self, sql):
        return self.conn.execute(sql).fetchall()

    def test_writes_one_row_per_world(self):
        _save_world_data(self.conn, 1, [
            world(301, 100, 'United States', False, '-'),
            world(302, 50, 'Germany', True, 'Blast Furnace'),
        ])
        self.assertEqual(
            self.rows('SELECT world_number, player_count FROM world_data ORDER BY world_number'),
            [(301, 100), (302, 50)])

    def test_new_locations_and_activities_are_created_once(self):
        batch = [world(301, 100, 'United States', False, '-'),
                 world(302, 110, 'United States', False, '-'),
                 world(303, 120, 'Germany', False, '-')]
        _save_world_data(self.conn, 1, batch)
        self.assertEqual(sorted(r[0] for r in self.rows('SELECT name FROM locations')),
                         ['Germany', 'United States'])
        self.assertEqual(self.rows('SELECT COUNT(*) FROM activities')[0][0], 1)

    def test_details_are_reused_across_scrapes(self):
        batch = [world(301, 100, 'United States', False, '-')]
        _save_world_data(self.conn, 1, batch)
        self.conn.execute('INSERT INTO scrape_events (id, timestamp) VALUES (2, ?)', ('t2',))
        _save_world_data(self.conn, 2, batch)

        # The second scrape must not create a duplicate location/activity/detail.
        self.assertEqual(self.rows('SELECT COUNT(*) FROM locations')[0][0], 1)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM activities')[0][0], 1)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM world_details')[0][0], 1)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM world_data')[0][0], 2)

    def test_distinct_combinations_get_distinct_details(self):
        _save_world_data(self.conn, 1, [
            world(301, 100, 'United States', False, '-'),
            world(302, 100, 'United States', True, '-'),
            world(303, 100, 'United States', False, 'Blast Furnace'),
        ])
        self.assertEqual(self.rows('SELECT COUNT(*) FROM world_details')[0][0], 3)
        detail_ids = [r[0] for r in self.rows('SELECT detail_id FROM world_data')]
        self.assertEqual(len(set(detail_ids)), 3)

    def test_world_data_points_at_the_right_detail(self):
        _save_world_data(self.conn, 1, [
            world(301, 100, 'United States', False, '-'),
            world(302, 50, 'Germany', True, 'Blast Furnace'),
        ])
        joined = self.rows('''
            SELECT wd.world_number, loc.name, det.is_f2p, act.description
            FROM world_data wd
            JOIN world_details det ON wd.detail_id = det.id
            JOIN locations loc ON det.location_id = loc.id
            JOIN activities act ON det.activity_id = act.id
            ORDER BY wd.world_number
        ''')
        self.assertEqual(joined, [(301, 'United States', 0, '-'),
                                  (302, 'Germany', 1, 'Blast Furnace')])

    def test_empty_batch_writes_nothing(self):
        _save_world_data(self.conn, 1, [])
        self.assertEqual(self.rows('SELECT COUNT(*) FROM world_data')[0][0], 0)


class InternTest(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute(
            'CREATE TABLE locations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)')

    def tearDown(self):
        self.conn.close()

    def test_inserts_once_and_caches(self):
        cache = {}
        first = _intern(self.conn, cache, 'locations', 'name', 'Japan')
        second = _intern(self.conn, cache, 'locations', 'name', 'Japan')
        self.assertEqual(first, second)
        self.assertEqual(cache, {'Japan': first})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM locations').fetchone()[0], 1)

    def test_a_seeded_value_is_not_reinserted(self):
        self.conn.execute("INSERT INTO locations (id, name) VALUES (7, 'Japan')")
        cache = {'Japan': 7}
        self.assertEqual(_intern(self.conn, cache, 'locations', 'name', 'Japan'), 7)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM locations').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
