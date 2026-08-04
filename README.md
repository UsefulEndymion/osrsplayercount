# OSRS Player Count Tracker

**Live Site:** [www.osrsplayercount.com](https://www.osrsplayercount.com)

A comprehensive tool to track, store, and visualize Old School RuneScape (OSRS) player populations. It scrapes data from the OSRS homepage and the official server list, storing it in a local SQLite database, and serves it via a Flask API with a modern, interactive frontend.

## Features

*   **Global Player Count**: Tracks the total number of players online.
*   **World-Level Tracking**: Records population for every individual world.
*   **Detailed Metrics**: Captures world location, activity (minigames/skills), and type (F2P/Members).
*   **Interactive Dashboard**:
    *   Real-time player count display.
    *   Historical graphs with zoom and pan capabilities.
    *   **Advanced Filtering**: Filter history by World, Region (Location), World Type (F2P/Members), or Activity.
    *   **Comparison Mode**: Compare F2P vs Members, compare Regions side-by-side, or pick up to 8 specific worlds and activities to plot together.
    *   **Shareable Links**: The URL tracks the current view, so copying it from the address bar hands someone the exact filters, comparison, granularity and range you are looking at. A preset range stays relative ("last 7d"); a hand-typed range is shared as the absolute instants it covers.

## Requirements

*   **Python**: 3.8+
*   **Dependencies**: `flask`, `flask-cors`, `requests`, `beautifulsoup4`

## Installation

1.  **Clone the repository** (or download the source).
2.  **Create a virtual environment and install dependencies**:

    Linux / macOS:
    ```sh
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    ```

    Windows (PowerShell):
    ```powershell
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    ```

Commands below assume the virtual environment is active. On Linux you can skip
activating it by calling `.venv/bin/python` directly.

## Configuration

All configurable settings are located in `config.py`. You can adjust:

*   `SCRAPE_INTERVAL`: How often the global player count is checked (default: 300s / 5 mins).
*   `WORLD_SCRAPE_INTERVAL`: How often detailed world data is scraped (default: 1800s / 30 mins).
*   `REQUEST_TIMEOUT`: Timeout for network requests.
*   `DB_NAME`: Name of the SQLite database file.

## Usage

### 1. Start the Tracker
The tracker runs in the background, scraping data and saving it to `osrs_data.db`.

```sh
python rs_tracker.py
```
*   *Note: The database will be automatically created if it doesn't exist.*

### 2. Start the Web Server
The API serves the dashboard and provides data endpoints.

```sh
python osrs_api.py
```
*   Access the dashboard at: **http://127.0.0.1:5000**
*   *Note: this entry point enables Flask's debug mode and is for local development
    only. In production the app is loaded through WSGI, which imports `app` and never
    runs this block.*

## API Documentation

### `GET /api/latest`
Returns the most recent global snapshot.
*   **Response**:
    ```json
    {
        "timestamp": "2025-12-04T12:00:00Z",
        "count": 125000,
        "f2p_count": 45000,
        "members_count": 80000
    }
    ```

### `GET /api/metadata`
Returns available filters for the frontend.
*   **Response**: Lists of all tracked `worlds`, `locations`, and `activities`.

### `GET /api/history`
Returns historical data points for graphing.
*   **Parameters**:
    *   `start` / `end`: ISO timestamps to define the range.
    *   `limit`: Number of points to return (if no range specified).
    *   `unit` / `step`: For data aggregation (e.g., `unit=minute`, `step=15`).
    *   **Filters**:
        *   `world_id`: Filter by specific world number.
        *   `location_id`: Filter by region ID.
        *   `is_f2p`: `1` for F2P, `0` for Members.
*   When any filter is used, per-world data is queried and the range defaults to the
    last 7 days if `start` is omitted. Unfiltered global queries have no such default.

## Deployment

The live site runs on [PythonAnywhere](https://www.pythonanywhere.com/). Two pieces run
independently:

*   **Web app** — loaded via a WSGI file that imports `app` from `osrs_api.py`. Flask's
    `__main__` block is not used.
*   **Tracker** — `rs_tracker.py` runs separately as an always-on task. Without it the
    site serves stale data.

Notes for anyone reproducing this setup:

*   Map `/static/` in the Web tab so static files are served directly rather than
    through Flask.
*   The database is a single SQLite file alongside the code. `VACUUM INTO` (never `cp`)
    is the safe way to snapshot it while the tracker is writing.
*   Nothing about the application is host-specific; any WSGI host plus a scheduled
    process for the tracker will work.

## Data Dumps

Full database snapshots are published as
[GitHub Releases](https://github.com/UsefulEndymion/osrsplayercount/releases/latest),
tagged `data-YYYY-MM-DD`. Each release carries the compressed SQLite database and a CSV
of the global player-count series, along with row counts, checksums, and known caveats.

`tools/make_dump.py` builds and publishes them; see `--help` for options, including
`--dry-run` to build the assets without publishing.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

**Configuration / Paths**
- Both scripts compute `BASE_DIR` using `__file__` and place `osrs_data.db` in the same directory as the scripts. If you move files, update the paths accordingly, or run the scripts from their directory.

**Development**
- Lint / format with your preferred tools. Tests are not included in this repository.
---
