"""Grouping of the SLU activity strings.

The cases here are the real descriptions from the production database, including
the two Jagex whitespace typos, because the point of the module is to survive
exactly the strings Jagex actually publishes.
"""
import unittest

from activities import group_activities


def names(entries):
    return [entry['name'] for entry in entries]


def ids_for(entries, name):
    return next(entry['ids'] for entry in entries if entry['name'] == name)


class GroupActivitiesTest(unittest.TestCase):

    def test_no_activity_bucket_is_dropped(self):
        self.assertEqual(group_activities([(5, '-')]), [])

    def test_family_collapses_to_the_prefix(self):
        entries = group_activities([
            (66, 'Castle Wars 1'), (31, 'Castle Wars 2'), (7, 'Castle Wars - Free'),
        ])
        self.assertEqual(entries, [{'name': 'Castle Wars', 'ids': [7, 31, 66]}])

    def test_ungrouped_activity_keeps_its_own_name(self):
        entries = group_activities([(49, 'Guardians of the Rift'), (25, 'Wintertodt')])
        self.assertEqual(names(entries), ['Guardians of the Rift', 'Wintertodt'])

    def test_a_family_of_one_keeps_the_full_description(self):
        # 'Trade' is a real prefix, but with a single member the specific name
        # says more than the prefix does.
        entries = group_activities([(6, 'Trade - Free')])
        self.assertEqual(names(entries), ['Trade - Free'])

    def test_longest_prefix_wins(self):
        entries = group_activities([
            (17, 'Deadman'), (76, 'Deadman - 3-60'), (72, 'Deadman - Permanent'),
            (78, 'Deadman Finale - 45-60'), (80, 'Deadman Finale - 96+'),
        ])
        self.assertEqual(names(entries), ['Deadman', 'Deadman Finale'])
        self.assertEqual(ids_for(entries, 'Deadman'), [17, 72, 76])
        self.assertEqual(ids_for(entries, 'Deadman Finale'), [78, 80])

    def test_a_prefix_must_be_at_the_start(self):
        # 'Event PvP World' contains 'PvP World' but is a different activity.
        entries = group_activities([
            (65, 'PvP World'), (10, 'PvP World - Free'), (14, 'Event PvP World'),
        ])
        self.assertEqual(names(entries), ['Event PvP World', 'PvP World'])
        self.assertEqual(ids_for(entries, 'PvP World'), [10, 65])

    def test_a_prefix_must_end_on_a_separator(self):
        # Guards against a future 'Trader'-style name joining 'Trade'.
        entries = group_activities([(6, 'Trade - Free'), (68, 'Trade - Members'),
                                    (900, 'Traders Guild')])
        self.assertEqual(names(entries), ['Trade', 'Traders Guild'])

    def test_whitespace_typos_land_in_the_same_group(self):
        entries = group_activities([
            (83, 'Leagues VI'),
            (92, 'Leagues VI - The Hueycoatl'),
            (93, 'Leagues VI -  The Hueycoatl'),   # double space, as published
            (96, 'Leagues VI -Barbarian Assault'),  # missing space, as published
        ])
        self.assertEqual(entries, [{'name': 'Leagues VI', 'ids': [83, 92, 93, 96]}])

    def test_no_separator_still_groups(self):
        entries = group_activities([(28, 'Brimhaven Agility'),
                                    (45, 'Brimhaven Agility Arena')])
        self.assertEqual(entries, [{'name': 'Brimhaven Agility', 'ids': [28, 45]}])

    def test_parenthesised_variants_group(self):
        entries = group_activities([
            (69, 'PvP Arena (AUS)'), (64, 'PvP Arena (UK)'), (16, 'PvP Arena (US)'),
        ])
        self.assertEqual(entries, [{'name': 'PvP Arena', 'ids': [16, 64, 69]}])

    def test_skill_totals_stay_separate(self):
        # A numeric prefix, deliberately not a family: the thresholds are the
        # interesting part and summing them would mix unlike populations.
        entries = group_activities([(22, '1250 skill total'), (35, '2200 skill total')])
        self.assertEqual(names(entries), ['1250 skill total', '2200 skill total'])

    def test_entries_are_sorted_case_insensitively(self):
        entries = group_activities([(1, 'Zalcano'), (2, 'LMS Casual'),
                                    (3, 'LMS Competitive'), (4, 'Leagues VI'),
                                    (5, 'Leagues VI - CoX')])
        self.assertEqual(names(entries), ['Leagues VI', 'LMS', 'Zalcano'])


if __name__ == '__main__':
    unittest.main()
