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
    return "User-agent: *\nDisallow:", 200, {'Content-Type': 'text/plain'}

@app.route('/sitemap.xml')
def sitemap():
    # Basic sitemap
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
   <url>
      <loc>https://osrsplayercount.com/</loc>
      <changefreq>always</changefreq>
      <priority>1.0</priority>
   </url>
</urlset>"""
    return xml, 200, {'Content-Type': 'application/xml'}

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
        
        # Get Worlds (Distinct world numbers from world_data)
        worlds = conn.execute('SELECT DISTINCT world_number FROM world_data ORDER BY world_number').fetchall()
        
        return jsonify({
            "locations": [{"id": row['id'], "name": row['name']} for row in locations],
            "activities": [{"id": row['id'], "description": row['description']} for row in activities],
            "worlds": [row['world_number'] for row in worlds]
        })
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

            # Time Filters
            if start_dt:
                where_clauses.append("se.timestamp >= ?")
                params.append(start_dt.isoformat())
            if end_dt:
                where_clauses.append("se.timestamp <= ?")
                params.append(end_dt.isoformat())
                
            # Limit (only if no time range)
            limit_clause = ""
            if not start_dt and not end_dt and limit:
                 lim = limit if limit else 288
                 limit_clause = f"LIMIT {lim}"
                 order_by = "ORDER BY se.timestamp DESC"
                 
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
                 
        else:
            # Standard Global History (players table)
            table = "players"
            col_ts = "timestamp"
            col_count = "count"
            
            select_clause = ""
            group_by = ""
            
            if unit:
                # Aggregation Logic
                if unit == 'minute':
                    step_seconds = (step if step else 5) * 60
                    select_clause = f"SELECT datetime((strftime('%s', {col_ts}) / {step_seconds}) * {step_seconds}, 'unixepoch') as timestamp, {agg_func} as count"
                    group_by = f"GROUP BY (strftime('%s', {col_ts}) / {step_seconds})"
                elif unit == 'hour':
                    select_clause = f"SELECT strftime('%Y-%m-%dT%H:00:00Z', {col_ts}) as timestamp, {agg_func} as count"
                    group_by = f"GROUP BY strftime('%Y-%m-%dT%H', {col_ts})"
                elif unit == 'day':
                    select_clause = f"SELECT strftime('%Y-%m-%d', {col_ts}) as timestamp, {agg_func} as count"
                    group_by = f"GROUP BY strftime('%Y-%m-%d', {col_ts})"
                elif unit == 'week':
                    select_clause = f"SELECT date({col_ts}, 'weekday 0', '-6 days') as timestamp, {agg_func} as count"
                    group_by = f"GROUP BY strftime('%Y-%W', {col_ts})"
                elif unit == 'month':
                    select_clause = f"SELECT strftime('%Y-%m-01', {col_ts}) as timestamp, {agg_func} as count"
                    group_by = f"GROUP BY strftime('%Y-%m', {col_ts})"
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
            
            if not start_dt and not end_dt and not unit:
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