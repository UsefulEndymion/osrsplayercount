**OSRS Player Count**

Simple scraper and Flask API that records Old School RuneScape (OSRS) online player counts to a local SQLite database and serves the data via a small web API and frontend.

**Overview**

- **Purpose**: Scrape the OSRS homepage for player count, save time-stamped samples to `osrs_data.db`, and serve the latest and history via a Flask API and simple web UI in `templates/index.html`.
- **Main files**: `osrs_api.py` (Flask web server), `rs_tracker.py` (scraper / data writer), `requirements.txt` (dependencies), `templates/index.html` (frontend).

**Requirements**
- **Python**: 3.8+
- **Dependencies**: See `requirements.txt` in the project root. Key packages are `flask`, `flask-cors`, and `requests`.

**Installation**
- Create a virtual environment and install dependencies.

PowerShell example:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r c:\git\osrsplayercount\osrsplayercount\requirements.txt
```

Adjust the path to `requirements.txt` if you open a different working directory.

**Running the API server (development)**
- The API serves the web UI and endpoints:
  - `GET /api/latest` — returns the most recent sample
  - `GET /api/history?limit=288` — returns historical samples (default 288)

PowerShell:

```powershell
python c:\git\osrsplayercount\osrsplayercount\osrs_api.py
```

The server runs by default on `http://127.0.0.1:5000` when launched directly.

**API Documentation**

- `GET /api/latest`
  - Returns the most recent single sample.
  - Response (application/json):
    ```json
    { "timestamp": "2025-12-02T12:00:00Z", "count": 12345 }
    ```
  - The frontend formats this timestamp into the user's local timezone for display.

- `GET /api/history`
  - Returns an array of samples for plotting or analysis.
  - Query parameters (optional):
    - `limit` (int): return the last `limit` raw rows when no `start`/`end` provided (default used when omitted is 288).
    - `start` (ISO datetime string): include rows with `timestamp >= start` (example: `2025-12-01T00:00:00Z`).
    - `end` (ISO datetime string): include rows with `timestamp <= end`.
    - `unit` (string): aggregation unit, one of `minute`, `hour`, `day`. When provided the server aggregates points into buckets for that unit.
    - `step` (int): when used with `unit=minute`, bucket size in minutes (e.g. `5`, `15`, `30`). Note: minute-level queries are limited to a maximum span of 1 day.

  - Behavior:
    - If `unit=minute` and `step` provided the server aggregates into `step`-minute buckets and returns one point per bucket (timestamp is ISO UTC, count is rounded average).
    - If `unit` is `hour` or `day` the server aggregates accordingly.
    - If no aggregation params are provided the endpoint returns raw rows between `start`/`end` (if given) or the last `limit` rows.

  - Response example (raw rows):
    ```json
    [
      { "timestamp": "2025-12-01T00:00:00Z", "count": 12300 },
      { "timestamp": "2025-12-01T00:05:00Z", "count": 12250 },
      ...
    ]
    ```

  - Aggregation example (15-minute buckets):
    ```shell
    curl 'http://127.0.0.1:5000/api/history?start=2025-12-01T00:00:00Z&end=2025-12-02T00:00:00Z&unit=minute&step=15'
    ```

  - The server will return HTTP 400 for invalid or disallowed requests (for example, requesting minute-level aggregation for a span > 1 day). Error responses are JSON with an `error` message.

  - Response format for errors:
    ```json
    { "error": "Minute-level queries are limited to a maximum span of 1 day." }
    ```

**Running the tracker (scraper)**
- `rs_tracker.py` scrapes the OSRS homepage and appends samples to `osrs_data.db`. It includes a built-in database initializer.

PowerShell:

```powershell
python c:\git\osrsplayercount\osrsplayercount\rs_tracker.py
```

The tracker runs continuously and sleeps 5 minutes between scrapes. Press Ctrl+C to stop.

**Database**
- The SQLite database file is `osrs_data.db` and is created next to the scripts. The table `players` contains `id`, `timestamp`, and `count`.
- The tracker enables WAL journal mode so the DB can be read while the tracker writes.

**Configuration / Paths**
- Both scripts compute `BASE_DIR` using `__file__` and place `osrs_data.db` in the same directory as the scripts. If you move files, update the paths accordingly, or run the scripts from their directory.

**Development**
- Lint / format with your preferred tools. Tests are not included in this repository.

**License**
- This repository contains no license file. Add one if you intend to publish.
---
