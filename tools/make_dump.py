#!/usr/bin/env python3
"""Build and publish the public data dump.

Snapshots the live SQLite database, verifies it, attaches provenance, and
publishes it to GitHub Releases together with a CSV of the global player-count
series.

Designed to run unattended as a PythonAnywhere scheduled task, and by hand from
a laptop -- nothing in here is PA-specific. `gh` is not installed on PA, so
releases go through the GitHub REST API rather than the CLI.

The database is served in WAL mode with the tracker writing to it continuously,
so the snapshot must go through VACUUM INTO (or iterdump). A byte copy of a live
WAL database is a torn file.

Usage:
    python tools/make_dump.py --dry-run           # build artifacts, publish nothing
    GITHUB_TOKEN=... python tools/make_dump.py    # build and publish
"""

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import os
import shutil
import sqlite3
import sys

REPO = "UsefulEndymion/osrsplayercount"
API = "https://api.github.com"

# Minimum age of the newest release before another one is due. PythonAnywhere
# has no monthly schedule, so the task runs daily and skips until this elapses;
# 28 makes that work out to roughly monthly.
MIN_DAYS_BETWEEN_RELEASES = 28

# VACUUM INTO landed in SQLite 3.27.
MIN_SQLITE = (3, 27, 0)

COUNTED_TABLES = [
    "players",
    "world_data",
    "scrape_events",
    "world_details",
    "locations",
    "activities",
    "history_import",
]


def log(msg):
    print(msg, flush=True)


def die(msg):
    print("ERROR: " + msg, file=sys.stderr, flush=True)
    sys.exit(1)


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------

def snapshot_vacuum(src, dest):
    """Consistent snapshot of a live WAL database, compacted on the way out.

    Does not block the tracker. Costs ~2x the database size transiently, which
    is the reason the iterdump strategy exists as an escape hatch.
    """
    conn = sqlite3.connect(src)
    try:
        conn.execute("VACUUM INTO ?", (dest,))
    finally:
        conn.close()


def sql_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def snapshot_iterdump(src, dest_gz, meta_rows):
    """Low-disk alternative: stream SQL text straight into a gzip file.

    Costs only the compressed output rather than 2x the database. Wrapped in a
    read transaction so it is still a consistent snapshot and still does not
    block the tracker. The tradeoff lands on the downloader, who has to restore
    it with `gunzip -c ... | sqlite3 new.db` instead of just opening the file.

    Switch to this when VACUUM INTO stops fitting in the PythonAnywhere quota --
    around a 300 MB database.

    `dump_meta` is appended as SQL rather than written into a copy, since having
    a copy to write into is exactly what this strategy avoids.
    """
    conn = sqlite3.connect(src)
    try:
        conn.execute("BEGIN")
        with gzip.open(dest_gz, "wt", encoding="utf-8") as fh:
            for line in conn.iterdump():
                fh.write(line + "\n")
            fh.write("CREATE TABLE IF NOT EXISTS dump_meta "
                     "(key TEXT PRIMARY KEY, value TEXT);\n")
            for key, value in meta_rows:
                fh.write("INSERT OR REPLACE INTO dump_meta VALUES (%s, %s);\n"
                         % (sql_quote(key), sql_quote(value)))
    finally:
        conn.close()


def verify(path):
    """Integrity-check the snapshot, never the original."""
    conn = sqlite3.connect(path)
    try:
        quick = conn.execute("PRAGMA quick_check").fetchall()
        if [r[0] for r in quick] != ["ok"]:
            die("quick_check failed on %s: %s" % (path, quick))

        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            die("foreign_key_check found %d violations in %s" % (len(fk), path))
    finally:
        conn.close()
    log("  integrity: quick_check ok, foreign_key_check clean")


# --------------------------------------------------------------------------
# Stats and provenance
# --------------------------------------------------------------------------

def collect_stats(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        stats = {"counts": {}}
        for table in COUNTED_TABLES:
            stats["counts"][table] = conn.execute(
                "SELECT COUNT(*) FROM %s" % table).fetchone()[0]

        row = conn.execute(
            "SELECT MIN(timestamp) lo, MAX(timestamp) hi FROM players").fetchone()
        stats["players_range"] = (row["lo"], row["hi"])

        row = conn.execute(
            "SELECT MIN(timestamp) lo, MAX(timestamp) hi FROM scrape_events").fetchone()
        stats["worlds_range"] = (row["lo"], row["hi"])

        row = conn.execute(
            "SELECT MIN(period_start) lo, MAX(period_end) hi FROM history_import").fetchone()
        stats["import_range"] = (row["lo"], row["hi"])

        # The archived peak series stops roughly two weeks before the average
        # series, and no better capture exists. Downloaders will otherwise read
        # the shorter Peak line as data loss on our end.
        stats["import_stat_ends"] = {
            r["stat"]: r["hi"] for r in conn.execute(
                "SELECT stat, MAX(period_start) hi FROM history_import GROUP BY stat")
        }

        # The 2000 sentinel: rs_tracker maps any unparseable world count to 2000,
        # which is also what a genuinely full world reports. Quantify it so the
        # release notes can be honest about the ambiguity rather than hand-wave.
        stats["sentinel"] = conn.execute(
            "SELECT COUNT(*) FROM world_data WHERE player_count = 2000").fetchone()[0]
        return stats
    finally:
        conn.close()


def build_meta_rows(generated_at, stats):
    """Provenance baked into the file itself.

    The .db travels separately from the release page and will get re-hosted, so
    anything not inside the file is liable to be lost.
    """
    ends = stats["import_stat_ends"]
    return [
        ("generated_at", generated_at),
        ("source", "https://www.osrsplayercount.com"),
        ("repository", "https://github.com/%s" % REPO),
        ("license", "MIT (see repository LICENSE)"),
        ("schema_version", "1"),
        ("era_1_imported",
         "%s..%s weekly avg+peak, global only, table history_import, "
         "sourced from misplaceditems.com via the Wayback Machine"
         % (stats["import_range"][0], stats["import_range"][1])),
        ("era_2_global_only",
         "%s..%s native ~5-minute global samples, table players; "
         "no per-world data exists for this window"
         % (stats["players_range"][0], stats["worlds_range"][0])),
        ("era_3_full",
         "%s..%s native ~5-minute global samples plus ~30-minute per-world "
         "scrapes, tables players + world_data"
         % (stats["worlds_range"][0], stats["worlds_range"][1])),
        ("caveat_full_worlds",
         "world_data.player_count = 2000 means either a genuinely full world or "
         "a count that failed to parse; the two are not distinguishable. "
         "%d rows affected." % stats["sentinel"]),
        ("caveat_retired_worlds",
         "There is no worlds dimension table, so worlds retired by Jagex are "
         "not distinguishable from live ones."),
        ("caveat_activity_names",
         "Activity names are stored exactly as published and are not "
         "de-duplicated; variants such as 'Castle Wars 1' and 'Castle Wars 2' "
         "are distinct rows by design."),
        ("caveat_peak_series_ends_early",
         "In history_import the peak series ends %s while the average series "
         "runs to %s. The archive holds nothing later for peak; this is a gap "
         "in the source, not data loss."
         % (ends.get("peak", "n/a"), ends.get("avg", "n/a"))),
    ]


def write_dump_meta(path, meta_rows):
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS dump_meta "
                     "(key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany("INSERT OR REPLACE INTO dump_meta VALUES (?, ?)", meta_rows)
        conn.commit()
    finally:
        conn.close()
    log("  dump_meta: %d provenance rows" % len(meta_rows))


def write_global_csv(db_path, csv_path):
    """The stitched global series, deliberately unaggregated.

    Downloaders can aggregate; they cannot un-aggregate. Same source-fidelity
    principle that keeps the activity names unmerged.
    """
    query = (
        "SELECT period_start AS ts, count, stat, 'week' AS granularity, source "
        "FROM history_import "
        "UNION ALL "
        "SELECT timestamp, count, 'sample', 'instant', 'native' FROM players "
        "ORDER BY ts"
    )
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(query)
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["timestamp", "count", "stat", "granularity", "source"])
            n = 0
            for row in rows:
                writer.writerow(row)
                n += 1
    finally:
        conn.close()
    log("  global CSV: %d rows" % n)
    return n


# --------------------------------------------------------------------------
# Packaging
# --------------------------------------------------------------------------

def gzip_file(src, dest):
    with open(src, "rb") as fin, gzip.open(dest, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout, length=1024 * 1024)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def render_notes(date_str, stats, assets, csv_rows):
    counts = stats["counts"]
    lines = [
        "Full snapshot of the OSRS Player Count database, taken %s." % date_str,
        "",
        "## Files",
        "",
        "| file | size | sha256 |",
        "|---|---|---|",
    ]
    for name, path, digest in assets:
        lines.append("| `%s` | %s | `%s` |"
                     % (name, human(os.path.getsize(path)), digest))

    lines += [
        "",
        "## Coverage",
        "",
        "This dataset has **three eras**, and they do not all start at the same time:",
        "",
        "| period | what exists | tables |",
        "|---|---|---|",
        "| %s .. %s | weekly average + peak, global only | `history_import` |"
        % (stats["import_range"][0][:10], stats["import_range"][1][:10]),
        "| %s .. %s | ~5-minute global samples, **no per-world data** | `players` |"
        % (stats["players_range"][0][:10], stats["worlds_range"][0][:10]),
        "| %s .. %s | ~5-minute global samples + ~30-minute per-world scrapes | `players`, `world_data` |"
        % (stats["worlds_range"][0][:10], stats["worlds_range"][1][:10]),
        "",
        "The middle era is the one that catches people out: global history begins "
        "before per-world tracking did, so joining the two leaves a gap of several "
        "weeks where only the global series exists.",
        "",
        "## Row counts",
        "",
        "| table | rows |",
        "|---|---|",
    ]
    for table in COUNTED_TABLES:
        lines.append("| `%s` | %s |" % (table, "{:,}".format(counts[table])))

    sentinel_pct = 100.0 * stats["sentinel"] / max(counts["world_data"], 1)
    lines += [
        "",
        "## Known caveats",
        "",
        "- **`player_count = 2000` is ambiguous.** The tracker maps any count it "
        "cannot parse to 2000, which is also what a genuinely full world reports. "
        "The two are not distinguishable after the fact. Affects %s rows (%.2f%% of "
        "`world_data`); most appear to be real full-world readings, since they "
        "concentrate in a handful of consistently busy worlds."
        % ("{:,}".format(stats["sentinel"]), sentinel_pct),
        "- **Retired worlds are not flagged.** There is no `worlds` dimension table, "
        "so worlds Jagex has since removed look identical to live ones.",
        "- **Activity names are not de-duplicated.** Variants like `Castle Wars 1` / "
        "`Castle Wars 2` are stored exactly as published. Group them at display time "
        "if you need to; the raw strings are kept for fidelity.",
        "- **The archived peak series ends before the average series.** In "
        "`history_import`, peak runs to %s but average runs to %s. Nothing later "
        "exists for peak in the source archive, so a Peak line will look like it "
        "stops early. That is the source, not data loss."
        % (stats["import_stat_ends"].get("peak", "n/a"),
           stats["import_stat_ends"].get("avg", "n/a")),
        "- **Gaps exist** wherever the tracker was down. They are not interpolated.",
        "",
        "## Provenance",
        "",
        "History before %s is weekly pre-aggregated data imported from "
        "misplaceditems.com via the Wayback Machine, and is credited per-row in "
        "`history_import.source`. Everything after is native tracking by this project."
        % stats["import_range"][1][:10],
        "",
        "The snapshot also carries a `dump_meta` table repeating all of the above, so "
        "the file stays self-describing if it gets separated from this page.",
        "",
        "## Using it",
        "",
        "```sh",
        "gunzip %s" % assets[0][0],
        "sqlite3 %s \"SELECT * FROM dump_meta;\"" % assets[0][0][:-3],
        "```",
        "",
        "`%s` is the global player-count series on its own (%s rows), for anyone who "
        "does not want to open a SQLite database."
        % (assets[1][0], "{:,}".format(csv_rows)),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------

def gh_headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def check_release_interval(token, force, if_due):
    """Guard against publishing on top of a release that is still recent.

    Two callers with opposite needs. A person running this by hand wants a loud
    error, since they meant to publish now. The scheduled task runs daily and
    needs a quiet exit 0 -- anything else emails a failure report every day of
    the month it is waiting.
    """
    import requests

    resp = requests.get("%s/repos/%s/releases/latest" % (API, REPO),
                        headers=gh_headers(token), timeout=30)
    if resp.status_code == 404:
        log("  no existing release; this will be the first")
        return
    if not resp.ok:
        die("could not read latest release: %s %s" % (resp.status_code, resp.text[:200]))

    published = resp.json().get("published_at")
    if not published:
        return
    when = dt.datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc)
    age = (dt.datetime.now(dt.timezone.utc) - when).days
    log("  newest release is %d days old (%s)" % (age, published[:10]))
    if age >= MIN_DAYS_BETWEEN_RELEASES or force:
        return

    if if_due:
        log("Not due for %d more days. Nothing to do."
            % (MIN_DAYS_BETWEEN_RELEASES - age))
        sys.exit(0)
    die("newest release is only %d days old (minimum %d). Use --force to "
        "override." % (age, MIN_DAYS_BETWEEN_RELEASES))


def read_token(args):
    """Token from a file in preference to the environment.

    A scheduled task's command line is visible in PythonAnywhere's task list and
    in process listings, so the token should not be typed into it.
    """
    if args.token_file:
        path = os.path.expanduser(args.token_file)
        if not os.path.exists(path):
            die("no token file at %s" % path)
        with open(path) as fh:
            token = fh.read().strip()
        if not token:
            die("token file %s is empty" % path)
        return token
    return os.environ.get("GITHUB_TOKEN")


def publish(token, tag, title, body, assets, draft=False):
    import requests

    resp = requests.post(
        "%s/repos/%s/releases" % (API, REPO),
        headers=gh_headers(token),
        json={"tag_name": tag, "name": title, "body": body,
              "draft": draft, "prerelease": False},
        timeout=60,
    )
    if not resp.ok:
        die("creating release failed: %s %s" % (resp.status_code, resp.text[:400]))

    release = resp.json()
    upload_url = release["upload_url"].split("{")[0]

    for name, path, _digest in assets:
        log("  uploading %s (%s)" % (name, human(os.path.getsize(path))))
        with open(path, "rb") as fh:
            up = requests.post(
                upload_url,
                headers=dict(gh_headers(token),
                             **{"Content-Type": "application/octet-stream"}),
                params={"name": name},
                data=fh,
                timeout=1800,
            )
        if not up.ok:
            die("uploading %s failed: %s %s. The release was created at %s -- "
                "delete it before retrying."
                % (name, up.status_code, up.text[:300], release["html_url"]))

    return release["html_url"]


# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None,
                        help="path to the live database (default: from config.py)")
    parser.add_argument("--out-dir", default=".",
                        help="where to write artifacts (default: cwd)")
    parser.add_argument("--strategy", choices=("vacuum", "iterdump"),
                        default="vacuum",
                        help="vacuum: 2x disk, ships a .db.gz. "
                             "iterdump: low disk, ships a .sql.gz")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and verify artifacts but publish nothing")
    parser.add_argument("--force", action="store_true",
                        help="publish even if the newest release is recent")
    parser.add_argument("--draft", action="store_true",
                        help="create the release as a draft: assets upload but "
                             "nothing is public and no tag is created until you "
                             "publish it. Use this to test the publish path.")
    parser.add_argument("--token-file",
                        help="read the GitHub token from this file instead of "
                             "$GITHUB_TOKEN. Prefer this for scheduled tasks.")
    parser.add_argument("--if-due", action="store_true",
                        help="exit 0 without publishing when a release is not "
                             "yet due. For scheduled tasks, which run daily.")
    parser.add_argument("--keep-artifacts", action="store_true",
                        help="keep the local asset files after a successful "
                             "publish instead of deleting them")
    args = parser.parse_args()

    if sqlite3.sqlite_version_info < MIN_SQLITE:
        die("SQLite %s is too old; need %s or newer for VACUUM INTO"
            % (sqlite3.sqlite_version, ".".join(str(n) for n in MIN_SQLITE)))

    db_path = args.db
    if db_path is None:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import DB_PATH
        db_path = DB_PATH
    if not os.path.exists(db_path):
        die("no database at %s" % db_path)

    token = read_token(args)
    if not args.dry_run and not token:
        die("No token. Set GITHUB_TOKEN or pass --token-file. Use --dry-run to "
            "build artifacts without publishing.")

    now = dt.datetime.now(dt.timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    tag = "data-%s" % date_str

    out = args.out_dir
    if not os.path.isdir(out):
        os.makedirs(out)

    db_name = "osrs_data-%s.db" % date_str
    snap = os.path.join(out, db_name)
    csv_name = "osrs_global-%s.csv" % date_str
    csv_path = os.path.join(out, csv_name)

    if not args.dry_run:
        log("Checking release interval...")
        check_release_interval(token, args.force, args.if_due)

    # The intermediate uncompressed snapshot is the expensive thing on a quota'd
    # host, so its removal goes in a finally -- a failed upload must not strand
    # ~100 MB until someone notices.
    try:
        if args.strategy == "iterdump":
            gz_name = "osrs_data-%s.sql.gz" % date_str
            gz_path = os.path.join(out, gz_name)

            # There is no copy to check -- not making one is the entire point of
            # this strategy -- so the source is all that can be verified.
            log("Verifying source...")
            verify(db_path)

            log("Collecting stats...")
            stats = collect_stats(db_path)

            log("Writing global series CSV...")
            csv_rows = write_global_csv(db_path, csv_path)

            log("Snapshotting (iterdump -> %s)..." % gz_name)
            snapshot_iterdump(db_path, gz_path,
                              build_meta_rows(generated_at, stats))
            log("  wrote %s" % human(os.path.getsize(gz_path)))
        else:
            gz_name = db_name + ".gz"
            gz_path = os.path.join(out, gz_name)

            log("Snapshotting (VACUUM INTO %s)..." % db_name)
            snapshot_vacuum(db_path, snap)
            log("  %s -> %s"
                % (human(os.path.getsize(db_path)), human(os.path.getsize(snap))))

            log("Verifying snapshot...")
            verify(snap)

            log("Collecting stats...")
            stats = collect_stats(snap)

            log("Writing provenance...")
            write_dump_meta(snap, build_meta_rows(generated_at, stats))

            log("Writing global series CSV...")
            csv_rows = write_global_csv(snap, csv_path)

            log("Compressing...")
            gzip_file(snap, gz_path)
            log("  %s -> %s"
                % (human(os.path.getsize(snap)), human(os.path.getsize(gz_path))))
    finally:
        if os.path.exists(snap):
            os.remove(snap)

    assets = [
        (gz_name, gz_path, sha256(gz_path)),
        (csv_name, csv_path, sha256(csv_path)),
    ]
    body = render_notes(date_str, stats, assets, csv_rows)
    title = "OSRS player count data — %s" % date_str

    notes_path = os.path.join(out, "release-notes-%s.md" % date_str)
    with open(notes_path, "w", encoding="utf-8") as fh:
        fh.write(body)

    if args.dry_run:
        log("")
        log("Dry run. Nothing published. Artifacts:")
        for name, path, digest in assets:
            log("  %s  %s  %s" % (name, human(os.path.getsize(path)), digest))
        log("  %s" % notes_path)
        return

    log("Publishing %s%s..." % (tag, " (draft)" if args.draft else ""))
    url = publish(token, tag, title, body, assets, draft=args.draft)

    # GitHub now holds the only copies that matter. Left in place these would
    # accumulate ~40 MB per run against a quota'd host.
    if not args.keep_artifacts:
        for _name, path, _digest in assets:
            os.remove(path)
        log("  removed local copies (--keep-artifacts to retain)")

    if args.draft:
        log("Draft created (not public, no tag yet): %s" % url)
        log("Publish it with:  gh release edit %s --draft=false" % tag)
        log("Discard it with:  gh release delete %s --yes" % tag)
    else:
        log("Published: %s" % url)


if __name__ == "__main__":
    main()
