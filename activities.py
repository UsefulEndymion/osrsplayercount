"""Display-only grouping of the SLU activity strings.

Jagex publishes activities as free text, so one real activity reaches us under
several names: `Castle Wars 1`, `Castle Wars 2`, `Castle Wars - Free`. The
database keeps every string exactly as scraped; grouping happens here, on the
way out, so the stored rows and the data dump stay faithful to the source.
"""

# The no-activity bucket. 179 worlds carry it, which makes it both meaningless
# as a filter and by far the largest entry, so it never reaches the dropdown.
NO_ACTIVITY = '-'

# Families that arrive under several names. A description belongs to a prefix
# when it equals the prefix or continues it after a space or dash, so
# `Event PvP World` never joins `PvP World`. Matched longest-first, which is
# what keeps `Deadman Finale - 96+` out of `Deadman`.
ACTIVITY_GROUP_PREFIXES = (
    'Brimhaven Agility',
    'Castle Wars',
    'Deadman',
    'Deadman Finale',
    'LMS',
    'Leagues VI',
    'PvP Arena',
    'PvP World',
    'Trade',
    'Wilderness PK',
)


def _family_prefix(description, ordered_prefixes):
    for prefix in ordered_prefixes:
        if description == prefix:
            return prefix
        if description.startswith(prefix) and description[len(prefix)] in ' -':
            return prefix
    return None


def group_activities(activities):
    """[(id, description)] -> [{"name": str, "ids": [int]}] for the filter UI.

    Members of a known family collapse into one entry carrying every member id;
    everything else is its own entry. A family that turns out to have a single
    member keeps that member's full description — there is nothing to merge, and
    the specific name says more than the prefix.

    Grouped ids span every era the members existed in, so a family whose members
    retired at different times steps down as they drop out. That is what the
    data says; it is not an outage.
    """
    ordered = sorted(ACTIVITY_GROUP_PREFIXES, key=len, reverse=True)

    families, entries = {}, []
    for activity_id, description in activities:
        if description == NO_ACTIVITY:
            continue
        prefix = _family_prefix(description, ordered)
        if prefix is None:
            entries.append((description, [activity_id]))
        else:
            families.setdefault(prefix, []).append((description, activity_id))

    for prefix, members in families.items():
        if len(members) == 1:
            entries.append((members[0][0], [members[0][1]]))
        else:
            entries.append((prefix, sorted(member_id for _, member_id in members)))

    return [{"name": name, "ids": ids}
            for name, ids in sorted(entries, key=lambda entry: entry[0].lower())]
