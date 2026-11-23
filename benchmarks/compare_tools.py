"""Score csv-safe-restore and other tools on the datasets from make_dataset.py.

What is measured, per tool and per scenario:

* how many truly damaged identifier cells the tool points at,
* whether it tells damage apart from intentional identifier edits,
* correct repairs, false repairs and damaged cells left for a human,
* wall-clock runtime (median of three runs, including process start-up).

Fairness notes
--------------
daff, csv-diff and datacompy are *diff* tools. They are built to show every
difference, not to judge or repair it, so "repairs" is reported as "n/a" for
them, not as a failure. UnCorrupt scans a single file for Excel damage to gene
symbols and similar; it has no reference file to compare with. Each tool is
given the key column and, where it matters, is told to read everything as text.

To show why "just copy the reference values back" is not a safe repair, the
script also scores a ``naive revert`` baseline: every identifier cell that
differs from the reference row with the same key is overwritten. This is not a
real tool; it is what a quick script on top of a diff would do.

Usage:  python benchmarks/compare_tools.py [--data benchmarks/data] [--markdown results.md]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

KEY = "record_id"
IDENTIFIERS = ["sku", "barcode", "postal_code", "internal_id"]
BIN = Path(sys.executable).parent

DATACOMPY_SCRIPT = """
import json, sys
import pandas as pd
import datacompy
read = lambda p: pd.read_csv(p, dtype=str, keep_default_na=False)
compare = datacompy.PandasCompare(read(sys.argv[1]), read(sys.argv[2]), join_columns=["record_id"])
mismatch = compare.all_mismatch()
cells = []
for column in sys.argv[3].split(","):
    if column + "_df2" in mismatch.columns:
        changed = mismatch[mismatch[column + "_df1"] != mismatch[column + "_df2"]]
        cells += [[k, column, v] for k, v in zip(changed["record_id"], changed[column + "_df2"])]
print(json.dumps({"cells": cells, "added": compare.df2_unq_rows.values.tolist()}))
"""


def read_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    return rows[0], rows[1:]


def timed(command: list[str], runs: int = 3) -> tuple[subprocess.CompletedProcess, float]:
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        result = subprocess.run(command, capture_output=True, text=True)
        times.append(time.perf_counter() - start)
    return result, statistics.median(times)


class Report:
    """What one tool said about the edited file."""

    def __init__(self, name: str, seconds: float, repairs: str = "n/a (no repair feature)"):
        self.name = name
        self.seconds = seconds
        self.repairs = repairs
        self.cells: set[tuple[str, str, str]] = set()  # (record_id, column, edited value)
        self.cell_positions: set[tuple[int, str]] = set()  # (edited row, column)
        self.damage_positions: set[tuple[int, str]] = set()  # cells called "damage"
        self.rows: set[tuple[str, ...]] = set()  # whole rows reported, no cell detail
        self.row_positions: set[int] = set()
        self.separates_damage = False
        self.note = ""

    def outcome(self, truth: dict[str, str], row: list[str]) -> str:
        position = int(truth["row"])
        cell = (position, truth["column"])
        if cell in self.damage_positions:
            return "damage"
        if cell in self.cell_positions:
            return "cell"
        if truth["record_id"] and (truth["record_id"], truth["column"], truth["edited_value"]) in self.cells:
            return "cell"
        if position in self.row_positions or tuple(row) in self.rows:
            return "row"
        return "silent"


# ------------------------------------------------------------------ the tools


def run_csv_safe_restore(
    reference: Path, edited: Path, work: Path, strict: bool = True
) -> tuple[Report, Path]:
    """Run the default (strict) mode, or --trust-key when ``strict`` is False."""
    output = work / ("repaired.csv" if strict else "repaired-trust-key.csv")
    common = [str(reference), str(edited), "--key", KEY, "--identifier-columns", ",".join(IDENTIFIERS)]
    if not strict:
        common.append("--trust-key")
    _, check_seconds = timed([str(BIN / "csv-safe-restore"), "check", *common, "--format", "json"])
    result, repair_seconds = timed(
        [str(BIN / "csv-safe-restore"), "repair", *common, "--format", "json",
         "--output", str(output), "--overwrite"]
    )
    data = json.loads(result.stdout)
    report = Report(
        "csv-safe-restore (default, strict)" if strict else "csv-safe-restore --trust-key",
        check_seconds,
    )
    report.separates_damage = True
    report.note = f"repair: {repair_seconds:.2f} s"
    for finding in data["confirmed_damage"]:
        report.damage_positions.add((finding["line"] - 2, finding["column"]))
    for finding in data["review_cells"]:
        report.cell_positions.add((finding["line"] - 2, finding["column"]))
    for finding in data["review_records"] + data["new_records"]:
        report.row_positions.add(finding["line"] - 2)
    return report, output


def run_daff(reference: Path, edited: Path, work: Path) -> Report:
    output = work / "daff.csv"
    _, seconds = timed(
        [str(BIN / "daff"), "diff", "--id", KEY, "--output", str(output), str(reference), str(edited)]
    )
    report = Report("daff", seconds)
    header, rows = read_rows(output)
    for row in rows:
        action = row[0]
        if action == "+++":
            report.rows.add(tuple(row[1:]))
        elif "->" in action:  # modified row; changed cells look like old->new
            record_id = row[header.index(KEY)]
            for name, cell in zip(header[1:], row[1:]):
                if action in cell:
                    report.cells.add((record_id, name, cell.split(action, 1)[1]))
    return report


def run_csv_diff(reference: Path, edited: Path, work: Path) -> Report:
    result, seconds = timed(
        [str(BIN / "csv-diff"), str(reference), str(edited), "--key", KEY, "--json"]
    )
    report = Report("csv-diff", seconds)
    data = json.loads(result.stdout)
    header, _ = read_rows(edited)
    for change in data["changed"]:
        for name, (_, new) in change["changes"].items():
            report.cells.add((change["key"], name, new))
    for row in data["added"]:
        report.rows.add(tuple(row[name] for name in header))
    return report


def run_datacompy(reference: Path, edited: Path, work: Path) -> Report:
    result, seconds = timed(
        [sys.executable, "-c", DATACOMPY_SCRIPT, str(reference), str(edited), ",".join(IDENTIFIERS)]
    )
    if result.returncode != 0:
        raise SystemExit(f"datacompy failed:\n{result.stderr}")
    report = Report("datacompy", seconds)
    data = json.loads(result.stdout)
    report.cells = {tuple(cell) for cell in data["cells"]}
    report.rows = {tuple(row) for row in data["added"]}
    report.note = "files read with dtype=str"
    return report


def run_uncorrupt(reference: Path, edited: Path, work: Path) -> Report:
    result, seconds = timed([str(BIN / "uncorrupt"), "detect", str(edited), "--json"])
    report = Report("UnCorrupt", seconds, repairs="n/a (detector only)")
    data = json.loads(result.stdout)
    header, _ = read_rows(edited)
    for suspicion in data["suspicions"]:
        row = suspicion.get("row")
        column = suspicion.get("column")
        if isinstance(row, int) and column in header:
            report.cell_positions.add((row, column))
            report.cell_positions.add((row - 1, column))
            report.cell_positions.add((row - 2, column))
    report.note = f"single-file scan, no reference; {len(data['suspicions'])} suspicion(s) raised"
    return report


# -------------------------------------------------------------------- scoring


def score_repairs(
    header: list[str], edited: list[list[str]], repaired: list[list[str]], truth: list[dict[str, str]]
) -> dict[str, int]:
    """Compare a repaired file with the truth, cell by cell."""
    truth_by_cell = {(int(t["row"]), t["column"]): t for t in truth}
    counts = Counter()
    if len(repaired) != len(edited):
        raise SystemExit("repaired file has a different number of rows than the edited file")
    for position, (before, after) in enumerate(zip(edited, repaired)):
        for name, old, new in zip(header, before, after):
            cell = truth_by_cell.get((position, name))
            if old != new:
                correct = cell is not None and cell["label"] == "damaged" and new == cell["true_original"]
                counts["correct" if correct else "false"] += 1
            elif cell is not None and cell["label"] == "damaged":
                counts["unresolved"] += 1
    return {"correct": counts["correct"], "false": counts["false"], "unresolved": counts["unresolved"]}


def naive_revert(
    header: list[str], reference: list[list[str]], edited: list[list[str]]
) -> list[list[str]]:
    key = header.index(KEY)
    by_key = {row[key]: row for row in reference}
    repaired = []
    for row in edited:
        row = list(row)
        source = by_key.get(row[key])
        if source is not None:
            for name in IDENTIFIERS:
                row[header.index(name)] = source[header.index(name)]
        repaired.append(row)
    return repaired


def score_scenario(folder: Path) -> list[dict[str, str]]:
    reference_path, edited_path = folder / "reference.csv", folder / "edited.csv"
    header, reference = read_rows(reference_path)
    _, edited = read_rows(edited_path)
    with open(folder / "truth.csv", newline="", encoding="utf-8") as handle:
        truth = list(csv.DictReader(handle))
    damaged = [t for t in truth if t["label"] == "damaged"]
    intentional = [t for t in truth if t["label"] == "intentional_edit"]
    before = {p: p.read_bytes() for p in (reference_path, edited_path)}

    results = []
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        ours = []
        for strict in (True, False):
            report, repaired_path = run_csv_safe_restore(reference_path, edited_path, work, strict)
            repairs = score_repairs(header, edited, read_rows(repaired_path)[1], truth)
            report.repairs = (
                f"{repairs['correct']} correct, {repairs['false']} false, "
                f"{repairs['unresolved']} left for review"
            )
            ours.append(report)
        reports = ours + [
            run(reference_path, edited_path, work)
            for run in (run_daff, run_csv_diff, run_datacompy, run_uncorrupt)
        ]
    assert all(p.read_bytes() == content for p, content in before.items()), "an input file changed"

    for report in reports:
        seen = Counter(report.outcome(t, edited[int(t["row"])]) for t in damaged)
        edits = Counter(report.outcome(t, edited[int(t["row"])]) for t in intentional)
        if report.separates_damage:
            pointed = f"{seen['damage']} as damage, {seen['cell'] + seen['row']} for review"
            telling = f"yes ({edits['damage']} of {len(intentional)} edits called damage)"
        else:
            pointed = f"{seen['cell']} as a changed cell, {seen['row']} as an added row"
            telling = f"no ({edits['cell'] + edits['row']} of {len(intentional)} edits listed the same way)"
        results.append(
            {"tool": report.name, "damaged cells reported": pointed,
             "not reported": str(seen["silent"]), "separates damage from edits": telling,
             "repairs": report.repairs, "seconds": f"{report.seconds:.2f}", "note": report.note}
        )

    baseline = score_repairs(header, edited, naive_revert(header, reference, edited), truth)
    results.append(
        {"tool": "naive revert (baseline, not a tool)", "damaged cells reported": "-",
         "not reported": "-", "separates damage from edits": "no",
         "repairs": f"{baseline['correct']} correct, {baseline['false']} false, "
                    f"{baseline['unresolved']} left",
         "seconds": "-", "note": "overwrites every differing identifier cell by key"}
    )
    summary = (
        f"{len(edited)} edited rows; {len(damaged)} truly damaged identifier cells; "
        f"{len(intentional)} intentional identifier edits"
    )
    return [{"summary": summary}] + results


def as_markdown(name: str, results: list[dict[str, str]]) -> str:
    columns = list(results[1])
    lines = [f"### Scenario: {name}", "", results[0]["summary"], "",
             "| " + " | ".join(columns) + " |", "|" + " --- |" * len(columns)]
    lines += ["| " + " | ".join(row[c] for c in columns) + " |" for row in results[1:]]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path(__file__).parent / "data")
    parser.add_argument("--markdown", type=Path, help="also write the tables to this file")
    args = parser.parse_args()

    sections = []
    false_repairs = 0
    for folder in sorted(p for p in args.data.iterdir() if (p / "truth.csv").exists()):
        results = score_scenario(folder)
        sections.append(as_markdown(folder.name, results))
        for ours in results[1:3]:  # csv-safe-restore, default (strict) and --trust-key
            false_repairs += int(ours["repairs"].split(" correct, ")[1].split(" false")[0])
    text = "\n".join(sections)
    print(text)
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")
    if false_repairs:
        print(f"RELEASE BLOCKER: csv-safe-restore made {false_repairs} false repair(s)")
        return 1
    print("csv-safe-restore made 0 false repairs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
