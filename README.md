# Stride

**A private running log that works offline.** Record runs, see your pace and weekly mileage, track a weekly goal, and watch your personal bests. Everything lives in one JSON file on your machine. No accounts, no network, no dependencies beyond Python 3.

## Features

- Log a run with a date, distance (km or miles), and time (`SS`, `MM:SS` or `H:MM:SS`), plus an optional note.
- Pace is calculated for you, shown per km or per mile with `--units`.
- `list` shows every run in a table, filterable by `--since` and `--until`.
- `delete` removes a run by its id.
- `stats` gives totals, average pace, longest run, and your current streak.
- `weeks` shows weekly mileage as a bar chart for the last N weeks.
- `pb` reports your fastest average pace at 5 km, 10 km, half marathon, and marathon distances.
- `streak` counts consecutive running days, and a missed day only breaks it once yesterday has also passed.
- `goal` sets a weekly distance target and shows progress for the current week (`goal --set 30`, `goal --clear`).
- `import` loads runs from a CSV file. Every row is either imported or reported with its line number. A file with any errors changes nothing. Duplicate runs are skipped.
- `export` writes your full log to CSV, and the output round-trips back through `import`.
- Input is validated throughout: impossible dates, future dates, seconds of 60 or more, distances over 500 km, and corrupt log files all produce a clear message instead of a traceback.
- Writes are atomic: the log is written to a temp file and then swapped in, so a crash can't leave a half-written file.

## Quick start

Stride is a single file, so there is nothing to install. Clone or copy the folder, then:

```sh
python3 stride.py add 2026-10-16 5 26:05 --note "easy loop"
python3 stride.py add 2026-10-14 12 1:06:00 --note "long run"
python3 stride.py list
python3 stride.py stats
```

To import the included sample of ten runs and set a goal:

```sh
python3 stride.py import examples/sample.csv
python3 stride.py goal --set 30
python3 stride.py goal
python3 stride.py weeks --last 4
python3 stride.py pb
```

Miles work too:

```sh
python3 stride.py --units mi add 2026-10-17 3.1 25:00 --mi
```

## How to run

```sh
python3 stride.py [--db PATH] [--units km|mi] [--today YYYY-MM-DD] COMMAND ...
```

- `--db PATH` picks the log file. It defaults to `~/.stride.json`.
- `--units` sets how distances and paces are shown. It also applies to `goal --set`.
- `--today` pretends a different date is today. Streaks and goals use it. This is handy for testing or back-filling.

Commands: `add`, `list`, `delete`, `stats`, `weeks`, `pb`, `streak`, `goal`, `import`, `export`. Run `python3 stride.py COMMAND --help` for the options of each one.

Errors are printed to stderr with exit code 1. Bad command-line usage exits with code 2.

## How to test

The tests use [pytest](https://pytest.org), which is the only third-party dependency and is needed only for testing.

```sh
python3 -m pip install pytest
python3 -m pytest
```

The suite covers duration, distance, and date parsing (including malformed input), pace rounding, weekly grouping across week boundaries, streaks, personal bests, CSV line-numbered errors, CRLF and quoted fields, export and import round trips, and end-to-end CLI runs. These include all-or-nothing imports, duplicate skipping, goal progress, and recovery from a corrupt log.

## Project layout

```
stride.py              the whole program: parsing, storage, analysis, CLI
examples/sample.csv    ten sample runs for trying out import
tests/conftest.py      puts the project root on the import path
tests/test_stride.py   the test suite
```

## CSV format

```csv
date,distance,duration,note
2026-10-14,12.0,1:06:00,long run
2026-10-16,5.0,26:05,
```

`date` is `YYYY-MM-DD`. `distance` is in kilometres, or miles when you pass `--mi` to `import`. `duration` is `SS`, `MM:SS` or `H:MM:SS`. `note` is optional.

## Data

The log is a plain JSON file with a version number, the list of runs, and the weekly goal. Back it up by copying the file, or export it to CSV.
