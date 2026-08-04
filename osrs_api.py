from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import os
import sqlite3
from datetime import datetime, timedelta, timezone
import logging

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
                latest_scrape = conn.execute('SELECT id, timestamp FROM scrape_events ORDER BY timestamp DESC LIMIT 1').fetchone()
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
            "worlds": [row['world_number'] for row in worlds]
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
    """Render a datetime the way the DB stores timestamps: 2026-08-04T17:02:24Z.

    Every timestamp column (players, scrape_events, history_import) is `...Z`, and
    the range filters are string comparisons. datetime.isoformat() emits
    `...+00:00`, and '+' sorts below 'Z', so a row landing exactly on `end` was
    excluded by `<= ?`. Always use this for a value compared against a stored
    timestamp.
    """
    # Naive means UTC here, same as parse_iso assumes. astimezone() would otherwise
    # read it as system local time and shift it.
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


@app.route('/api/history/grouped')
def get_history_grouped():
    """Every series for a comparison in one query, instead of one request per world.

    Query parameters:
        - group_by (str): 'world' (default), 'location' or 'f2p'.
        - start, end, unit, step, agg: as /api/history. unit is required.
        - world_id, location_id, is_f2p: filters, as /api/history.

    Returns {"timestamps": [...], "series": [{"key": k, "counts": [n|null, ...]}]}
    where each counts array is aligned to timestamps by index.
    """
    group_by = request.args.get('group_by', default='world', type=str)
    if group_by not in GROUPINGS:
        return jsonify({"error": f"group_by must be one of: {', '.join(GROUPINGS)}"}), 400

    unit = request.args.get('unit', default=None, type=str)
    step = request.args.get('step', default=None, type=int)
    agg = request.args.get('agg', default='max', type=str)
    world_id = request.args.get('world_id', default=None, type=int)
    location_id = request.args.get('location_id', default=None, type=int)
    is_f2p = request.args.get('is_f2p', default=None, type=int)

    buckets = _bucket_exprs(unit, step, 'timestamp')
    if not buckets:
        return jsonify({"error": "unit is required: minute, hour, day, week or month."}), 400

    # Unlike /api/history this never serves an unbounded range: many series over
    # all of world_data is a guaranteed full scan.
    end_dt = parse_iso(request.args.get('end')) or datetime.now(timezone.utc)
    start_dt = parse_iso(request.args.get('start')) or (end_dt - timedelta(days=7))
    if start_dt > end_dt:
        return jsonify({"error": "start must be before end."}), 400
    if unit == 'minute' and (end_dt - start_dt) > timedelta(days=30):
        return jsonify({"error": "Minute-level queries cannot span more than 30 days. Please use a larger unit (hour/day) or a shorter time range."}), 400

    agg_func = "ROUND(AVG(count))" if agg == 'avg' else "MAX(count)"
    key_expr, sum_within_scrape = GROUPINGS[group_by]

    conn = get_db_connection()
    try:
        # Resolve the range to scrape_id bounds so this is a range scan over the
        # world_data PK, not a filter on se.timestamp. Same reasoning as get_history.
        lo = conn.execute('SELECT MIN(id) FROM scrape_events WHERE timestamp >= ?',
                          (iso_z(start_dt),)).fetchone()[0]
        hi = conn.execute('SELECT MAX(id) FROM scrape_events WHERE timestamp <= ?',
                          (iso_z(end_dt),)).fetchone()[0]
        if lo is None or hi is None:
            return jsonify({"timestamps": [], "series": []})

        from_clause = "FROM world_data wd JOIN scrape_events se ON wd.scrape_id = se.id"
        if sum_within_scrape or location_id is not None or is_f2p is not None:
            from_clause += " JOIN world_details det ON wd.detail_id = det.id"

        where_clauses = ["wd.scrape_id >= ?", "wd.scrape_id <= ?"]
        params = [lo, hi]
        if world_id is not None:
            where_clauses.append("wd.world_number = ?")
            params.append(world_id)
        if location_id is not None:
            where_clauses.append("det.location_id = ?")
            params.append(location_id)
        if is_f2p is not None:
            where_clauses.append("det.is_f2p = ?")
            params.append(is_f2p)
        where_str = "WHERE " + " AND ".join(where_clauses)

        # Reject oversized requests before running the query rather than after.
        # Only worlds can realistically blow the cap (hundreds of series); grouping
        # by location or f2p is a handful either way.
        if group_by == 'world':
            bucket_secs = BUCKET_SECONDS.get(unit) or (step if step else 5) * 60
            scrapes = hi - lo + 1
            est_buckets = min((end_dt - start_dt).total_seconds() // bucket_secs + 1, scrapes)
            est_series = conn.execute(
                'SELECT COUNT(*) FROM world_data WHERE scrape_id = ?', (hi,)).fetchone()[0]
            if est_buckets * est_series > MAX_GROUPED_POINTS:
                return jsonify({"error": "Too many points for one comparison. Use a coarser granularity or a shorter time range."}), 400

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
        query = (f"SELECT {bucket_select} AS ts, k, {agg_func} AS count FROM ({inner}) "
                 f"GROUP BY {bucket_group}, k ORDER BY ts ASC LIMIT {MAX_GROUPED_POINTS + 1}")

        rows = conn.execute(query, params).fetchall()
        if len(rows) > MAX_GROUPED_POINTS:
            return jsonify({"error": "Too many points for one comparison. Use a coarser granularity or a shorter time range."}), 400

        # Rows arrive ordered by bucket, so timestamps builds up in order and each
        # series is padded to the current bucket index as it goes.
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

        return jsonify({
            "timestamps": timestamps,
            "series": [{"key": k, "counts": series[k]}
                       for k in sorted(series, key=lambda v: (v is None, v))]
        })
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
        
        NEW FILTERS:
        - world_id (int): Filter by specific world number.
        - location_id (int): Filter by location ID.
        - is_f2p (bool/int): Filter by F2P status (1=True, 0=False).
    """
    # Query params
    limit = request.args.get('limit', default=None, type=int)
    start = request.args.get('start', default=None, type=str)
    end = request.args.get('end', default=None, type=str)
    unit = request.args.get('unit', default=None, type=str)
    step = request.args.get('step', default=None, type=int)
    agg = request.args.get('agg', default='max', type=str)
    
    # New Filters
    world_id = request.args.get('world_id', default=None, type=int)
    location_id = request.args.get('location_id', default=None, type=int)
    is_f2p = request.args.get('is_f2p', default=None, type=int) # 0 or 1

    # Determine aggregation SQL function
    agg_func = "MAX(count)"
    if agg == 'avg':
        agg_func = "ROUND(AVG(count))"

    start_dt = parse_iso(start)
    end_dt = parse_iso(end)

    # Enforce server-side limit: minute-level queries (unit=minute) cannot span more than 30 days
    if unit == 'minute' and step:
        # If start/end are provided, validate duration. If missing, treat as last-24h (allowed)
        if start_dt is None and end_dt is None:
            pass # Defaults to last 24h later
        elif start_dt and end_dt:
            duration = end_dt - start_dt
            if duration > timedelta(days=30):
                return jsonify({"error": "Minute-level queries cannot span more than 30 days. Please use a larger unit (hour/day) or a shorter time range."}), 400
        elif start_dt:
            # If only start is provided, check against now
            duration = datetime.now(timezone.utc) - start_dt
            if duration > timedelta(days=30):
                return jsonify({"error": "Minute-level queries cannot span more than 30 days."}), 400

    conn = get_db_connection()
    try:
        # --- QUERY CONSTRUCTION ---
        
        # Determine if we are querying the main 'players' table or the 'world_data' system
        use_world_data = (world_id is not None) or (location_id is not None) or (is_f2p is not None)
        
        if use_world_data:
            # We are querying detailed world data
            # Note: Aggregation (unit/step) logic for world data is complex. 
            # For now, let's implement raw data return for world data, 
            # or simple aggregation if requested.
            
            # Base Join
            from_clause = "FROM world_data wd JOIN scrape_events se ON wd.scrape_id = se.id"
            where_clauses = []
            params = []
            group_by = ""
            order_by = "ORDER BY se.timestamp ASC"
            
            # If filtering by location or f2p, we need world_details
            if location_id is not None or is_f2p is not None:
                from_clause += " JOIN world_details det ON wd.detail_id = det.id"
                
            # Select Timestamp
            select_clause = "SELECT se.timestamp as timestamp"
            
            # Select Count
            if world_id is not None:
                # Specific world -> just the count
                select_clause += ", wd.player_count as count"
                where_clauses.append("wd.world_number = ?")
                params.append(world_id)
            else:
                # Location or F2P -> Sum of counts
                select_clause += ", SUM(wd.player_count) as count"
                group_by = "GROUP BY se.id" # Group by scrape event
                
            # Apply Location/F2P filters (applies to both specific world and aggregated queries)
            if location_id is not None:
                where_clauses.append("det.location_id = ?")
                params.append(location_id)
            
            if is_f2p is not None:
                where_clauses.append("det.is_f2p = ?")
                params.append(is_f2p)

            # Resolve the time range to a scrape_id range and filter on that instead
            # of se.timestamp. scrape_id is the world_data PK prefix, so this is a
            # range scan; filtering on se.timestamp makes SQLite scan all ~4M rows
            # and only then discard them (4800ms vs 10ms on a 7 day window).
            if start_dt:
                lo = conn.execute('SELECT MIN(id) FROM scrape_events WHERE timestamp >= ?',
                                  (iso_z(start_dt),)).fetchone()[0]
                if lo is None:
                    return jsonify([])
                where_clauses.append("wd.scrape_id >= ?")
                params.append(lo)
            if end_dt:
                hi = conn.execute('SELECT MAX(id) FROM scrape_events WHERE timestamp <= ?',
                                  (iso_z(end_dt),)).fetchone()[0]
                if hi is None:
                    return jsonify([])
                where_clauses.append("wd.scrape_id <= ?")
                params.append(hi)
                
            where_str = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            # One (timestamp, count) row per scrape event.
            inner = f"{select_clause} {from_clause} {where_str} {group_by}"

            buckets = _bucket_exprs(unit, step, 'timestamp')
            if buckets:
                # Two levels on purpose: the inner SUM adds up worlds at one instant,
                # the outer agg reduces across instants in the bucket. Collapsing them
                # would sum across time.
                bucket_select, bucket_group = buckets
                query = (f"SELECT {bucket_select} as timestamp, {agg_func} as count "
                         f"FROM ({inner}) GROUP BY {bucket_group} ORDER BY timestamp ASC")
            else:
                limit_clause = ""
                if not start_dt and not end_dt and limit:
                    limit_clause = f"LIMIT {limit}"
                    order_by = "ORDER BY se.timestamp DESC"
                query = f"{inner} {order_by} {limit_clause}"

            rows = conn.execute(query, params).fetchall()
            
            results = []
            for row in rows:
                results.append({
                    "timestamp": row['timestamp'],
                    "count": row['count']
                })
                
            if "DESC" in order_by:
                results.reverse()
                
            return jsonify(results)
                 
        else:
            # Standard Global History (players table)
            table = "players"
            col_ts = "timestamp"
            col_count = "count"
            
            select_clause = ""
            group_by = ""
            
            buckets = _bucket_exprs(unit, step, col_ts)
            if buckets:
                bucket_select, bucket_group = buckets
                select_clause = f"SELECT {bucket_select} as timestamp, {agg_func} as count"
                group_by = f"GROUP BY {bucket_group}"
            else:
                # Raw Data
                select_clause = f"SELECT {col_ts}, {col_count}"
            
            from_clause = f"FROM {table}"
            where_clauses = []
            params = []

            if start_dt:
                where_clauses.append(f"{col_ts} >= ?")
                params.append(iso_z(start_dt))
            if end_dt:
                where_clauses.append(f"{col_ts} <= ?")
                params.append(iso_z(end_dt))

            # Split the two eras at the end of imported coverage. Keeping this on the
            # native query as well as the imported one means the old weekly rows can
            # sit in `players` without being double-counted, which is what lets the
            # data load and the code deploy happen as separate steps.
            import_end = _import_end(conn)
            if import_end:
                where_clauses.append(f"{col_ts} >= ?")
                params.append(import_end)

            limit_clause = ""
            order_by = "ORDER BY timestamp ASC"

            if not start_dt and not end_dt and not buckets:
                lim = limit if limit else 288
                limit_clause = f"LIMIT {lim}"
                order_by = "ORDER BY timestamp DESC"

            where_str = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            query = f"{select_clause} {from_clause} {where_str} {group_by} {order_by} {limit_clause}"
            
            rows = conn.execute(query, params).fetchall()

            results = []
            for row in rows:
                results.append({
                    "timestamp": row['timestamp'],
                    "count": row['count']
                })

            if "DESC" in order_by:
                results.reverse()

            # Prepend the imported era when the request actually reaches into it.
            # Skipped for the bare "last N samples" call, which is asking about now.
            if import_end and (buckets or start_dt or end_dt):
                if not start_dt or iso_z(start_dt) < import_end:
                    results = _imported_history(conn, start_dt, end_dt, unit, agg) + results

            return jsonify(results)

    except Exception as e:
        logger.error(f"Error in get_history: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    # Run the server on port 5000
    print("API Server starting on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)