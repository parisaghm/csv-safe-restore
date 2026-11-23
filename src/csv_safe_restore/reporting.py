"""Turn an Analysis into a text report (for people) or a JSON report (for scripts)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import __version__, detection, matching
from .detection import Analysis, CellFinding, RowFinding

JSON_SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

_REVIEW_TITLES = {
    detection.SUSPICIOUS_CHANGE: "looks damaged, but not in a way the reference value explains",
    detection.UNEXPLAINED_CHANGE: "identifier changed, no known corruption pattern",
    detection.UNRECOVERABLE: "looks damaged, no trusted reference record",
    detection.INVALID_GTIN: "barcode check failed, no trusted reference record",
    detection.UNCORROBORATED: "spreadsheet damage, but not restored because the record is not confirmed",
    detection.DAMAGED_REFERENCE: "the reference value itself looks damaged",
    matching.MISSING_KEY: "record has no key",
    matching.DUPLICATE_KEY: "key is not unique",
    matching.AMBIGUOUS_KEY: "key could belong to more than one reference record",
    matching.SUSPECTED_KEY_DAMAGE: "the key itself looks damaged",
    matching.KEY_REASSIGNED: "the key may belong to a different record now",
}

_NOTHING_COMPARED = (
    "No records could be matched by key, so nothing was checked. "
    "Is --key the right column, and are these two versions of the same data?"
)


def path_text(path: Path | None) -> str | None:
    """A file name for a report, with undecodable bytes shown as \\xff."""
    if path is None:
        return None
    return os.fsencode(path).decode("utf-8", errors="backslashreplace")


def exit_code(analysis: Analysis, command: str) -> int:
    """0 = nothing to do, 1 = something needs attention.

    For ``check``, repairable damage counts as needing attention. For
    ``repair``, it has been fixed, so only unresolved findings count.
    """
    pending = analysis.review_count
    if command == "check":
        pending += len(analysis.repairs)
    # Zero matched records is never a clean result: nothing was compared.
    return EXIT_FINDINGS if pending or analysis.nothing_compared else EXIT_OK


def result_line(analysis: Analysis, command: str, output: Path | None) -> str:
    repairs, review = len(analysis.repairs), analysis.review_count
    if command == "repair":
        text = f"Wrote {path_text(output)} with {repairs} restored value(s)."
        if review:
            text += f" {review} finding(s) still need review; they were left unchanged."
        if analysis.nothing_compared:
            text += " " + _NOTHING_COMPARED
        return text
    if analysis.nothing_compared:
        return _NOTHING_COMPARED
    if not repairs and not review:
        return f"No identifier damage found in {analysis.matched_records} matched record(s)."
    parts = []
    if repairs:
        parts.append(f"{repairs} value(s) can be restored safely with the repair command")
    if review:
        parts.append(f"{review} finding(s) need review")
    return "; ".join(parts) + "."


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------


def _key_dict(analysis: Analysis, key: matching.Key) -> dict[str, str]:
    return dict(zip(analysis.key_columns, key))


def _cell_dict(analysis: Analysis, finding: CellFinding) -> dict[str, Any]:
    return {
        "category": finding.category,
        "line": finding.line,
        "key": _key_dict(analysis, finding.key),
        "column": finding.column,
        "reference_value": finding.original,
        "edited_value": finding.current,
        "pattern": finding.pattern,
        "detail": finding.detail,
    }


def _row_dict(analysis: Analysis, finding: RowFinding) -> dict[str, Any]:
    return {
        "category": finding.category,
        "file": finding.file,
        "line": finding.line,
        "key": _key_dict(analysis, finding.key),
        "detail": finding.detail,
    }


def summary(analysis: Analysis) -> dict[str, int]:
    def cells(category: str) -> int:
        return len(analysis.cells(category))

    def rows(category: str) -> int:
        return len(analysis.rows(category))

    return {
        "reference_records": len(analysis.original.rows),
        "edited_records": len(analysis.edited.rows),
        "matched_records": analysis.matched_records,
        "new_records": rows(matching.NEW_RECORD),
        "deleted_records": rows(detection.DELETED_RECORD),
        "unmatched_records": rows(matching.MISSING_KEY)
        + rows(matching.DUPLICATE_KEY)
        + rows(matching.AMBIGUOUS_KEY)
        + rows(matching.SUSPECTED_KEY_DAMAGE)
        + rows(matching.KEY_REASSIGNED),
        "identifier_cells_unchanged": analysis.unchanged_identifier_cells,
        "confirmed_damage": cells(detection.CONFIRMED_DAMAGE),
        "suspicious_changes": cells(detection.SUSPICIOUS_CHANGE),
        "unexplained_changes": cells(detection.UNEXPLAINED_CHANGE),
        "unrecoverable_values": cells(detection.UNRECOVERABLE),
        "invalid_gtins": cells(detection.INVALID_GTIN),
        "damage_not_restored_unconfirmed": cells(detection.UNCORROBORATED),
        "damaged_reference_values": cells(detection.DAMAGED_REFERENCE),
        "collapsed_values": len(analysis.collisions),
        "other_column_edits_kept": sum(analysis.other_edits.values()),
        "needs_review": analysis.review_count,
    }


def to_dict(analysis: Analysis, command: str, output: Path | None) -> dict[str, Any]:
    review_cells = [f for f in analysis.cell_findings if f.category != detection.CONFIRMED_DAMAGE]
    informational = detection.INFORMATIONAL_ROW_CATEGORIES
    return {
        "tool": "csv-safe-restore",
        "version": __version__,
        "schema_version": JSON_SCHEMA_VERSION,
        "command": command,
        "strict": analysis.strict,
        "reference_file": path_text(analysis.original.path),
        "edited_file": path_text(analysis.edited.path),
        "output_file": path_text(output),
        "key_columns": list(analysis.key_columns),
        "identifier_columns": list(analysis.identifier_columns),
        "identifier_columns_auto_detected": analysis.identifiers_auto_detected,
        "gtin_columns": list(analysis.gtin_columns),
        "summary": summary(analysis),
        "repairs_applied": command == "repair",
        "confirmed_damage": [_cell_dict(analysis, f) for f in analysis.repairs],
        "review_cells": [_cell_dict(analysis, f) for f in review_cells],
        "review_records": [
            _row_dict(analysis, f) for f in analysis.row_findings if f.category not in informational
        ],
        "new_records": [_row_dict(analysis, f) for f in analysis.rows(matching.NEW_RECORD)],
        "deleted_records": [
            _row_dict(analysis, f) for f in analysis.rows(detection.DELETED_RECORD)
        ],
        "collapsed_values": [
            {
                "column": c.column,
                "edited_value": c.current,
                "reference_values": c.originals,
                "lines": c.lines,
            }
            for c in analysis.collisions
        ],
        "other_column_edits_kept": analysis.other_edits,
        "columns_only_in_reference": analysis.columns_only_in_reference,
        "columns_only_in_edited": analysis.columns_only_in_edited,
        "warnings": analysis.warnings,
        "result": result_line(analysis, command, output),
        "exit_code": exit_code(analysis, command),
    }


def to_json(analysis: Analysis, command: str, output: Path | None) -> str:
    return json.dumps(to_dict(analysis, command, output), indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------


def _limited(items: list, limit: int) -> tuple[list, str | None]:
    if limit and len(items) > limit:
        hidden = len(items) - limit
        return items[:limit], f"  ... and {hidden} more (use --format json for the full list)"
    return items, None


def _section(lines: list[str], title: str, entries: list[str], limit: int) -> None:
    if not entries:
        return
    lines.append("")
    lines.append(f"{title} ({len(entries)})")
    shown, more = _limited(entries, limit)
    lines.extend(shown)
    if more:
        lines.append(more)


def to_text(analysis: Analysis, command: str, output: Path | None, limit: int = 50) -> str:
    def key_text(key: matching.Key) -> str:
        return matching.format_key(analysis.key_columns, key)

    counts = summary(analysis)
    identifiers = ", ".join(analysis.identifier_columns)
    if analysis.identifiers_auto_detected:
        identifiers += "  (detected automatically; set --identifier-columns to choose)"

    lines = [
        f"csv-safe-restore {__version__} - {command}",
        f"  Reference file     : {path_text(analysis.original.path)} "
        f"({counts['reference_records']} records)",
        f"  Edited file        : {path_text(analysis.edited.path)} "
        f"({counts['edited_records']} records)",
    ]
    if output:
        lines.append(f"  Repaired file      : {path_text(output)}")
    lines += [
        f"  Key                : {', '.join(analysis.key_columns)}",
        f"  Identifier columns : {identifiers}",
    ]
    if analysis.gtin_columns:
        lines.append(f"  GTIN check on      : {', '.join(analysis.gtin_columns)}")
    if analysis.strict:
        lines.append(
            "  Mode               : strict (default; repair only where another column "
            "confirms the record)"
        )
    else:
        lines.append(
            "  Mode               : --trust-key (the key alone decides; wrong if record "
            "IDs were renumbered)"
        )

    damage_label = "Damage repaired" if command == "repair" else "Confirmed damage (repairable)"
    other = ""
    if analysis.other_edits:
        other = "  (" + ", ".join(f"{k}: {v}" for k, v in analysis.other_edits.items()) + ")"
    rows = [
        ("Records matched by key", counts["matched_records"], ""),
        ("New records (only in edited file)", counts["new_records"], ""),
        ("Deleted records (only in reference)", counts["deleted_records"], ""),
        ("Records that could not be matched", counts["unmatched_records"], ""),
        ("Identifier values unchanged", counts["identifier_cells_unchanged"], ""),
        (damage_label, counts["confirmed_damage"], ""),
        ("Findings that need review", counts["needs_review"], ""),
        ("Edits kept in other columns", counts["other_column_edits_kept"], other),
    ]
    lines += ["", "SUMMARY"]
    lines += [f"  {label:<38}{value:>7}{extra}" for label, value, extra in rows]

    title = (
        "REPAIRED - restored from the reference file"
        if command == "repair"
        else "CONFIRMED DAMAGE - the repair command will restore these"
    )
    _section(
        lines,
        title,
        [
            f"  line {f.line}  {key_text(f.key)}  {f.column}: {f.current!r} -> {f.original!r}"
            f"  [{f.detail}]"
            for f in analysis.repairs
        ],
        limit,
    )

    review: list[str] = []
    for f in analysis.cell_findings:
        if f.category == detection.CONFIRMED_DAMAGE:
            continue
        values = f"now {f.current!r}"
        if f.original is not None:
            values = f"reference {f.original!r}, {values}"
        review.append(
            f"  line {f.line}  {key_text(f.key)}  {f.column}: {values}\n"
            f"      {_REVIEW_TITLES[f.category]}: {f.detail}"
        )
    for r in analysis.row_findings:
        if r.category in detection.INFORMATIONAL_ROW_CATEGORIES:
            continue
        review.append(
            f"  line {r.line}  {key_text(r.key)}\n      {_REVIEW_TITLES[r.category]}: {r.detail}"
        )
    _section(lines, "NEEDS REVIEW - left exactly as in the edited file", review, limit)

    _section(
        lines,
        "COLLAPSED VALUES - different reference values that now look the same",
        [
            f"  {c.column}: {c.current!r} now stands for {', '.join(map(repr, c.originals))}"
            f"  (lines {', '.join(map(str, c.lines))})"
            for c in analysis.collisions
        ],
        limit,
    )
    _section(
        lines,
        "NEW RECORDS - kept as they are",
        [f"  line {r.line}  {key_text(r.key)}" for r in analysis.rows(matching.NEW_RECORD)],
        limit,
    )
    _section(
        lines,
        "DELETED RECORDS - in the reference file only, not added back",
        [
            f"  reference line {r.line}  {key_text(r.key)}"
            + ("" if r.detail == "not in the edited file" else f"  ({r.detail})")
            for r in analysis.rows(detection.DELETED_RECORD)
        ],
        limit,
    )

    notes = list(analysis.warnings)
    if analysis.columns_only_in_reference:
        notes.append(
            "Columns only in the reference file: " + ", ".join(analysis.columns_only_in_reference)
        )
    if analysis.columns_only_in_edited:
        notes.append("Columns only in the edited file: " + ", ".join(analysis.columns_only_in_edited))
    if notes:
        lines += ["", "NOTES"]
        lines += [f"  - {note}" for note in notes]

    lines += ["", "RESULT: " + result_line(analysis, command, output)]
    return "\n".join(lines) + "\n"
