import datetime as dt
import io
import json
import shutil
from pathlib import Path

import pytest

import stride
from stride import StrideError

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
TODAY = dt.date(2026, 10, 18)


def make_run(id_, day, km, seconds, note=""):
    return {"id": id_, "date": day, "km": km, "seconds": seconds, "note": note}


# ---------- parsing ----------

@pytest.mark.parametrize("text, expected", [
    ("45", 45),
    ("27:40", 1660),
    ("1:58:30", 7110),
    ("0:00:01", 1),
    (" 9:05 ", 545),
])
def test_parse_duration_accepts_common_forms(text, expected):
    assert stride.parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "1:60", "5:75", "1:2:3:4", "0", "00:00", ":30", "1.5:00", "-5"])
def test_parse_duration_rejects_bad_input(text):
    with pytest.raises(StrideError):
        stride.parse_duration(text)


def test_parse_distance_converts_miles_to_km():
    assert stride.parse_distance("1", miles=True) == 1.609
    assert stride.parse_distance("5.5") == 5.5


@pytest.mark.parametrize("text", ["0", "-3", "nan", "inf", "501", "abc"])
def test_parse_distance_rejects_nonsense(text):
    with pytest.raises(StrideError):
        stride.parse_distance(text)


def test_parse_date_rejects_future_and_garbage():
    assert stride.parse_date("2026-10-18", TODAY) == TODAY
    with pytest.raises(StrideError, match="future"):
        stride.parse_date("2026-10-19", TODAY)
    with pytest.raises(StrideError, match="YYYY-MM-DD"):
        stride.parse_date("2026-02-30", TODAY)


# ---------- formatting ----------

def test_format_pace_rounds_to_whole_seconds_and_carries():
    # 59.6 s per km rounds up to a full minute, not "0:60".
    assert stride.format_pace(596, 10.0, "km") == "1:00/km"
    assert stride.format_pace(1660, 5.0, "km") == "5:32/km"


def test_format_pace_in_miles_scales_per_unit():
    # 5 km in 25:00 is 300 s/km, which is about 482.8 s per mile.
    assert stride.format_pace(1500, 5.0, "mi") == "8:03/mi"


def test_format_duration_switches_to_hours():
    assert stride.format_duration(1660) == "27:40"
    assert stride.format_duration(3725) == "1:02:05"


# ---------- analysis ----------

def test_weekly_totals_group_by_monday_across_week_boundary():
    runs = [
        make_run(1, "2026-10-11", 5.0, 1500),  # Sunday, week of Oct 5
        make_run(2, "2026-10-12", 5.0, 1500),  # Monday, week of Oct 12
        make_run(3, "2026-10-18", 3.0, 900),   # Sunday, same week as run 2
    ]
    totals = stride.weekly_totals(runs)
    assert totals[dt.date(2026, 10, 5)] == 5.0
    assert totals[dt.date(2026, 10, 12)] == 8.0


def test_week_km_counts_only_the_current_week():
    runs = [make_run(1, "2026-10-11", 5.0, 1500), make_run(2, "2026-10-13", 4.0, 1200)]
    assert stride.week_km(runs, dt.date(2026, 10, 14)) == 4.0


def test_streak_counts_back_from_today_or_yesterday():
    runs = [make_run(1, "2026-10-16", 5, 1500), make_run(2, "2026-10-17", 5, 1500),
            make_run(3, "2026-10-18", 5, 1500)]
    assert stride.streak_length(runs, TODAY) == 3
    # No run yet today: the streak is still alive through yesterday.
    assert stride.streak_length(runs[:2], TODAY) == 2
    # A gap of two days breaks it.
    assert stride.streak_length(runs[:1], TODAY) == 0


def test_personal_bests_pick_fastest_eligible_run():
    runs = [
        make_run(1, "2026-10-01", 5.0, 1500),   # 300 s/km
        make_run(2, "2026-10-02", 10.0, 3000),  # 300 s/km
        make_run(3, "2026-10-03", 10.0, 2900),  # 290 s/km, fastest 10 km
        make_run(4, "2026-10-04", 4.0, 1000),   # too short for any 5 km+ bucket
    ]
    bests = dict(stride.personal_bests(runs))
    assert bests["5 km"]["id"] == 3
    assert bests["10 km"]["id"] == 3
    assert bests["Half marathon"] is None


def test_summarize_empty_and_full():
    empty = stride.summarize([])
    assert empty["runs"] == 0 and empty["avg_seconds_per_km"] is None
    runs = [make_run(1, "2026-10-01", 5.0, 1500), make_run(2, "2026-10-02", 10.0, 3000)]
    s = stride.summarize(runs)
    assert s["km"] == 15.0 and s["seconds"] == 4500
    assert s["avg_seconds_per_km"] == 300
    assert s["longest"]["id"] == 2


# ---------- CSV ----------

def test_csv_errors_carry_line_numbers_and_blank_rows_are_skipped(tmp_path):
    text = (
        "date,distance,duration,note\n"
        "2026-10-01,5,25:00,ok\n"
        "\n"
        "2026-10-02,abc,25:00,bad distance\n"
        "2026-10-03,5,25:00\n"
        "2026-10-04,5,25:00,fine\n"
    )
    runs, errors = stride.read_csv_runs(io.StringIO(text), False, TODAY)
    assert len(runs) == 2
    assert len(errors) == 2
    assert errors[0].startswith("line 4:")
    assert "distance" in errors[0]
    assert errors[1].startswith("line 5:")
    assert "expected 4 columns, found 3" in errors[1]


def test_csv_handles_crlf_and_quoted_commas():
    text = 'date,distance,duration,note\r\n2026-10-01,5,25:00,"hilly, windy"\r\n'
    runs, errors = stride.read_csv_runs(io.StringIO(text, newline=""), False, TODAY)
    assert errors == []
    assert runs[0]["note"] == "hilly, windy"


def test_csv_missing_header_column_is_fatal():
    with pytest.raises(StrideError, match="missing column"):
        stride.read_csv_runs(io.StringIO("date,distance\n"), False, TODAY)


def test_export_then_import_round_trips(tmp_path):
    db = stride.empty_db()
    db["runs"] = [
        make_run(1, "2026-10-01", 5.0, 1500, "easy"),
        make_run(2, "2026-10-03", 12.25, 3725, "with, comma"),
    ]
    buf = io.StringIO()
    stride.write_csv_runs(buf, db["runs"])
    runs, errors = stride.read_csv_runs(io.StringIO(buf.getvalue()), False, TODAY)
    assert errors == []
    assert [(r["date"], r["km"], r["seconds"], r["note"]) for r in runs] == [
        ("2026-10-01", 5.0, 1500, "easy"),
        ("2026-10-03", 12.25, 3725, "with, comma"),
    ]


# ---------- CLI end to end ----------

def cli(db, *args, today="2026-10-18"):
    out = io.StringIO()
    code = stride.run_cli(["--db", str(db), "--today", today, *args], out=out)
    return code, out.getvalue()


def test_add_list_delete_flow(tmp_path, capsys):
    db = tmp_path / "log.json"
    code, out = cli(db, "add", "2026-10-16", "5", "26:05", "--note", "easy")
    assert code == 0
    assert "added #1" in out and "5:13/km" in out
    code, out = cli(db, "list")
    assert "#1" in out and "easy" in out
    code, out = cli(db, "delete", "1")
    assert code == 0 and "deleted #1" in out
    code, out = cli(db, "delete", "1")
    assert code == 1
    assert "no run with id 1" in capsys.readouterr().err


def test_cli_rejects_future_run_without_touching_file(tmp_path, capsys):
    db = tmp_path / "log.json"
    code, _ = cli(db, "add", "2026-12-01", "5", "25:00")
    assert code == 1
    assert not db.exists()
    assert "future" in capsys.readouterr().err


def test_import_is_all_or_nothing_and_skips_duplicates(tmp_path, capsys):
    db = tmp_path / "log.json"
    bad = tmp_path / "bad.csv"
    bad.write_text("date,distance,duration\n2026-10-01,5,25:00\n2026-10-02,x,25:00\n")
    code, _ = cli(db, "import", str(bad))
    assert code == 1
    assert "line 3" in capsys.readouterr().err
    assert not db.exists()

    good = EXAMPLES / "sample.csv"
    code, out = cli(db, "import", str(good))
    assert code == 0 and "imported 10 run(s)" in out
    code, out = cli(db, "import", str(good))
    assert code == 0
    assert "imported 0 run(s), skipped 10 duplicate(s)" in out


def test_goal_progress_and_weeks_chart(tmp_path):
    db = tmp_path / "log.json"
    shutil.copy(EXAMPLES / "sample.csv", tmp_path / "sample.csv")
    cli(db, "import", str(tmp_path / "sample.csv"))
    cli(db, "goal", "--set", "30")
    code, out = cli(db, "goal")
    # Week of Oct 12 contains 5.0 + 12.0 + 5.0 = 22.0 km out of 30.
    assert "22.00 km of 30.00 km (73%)" in out
    code, out = cli(db, "weeks", "--last", "3")
    lines = out.strip().splitlines()
    assert len(lines) == 3
    assert lines[-1].startswith("2026-W42")


def test_export_writes_csv_and_stats_summarise(tmp_path):
    db = tmp_path / "log.json"
    cli(db, "import", str(EXAMPLES / "sample.csv"))
    dest = tmp_path / "out.csv"
    code, out = cli(db, "export", str(dest))
    assert code == 0 and dest.read_text().splitlines()[0] == "date,distance,duration,note"
    code, out = cli(db, "stats")
    assert "runs:         10" in out
    # The last sample run was on Oct 16 and today is Oct 18, so the streak has lapsed.
    assert "streak:       0 day(s)" in out


def test_corrupt_log_reports_error_instead_of_crashing(tmp_path, capsys):
    db = tmp_path / "log.json"
    db.write_text("{not json")
    code, _ = cli(db, "streak")
    assert code == 1
    assert "cannot read" in capsys.readouterr().err
    db.write_text(json.dumps({"runs": "nope"}))
    code, _ = cli(db, "streak")
    assert code == 1


def test_damaged_run_entry_is_reported_by_position(tmp_path, capsys):
    db = tmp_path / "log.json"
    good = {"id": 1, "date": "2026-10-01", "km": 5.0, "seconds": 1500, "note": ""}
    broken = {"id": 2, "date": "2026-10-01", "km": 5.0}  # no duration
    db.write_text(json.dumps({"runs": [good, broken]}))
    code, _ = cli(db, "stats")
    assert code == 1
    assert "run 2 has an invalid duration" in capsys.readouterr().err


def test_import_accepts_utf8_bom_from_spreadsheet_exports(tmp_path):
    db = tmp_path / "log.json"
    csv_path = tmp_path / "excel.csv"
    csv_path.write_text("﻿date,distance,duration\n2026-10-01,5,25:00\n", encoding="utf-8")
    code, out = cli(db, "import", str(csv_path))
    assert code == 0 and "imported 1 run(s)" in out


def test_list_filter_accepts_future_dates(tmp_path):
    db = tmp_path / "log.json"
    cli(db, "import", str(EXAMPLES / "sample.csv"))
    code, out = cli(db, "list", "--until", "2026-12-31")
    assert code == 0 and "#10" in out
