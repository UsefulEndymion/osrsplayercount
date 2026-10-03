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
SCRAPE_INTERVAL = 300  # 5 minutes
WORLD_SCRAPE_INTERVAL = 1800  # 30 minutes
REQUEST_TIMEOUT = 15
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')

# How old the newest row may get before /api/health reports failure.
PLAYERS_MAX_AGE = 6 * SCRAPE_INTERVAL  # 30 minutes
WORLD_DATA_MAX_AGE = 3 * WORLD_SCRAPE_INTERVAL  # 90 minutes

# Disk check in /api/health. Off unless DISK_QUOTA_MB is set in the environment,
# because it walks every path below. PythonAnywhere's quota counts home and /tmp.
DISK_QUOTA_MB = int(os.environ.get('DISK_QUOTA_MB', 0))
DISK_ALERT_FRACTION = 0.5
DISK_USAGE_PATHS = [os.path.expanduser('~'), '/tmp']
DISK_CHECK_INTERVAL = 600  # 10 minutes
