import requests
import sqlite3
import datetime
import time
import re
import os

# --- Configuration ---
# FORCE the DB to be in the same folder as this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "osrs_data.db")
URL = "https://oldschool.runescape.com/"

def init_db():
    """
    Creates the database and sets it to 'Write-Ahead Logging' (WAL) mode.
    WAL mode allows you to open/view the database file while the bot is writing to it.
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

    # Create an index on timestamp for fast graphing later
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON players(timestamp);")

    conn.commit()
    return conn

def get_osrs_count():
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        # Timeout ensures the bot doesn't hang forever on a bad connection
        response = requests.get(URL, headers=headers, timeout=15)
        response.raise_for_status()

        # Find the number in the HTML
        match = re.search(r"([\d,]+)\s*(?:people playing|players online)", response.text, re.IGNORECASE)
        if match:
            return int(match.group(1).replace(',', ''))

    except Exception as e:
        print(f"Error scraping: {e}")

    return None

def main():
    # 1. Initialize Database
    conn = init_db()
    print(f"Bot started. Saving to: {os.path.abspath(DB_PATH)}")
    print("Press Ctrl+C to stop.\n")

    while True:
        try:
            # 2. Scrape Data
            count = get_osrs_count()
            # Store timezone-aware UTC timestamps in ISO 8601 so clients can
            # reliably parse and convert to the viewer's local timezone.
            # Use timezone-aware API to avoid DeprecationWarning for utcnow().
            current_time = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')

            if count:
                # 3. Save to Database
                # We use a context manager (with conn:) to handle transactions safely
                with conn:
                    conn.execute(
                        "INSERT INTO players (timestamp, count) VALUES (?, ?)",
                        (current_time, count)
                    )
                print(f"[{current_time}] Saved: {count:,}")
            else:
                print(f"[{current_time}] Failed to get count.")

        except Exception as e:
            print(f"Critical Error in loop: {e}")
            # Try to reconnect to DB if something broke the connection
            try:
                conn = init_db()
            except:
                pass

        # 4. Wait 5 minutes (300 seconds)
        time.sleep(300)

if __name__ == "__main__":
    main()