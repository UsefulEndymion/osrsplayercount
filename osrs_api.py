from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import sqlite3
import os
from datetime import datetime, timedelta, timezone

# --- PATH CONFIGURATION ---
# We need absolute paths for PythonAnywhere
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "osrs_data.db")

# Tell Flask where to find the static files and templates
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))
CORS(app)

DB_NAME = "osrs_data.db"

def get_db_connection():
    # Connect to the database file
    conn = sqlite3.connect(DB_PATH)
    # This little line is magic: it lets us access columns by name (row['count'])
    # instead of index (row[2])
    conn.row_factory = sqlite3.Row 
    return conn

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/latest')
def get_latest():
    """Returns the most recent single data point."""
    conn = get_db_connection()
    # Get the last row added
    row = conn.execute('SELECT * FROM players ORDER BY id DESC LIMIT 1').fetchone()
    conn.close()
    
    if row:
        return jsonify({
            "timestamp": row['timestamp'],
            "count": row['count']
        })
    else:
        return jsonify({"error": "No data found"}), 404

@app.route('/api/history')
def get_history():
        """
        Returns data points for a graph.

        Query parameters (all optional):
            - limit (int): return the last `limit` raw rows (default used when no start/end provided; default 288)
            - start (ISO datetime string): include rows with timestamp >= start
            - end (ISO datetime string): include rows with timestamp <= end
            - unit (str): aggregation unit, one of 'minute', 'hour', 'day'. When provided the server will aggregate points into buckets for that unit.
            - step (int): when used with `unit=minute`, bucket size in minutes (e.g. 5, 15, 30). Note: minute-level queries are limited to a maximum span of 1 day.

        Behavior:
            - If `unit=minute` and `step` provided the server aggregates into `step`-minute buckets (timestamp returned as ISO UTC) and returns one point per bucket.
            - If `unit` is 'hour' or 'day' the server aggregates by hour/day respectively and returns one point per bucket.
            - If no aggregation params provided, the endpoint returns raw rows between `start`/`end` (if given) or the last `limit` rows.

        Response: JSON array of objects: [{"timestamp": <ISO UTC string>, "count": <number>}, ...]
        """
    # Query params
    limit = request.args.get('limit', default=None, type=int)
    start = request.args.get('start', default=None, type=str)
    end = request.args.get('end', default=None, type=str)
    unit = request.args.get('unit', default=None, type=str)  # e.g., 'minute','hour','day'
    step = request.args.get('step', default=None, type=int)   # e.g., 5 (minutes)

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

    # Enforce server-side limit: minute-level queries (unit=minute) cannot span more than 1 day
    if unit == 'minute' and step:
        # If start/end are provided, validate duration. If missing, treat as last-24h (allowed)
        if start_dt is None and end_dt is None:
            # allowed; we'll default to last 24h in the query logic if needed
            pass
        else:
            # If one of start or end is missing, we can't compute duration accurately — reject request
            if start_dt is None or end_dt is None:
                return jsonify({"error": "Please provide both 'start' and 'end' for minute-level queries."}), 400
            duration = end_dt - start_dt
            if duration > timedelta(days=1):
                return jsonify({"error": "Minute-level queries are limited to a maximum span of 1 day."}), 400

    conn = get_db_connection()

    # If client requested aggregation by minute with a step (e.g., 5m,15m,30m)
    if unit == 'minute' and step:
        # Aggregate into buckets of `step` minutes using unix epoch arithmetic
        step_seconds = step * 60
        sql = '''
            SELECT
                strftime('%Y-%m-%dT%H:%M:%SZ', (strftime('%s', timestamp) - (strftime('%s', timestamp) % ?)), 'unixepoch') AS timestamp,
                ROUND(AVG(count)) AS count
            FROM players
            WHERE (? IS NULL OR timestamp >= ?) AND (? IS NULL OR timestamp <= ?)
            GROUP BY (strftime('%s', timestamp) - (strftime('%s', timestamp) % ?))
            ORDER BY timestamp ASC
        '''
        params = (step_seconds, start, start, end, end, step_seconds)
        rows = conn.execute(sql, params).fetchall()

    elif unit in ('hour', 'day'):
        # Simple aggregation into hour/day buckets
        if unit == 'hour':
            bucket_fmt = "%Y-%m-%dT%H:00:00Z"
        else:
            bucket_fmt = "%Y-%m-%dT00:00:00Z"
        sql = f'''
            SELECT
                strftime('{bucket_fmt}', timestamp) AS timestamp,
                ROUND(AVG(count)) AS count
            FROM players
            WHERE (? IS NULL OR timestamp >= ?) AND (? IS NULL OR timestamp <= ?)
            GROUP BY timestamp
            ORDER BY timestamp ASC
        '''
        params = (start, start, end, end)
        rows = conn.execute(sql, params).fetchall()

    else:
        # Default: return last `limit` rows (preserve old behavior) or rows between start/end
        if start or end:
            sql = 'SELECT timestamp, count FROM players WHERE (? IS NULL OR timestamp >= ?) AND (? IS NULL OR timestamp <= ?) ORDER BY timestamp ASC'
            params = (start, start, end, end)
            rows = conn.execute(sql, params).fetchall()
        else:
            # Use limit if provided, otherwise default to 288
            use_limit = limit if limit is not None else 288
            sql = 'SELECT timestamp, count FROM players ORDER BY id DESC LIMIT ?'
            rows = conn.execute(sql, (use_limit,)).fetchall()
            rows = rows[::-1]  # reverse chronological order

    conn.close()

    data = [{"timestamp": row['timestamp'], "count": row['count']} for row in rows]
    return jsonify(data)

if __name__ == '__main__':
    # Run the server on port 5000
    print("API Server starting on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)