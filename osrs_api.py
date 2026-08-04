from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

from activities import group_activities
from config import BASE_DIR
from database import get_db_connection

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Tell Flask where to find the static files and templates
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))
CORS(app)

# Pretty-printed JSON is 3x the bytes on the grouped endpoint.
if hasattr(app, 'json'):
    app.json.compact = True
else:
    app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/robots.txt')
def robots():
    return app.send_static_file('robots.txt')


@app.route('/sitemap.xml')
def sitemap():
    return app.send_static_file('sitemap.xml')


@app.route('/api/latest')
def get_latest():
    """Returns the most recent single data point with F2P/Members breakdown."""
    conn = get_db_connection()
    try:
        # Get the last row added to players (global count)
        row = conn.execute('SELECT * FROM players ORDER BY id DESC LIMIT 1').fetchone()

        f2p_count = 0
        members_count = 0

        if row:
            try:
                latest_scrape = conn.execute(
                    'SELECT id, timestamp FROM scrape_events ORDER BY timestamp DESC LIMIT 1').fetchone()
                breakdown_ts = None
                if latest_scrape:
                    scrape_id = latest_scrape['id']
                    breakdown_ts = latest_scrape['timestamp']

                    # Calculate F2P count
                    f2p_res = conn.execute('''
                        SELECT SUM(wd.player_count) as count
                        FROM world_data wd
                        JOIN world_details det ON wd.detail_id = det.id
                        WHERE wd.scrape_id = ? AND det.is_f2p = 1
                    ''', (scrape_id,)).fetchone()
                    f2p_count = f2p_res['count'] if f2p_res and f2p_res['count'] else 0

                    # Calculate Members count
                    mem_res = conn.execute('''
                        SELECT SUM(wd.player_count) as count
                        FROM world_data wd
                        JOIN world_details det ON wd.detail_id = det.id
                        WHERE wd.scrape_id = ? AND det.is_f2p = 0
                    ''', (scrape_id,)).fetchone()
                    members_count = mem_res['count'] if mem_res and mem_res['count'] else 0
            except Exception as e:
                logger.error(f"Error fetching breakdown: {e}")

        if row:
            return jsonify({
                "timestamp": row['timestamp'],
                "count": row['count'],
                "f2p_count": f2p_count,
                "members_count": members_count,
                "breakdown_timestamp": breakdown_ts
            })
        else:
            return jsonify({"error": "No data found"}), 404
    finally:
        conn.close()


@app.route('/api/metadata')
def get_metadata():
    """Returns lists of worlds, locations, and activities for filtering."""
    conn = get_db_connection()
    try:
        # Get Locations
        locations = conn.execute('SELECT id, name FROM locations ORDER BY name').fetchall()

        # Get Activities
        activities = conn.execute('SELECT id, description FROM activities ORDER BY description').fetchall()

        # The floor on every filtered query. Before this the only history is the
        # imported weekly series, which is site-wide with no per-world breakdown,
        # so any filter silently starts here rather than where the user asked.
        world_data_start = conn.execute(
            'SELECT MIN(timestamp) FROM scrape_events').fetchone()[0]

        # Loose index scan over idx_world_number: one seek per world instead of
        # walking ~4M rows. Not SELECT DISTINCT, which is ~215ms here vs ~2ms.
        worlds = conn.execute('''
            WITH RECURSIVE distinct_worlds(world_number) AS (
                SELECT MIN(world_number) FROM world_data
                UNION ALL
                SELECT (SELECT MIN(world_number) FROM world_data
                        WHERE world_number > distinct_worlds.world_number)
                FROM distinct_worlds
                WHERE distinct_worlds.world_number IS NOT NULL
            )
            SELECT world_number FROM distinct_worlds
            WHERE world_number IS NOT NULL
            ORDER BY world_number
        ''').fetchall()

        return jsonify({
            "locations": [{"id": row['id'], "name": row['name']} for row in locations],
            "activities": [{"id": row['id'], "description": row['description']} for row in activities],
            # Raw descriptions above, the dropdown's collapsed view here. The UI
            # wants the second; anything reading the source strings wants the first.
            "activity_groups": group_activities(
                (row['id'], row['description']) for row in activities),
            "worlds": [row['world_number'] for row in worlds],
            "world_data_start": world_data_start
        })
    finally:
        conn.close()


ISO_Z = '%Y-%m-%dT%H:%M:%SZ'


def parse_iso(ts):
    """Parse an ISO timestamp, accepting a trailing Z. None if unparseable."""
    if not ts:
        return None
    ts2 = ts[:-1] if ts.endswith('Z') else ts
    try:
        return datetime.fromisoformat(ts2).replace(tzinfo=timezone.utc)
    except Exception:
        try:
            return datetime.strptime(ts2, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        except Exception:
            return None


def iso_z(dt):
    """Format for comparison against stored timestamps, which are all `...Z`.

    isoformat() emits `...+00:00`, and '+' sorts below 'Z'.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(ISO_Z)


def _bucket_exprs(unit, step, col):
    """(select, group by) expressions bucketing `col` by unit. None if unit is unset
    or unrecognised, in which case the caller should return raw rows."""
    if unit == 'minute':
        secs = (step if step else 5) * 60
        return (f"datetime((strftime('%s', {col}) / {secs}) * {secs}, 'unixepoch')",
                f"(strftime('%s', {col}) / {secs})")
    if unit == 'hour':
        return (f"strftime('%Y-%m-%dT%H:00:00Z', {col})", f"strftime('%Y-%m-%dT%H', {col})")
    if unit == 'day':
        return (f"strftime('%Y-%m-%d', {col})", f"strftime('%Y-%m-%d', {col})")
    if unit == 'week':
        # Group by the same Monday the label shows. Grouping by strftime('%Y-%W')
        # instead splits the week straddling New Year into two buckets that then
        # render as two points on the same date.
        return (f"date({col}, 'weekday 0', '-6 days')", f"date({col}, 'weekday 0', '-6 days')")
    if unit == 'month':
        return (f"strftime('%Y-%m-01', {col})", f"strftime('%Y-%m', {col})")
    return None


# Global history spans two eras: weekly rows imported from misplaceditems before
# native tracking began, and 5-minute samples in `players` after. `history_import`
# names its statistic 'avg'/'peak'; the API's agg param says 'avg'/'max'.
IMPORT_STAT = {'avg': 'avg', 'max': 'peak'}


def _import_end(conn):
    """Where imported coverage stops, or None if nothing has been imported.

    Doubles as the lower bound of the native query, so the two eras can never both
    answer for the same instant. That matters while the old weekly rows are still
    sitting in `players` partway through a rollout.
    """
    try:
        row = conn.execute('SELECT MAX(period_end) FROM history_import').fetchone()
    except sqlite3.OperationalError:
        return None  # table not created yet; behave exactly as before
    return row[0] if row else None


def _imported_history(conn, start_dt, end_dt, unit, agg):
    """The imported era, bucketed no finer than the weekly source data.

    Only ever rolls a series up into its own statistic -- peaks with MAX, averages
    with AVG. Deriving a monthly peak from weekly averages (or vice versa) is not
    possible, and filtering to one `stat` first is what prevents it.
    """
    where = ['stat = ?']
    params = [IMPORT_STAT.get(agg, 'peak')]
    # A week is included when it overlaps the range at all, not only when it starts
    # inside it, or a range landing mid-week would drop the week containing it.
    if start_dt:
        where.append('period_end > ?')
        params.append(iso_z(start_dt))
    if end_dt:
        where.append('period_start <= ?')
        params.append(iso_z(end_dt))
    where_str = 'WHERE ' + ' AND '.join(where)

    if unit == 'month':
        agg_func = 'ROUND(AVG(count))' if agg == 'avg' else 'MAX(count)'
        query = (f"SELECT strftime('%Y-%m-01', period_start) as timestamp, {agg_func} as count "
                 f"FROM history_import {where_str} "
                 f"GROUP BY strftime('%Y-%m', period_start) ORDER BY timestamp ASC")
    else:
        # Minute/hour/day all floor to week: the source has nothing finer, and the
        # rows are already one per week, so this is a passthrough rather than a
        # re-bucketing. Re-bucketing would be wrong anyway -- the site's weeks start
        # on varying weekdays, so snapping them to Mondays would collide pairs.
        query = (f"SELECT period_start as timestamp, count "
                 f"FROM history_import {where_str} ORDER BY period_start ASC")

    return [{'timestamp': r['timestamp'], 'count': r['count']}
            for r in conn.execute(query, params)]


def _rows_to_series(rows, descending=False):
    """Query rows to the [{timestamp, count}] the frontend expects, always ascending."""
    results = [{"timestamp": r['timestamp'], "count": r['count']} for r in rows]
    if descending:
        results.reverse()
    return results


def _scrape_id_bounds(conn, start_dt, end_dt):
    """A time range as (min_id, max_id) over scrape_events.

    scrape_id is the world_data PK prefix, so filtering on the resolved ids is a
    range scan; filtering on se.timestamp makes SQLite walk every row and only
    then discard it. None when the range holds no scrapes at all; max_id is None
    when the range is open-ended.
    """
    lo = conn.execute('SELECT MIN(id) FROM scrape_events WHERE timestamp >= ?',
                      (iso_z(start_dt),)).fetchone()[0]
    if lo is None:
        return None
    hi = None
    if end_dt:
        hi = conn.execute('SELECT MAX(id) FROM scrape_events WHERE timestamp <= ?',
                          (iso_z(end_dt),)).fetchone()[0]
        if hi is None:
            return None
    return lo, hi


def _world_detail_clauses(q):
    """(clauses, params) for the world_details filters shared by both endpoints."""
    clauses, params = [], []
    if q.location_id is not None:
        clauses.append("det.location_id = ?")
        params.append(q.location_id)
    if q.is_f2p is not None:
        clauses.append("det.is_f2p = ?")
        params.append(q.is_f2p)
    if q.activity_ids:
        # A group selection arrives as every member id, so this is an IN even
        # when the user picked one thing from the dropdown.
        clauses.append(f"det.activity_id IN ({','.join('?' * len(q.activity_ids))})")
        params.extend(q.activity_ids)
    return clauses, params


# group_by value -> (key expression, whether counts must be summed across worlds
# within a scrape before aggregating across time)
GROUPINGS = {
    'world': ('wd.world_number', False),
    'location': ('det.location_id', True),
    'f2p': ('det.is_f2p', True),
}

# Ceiling on (buckets x series) returned by /api/history/grouped. Sized to admit
# everything the UI can ask for up to 30d at minute granularity (~460k points);
# rejects hourly-over-years and other requests no chart can render anyway.
MAX_GROUPED_POINTS = 600000

BUCKET_SECONDS = {'hour': 3600, 'day': 86400, 'week': 604800, 'month': 2592000}

TOO_MANY_POINTS_MSG = ("Too many points for one comparison. "
                       "Use a coarser granularity or a shorter time range.")

MINUTE_SPAN_MSG = ("Minute-level queries cannot span more than 30 days. "
                   "Please use a larger unit (hour/day) or a shorter time range.")

# Bound on the IN list built from activity_id. The largest real group is 16
# members, so this only ever rejects input no dropdown could have produced.
MAX_ACTIVITY_IDS = 200

TOO_MANY_ACTIVITIES_MSG = f"At most {MAX_ACTIVITY_IDS} activity_id values are allowed."


def _parse_activity_ids(values):
    """Repeated and/or comma-separated `activity_id` params -> unique ints.

    Unparseable tokens are dropped rather than rejected, which is how `type=int`
    already treats the other filters: a bad value means no filter, not a 400.
    """
    ids = []
    for value in values:
        for token in value.split(','):
            token = token.strip()
            if not token:
                continue
            try:
                parsed = int(token)
            except ValueError:
                continue
            if parsed not in ids:
                ids.append(parsed)
    return tuple(ids)


@dataclass
class HistoryQuery:
    """Parsed /api/history query params. New filters get a field here and a
    clause in _world_history; nothing in between needs to know about them."""
    limit: int = None
    start_dt: datetime = None
    end_dt: datetime = None
    unit: str = None
    step: int = None
    agg: str = 'max'
    world_id: int = None
    location_id: int = None
    is_f2p: int = None
    activity_ids: tuple = ()

    @classmethod
    def from_request(cls):
        return cls(
            limit=request.args.get('limit', default=None, type=int),
            start_dt=parse_iso(request.args.get('start')),
            end_dt=parse_iso(request.args.get('end')),
            unit=request.args.get('unit', default=None, type=str),
            step=request.args.get('step', default=None, type=int),
            agg=request.args.get('agg', default='max', type=str),
            world_id=request.args.get('world_id', default=None, type=int),
            location_id=request.args.get('location_id', default=None, type=int),
            is_f2p=request.args.get('is_f2p', default=None, type=int),
            activity_ids=_parse_activity_ids(request.args.getlist('activity_id')),
        )

    @property
    def agg_func(self):
        return "ROUND(AVG(count))" if self.agg == 'avg' else "MAX(count)"

    @property
    def needs_details_join(self):
        """Whether any filter reads world_details, and so needs it joined in."""
        return (self.location_id is not None or self.is_f2p is not None
                or bool(self.activity_ids))

    @property
    def use_world_data(self):
        """Whether any filter forces the per-world tables instead of `players`."""
        return self.world_id is not None or self.needs_details_join

    @property
    def too_many_activities(self):
        return len(self.activity_ids) > MAX_ACTIVITY_IDS

    @property
    def minute_span_exceeded(self):
        """Minute granularity over more than 30 days. A missing start means the
        query defaults to a recent window later, so there is nothing to check."""
        if self.unit != 'minute' or not self.step or self.start_dt is None:
            return False
        end = self.end_dt or datetime.now(timezone.utc)
        return (end - self.start_dt) > timedelta(days=30)


def _world_history(conn, q):
    """One series from world_data: a single world's count, or the sum over the
    worlds matching the filters at each scrape."""
    from_clause = "FROM world_data wd JOIN scrape_events se ON wd.scrape_id = se.id"
    if q.needs_details_join:
        from_clause += " JOIN world_details det ON wd.detail_id = det.id"

    where_clauses, params = [], []
    if q.world_id is not None:
        select_clause = "SELECT se.timestamp as timestamp, wd.player_count as count"
        group_by = ""
        where_clauses.append("wd.world_number = ?")
        params.append(q.world_id)
    else:
        select_clause = "SELECT se.timestamp as timestamp, SUM(wd.player_count) as count"
        group_by = "GROUP BY se.id"

    detail_clauses, detail_params = _world_detail_clauses(q)
    where_clauses += detail_clauses
    params += detail_params

    # `limit` means "most recent N", which only applies when no range was given.
    tail_request = q.start_dt is None and q.end_dt is None

    # An unbounded range here has no scrape_id bounds to restrict to, so it
    # scans all of world_data. Default to 7d, as /api/history/grouped does.
    start_dt, end_dt = q.start_dt, q.end_dt
    if start_dt is None:
        end_dt = end_dt or datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=7)

    bounds = _scrape_id_bounds(conn, start_dt, end_dt)
    if bounds is None:
        return []
    lo, hi = bounds
    where_clauses.append("wd.scrape_id >= ?")
    params.append(lo)
    if hi is not None:
        where_clauses.append("wd.scrape_id <= ?")
        params.append(hi)

    where_str = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    # One (timestamp, count) row per scrape event.
    inner = f"{select_clause} {from_clause} {where_str} {group_by}"

    buckets = _bucket_exprs(q.unit, q.step, 'timestamp')
    descending = False
    if buckets:
        # Two levels on purpose: the inner SUM adds up worlds at one instant,
        # the outer agg reduces across instants in the bucket. Collapsing them
        # would sum across time.
        bucket_select, bucket_group = buckets
        query = (f"SELECT {bucket_select} as timestamp, {q.agg_func} as count "
                 f"FROM ({inner}) GROUP BY {bucket_group} ORDER BY timestamp ASC")
    else:
        descending = bool(tail_request and q.limit)
        order_by = "ORDER BY se.timestamp DESC" if descending else "ORDER BY se.timestamp ASC"
        limit_clause = f"LIMIT {q.limit}" if descending else ""
        query = f"{inner} {order_by} {limit_clause}"

    return _rows_to_series(conn.execute(query, params).fetchall(), descending)


def _global_history(conn, q):
    """The global series from `players`, with the imported era spliced on."""
    buckets = _bucket_exprs(q.unit, q.step, 'timestamp')
    if buckets:
        bucket_select, bucket_group = buckets
        select_clause = f"SELECT {bucket_select} as timestamp, {q.agg_func} as count"
        group_by = f"GROUP BY {bucket_group}"
    else:
        select_clause = "SELECT timestamp, count"
        group_by = ""

    where_clauses, params = [], []
    if q.start_dt:
        where_clauses.append("timestamp >= ?")
        params.append(iso_z(q.start_dt))
    if q.end_dt:
        where_clauses.append("timestamp <= ?")
        params.append(iso_z(q.end_dt))

    # Split the two eras at the end of imported coverage. Keeping this on the
    # native query as well as the imported one means the old weekly rows can
    # sit in `players` without being double-counted, which is what lets the
    # data load and the code deploy happen as separate steps.
    import_end = _import_end(conn)
    if import_end:
        where_clauses.append("timestamp >= ?")
        params.append(import_end)

    # No range and no bucketing is the "last N samples" call: take the tail and
    # flip it back to ascending.
    descending = not q.start_dt and not q.end_dt and not buckets
    order_by = "ORDER BY timestamp DESC" if descending else "ORDER BY timestamp ASC"
    limit_clause = f"LIMIT {q.limit or 288}" if descending else ""

    where_str = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    query = f"{select_clause} FROM players {where_str} {group_by} {order_by} {limit_clause}"

    results = _rows_to_series(conn.execute(query, params).fetchall(), descending)

    # Prepend the imported era when the request actually reaches into it.
    # Skipped for the bare "last N samples" call, which is asking about now.
    if import_end and (buckets or q.start_dt or q.end_dt):
        if not q.start_dt or iso_z(q.start_dt) < import_end:
            results = _imported_history(conn, q.start_dt, q.end_dt, q.unit, q.agg) + results

    return results


def _grouped_error(q, group_by, buckets):
    """The 400 response for an unusable /api/history/grouped request, else None."""
    if group_by not in GROUPINGS:
        return jsonify({"error": f"group_by must be one of: {', '.join(GROUPINGS)}"}), 400
    if q.too_many_activities:
        return jsonify({"error": TOO_MANY_ACTIVITIES_MSG}), 400
    if not buckets:
        return jsonify({"error": "unit is required: minute, hour, day, week or month."}), 400
    if q.start_dt > q.end_dt:
        return jsonify({"error": "start must be before end."}), 400
    if q.unit == 'minute' and (q.end_dt - q.start_dt) > timedelta(days=30):
        return jsonify({"error": MINUTE_SPAN_MSG}), 400
    return None


def _grouped_over_cap(conn, q, lo, hi):
    """Whether the response would exceed the point cap, estimated without running
    the query. Only worlds can realistically blow it (hundreds of series);
    grouping by location or f2p is a handful either way."""
    bucket_secs = BUCKET_SECONDS.get(q.unit) or (q.step if q.step else 5) * 60
    scrapes = hi - lo + 1
    est_buckets = min((q.end_dt - q.start_dt).total_seconds() // bucket_secs + 1, scrapes)
    est_series = conn.execute(
        'SELECT COUNT(*) FROM world_data WHERE scrape_id = ?', (hi,)).fetchone()[0]
    return est_buckets * est_series > MAX_GROUPED_POINTS


def _grouped_query(q, group_by, buckets, lo, hi):
    """(sql, params) for one row per (bucket, series key), ordered by bucket."""
    key_expr, sum_within_scrape = GROUPINGS[group_by]

    from_clause = "FROM world_data wd JOIN scrape_events se ON wd.scrape_id = se.id"
    if sum_within_scrape or q.needs_details_join:
        from_clause += " JOIN world_details det ON wd.detail_id = det.id"

    where_clauses = ["wd.scrape_id >= ?", "wd.scrape_id <= ?"]
    params = [lo, hi]
    if q.world_id is not None:
        where_clauses.append("wd.world_number = ?")
        params.append(q.world_id)
    detail_clauses, detail_params = _world_detail_clauses(q)
    where_clauses += detail_clauses
    params += detail_params
    where_str = "WHERE " + " AND ".join(where_clauses)

    if sum_within_scrape:
        # Two levels, as in get_history: inner adds up worlds at one instant,
        # outer reduces across the instants in a bucket.
        inner = (f"SELECT se.timestamp AS timestamp, {key_expr} AS k, SUM(wd.player_count) AS count "
                 f"{from_clause} {where_str} GROUP BY se.id, k")
    else:
        # world_data PK is (scrape_id, world_number), so a row already is one
        # world at one instant.
        inner = (f"SELECT se.timestamp AS timestamp, {key_expr} AS k, wd.player_count AS count "
                 f"{from_clause} {where_str}")

    bucket_select, bucket_group = buckets
    query = (f"SELECT {bucket_select} AS ts, k, {q.agg_func} AS count FROM ({inner}) "
             f"GROUP BY {bucket_group}, k ORDER BY ts ASC LIMIT {MAX_GROUPED_POINTS + 1}")
    return query, params


def _align_series(rows):
    """Bucket-ordered rows into timestamps plus one count array per key.

    Rows arrive ordered by bucket, so timestamps builds up in order and each
    series is padded to the current bucket index as it goes.
    """
    timestamps = []
    ts_index = {}
    series = {}
    for row in rows:
        idx = ts_index.get(row['ts'])
        if idx is None:
            idx = len(timestamps)
            ts_index[row['ts']] = idx
            timestamps.append(row['ts'])
        counts = series.setdefault(row['k'], [])
        counts.extend([None] * (idx - len(counts)))
        counts.append(row['count'])

    for counts in series.values():
        counts.extend([None] * (len(timestamps) - len(counts)))

    return {
        "timestamps": timestamps,
        "series": [{"key": k, "counts": series[k]}
                   for k in sorted(series, key=lambda v: (v is None, v))]
    }


@app.route('/api/history/grouped')
def get_history_grouped():
    """Every series for a comparison in one query, instead of one request per world.

    Query parameters:
        - group_by (str): 'world' (default), 'location' or 'f2p'.
        - start, end, unit, step, agg: as /api/history. unit is required.
        - world_id, location_id, is_f2p, activity_id: filters, as /api/history.

    Returns {"timestamps": [...], "series": [{"key": k, "counts": [n|null, ...]}]}
    where each counts array is aligned to timestamps by index.
    """
    q = HistoryQuery.from_request()
    group_by = request.args.get('group_by', default='world', type=str)
    buckets = _bucket_exprs(q.unit, q.step, 'timestamp')

    # Unlike /api/history this never serves an unbounded range: many series over
    # all of world_data is a guaranteed full scan.
    q.end_dt = q.end_dt or datetime.now(timezone.utc)
    q.start_dt = q.start_dt or (q.end_dt - timedelta(days=7))

    error = _grouped_error(q, group_by, buckets)
    if error:
        return error

    conn = get_db_connection()
    try:
        bounds = _scrape_id_bounds(conn, q.start_dt, q.end_dt)
        if bounds is None:
            return jsonify({"timestamps": [], "series": []})
        lo, hi = bounds

        # Reject oversized requests before running the query rather than after.
        if group_by == 'world' and _grouped_over_cap(conn, q, lo, hi):
            return jsonify({"error": TOO_MANY_POINTS_MSG}), 400

        query, params = _grouped_query(q, group_by, buckets, lo, hi)
        rows = conn.execute(query, params).fetchall()
        if len(rows) > MAX_GROUPED_POINTS:
            return jsonify({"error": TOO_MANY_POINTS_MSG}), 400

        return jsonify(_align_series(rows))
    except Exception as e:
        logger.error(f"Error in get_history_grouped: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/history')
def get_history():
    """
    Returns data points for a graph.
    Query parameters (all optional):
        - limit (int): return the last `limit` raw rows (default used when no start/end provided; default 288)
        - start (ISO datetime string): include rows with timestamp >= start
        - end (ISO datetime string): include rows with timestamp <= end
        - unit (str): aggregation unit, one of 'minute', 'hour', 'day', 'week', 'month'.
        - step (int): bucket size in minutes.
        - agg (str): 'max' or 'avg'.
        - world_id (int): Filter by specific world number.
        - location_id (int): Filter by location ID.
        - is_f2p (bool/int): Filter by F2P status (1=True, 0=False).
        - activity_id (int): Filter by activity. Repeatable, and accepts a
          comma-separated list, so one grouped dropdown entry can send every
          member id at once. Multiple ids sum the worlds carrying any of them.
    """
    q = HistoryQuery.from_request()
    if q.minute_span_exceeded:
        return jsonify({"error": MINUTE_SPAN_MSG}), 400
    if q.too_many_activities:
        return jsonify({"error": TOO_MANY_ACTIVITIES_MSG}), 400

    conn = get_db_connection()
    try:
        builder = _world_history if q.use_world_data else _global_history
        return jsonify(builder(conn, q))
    except Exception as e:
        logger.error(f"Error in get_history: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


if __name__ == '__main__':
    # Run the server on port 5000
    print("API Server starting on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)
