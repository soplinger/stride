#!/usr/bin/env python3
"""Stride: a private running log with pace, weekly mileage, goals and bests."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

MI_TO_KM = 1.609344
MAX_KM = 500.0
DEFAULT_DB = Path.home() / ".stride.json"
CSV_FIELDS = ["date", "distance", "duration", "note"]
PB_DISTANCES = [
    (5.0, "5 km"),
    (10.0, "10 km"),
    (21.0975, "Half marathon"),
    (42.195, "Marathon"),
]


class StrideError(Exception):
    """A user-facing problem: bad input, an unreadable file, or a bad request."""


# --------------------------------------------------------------------------
# Parsing and formatting
# --------------------------------------------------------------------------

def parse_date(text: str, today: dt.date) -> dt.date:
    try:
        day = dt.date.fromisoformat(text.strip())
    except ValueError:
        raise StrideError(f"invalid date {text!r}; use YYYY-MM-DD")
    if day > today:
        raise StrideError(f"date {day.isoformat()} is in the future")
    return day


def parse_filter_date(text: str) -> dt.date:
    """Dates used only for filtering may lie in the future."""
    try:
        return dt.date.fromisoformat(text.strip())
    except ValueError:
        raise StrideError(f"invalid date {text!r}; use YYYY-MM-DD")


def parse_distance(text: str, miles: bool = False) -> float:
    """Return the distance in kilometres, rounded to the metre."""
    try:
        value = float(text)
    except ValueError:
        raise StrideError(f"invalid distance {text!r}")
    if not math.isfinite(value) or value <= 0:
        raise StrideError(f"distance must be a positive number, got {text!r}")
    km = value * MI_TO_KM if miles else value
    if km > MAX_KM:
        raise StrideError(f"distance {km:.1f} km is over the {MAX_KM:.0f} km limit")
    return round(km, 3)


def parse_duration(text: str) -> int:
    """Parse SS, MM:SS or H:MM:SS into whole seconds."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(p.isdecimal() for p in parts):
        raise StrideError(f"invalid duration {text!r}; use SS, MM:SS or H:MM:SS")
    nums = [int(p) for p in parts]
    if len(nums) > 1 and any(n >= 60 for n in nums[1:]):
        raise StrideError(f"invalid duration {text!r}; minutes and seconds must be under 60")
    seconds = 0
    for n in nums:
        seconds = seconds * 60 + n
    if seconds <= 0:
        raise StrideError(f"duration must be greater than zero, got {text!r}")
    return seconds


def format_duration(seconds: int) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_distance(km: float, units: str) -> str:
    if units == "mi":
        return f"{km / MI_TO_KM:.2f} mi"
    return f"{km:.2f} km"


def pace_per_unit(seconds: int, km: float, units: str) -> float:
    """Seconds per kilometre or per mile, depending on units."""
    per_km = seconds / km
    return per_km * MI_TO_KM if units == "mi" else per_km


def format_pace(seconds: int, km: float, units: str) -> str:
    total = round(pace_per_unit(seconds, km, units))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}/{units}"


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def empty_db() -> dict:
    return {"version": 1, "runs": [], "goal_weekly_km": None}


def load_db(path: Path) -> dict:
    if not path.exists():
        return empty_db()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StrideError(f"cannot read {path}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        raise StrideError(f"{path} is not a stride log")
    for index, run in enumerate(data["runs"], start=1):
        problem = check_stored_run(run)
        if problem:
            raise StrideError(f"{path} is damaged: run {index} {problem}")
    data.setdefault("goal_weekly_km", None)
    return data


def check_stored_run(run) -> str | None:
    """Return a description of what is wrong with a stored run, or None if it is fine."""
    if not isinstance(run, dict):
        return "is not an object"
    if not isinstance(run.get("id"), int) or isinstance(run.get("id"), bool):
        return "has no integer id"
    try:
        dt.date.fromisoformat(run.get("date"))
    except (TypeError, ValueError):
        return "has an invalid date"
    km = run.get("km")
    if not isinstance(km, (int, float)) or not math.isfinite(km) or km <= 0:
        return "has an invalid distance"
    seconds = run.get("seconds")
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        return "has an invalid duration"
    return None


def save_db(path: Path, db: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(db, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise StrideError(f"cannot write {path}: {exc}")


def next_id(runs: list) -> int:
    return max((r["id"] for r in runs), default=0) + 1


# --------------------------------------------------------------------------
# Analysis (pure functions over run dicts)
# --------------------------------------------------------------------------

def run_date(run: dict) -> dt.date:
    return dt.date.fromisoformat(run["date"])


def week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def weekly_totals(runs: list) -> dict:
    """Map each Monday to the kilometres run in that ISO week."""
    totals: dict = {}
    for run in runs:
        monday = week_start(run_date(run))
        totals[monday] = totals.get(monday, 0.0) + run["km"]
    return totals


def week_km(runs: list, day: dt.date) -> float:
    start = week_start(day)
    end = start + dt.timedelta(days=7)
    return sum(r["km"] for r in runs if start <= run_date(r) < end)


def streak_length(runs: list, today: dt.date) -> int:
    """Consecutive days with a run, counting back from today or yesterday."""
    days = {run_date(r) for r in runs}
    day = today if today in days else today - dt.timedelta(days=1)
    count = 0
    while day in days:
        count += 1
        day -= dt.timedelta(days=1)
    return count


def personal_bests(runs: list) -> list:
    """Fastest average pace among runs at least as long as each distance."""
    results = []
    for threshold, label in PB_DISTANCES:
        eligible = [r for r in runs if r["km"] >= threshold]
        best = min(eligible, key=lambda r: r["seconds"] / r["km"]) if eligible else None
        results.append((label, best))
    return results


def summarize(runs: list) -> dict:
    total_km = sum(r["km"] for r in runs)
    total_seconds = sum(r["seconds"] for r in runs)
    return {
        "runs": len(runs),
        "km": total_km,
        "seconds": total_seconds,
        "avg_seconds_per_km": total_seconds / total_km if total_km else None,
        "longest": max(runs, key=lambda r: r["km"]) if runs else None,
    }


# --------------------------------------------------------------------------
# CSV import and export
# --------------------------------------------------------------------------

def read_csv_runs(handle, miles: bool, today: dt.date) -> tuple:
    """Return (runs, errors). Errors carry the file line number.

    Nothing is dropped silently: every non-blank row is either a run or an error.
    """
    reader = csv.reader(handle)
    header = next(reader, None)
    if header is None:
        raise StrideError("CSV file is empty")
    columns = [h.strip().lower() for h in header]
    missing = [c for c in ("date", "distance", "duration") if c not in columns]
    if missing:
        raise StrideError(f"CSV header is missing column(s): {', '.join(missing)}")

    runs, errors = [], []
    for row in reader:
        lineno = reader.line_num
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(columns):
            errors.append(f"line {lineno}: expected {len(columns)} columns, found {len(row)}")
            continue
        record = dict(zip(columns, row))
        try:
            runs.append({
                "date": parse_date(record["date"], today).isoformat(),
                "km": parse_distance(record["distance"], miles),
                "seconds": parse_duration(record["duration"]),
                "note": record.get("note", "").strip(),
            })
        except StrideError as exc:
            errors.append(f"line {lineno}: {exc}")
    return runs, errors


def write_csv_runs(handle, runs: list) -> None:
    writer = csv.writer(handle)
    writer.writerow(CSV_FIELDS)
    for run in sorted(runs, key=lambda r: (r["date"], r["id"])):
        writer.writerow([
            run["date"],
            f"{run['km']:.3f}",
            format_duration(run["seconds"]),
            run.get("note", ""),
        ])


# --------------------------------------------------------------------------
# Command handlers
# --------------------------------------------------------------------------

def render_table(headers: list, rows: list) -> list:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return lines


def cmd_add(db, args, today, out):
    day = parse_date(args.date, today)
    km = parse_distance(args.distance, args.mi)
    seconds = parse_duration(args.duration)
    run = {
        "id": next_id(db["runs"]),
        "date": day.isoformat(),
        "km": km,
        "seconds": seconds,
        "note": args.note.strip(),
    }
    db["runs"].append(run)
    print(
        f"added #{run['id']}: {run['date']} {format_distance(km, args.units)} "
        f"in {format_duration(seconds)} at {format_pace(seconds, km, args.units)}",
        file=out,
    )
    return True


def cmd_list(db, args, today, out):
    runs = db["runs"]
    since = parse_filter_date(args.since) if args.since else None
    until = parse_filter_date(args.until) if args.until else None
    if since and until and since > until:
        raise StrideError("--since must not be after --until")
    picked = [
        r for r in runs
        if (since is None or run_date(r) >= since)
        and (until is None or run_date(r) <= until)
    ]
    picked.sort(key=lambda r: (r["date"], r["id"]))
    if not picked:
        print("no runs match", file=out)
        return False
    rows = [
        [
            f"#{r['id']}",
            r["date"],
            format_distance(r["km"], args.units),
            format_duration(r["seconds"]),
            format_pace(r["seconds"], r["km"], args.units),
            r["note"],
        ]
        for r in picked
    ]
    for line in render_table(["id", "date", "distance", "time", "pace", "note"], rows):
        print(line, file=out)
    return False


def cmd_delete(db, args, today, out):
    for i, run in enumerate(db["runs"]):
        if run["id"] == args.id:
            del db["runs"][i]
            print(f"deleted #{args.id} ({run['date']})", file=out)
            return True
    raise StrideError(f"no run with id {args.id}")


def cmd_stats(db, args, today, out):
    s = summarize(db["runs"])
    if not s["runs"]:
        print("no runs logged yet", file=out)
        return False
    longest = s["longest"]
    print(f"runs:         {s['runs']}", file=out)
    print(f"distance:     {format_distance(s['km'], args.units)}", file=out)
    print(f"time:         {format_duration(s['seconds'])}", file=out)
    print(
        f"average pace: {format_pace(s['seconds'], s['km'], args.units)}",
        file=out,
    )
    print(
        f"longest run:  {format_distance(longest['km'], args.units)} on {longest['date']}",
        file=out,
    )
    print(f"streak:       {streak_length(db['runs'], today)} day(s)", file=out)
    return False


def cmd_weeks(db, args, today, out):
    if args.last < 1:
        raise StrideError("--last must be at least 1")
    totals = weekly_totals(db["runs"])
    current = week_start(today)
    weeks = [current - dt.timedelta(weeks=n) for n in range(args.last - 1, -1, -1)]
    peak = max((totals.get(w, 0.0) for w in weeks), default=0.0)
    for monday in weeks:
        km = totals.get(monday, 0.0)
        bar = "#" * round(km / peak * 30) if peak else ""
        iso_year, iso_week, _ = monday.isocalendar()
        label = f"{iso_year}-W{iso_week:02d}"
        print(f"{label}  {format_distance(km, args.units):>10}  {bar}", file=out)
    return False


def cmd_pb(db, args, today, out):
    if not db["runs"]:
        print("no runs logged yet", file=out)
        return False
    for label, best in personal_bests(db["runs"]):
        if best is None:
            print(f"{label:<14} no run yet", file=out)
            continue
        pace = format_pace(best["seconds"], best["km"], args.units)
        print(
            f"{label:<14} {pace:<10} {format_distance(best['km'], args.units)} "
            f"on {best['date']} (#{best['id']})",
            file=out,
        )
    return False


def cmd_streak(db, args, today, out):
    print(f"{streak_length(db['runs'], today)} day(s) in a row", file=out)
    return False


def cmd_goal(db, args, today, out):
    if args.clear:
        db["goal_weekly_km"] = None
        print("weekly goal cleared", file=out)
        return True
    if args.set is not None:
        km = parse_distance(str(args.set), args.units == "mi")
        db["goal_weekly_km"] = km
        print(f"weekly goal set to {format_distance(km, args.units)}", file=out)
        return True
    goal = db.get("goal_weekly_km")
    if goal is None:
        print("no weekly goal set; try: stride goal --set 30", file=out)
        return False
    done = week_km(db["runs"], today)
    percent = round(done / goal * 100)
    monday = week_start(today)
    print(
        f"week of {monday.isoformat()}: {format_distance(done, args.units)} of "
        f"{format_distance(goal, args.units)} ({percent}%)",
        file=out,
    )
    return False


def cmd_import(db, args, today, out):
    path = Path(args.path)
    try:
        # utf-8-sig drops the byte-order mark that spreadsheet exports often add.
        with path.open(newline="", encoding="utf-8-sig") as handle:
            incoming, errors = read_csv_runs(handle, args.mi, today)
    except OSError as exc:
        raise StrideError(f"cannot read {path}: {exc}")
    except UnicodeDecodeError:
        raise StrideError(f"{path} is not UTF-8 text")
    if errors:
        # All-or-nothing: a file with errors changes nothing.
        raise StrideError("import aborted, no runs were added:\n  " + "\n  ".join(errors))

    existing = {(r["date"], r["km"], r["seconds"]) for r in db["runs"]}
    added = skipped = 0
    for run in incoming:
        key = (run["date"], run["km"], run["seconds"])
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        run["id"] = next_id(db["runs"])
        db["runs"].append(run)
        added += 1
    print(f"imported {added} run(s), skipped {skipped} duplicate(s)", file=out)
    return added > 0


def cmd_export(db, args, today, out):
    path = Path(args.path)
    try:
        with path.open("w", newline="", encoding="utf-8") as handle:
            write_csv_runs(handle, db["runs"])
    except OSError as exc:
        raise StrideError(f"cannot write {path}: {exc}")
    print(f"exported {len(db['runs'])} run(s) to {path}", file=out)
    return False


HANDLERS = {
    "add": cmd_add,
    "list": cmd_list,
    "delete": cmd_delete,
    "stats": cmd_stats,
    "weeks": cmd_weeks,
    "pb": cmd_pb,
    "streak": cmd_streak,
    "goal": cmd_goal,
    "import": cmd_import,
    "export": cmd_export,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stride",
        description="A private running log: pace, weekly mileage, goals and bests.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help="path to the JSON log (default: ~/.stride.json)")
    parser.add_argument("--units", choices=["km", "mi"], default="km",
                        help="units for display and for --set values (default: km)")
    parser.add_argument("--today", metavar="YYYY-MM-DD", default=None,
                        help="pretend today is this date (for streaks and goals)")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="log a run")
    add.add_argument("date", help="YYYY-MM-DD")
    add.add_argument("distance", help="distance in km (or mi with --mi)")
    add.add_argument("duration", help="SS, MM:SS or H:MM:SS")
    add.add_argument("--mi", action="store_true", help="distance is in miles")
    add.add_argument("--note", default="", help="free-text note")

    lst = sub.add_parser("list", help="list runs")
    lst.add_argument("--since", metavar="YYYY-MM-DD")
    lst.add_argument("--until", metavar="YYYY-MM-DD")

    dele = sub.add_parser("delete", help="delete a run by id")
    dele.add_argument("id", type=int)

    sub.add_parser("stats", help="totals, average pace and streak")

    weeks = sub.add_parser("weeks", help="weekly mileage with a bar chart")
    weeks.add_argument("--last", type=int, default=8, help="how many weeks (default: 8)")

    sub.add_parser("pb", help="fastest average pace at standard distances")
    sub.add_parser("streak", help="consecutive running days")

    goal = sub.add_parser("goal", help="show or set the weekly distance goal")
    goal.add_argument("--set", type=float, metavar="DIST", help="set the weekly goal")
    goal.add_argument("--clear", action="store_true", help="remove the weekly goal")

    imp = sub.add_parser("import", help="import runs from a CSV file")
    imp.add_argument("path")
    imp.add_argument("--mi", action="store_true", help="distances in the CSV are miles")

    exp = sub.add_parser("export", help="export all runs to a CSV file")
    exp.add_argument("path")
    return parser


def resolve_today(text: str | None) -> dt.date:
    if text is None:
        return dt.date.today()
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise StrideError(f"invalid --today {text!r}; use YYYY-MM-DD")


def run_cli(argv=None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        today = resolve_today(args.today)
        db = load_db(args.db)
        changed = HANDLERS[args.command](db, args, today, out)
        if changed:
            save_db(args.db, db)
    except StrideError as exc:
        print(f"stride: error: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    sys.exit(run_cli())


if __name__ == "__main__":
    main()
