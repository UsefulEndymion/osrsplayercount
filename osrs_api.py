from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import os
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
        return (f"date({col}, 'weekday 0', '-6 days')", f"strftime('%Y-%W', {col})")
    if unit == 'month':
        return (f"strftime('%Y-%m-01', {col})", f"strftime('%Y-%m', {col})")
    return None


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

    # Helper to parse ISO timestamps robustly (accept trailing Z)
    def parse_iso(ts):
        if not ts:
            return None
        try:
            # strip Z and parse as naive then set UTC
            if ts.endswith('Z'):
                ts2 = ts[:-1]
            else:
                ts2 = ts
            # datetime.fromisoformat handles 'YYYY-MM-DDTHH:MM:SS[.ffffff]'
            return datetime.fromisoformat(ts2).replace(tzinfo=timezone.utc)
        except Exception:
            # fallback to strptime for basic formats
            try:
                return datetime.strptime(ts2, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
            except Exception:
                return None

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
                                  (start_dt.isoformat(),)).fetchone()[0]
                if lo is None:
                    return jsonify([])
                where_clauses.append("wd.scrape_id >= ?")
                params.append(lo)
            if end_dt:
                hi = conn.execute('SELECT MAX(id) FROM scrape_events WHERE timestamp <= ?',
                                  (end_dt.isoformat(),)).fetchone()[0]
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
                params.append(start_dt.isoformat())
            if end_dt:
                where_clauses.append(f"{col_ts} <= ?")
                params.append(end_dt.isoformat())
                
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