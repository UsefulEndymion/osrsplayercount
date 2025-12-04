import requests
import sqlite3
import datetime
import time
import re
import os
from bs4 import BeautifulSoup

# --- Configuration ---
# FORCE the DB to be in the same folder as this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "osrs_data.db")
URL = "https://oldschool.runescape.com/"
SLU_URL = "https://oldschool.runescape.com/slu"

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
    # This stores unique combinations of Location + Type + Activity
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
    # Optimization: Use Composite PK and WITHOUT ROWID to save space
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

    # Create an index on timestamp for fast graphing later
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON players(timestamp);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scrape_timestamp ON scrape_events(timestamp);")
    # Note: idx_world_scrape is redundant because the PK starts with scrape_id
    conn.execute("CREATE INDEX IF NOT EXISTS idx_world_number ON world_data(world_number);")

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

def get_world_data():
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(SLU_URL, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        world_rows = []
        
        # Find all rows with class 'server-list__row'
        rows = soup.find_all('tr', class_='server-list__row')
        
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 5:
                continue
                
            # 1. World Number
            world_link = cells[0].find('a', class_='server-list__world-link')
            if not world_link:
                continue
            world_text = world_link.get_text(strip=True) # e.g., "Old School 93"
            # Extract number
            world_match = re.search(r"Old School (\d+)", world_text)
            if not world_match:
                continue
            world_number = int(world_match.group(1))
            
            # 2. Player Count
            players_text = cells[1].get_text(strip=True) # e.g., "48 players"
            # Handle "FULL" or other non-numeric cases if they exist, though usually it's "X players"
            # Sometimes it might be empty or just "players" if 0?
            # Based on inspection: "0 players" exists.
            player_match = re.search(r"([\d,]+)", players_text)
            player_count = 0
            if player_match:
                player_count = int(player_match.group(1).replace(',', ''))
            
            # 3. Location
            location = cells[2].get_text(strip=True)
            
            # 4. Type
            type_text = cells[3].get_text(strip=True).lower()
            is_f2p = 'free' in type_text
            
            # 5. Activity
            activity = cells[4].get_text(strip=True)
            
            world_rows.append({
                'world_number': world_number,
                'player_count': player_count,
                'location': location,
                'is_f2p': is_f2p,
                'activity': activity
            })
            
        return world_rows

    except Exception as e:
        print(f"Error scraping world data: {e}")
        return []

def main():
    # 1. Initialize Database
    conn = init_db()
    print(f"Bot started. Saving to: {os.path.abspath(DB_PATH)}")
    print("Press Ctrl+C to stop.\n")

    # Track last world scrape time
    last_world_scrape = 0
    WORLD_SCRAPE_INTERVAL = 300  # 5 minutes (temporarily increased for testing)

    while True:
        try:
            # 2. Scrape Data
            # Always get total count
            count = get_osrs_count()
            
            # Check if we should scrape worlds
            current_ts = time.time()
            scrape_worlds = (current_ts - last_world_scrape >= WORLD_SCRAPE_INTERVAL)
            
            world_data_list = []
            if scrape_worlds:
                world_data_list = get_world_data()
                if world_data_list:
                    last_world_scrape = current_ts
            
            # Store timezone-aware UTC timestamps in ISO 8601 so clients can
            # reliably parse and convert to the viewer's local timezone.
            # Use timezone-aware API to avoid DeprecationWarning for utcnow().
            current_time = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')

            # 3. Save to Database
            # We use a context manager (with conn:) to handle transactions safely
            with conn:
                if count:
                    conn.execute(
                        "INSERT INTO players (timestamp, count) VALUES (?, ?)",
                        (current_time, count)
                    )
                    print(f"[{current_time}] Saved total count: {count:,}")
                else:
                    print(f"[{current_time}] Failed to get total count.")
                
                if scrape_worlds:
                    if world_data_list:
                        print(f"[{current_time}] Saving data for {len(world_data_list)} worlds...")
                        
                        # 0. Create Scrape Event
                        cursor = conn.execute("INSERT INTO scrape_events (timestamp) VALUES (?)", (current_time,))
                        scrape_id = cursor.lastrowid

                        # Cache locations
                        location_map = {}
                        for row in conn.execute("SELECT name, id FROM locations"):
                            location_map[row[0]] = row[1]

                        # Cache activities
                        activity_map = {}
                        for row in conn.execute("SELECT description, id FROM activities"):
                            activity_map[row[0]] = row[1]

                        # Cache details (unique combos of loc_id, f2p, activity_id)
                        # Key: (location_id, is_f2p, activity_id) -> Value: detail_id
                        details_map = {}
                        for row in conn.execute("SELECT location_id, is_f2p, activity_id, id FROM world_details"):
                            # SQLite returns is_f2p as 0/1, ensure we match types
                            details_map[(row[0], bool(row[1]), row[2])] = row[3]

                        data_to_insert = []

                        for w in world_data_list:
                            # 1. Handle Location
                            loc_name = w['location']
                            if loc_name not in location_map:
                                cursor = conn.execute("INSERT INTO locations (name) VALUES (?)", (loc_name,))
                                location_map[loc_name] = cursor.lastrowid
                            
                            loc_id = location_map[loc_name]

                            # 2. Handle Activity
                            act_desc = w['activity']
                            if act_desc not in activity_map:
                                cursor = conn.execute("INSERT INTO activities (description) VALUES (?)", (act_desc,))
                                activity_map[act_desc] = cursor.lastrowid
                            
                            act_id = activity_map[act_desc]
                            
                            # 3. Handle Details (The unique combo)
                            detail_key = (loc_id, w['is_f2p'], act_id)
                            
                            if detail_key not in details_map:
                                cursor = conn.execute(
                                    "INSERT INTO world_details (location_id, is_f2p, activity_id) VALUES (?, ?, ?)", 
                                    (loc_id, w['is_f2p'], act_id)
                                )
                                details_map[detail_key] = cursor.lastrowid
                            
                            detail_id = details_map[detail_key]

                            # 4. Prepare Data
                            data_to_insert.append((
                                scrape_id, 
                                w['world_number'], 
                                w['player_count'],
                                detail_id
                            ))

                        conn.executemany(
                            "INSERT INTO world_data (scrape_id, world_number, player_count, detail_id) VALUES (?, ?, ?, ?)",
                            data_to_insert
                        )
                        print(f"[{current_time}] Saved world data.")
                    else:
                        print(f"[{current_time}] Failed to get world data or list empty.")

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