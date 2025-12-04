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
    *   **Advanced Filtering**: Filter history by World, Region (Location), or World Type (F2P/Members).
    *   **Comparison Mode**: Compare F2P vs Members, or compare different Regions side-by-side.

## Requirements

*   **Python**: 3.8+
*   **Dependencies**: `flask`, `flask-cors`, `requests`, `beautifulsoup4`

## Installation

1.  **Clone the repository** (or download the source).
2.  **Create a virtual environment**:
    ```powershell
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    ```
3.  **Install dependencies**:
    ```powershell
    pip install -r requirements.txt
    ```

## Configuration

All configurable settings are located in `config.py`. You can adjust:

*   `SCRAPE_INTERVAL`: How often the global player count is checked (default: 300s / 5 mins).
*   `WORLD_SCRAPE_INTERVAL`: How often detailed world data is scraped (default: 1800s / 30 mins).
*   `REQUEST_TIMEOUT`: Timeout for network requests.
*   `DB_NAME`: Name of the SQLite database file.

## Usage

### 1. Start the Tracker
The tracker runs in the background, scraping data and saving it to `osrs_data.db`.

```powershell
python rs_tracker.py
```
*   *Note: The database will be automatically created if it doesn't exist.*

### 2. Start the Web Server
The API serves the dashboard and provides data endpoints.

```powershell
python osrs_api.py
```
*   Access the dashboard at: **http://127.0.0.1:5000**

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

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

**Configuration / Paths**
- Both scripts compute `BASE_DIR` using `__file__` and place `osrs_data.db` in the same directory as the scripts. If you move files, update the paths accordingly, or run the scripts from their directory.

**Development**
- Lint / format with your preferred tools. Tests are not included in this repository.
---
