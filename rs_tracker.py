import requests
import datetime
import time
import re
import os
import logging
from bs4 import BeautifulSoup

from config import DB_PATH, OSRS_MAIN_URL, OSRS_SLU_URL, WORLD_SCRAPE_INTERVAL, REQUEST_TIMEOUT, USER_AGENT, SCRAPE_INTERVAL
from database import init_db

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("tracker.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def get_osrs_count():
    try:
        headers = {'User-Agent': USER_AGENT}
        # Timeout ensures the bot doesn't hang forever on a bad connection
        response = requests.get(OSRS_MAIN_URL, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        # Find the number in the HTML
        match = re.search(r"([\d,]+)\s*(?:people playing|players online)", response.text, re.IGNORECASE)
        if match:
            return int(match.group(1).replace(',', ''))

    except Exception as e:
        logger.error(f"Error scraping total count: {e}")

    return None

def get_world_data():
    try:
        headers = {'User-Agent': USER_AGENT}
        response = requests.get(OSRS_SLU_URL, headers=headers, timeout=REQUEST_TIMEOUT)
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
        logger.error(f"Error scraping world data: {e}")
        return []

def main():
    # 1. Initialize Database
    conn = init_db()
    logger.info(f"Bot started. Saving to: {os.path.abspath(DB_PATH)}")
    
    # Track last world scrape time
    last_world_scrape = 0

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
            
            # Store timezone-aware UTC timestamps in ISO 8601
            current_time = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')

            # 3. Save to Database
            with conn:
                if count:
                    conn.execute(
                        "INSERT INTO players (timestamp, count) VALUES (?, ?)",
                        (current_time, count)
                    )
                    logger.info(f"[{current_time}] Saved total count: {count:,}")
                else:
                    logger.warning(f"[{current_time}] Failed to get total count.")
                
                if scrape_worlds:
                    if world_data_list:
                        logger.info(f"[{current_time}] Saving data for {len(world_data_list)} worlds...")
                        
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
                        details_map = {}
                        for row in conn.execute("SELECT location_id, is_f2p, activity_id, id FROM world_details"):
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
                        logger.info(f"[{current_time}] Saved world data.")
                    else:
                        logger.warning(f"[{current_time}] Failed to get world data or list empty.")

        except Exception as e:
            logger.critical(f"Critical Error in loop: {e}")
            # Try to reconnect to DB if something broke the connection
            try:
                conn = init_db()
            except:
                pass

        # 4. Wait for the configured interval
        time.sleep(SCRAPE_INTERVAL)

if __name__ == "__main__":
    main()