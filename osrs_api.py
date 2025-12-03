from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import sqlite3
import os

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
    Optional: ?limit=100 to get only the last 100 entries.
    """
    # Get 'limit' from URL (e.g., /api/history?limit=50)
    limit = request.args.get('limit', default=288, type=int) # Default 288 = 24 hours (12 * 24)
    
    conn = get_db_connection()
    query = f'SELECT timestamp, count FROM players ORDER BY id DESC LIMIT ?'
    rows = conn.execute(query, (limit,)).fetchall()
    conn.close()
    
    # Convert database rows to a clean list of dictionaries
    # We reverse it ([::-1]) so the graph draws from Left (old) to Right (new)
    data = [{"timestamp": row['timestamp'], "count": row['count']} for row in rows][::-1]
    
    return jsonify(data)

if __name__ == '__main__':
    # Run the server on port 5000
    print("API Server starting on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)