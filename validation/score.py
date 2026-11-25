"""Score csv-safe-restore on the Phase 2 validation data, cell by cell.

For every table (products, orders) and every edited file found in
``validation/data/<table>/<profile>/edited.csv`` the tool is run in its default
mode and with ``--trust-key``. Each identifier cell of the repaired file is
compared with ``intended.csv`` (what the person meant to save) and sorted into
exactly one outcome:

``restored``
    The tool changed the cell and the result is the intended value.
``left_for_review``
    The spreadsheet changed the cell and the tool left it as it was (and, in
    the default mode, listed it in its report). Safe, but not repaired.
``intentional_undone``
    The person changed the identifier on purpose and the tool put the old
    value of the same record back. A documented limitation; counted on its
    own, never mixed with incorrect restorations.
``incorrect``
    The tool wrote any other value: another record's value or a value nobody
    intended. This must be zero.
``untouched``
    The cell was not damaged and the tool left it alone.

Every non-identifier cell must come out exactly as it went in; any change is
reported as ``other_column_changed``. The input files are hashed before and
after each run and must not change.

The warnings of each run are judged too, against what is known to be true:
the reference files are clean, so a "reference file itself" warning is always
false; a warning that names an unchecked column is true only if the
spreadsheet really changed that column. Warnings are also judged for a run
with automatic column detection (no ``--identifier-columns``).

An externally produced file (for example from a real Excel) can be scored with
``--edited path --table products --name excel-windows``; see
EXCEL_MANUAL_TEST.md.

Usage:  python validation/score.py [--data validation/data] [--markdown validation/results.md]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_dataset import TABLES  # noqa: E402

MODES = {"default": [], "--trust-key": ["--trust-key"]}
# The tool is run from the source in this repository, never from an older
# installed copy.
SOURCE = Path(__file__).resolve().parent.parent / "src"


def tool_environment() -> dict[str, str]:
    paths = [str(SOURCE), os.environ.get("PYTHONPATH", "")]
    return dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, paths)))
OUTCOMES = ["restored", "left_for_review", "intentional_undone", "incorrect", "untouched"]
MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"


def read_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    return rows[0], rows[1:]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def damage_kind(intended: str, edited: str) -> str:
    """Name what the spreadsheet did, by looking at the two values only."""
    if re.search(r"\d[.,]?\d*E[+-]?\d+$", edited, re.I) and "E" not in intended.upper():
        return "scientific notation"
    if re.search(rf"\d+/\d+/\d+|{MONTH}|\d{{4}}-\d\d-\d\d", edited, re.I) and edited != intended:
        return "converted to a date"
    if edited.upper() == intended.upper():
        return "letter case changed"
    if intended.startswith("+") and edited == intended[1:]:
        return "plus sign removed"
    if intended.isdigit() and edited == intended.lstrip("0"):
        return "leading zeros removed"
    if re.fullmatch(r"\d+[.,]\d+", intended) and re.fullmatch(r"\d+([.,]\d+)?", edited):
        return "decimal number reformatted"
    if intended.isdigit() and edited.isdigit() and len(intended) > 15:
        return "digits after the 15th lost"
    if re.fullmatch(r"\d+E\d+", intended, re.I):
        return "code read as a number"
    return "other"


@dataclass
class Result:
    outcomes: Counter = field(default_factory=Counter)
    by_kind: dict = field(default_factory=lambda: defaultdict(Counter))
    by_scenario: dict = field(default_factory=lambda: defaultdict(Counter))
    by_column: dict = field(default_factory=lambda: defaultdict(Counter))
    problems: list = field(default_factory=list)  # incorrect and intentional_undone cells
    warnings: list = field(default_factory=list)  # (verdict, text) for every warning
    undone_flagged: int = 0  # undone cells covered by the "on purpose" warning
    unwarned: list = field(default_factory=list)  # damaged, unchecked and never mentioned
    other_column_changed: int = 0
    inputs_unchanged: bool = True
    exit_code: int = 0


def run_tool(table: str, reference: Path, edited: Path,
             flags: list[str]) -> tuple[list[list[str]], int, dict]:
    spec = TABLES[table]
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "repaired.csv"
        command = [sys.executable, "-m", "csv_safe_restore", "repair", str(reference), str(edited),
                   "--key", spec["key"], "--output", str(output), "--format", "json", *flags]
        if "--auto-detect" in flags:  # not a tool option: leave out --identifier-columns
            command.remove("--auto-detect")
        else:
            command += ["--identifier-columns", ",".join(spec["identifiers"])]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                env=tool_environment())
        if result.returncode not in (0, 1):
            raise SystemExit(f"csv-safe-restore failed ({result.returncode}): {result.stderr}")
        return read_rows(output)[1], result.returncode, json.loads(result.stdout)


# Patterns a person could also produce on purpose (see detection._COULD_BE_TYPED).
TYPED_PATTERNS = {"leading_zeros_removed", "number_reformatted", "boolean_uppercased"}


def spreadsheet_damaged_columns(header: list[str], intended: list[list[str]],
                                edited: list[list[str]]) -> list[str]:
    """Columns in which the spreadsheet changed at least one value."""
    return [
        name for index, name in enumerate(header)
        if any(want[index] != got[index] and damage_kind(want[index], got[index]) != "other"
               for want, got in zip(intended, edited))
    ]


def judge_warnings(table: str, header: list[str], intended: list[list[str]],
                   edited: list[list[str]], warnings: list[str]) -> list[tuple[str, str]]:
    """Label each warning true, false, harmless or info, using the known truth."""
    spreadsheet_changed = spreadsheet_damaged_columns(header, intended, edited)
    judged = []
    for text in warnings:
        if text.startswith("The reference file itself"):
            verdict = "FALSE (the reference is clean)"
        elif "was not checked, but" in text:
            name = re.search(r"Column '([^']+)'", text).group(1)
            verdict = "true" if name in spreadsheet_changed else "FALSE (column not damaged)"
        elif text.startswith("Only the formatting changed"):
            names = re.findall(r"'([^']+)' \(\d+", text)
            wrong = [n for n in names if n not in spreadsheet_changed]
            identifiers = [n for n in names if n in TABLES[table]["identifiers"]]
            verdict = (f"FALSE for {', '.join(wrong)}" if wrong
                       else "true" if identifiers else "true, harmless here (amounts)")
        elif "still have their leading zeros" in text:
            verdict = "FALSE (the whole file went through the spreadsheet)"
        elif "could also have changed on purpose" in text:
            verdict = "true (cannot be checked further)"
        else:
            verdict = "info (unchanged since 0.1.0a1)"
        judged.append((verdict, text))
    return judged


def score(table: str, folder: Path, edited_path: Path, flags: list[str]) -> Result:
    spec = TABLES[table]
    header, reference = read_rows(folder / "reference.csv")
    intended_header, intended = read_rows(folder / "intended.csv")
    edited_header, edited = read_rows(edited_path)
    _, truth = read_rows(folder / "truth.csv")
    if not (header == intended_header == edited_header) or len(intended) != len(edited):
        raise SystemExit(f"{edited_path}: header or row count differs from intended.csv")
    key = header.index(spec["key"])
    by_key = {row[key]: row for row in reference}

    before = {p: digest(p) for p in (folder / "reference.csv", edited_path)}
    repaired, exit_code, json_report = run_tool(table, folder / "reference.csv", edited_path, flags)
    result = Result(exit_code=exit_code)
    result.inputs_unchanged = before == {p: digest(p) for p in before}
    warnings = json_report["warnings"]
    result.warnings = judge_warnings(table, header, intended, edited, warnings)
    result.unwarned = [
        name for name in spreadsheet_damaged_columns(header, intended, edited)
        if name not in json_report["identifier_columns"] and name != spec["key"]
        and not any(f"'{name}'" in text for text in warnings)
    ]
    typed = set()
    if any("could also have changed on purpose" in text for text in warnings):
        typed = {(c["line"], c["column"]) for c in json_report["confirmed_damage"]
                 if c["pattern"] in TYPED_PATTERNS}

    for position, (want, got, out, facts) in enumerate(zip(intended, edited, repaired, truth)):
        _, row_key, source_key, scenario, intentional = facts
        intentional_columns = set(filter(None, intentional.split(";")))
        source = by_key.get(source_key) if source_key else None
        for index, name in enumerate(header):
            if name not in spec["identifiers"]:
                if out[index] != got[index]:
                    result.other_column_changed += 1
                continue
            if out[index] == got[index]:
                outcome = "untouched" if got[index] == want[index] else "left_for_review"
            elif out[index] == want[index]:
                outcome = "restored"
            elif (name in intentional_columns and source is not None
                  and got[key] == source_key and out[index] == source[index]):
                outcome = "intentional_undone"
            else:
                outcome = "incorrect"
            result.outcomes[outcome] += 1
            result.by_scenario[scenario][outcome] += 1
            if got[index] != want[index]:
                kind = damage_kind(want[index], got[index])
                result.by_kind[kind][outcome] += 1
                result.by_column[name][outcome] += 1
            if outcome == "intentional_undone" and (position + 2, name) in typed:
                result.undone_flagged += 1
            if outcome in ("incorrect", "intentional_undone"):
                result.problems.append({
                    "line": position + 2, "key": got[key], "column": name, "scenario": scenario,
                    "intended": want[index], "edited": got[index], "written": out[index],
                    "outcome": outcome,
                })
    return result


def damaged(result: Result) -> int:
    return sum(n for kind in result.by_kind.values() for n in kind.values())


def false_warnings(result: Result) -> int:
    return sum(1 for verdict, _ in result.warnings if verdict.startswith("FALSE"))


def summary_line(name: str, result: Result) -> str:
    o = result.outcomes
    undone = o["intentional_undone"]
    flagged = f" ({result.undone_flagged} warned)" if undone else ""
    return (f"| {name} | {damaged(result)} | {o['restored']} | {o['left_for_review']} | "
            f"{o['incorrect']} | {undone}{flagged} | {result.other_column_changed} | "
            f"{'yes' if result.inputs_unchanged else '**NO**'} | {false_warnings(result)} | "
            f"{', '.join(result.unwarned) or '-'} |")


def report(results: dict[tuple[str, str, str], Result]) -> str:
    lines = [
        "# Phase 2 validation results",
        "",
        "Generated by `validation/score.py`. Edited files come from the real round trips in",
        "`validation/data/<table>/<profile>/` (see each `run.txt`). **No Excel was run.**",
        "",
        "| Table / profile / mode | Damaged cells | Restored | Left for review | Incorrect | "
        "Intentional change undone | Other columns changed | Inputs unchanged | False warnings | "
        "Damaged columns not mentioned |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [summary_line(" / ".join(k), r) for k, r in results.items()]
    lines += ["", "## Warnings", "",
              "Each warning of each run, judged against the known truth. Runs marked "
              "`auto-detect` name no identifier columns.", "",
              "| Run | Verdict | Warning |", "| --- | --- | --- |"]
    for name, result in results.items():
        for verdict, text in result.warnings:
            lines.append(f"| {' / '.join(name)} | {verdict} | {text.replace('|', '/')} |")
    for (table, profile, mode), result in results.items():
        if mode == "auto-detect":
            continue
        lines += ["", f"## {table} / {profile} / {mode}", "",
                  "| What the spreadsheet did | Cells | Restored | Left for review | Incorrect | Undone |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for kind, c in sorted(result.by_kind.items(), key=lambda kv: -sum(kv[1].values())):
            lines.append(f"| {kind} | {sum(c.values())} | {c['restored']} | {c['left_for_review']} | "
                         f"{c['incorrect']} | {c['intentional_undone']} |")
        lines += ["", "| Column | Damaged | Restored | Left for review | Incorrect |",
                  "| --- | --- | --- | --- | --- |"]
        for column, c in result.by_column.items():
            lines.append(f"| {column} | {sum(c.values())} | {c['restored']} | "
                         f"{c['left_for_review']} | {c['incorrect']} |")
        lines += ["", "| Scenario | Restored | Left for review | Incorrect | Undone |",
                  "| --- | --- | --- | --- | --- |"]
        for scenario, c in sorted(result.by_scenario.items()):
            lines.append(f"| {scenario} | {c['restored']} | {c['left_for_review']} | "
                         f"{c['incorrect']} | {c['intentional_undone']} |")
        if result.problems:
            lines += ["", "| Line | Key | Column | Scenario | Intended | In edited file | Written | Outcome |",
                      "| --- | --- | --- | --- | --- | --- | --- | --- |"]
            for p in result.problems:
                lines.append(f"| {p['line']} | {p['key']} | {p['column']} | {p['scenario']} | "
                             f"`{p['intended']}` | `{p['edited']}` | `{p['written']}` | {p['outcome']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path(__file__).parent / "data")
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--edited", type=Path, help="score one external file (e.g. from Excel)")
    parser.add_argument("--table", choices=sorted(TABLES), help="table the --edited file belongs to")
    parser.add_argument("--name", default="external", help="label for the --edited file")
    parser.add_argument("--delimiter", default=",",
                        help="field separator of the --edited file (Excel often uses ';')")
    args = parser.parse_args()

    jobs = []
    if args.edited:
        if not args.table:
            parser.error("--edited needs --table")
        edited = args.edited
        if args.delimiter != ",":  # rewrite with commas; values are not touched
            with open(edited, newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.reader(handle, delimiter=args.delimiter))
            edited = Path(tempfile.mkdtemp()) / "edited.csv"
            with open(edited, "w", newline="", encoding="utf-8") as handle:
                csv.writer(handle, lineterminator="\n").writerows(rows)
        jobs.append((args.table, args.name, edited))
    else:
        for table in sorted(TABLES):
            for edited in sorted((args.data / table).glob("*/edited.csv")):
                jobs.append((table, edited.parent.name, edited))
    results = {}
    for table, profile, edited in jobs:
        for mode, flags in MODES.items():
            results[(table, profile, mode)] = score(table, args.data / table, edited, flags)
        # What a user gets without --identifier-columns. Its counts cover the
        # same columns as the others, so cells in undetected columns show up
        # as left for review.
        results[(table, profile, "auto-detect")] = score(
            table, args.data / table, edited, ["--auto-detect"])

    print("| Table / profile / mode | Damaged | Restored | Review | Incorrect | Undone | Other cols "
          "| Inputs same | False warnings | Not mentioned |")
    for name, result in results.items():
        print(summary_line(" / ".join(name), result))
    if args.markdown:
        args.markdown.write_text(report(results), encoding="utf-8")
    incorrect = sum(r.outcomes["incorrect"] + r.other_column_changed for r in results.values())
    unchanged = all(r.inputs_unchanged for r in results.values())
    raise SystemExit(0 if incorrect == 0 and unchanged else 1)


if __name__ == "__main__":
    main()
