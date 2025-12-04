import sqlite3
import logging
from config import DB_PATH

logger = logging.getLogger(__name__)

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}")
        raise

def init_db():
    """
    Creates the database and sets it to 'Write-Ahead Logging' (WAL) mode.
    """
    conn = sqlite3.connect(DB_PATH)

    # Enable WAL mode (Crucial for concurrent access)
    conn.execute("PRAGMA journal_mode=WAL;")

    # Create the table if it doesn't exist
    conn.execute('''
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            count INTEGER
        )
    ''')

    # Create table for locations
    conn.execute('''
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
    ''')

    # Create table for activities
    conn.execute('''
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT UNIQUE
        )
    ''')

    # Create table for unique world configurations (metadata)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS world_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location_id INTEGER,
            is_f2p BOOLEAN,
            activity_id INTEGER,
            UNIQUE(location_id, is_f2p, activity_id),
            FOREIGN KEY(location_id) REFERENCES locations(id),
            FOREIGN KEY(activity_id) REFERENCES activities(id)
        )
    ''')

    # Create table for scrape events (timestamps)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS scrape_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME UNIQUE
        )
    ''')

    # Create table for time-series player counts
    conn.execute('''
        CREATE TABLE IF NOT EXISTS world_data (
            scrape_id INTEGER,
            world_number INTEGER,
            player_count INTEGER,
            detail_id INTEGER,
            PRIMARY KEY (scrape_id, world_number),
            FOREIGN KEY(scrape_id) REFERENCES scrape_events(id),
            FOREIGN KEY(detail_id) REFERENCES world_details(id)
        ) WITHOUT ROWID
    ''')

    # Create indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON players(timestamp);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scrape_timestamp ON scrape_events(timestamp);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_world_number ON world_data(world_number);")

    conn.commit()
    return conn
