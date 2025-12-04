import os

# Base Directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Database
DB_NAME = "osrs_data.db"
DB_PATH = os.path.join(BASE_DIR, DB_NAME)

# URLs
OSRS_MAIN_URL = "https://oldschool.runescape.com/"
OSRS_SLU_URL = "https://oldschool.runescape.com/slu"

# Scraper Settings
SCRAPE_INTERVAL = 300 # 5 minutes
WORLD_SCRAPE_INTERVAL = 1800  # 30 minutes
REQUEST_TIMEOUT = 15
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
