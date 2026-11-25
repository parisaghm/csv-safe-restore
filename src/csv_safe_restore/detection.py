"""Compare the two files and classify every difference in identifier columns.

Nothing here changes any data. The result (an :class:`Analysis`) is what both
the ``check`` report and the ``repair`` command are built from, so the report
always describes exactly what a repair would do.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace

from . import matching
from .checks import gtin_problem
from .errors import CsvSafeRestoreError
from .matching import Key, MatchResult
from .patterns import (
    BOOLEAN_UPPERCASED,
    LEADING_ZEROS,
    NUMBER_REFORMATTED,
    CorruptionIndex,
    chance_of_trailing_zeros,
    explain_corruption,
    is_spreadsheet_vulnerable,
    looks_damaged,
    looks_truncated,
    short_description,
)
from .reader import Table

# Cell categories
CONFIRMED_DAMAGE = "confirmed_damage"  # proven damage; the only thing repair changes
SUSPICIOUS_CHANGE = "suspicious_change"  # looks damaged, but does not fit the reference
UNEXPLAINED_CHANGE = "unexplained_change"  # identifier changed; maybe on purpose
UNRECOVERABLE = "unrecoverable"  # looks damaged and there is no trusted reference
INVALID_GTIN = "invalid_gtin"  # optional barcode check failed
UNCORROBORATED = "uncorroborated"  # confirmed damage, but no other column confirms the record
DAMAGED_REFERENCE = "damaged_reference"  # the reference value itself looks damaged

# Row categories reuse the names from matching.py, plus:
DELETED_RECORD = "deleted_record"

# Categories that are normal and need no attention.
INFORMATIONAL_ROW_CATEGORIES = {matching.NEW_RECORD, DELETED_RECORD}


@dataclass(frozen=True)
class CellFinding:
    category: str
    line: int  # line in the edited file
    row_index: int  # index into edited.rows
    key: Key
    column: str
    original: str | None  # None when there is no trusted reference record
    current: str
    pattern: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class RowFinding:
    category: str
    file: str  # "edited" or "reference"
    line: int
    key: Key
    detail: str


@dataclass(frozen=True)
class Collision:
    """Several different reference values that now look identical."""

    column: str
    current: str
    originals: list[str]
    lines: list[int]


@dataclass(frozen=True)
class UncheckedChange:
    """A change in a column that is not checked, which a spreadsheet would make."""

    line: int
    original: str
    current: str
    pattern: str


@dataclass
class Analysis:
    original: Table
    edited: Table
    key_columns: tuple[str, ...]
    identifier_columns: tuple[str, ...]
    identifiers_auto_detected: bool
    gtin_columns: tuple[str, ...]
    matches: MatchResult
    cell_findings: list[CellFinding] = field(default_factory=list)
    row_findings: list[RowFinding] = field(default_factory=list)
    collisions: list[Collision] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    matched_records: int = 0
    unchanged_identifier_cells: int = 0
    # Edits in ordinary columns of matched records; always kept.
    other_edits: dict[str, int] = field(default_factory=dict)
    # Those of the edits above that look exactly like spreadsheet damage. Only
    # used for warnings; these columns are never repaired.
    unchecked_damage: dict[str, list[UncheckedChange]] = field(default_factory=dict)
    columns_only_in_reference: list[str] = field(default_factory=list)
    columns_only_in_edited: list[str] = field(default_factory=list)
    strict: bool = True

    def cells(self, category: str) -> list[CellFinding]:
        return [f for f in self.cell_findings if f.category == category]

    def rows(self, category: str) -> list[RowFinding]:
        return [f for f in self.row_findings if f.category == category]

    @property
    def repairs(self) -> list[CellFinding]:
        return self.cells(CONFIRMED_DAMAGE)

    @property
    def nothing_compared(self) -> bool:
        """True when the files have records but not one pair could be matched."""
        return self.matched_records == 0 and bool(self.original.rows or self.edited.rows)

    @property
    def review_count(self) -> int:
        """Number of findings a person must look at (not fixed automatically)."""
        cells = sum(1 for f in self.cell_findings if f.category != CONFIRMED_DAMAGE)
        rows = sum(
            1 for f in self.row_findings if f.category not in INFORMATIONAL_ROW_CATEGORIES
        )
        return cells + rows


def _require_columns(table: Table, names: tuple[str, ...], role: str, which: str) -> None:
    missing = [name for name in names if name not in table.header]
    if missing:
        raise CsvSafeRestoreError(
            f"{role} column(s) {', '.join(map(repr, missing))} not found in the {which} "
            f"file ({table.path}). Columns there: {', '.join(map(repr, table.header))}"
        )


def _unique(names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(names))


def analyze(
    original: Table,
    edited: Table,
    key_columns: tuple[str, ...],
    identifier_columns: tuple[str, ...] | None = None,
    gtin_columns: tuple[str, ...] = (),
    strict: bool = True,
) -> Analysis:
    """Compare ``edited`` against the clean ``original`` without changing anything.

    By default (``strict=True``) damage is only repaired in rows where another
    column confirms the key match (see :func:`_apply_strict`). With
    ``strict=False`` (the ``--trust-key`` option) the key alone decides.
    """
    key_columns = _unique(key_columns)
    gtin_columns = _unique(gtin_columns)
    if not key_columns:
        raise CsvSafeRestoreError("At least one key column is required.")
    for table, which in ((original, "reference"), (edited, "edited")):
        _require_columns(table, key_columns, "Key", which)
        _require_columns(table, gtin_columns, "GTIN", which)
        if identifier_columns:
            _require_columns(table, identifier_columns, "Identifier", which)

    auto_detected = identifier_columns is None
    if identifier_columns is None:
        identifier_columns = _detect_identifier_columns(original, edited, key_columns)
    identifier_columns = _unique(identifier_columns + gtin_columns)
    overlap = [name for name in identifier_columns if name in key_columns]
    if overlap:
        raise CsvSafeRestoreError(
            f"Column(s) {', '.join(map(repr, overlap))} cannot be both a key and an "
            "identifier column. The key must be independent of the values being "
            "checked: a damaged identifier cannot be used to find its own original."
        )
    if not identifier_columns:
        raise CsvSafeRestoreError(
            "No identifier columns were given and none were detected automatically "
            "(no reference column has leading zeros, 12+ digit numbers or similar). "
            "Name them with --identifier-columns."
        )

    result = Analysis(
        original=original,
        edited=edited,
        key_columns=key_columns,
        identifier_columns=identifier_columns,
        identifiers_auto_detected=auto_detected,
        gtin_columns=gtin_columns,
        matches=matching.match_records(original, edited, key_columns),
        columns_only_in_reference=[c for c in original.header if c not in edited.header],
        columns_only_in_edited=[c for c in edited.header if c not in original.header],
        strict=strict,
    )
    _flag_reassigned_keys(result)
    _collect_row_findings(result)
    _collect_cell_findings(result)
    if strict:
        _apply_strict(result)
    _apply_gtin_checks(result)
    _collect_warnings(result)
    return result


def _detect_identifier_columns(
    original: Table, edited: Table, key_columns: tuple[str, ...]
) -> tuple[str, ...]:
    """Pick columns whose reference values have shapes that spreadsheets alter.

    A column is picked when more than half of its values (empty ones included) have such a
    shape (leading zeros, 12+ digits, ``1E5``, ``MAR1``). One odd value in a
    column of dates or amounts is not enough: picking that column would let
    a legitimate edit there be "restored". This only decides which columns
    to *look at*; it is never a reason to repair.
    """
    detected = []
    for index, name in enumerate(original.header):
        if name in key_columns or name not in edited.header:
            continue
        vulnerable, filled = _vulnerable_share(original, index)
        if vulnerable * 2 > filled:
            detected.append(name)
    return tuple(detected)


def _vulnerable_share(table: Table, index: int) -> tuple[int, int]:
    """(values with a fragile shape, all values) in one column.

    Empty cells count as not fragile, so a column with one fragile value and
    many empty cells is not picked.
    """
    values = [row[index] for row in table.rows]
    return sum(1 for value in values if is_spreadsheet_vulnerable(value)), len(values)


def _other_columns(result: Analysis) -> list[tuple[str, int, int]]:
    """Columns in both files that are neither key nor identifier."""
    original, edited = result.original, result.edited
    return [
        (name, original.column_index(name), edited.column_index(name))
        for name in edited.header
        if name in original.header
        and name not in result.identifier_columns
        and name not in result.key_columns
    ]


# How many other reference records are compared with a row before giving up.
_MAX_RIVALS = 500


def _compatible(reference: str, edited: str) -> bool:
    """True if ``edited`` can be the reference value after a spreadsheet round trip."""
    return (
        reference == edited
        or explain_corruption(reference, edited) is not None
        or bool(matching.collision_forms(reference) & matching.collision_forms(edited))
    )


def _flag_reassigned_keys(result: Analysis) -> None:
    """Distrust a match when the row looks more like another reference record.

    A key is only as good as its stability. If IDs were renumbered, or rows
    slid against the key column, a row carries one record's key and another
    record's data, and restoring by key would write the wrong record's values.

    That can only go wrong when the damaged value has more than one possible
    origin in its column (``123`` when the reference has ``00123`` and
    ``0123``). For such a value the other records that could be its origin are
    "rivals". If the rest of the row agrees better with a rival than with the
    record that has this key, nothing in the row is restored.

    "Agrees better" is decided first by the columns that cannot be explained
    by a spreadsheet round trip at all, then, on a tie, by the columns that
    are not exactly equal. So a rival that matches exactly beats a record
    that only matches after normalisation (``1.5`` for ``1.50``, ``777`` for
    ``0777``).
    """
    original, edited = result.original, result.edited
    columns = [
        (original.column_index(name), edited.column_index(name))
        for name in edited.header
        if name in original.header and name not in result.key_columns
    ]
    identifiers = [
        (original.column_index(name), edited.column_index(name))
        for name in result.identifier_columns
    ]
    indexes: dict[int, CorruptionIndex] = {}
    rows_by_value: dict[int, dict[str, list[int]]] = {}
    sources: dict[tuple[int, str], list[str]] = {}

    def rivals(column: int, restored: str, observed: str) -> list[int] | None:
        """Other reference rows the observed value may come from. None = too many."""
        if column not in indexes:
            by_value: dict[str, list[int]] = defaultdict(list)
            for index, row in enumerate(original.rows):
                by_value[row[column]].append(index)
            rows_by_value[column] = by_value
            indexes[column] = CorruptionIndex(by_value)
        if (column, observed) not in sources:
            found = indexes[column].sources_of(observed, limit=None)
            if observed in rows_by_value[column]:
                found.append(observed)  # a record that really has this value
            sources[(column, observed)] = found
        rows: list[int] = []
        for value in sources[(column, observed)]:
            if value != restored:
                rows.extend(rows_by_value[column][value])
                if len(rows) > _MAX_RIVALS:
                    return None
        return rows

    def disagreements(
        reference_row: list[str], edited_row: list[str], skip: int | None = None
    ) -> tuple[int, int]:
        """(incompatible columns, not exactly equal columns), leaving out ``skip``.

        The column being checked is left out when comparing with rivals: that
        a rival has the observed value is what makes it a rival, not evidence.
        """
        compared = [(a, b) for a, b in columns if a != skip]
        return (
            sum(1 for a, b in compared if not _compatible(reference_row[a], edited_row[b])),
            sum(1 for a, b in compared if reference_row[a] != edited_row[b]),
        )

    def flag(row_index: int, detail: str) -> None:
        match = result.matches.rows[row_index]
        result.matches.rows[row_index] = matching.RowMatch(
            matching.KEY_REASSIGNED,
            match.key,
            detail=detail + ". The keys may have been reassigned, so nothing in this row is restored",
        )

    for row_index, match in enumerate(result.matches.rows):
        if match.original_index is None:
            continue
        edited_row = edited.rows[row_index]
        original_row = original.rows[match.original_index]
        if disagreements(original_row, edited_row) == (0, 0):
            continue  # every column is exactly as in the record with this key
        for original_col, edited_col in identifiers:
            before, now = original_row[original_col], edited_row[edited_col]
            if before == now or explain_corruption(before, now) is None:
                continue
            own = disagreements(original_row, edited_row, skip=original_col)
            if own == (0, 0):
                continue  # the rest of the row is exactly this record: no rival fits better
            candidates = rivals(original_col, before, now)
            if candidates is None:
                flag(
                    row_index,
                    f"{now!r} could come from many reference records, and other columns "
                    "of this row do not fit the reference record with this key",
                )
                break
            better = next(
                (
                    i
                    for i in candidates
                    if disagreements(original.rows[i], edited_row, skip=original_col) < own
                ),
                None,
            )
            if better is not None:
                rival_key = tuple(
                    original.rows[better][original.column_index(k)] for k in result.key_columns
                )
                flag(
                    row_index,
                    "this row fits reference line "
                    f"{original.line_numbers[better]} "
                    f"({matching.format_key(result.key_columns, rival_key)}) better than the "
                    "reference record with this key",
                )
                break


def _collect_row_findings(result: Analysis) -> None:
    matches, original, edited = result.matches, result.original, result.edited
    for index, row in enumerate(matches.rows):
        if row.status != matching.MATCHED:
            result.row_findings.append(
                RowFinding(row.status, "edited", edited.line_numbers[index], row.key, row.detail)
            )
    for index, key, detail in matches.deleted:
        result.row_findings.append(
            RowFinding(DELETED_RECORD, "reference", original.line_numbers[index], key, detail)
        )
    result.matched_records = sum(1 for row in matches.rows if row.status == matching.MATCHED)


def _collect_cell_findings(result: Analysis) -> None:
    original, edited = result.original, result.edited
    identifier_pairs = [
        (name, original.column_index(name), edited.column_index(name))
        for name in result.identifier_columns
    ]
    other_pairs = _other_columns(result)
    other_edits: Counter[str] = Counter()
    unchecked_damage: dict[str, list[UncheckedChange]] = defaultdict(list)
    # (column, current value) -> {reference value: [lines]} for changed cells
    collapsed: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(dict)

    for row_index, match in enumerate(result.matches.rows):
        line = edited.line_numbers[row_index]
        edited_row = edited.rows[row_index]

        if match.original_index is None:
            # No trusted reference record. We can only point at values whose
            # shape gives the damage away; the original cannot be known.
            for name, _, edited_col in identifier_pairs:
                reason = looks_damaged(edited_row[edited_col])
                if reason:
                    result.cell_findings.append(
                        CellFinding(
                            UNRECOVERABLE,
                            line,
                            row_index,
                            match.key,
                            name,
                            None,
                            edited_row[edited_col],
                            detail=reason,
                        )
                    )
            continue

        original_row = original.rows[match.original_index]
        for name, original_col, edited_col in identifier_pairs:
            before, now = original_row[original_col], edited_row[edited_col]
            if before == now:
                result.unchanged_identifier_cells += 1
                continue
            corruption = explain_corruption(before, now)
            # Zeros after the 15th digit are left out: they are weak evidence,
            # and such a value can only "explain" itself anyway.
            damaged_reference = looks_damaged(before, include_truncation=False)
            if corruption is not None and damaged_reference:
                # Never copy back a value that is itself probably damaged.
                category, pattern, detail = (
                    DAMAGED_REFERENCE,
                    None,
                    f"{damaged_reference}; it is not copied back",
                )
            elif corruption is not None:
                category, pattern, detail = (
                    CONFIRMED_DAMAGE,
                    corruption.pattern,
                    corruption.description,
                )
            else:
                reason = looks_damaged(now)
                pattern = None
                if reason:
                    category = SUSPICIOUS_CHANGE
                    detail = reason
                else:
                    category = UNEXPLAINED_CHANGE
                    detail = "possibly an intentional edit"
            result.cell_findings.append(
                CellFinding(category, line, row_index, match.key, name, before, now, pattern, detail)
            )
            collapsed[(name, now)].setdefault(before, []).append(line)

        for name, original_col, edited_col in other_pairs:
            before, now = original_row[original_col], edited_row[edited_col]
            if before != now:
                other_edits[name] += 1
                corruption = explain_corruption(before, now)
                if corruption is not None:
                    unchecked_damage[name].append(
                        UncheckedChange(line, before, now, corruption.pattern)
                    )

    result.other_edits = dict(other_edits)
    result.unchecked_damage = dict(unchecked_damage)
    for (name, now), sources in collapsed.items():
        if len(sources) > 1:
            lines = sorted(line for found in sources.values() for line in found)
            result.collisions.append(Collision(name, now, sorted(sources), lines))


_NO_CORROBORATION = (
    "no other column of this row has a non-empty value that is unchanged and "
    "belongs to this record only, so the key is the only evidence that the two "
    "rows are the same record"
)


def _apply_strict(result: Analysis) -> None:
    """Hold back repairs that rest on the key alone.

    A repair is kept only if no other identifier cell of the row changed in an
    unexplained way, and the row has a *corroborating value*: in a column
    that is in both files, is not a key column and is not the column being
    repaired, a value that is

    * not empty,
    * exactly the same as in the matched reference record (no spreadsheet
      rules, no "looks alike"), and
    * held by exactly one record in the reference file and exactly one
      record in the edited file, and
    * not something a spreadsheet could have made of another reference
      value in that column (checked with the generous key guard).

    Such a value names the same record independently of the key, so a key
    that was renumbered or reused for a different record is caught.
    """
    original, edited = result.original, result.edited
    columns = [
        (name, original.column_index(name), edited.column_index(name))
        for name in edited.header
        if name in original.header and name not in result.key_columns
    ]
    counts: dict[str, tuple[Counter[str], Counter[str], matching.KeyPartIndex]] = {}

    def unique_in_both(name: str, original_col: int, edited_col: int, value: str) -> bool:
        if name not in counts:
            in_original = Counter(row[original_col] for row in original.rows)
            counts[name] = (
                in_original,
                Counter(row[edited_col] for row in edited.rows),
                matching.KeyPartIndex(set(in_original)),
            )
        in_original, in_edited, index = counts[name]
        return (
            in_original[value] == 1
            and in_edited[value] == 1
            # No other reference value could have been turned into this one
            # by a spreadsheet (another record's 1.50 shown as 1.5).
            and not index.sources(value, 1)
        )

    def corroborating(finding: CellFinding) -> str | None:
        match = result.matches.rows[finding.row_index]
        assert match.original_index is not None
        original_row = original.rows[match.original_index]
        edited_row = edited.rows[finding.row_index]
        for name, original_col, edited_col in columns:
            value = edited_row[edited_col]
            if (
                name != finding.column
                and value.strip() != ""
                and value == original_row[original_col]
                and unique_in_both(name, original_col, edited_col, value)
            ):
                return name
        return None

    # Another identifier of the row changed in a way no spreadsheet explains:
    # perhaps an edit, perhaps a different record. Strict mode does not decide.
    unexplained: dict[int, str] = {}
    for finding in result.cell_findings:
        if finding.category in (UNEXPLAINED_CHANGE, SUSPICIOUS_CHANGE, DAMAGED_REFERENCE):
            unexplained.setdefault(finding.row_index, finding.column)

    for position, finding in enumerate(result.cell_findings):
        if finding.category != CONFIRMED_DAMAGE:
            continue
        if finding.row_index in unexplained:
            result.cell_findings[position] = replace(
                finding,
                category=UNCORROBORATED,
                detail=f"{finding.detail}; identifier column "
                f"{unexplained[finding.row_index]!r} of this row changed in a way that is "
                "not spreadsheet damage, so this may not be the same record",
            )
            continue
        column = corroborating(finding)
        if column is None:
            result.cell_findings[position] = replace(
                finding,
                category=UNCORROBORATED,
                detail=f"{finding.detail}; {_NO_CORROBORATION}",
            )
        else:
            result.cell_findings[position] = replace(
                finding, detail=f"{finding.detail}; record confirmed by column {column!r}"
            )


def _apply_gtin_checks(result: Analysis) -> None:
    """Add barcode check-digit warnings. These never cause or block a repair."""
    if not result.gtin_columns:
        return
    edited = result.edited
    by_cell = {(f.row_index, f.column): i for i, f in enumerate(result.cell_findings)}
    for name in result.gtin_columns:
        column = edited.column_index(name)
        for row_index, row in enumerate(edited.rows):
            position = by_cell.get((row_index, name))
            finding = result.cell_findings[position] if position is not None else None
            if finding is not None and finding.category == CONFIRMED_DAMAGE:
                continue  # will be restored to the reference value
            match = result.matches.rows[row_index]
            if finding is None and match.original_index is not None:
                continue  # identical to the reference: nothing new to report
            if row[column] == "":
                continue
            problem = gtin_problem(row[column])
            if problem is None:
                continue
            note = f"not a valid GTIN ({problem})"
            if finding is not None:
                result.cell_findings[position] = replace(
                    finding, detail=f"{finding.detail}; {note}"
                )
            else:
                result.cell_findings.append(
                    CellFinding(
                        INVALID_GTIN,
                        edited.line_numbers[row_index],
                        row_index,
                        match.key,
                        name,
                        None,
                        row[column],
                        detail=note,
                    )
                )
    result.cell_findings.sort(key=lambda f: (f.row_index, edited.header.index(f.column)))


def _collect_warnings(result: Analysis) -> None:
    matches, original = result.matches, result.original
    if matches.reference_missing_key:
        lines = ", ".join(str(original.line_numbers[i]) for i in matches.reference_missing_key[:10])
        result.warnings.append(
            f"{len(matches.reference_missing_key)} reference record(s) have an empty key "
            f"and cannot be used (line {lines}"
            + (", ..." if len(matches.reference_missing_key) > 10 else "")
            + ")."
        )
    if matches.reference_duplicates:
        shown = "; ".join(
            matching.format_key(result.key_columns, key)
            for key in list(matches.reference_duplicates)[:5]
        )
        result.warnings.append(
            f"{len(matches.reference_duplicates)} key value(s) appear more than once in "
            f"the reference file and cannot be used for matching ({shown}"
            + ("; ..." if len(matches.reference_duplicates) > 5 else "")
            + ")."
        )
    for name in result.identifier_columns:
        column = original.column_index(name)
        values = [row[column] for row in original.rows]
        damaged = [value for value in values if looks_damaged(value, include_truncation=False)]
        if damaged:
            result.warnings.append(
                f"The reference file itself has {len(damaged)} value(s) in column {name!r} "
                f"that look damaged (for example {damaged[0]!r}). Values with an obvious "
                "damage shape are never copied back; all others are restored exactly as "
                "the reference file has them, so check that it is a clean copy."
            )
        truncated = _unlikely_trailing_zeros(values)
        if truncated:
            result.warnings.append(
                f"The reference file itself has {len(truncated)} long number(s) in column "
                f"{name!r} whose digits after the 15th are all zero (for example "
                f"{truncated[0]!r}), more than chance explains: a spreadsheet may have cut "
                "them off. They are restored exactly as the reference file has them, so "
                "check that it is a clean copy."
            )
    _warn_about_possible_intentional_edits(result)
    _warn_about_unchecked_damage(result)
    if result.collisions and result.repairs and not result.strict:
        result.warnings.append(
            "Some damaged values could come from more than one reference record "
            "(see COLLAPSED VALUES). With --trust-key, the key alone decides which "
            "original is restored. This is only right if keys mean the same records in "
            "both files; run without --trust-key if records may have been renumbered."
        )
    if result.identifiers_auto_detected:
        for index, name in enumerate(original.header):
            if name in result.key_columns or name in result.identifier_columns:
                continue
            if name not in result.edited.header:
                continue
            if name in result.unchecked_damage:
                continue  # already named, with what changed in it
            vulnerable, filled = _vulnerable_share(original, index)
            if vulnerable:
                result.warnings.append(
                    f"Column {name!r} has {vulnerable} of {filled} value(s) a spreadsheet "
                    "may alter, too few to treat it as an identifier column, so it was "
                    "not checked. Name it with --identifier-columns if it holds identifiers."
                )
        if result.repairs:
            result.warnings.append(
                "The identifier columns were detected automatically "
                f"({', '.join(result.identifier_columns)}). Check that every restored "
                "column really holds identifiers, or name them with --identifier-columns."
            )
    for name in result.columns_only_in_reference:
        column = original.column_index(name)
        if any(is_spreadsheet_vulnerable(row[column]) for row in original.rows):
            result.warnings.append(
                f"Column {name!r} looks like an identifier column in the reference file "
                "but is not in the edited file, so it was not checked. Was it renamed?"
            )
    for name in result.key_columns:
        column = original.column_index(name)
        example = next(
            (row[column] for row in original.rows if is_spreadsheet_vulnerable(row[column])),
            None,
        )
        if example is not None:
            result.warnings.append(
                f"Key column {name!r} contains values a spreadsheet may alter "
                f"(for example {example!r}). If a key was damaged, its record shows up "
                "below as unmatched and is not repaired."
            )


# A shape test cannot prove that a 16+ digit number was cut off: one genuine
# 16-digit number in ten ends in 0. The reference warning is given only when
# a column has more such numbers than this chance explains.
_TRUNCATION_CHANCE_LIMIT = 0.01


def _unlikely_trailing_zeros(values: list[str]) -> list[str]:
    """Values that look cut off, if there are too many of them to be chance."""
    distinct = list(dict.fromkeys(values))  # a repeated value is one piece of evidence
    truncated = [value for value in distinct if looks_truncated(value)]
    if not truncated:
        return []
    expected = sum(chance_of_trailing_zeros(value) for value in distinct)
    if _chance_of_at_least(len(truncated), expected) >= _TRUNCATION_CHANCE_LIMIT:
        return []
    return truncated


def _chance_of_at_least(count: int, expected: float) -> float:
    """Chance of ``count`` or more rare events when ``expected`` are expected (Poisson)."""
    if count <= expected:
        return 1.0
    if expected <= 0.0:  # e.g. numbers so long that their chance rounds to 0
        return 0.0
    term = math.exp(-expected + count * math.log(expected) - math.lgamma(count + 1))
    total, k = 0.0, count
    # Every next term is smaller by expected / (k + 1) < 1.
    while term > 0 and term > total * 1e-12:
        total += term
        k += 1
        term *= expected / k
    return total


# Changes a person can make on purpose that are identical to spreadsheet
# damage: removing leading zeros, dropping a plus sign or decimal zeros,
# writing TRUE for true. Scientific notation, cut-off digits, dates and error
# values are not typed on purpose in an identifier column.
_COULD_BE_TYPED = {LEADING_ZEROS, NUMBER_REFORMATTED, BOOLEAN_UPPERCASED}


def _warn_about_possible_intentional_edits(result: Analysis) -> None:
    """Say plainly that some restorations may undo a deliberate edit.

    Nothing here can know what a person meant. It only names the
    restorations whose edited value is something a person could have typed.
    """
    typed = [f for f in result.repairs if f.pattern in _COULD_BE_TYPED]
    if not typed:
        return
    example = typed[0]
    kinds = ", ".join(sorted({short_description(f.pattern) for f in typed}))
    result.warnings.append(
        f"{len(typed)} of the {len(result.repairs)} restoration(s) put back a value that a "
        f"person could also have changed on purpose ({kinds}; for example line "
        f"{example.line}, {example.column}: {example.current!r} -> {example.original!r}). "
        "A spreadsheet makes exactly the same change, so the tool cannot tell which it "
        "was. If anyone may have edited these values on purpose, check those restorations "
        "before using the file."
    )
    # A spreadsheet removes leading zeros from every number in a column. If
    # matched records kept theirs, the restored ones did not all lose them
    # that way. (New records are left out: they may have been added later.)
    original, edited = result.original, result.edited
    for name in dict.fromkeys(f.column for f in typed if f.pattern == LEADING_ZEROS):
        original_col, edited_col = original.column_index(name), edited.column_index(name)
        kept = sum(
            1
            for match, row in zip(result.matches.rows, edited.rows)
            if match.original_index is not None
            and _has_leading_zero(row[edited_col])
            and row[edited_col] == original.rows[match.original_index][original_col]
        )
        if kept:
            result.warnings.append(
                f"In column {name!r}, {kept} matched value(s) still have their leading zeros, "
                "so a spreadsheet did not remove the zeros from the whole column. Check the "
                "restored leading zeros in this column with extra care: some may have been "
                "removed on purpose."
            )


def _has_leading_zero(value: str) -> bool:
    return value.isascii() and value.isdigit() and len(value) > 1 and value[0] == "0"


_DECIMAL = re.compile(r"\d+\.\d+")


def _formatting_only(change: UncheckedChange) -> bool:
    """True when only the look of a number or true/false changed (1.50 -> 1.5)."""
    if change.pattern == BOOLEAN_UPPERCASED:
        return True
    return (
        change.pattern == NUMBER_REFORMATTED
        and _DECIMAL.fullmatch(change.original) is not None
        and change.current == change.original.rstrip("0").rstrip(".")
    )


def _warn_about_unchecked_damage(result: Analysis) -> None:
    """Name columns that are not checked but changed exactly like spreadsheet damage.

    These are warnings only: such a column is never repaired unless the user
    names it with --identifier-columns.
    """
    formatting: list[str] = []
    for name, changes in result.unchecked_damage.items():
        damage = [change for change in changes if not _formatting_only(change)]
        if damage:
            first = damage[0]
            kinds = ", ".join(sorted({short_description(change.pattern) for change in changes}))
            result.warnings.append(
                f"Column {name!r} was not checked, but {len(changes)} of its value(s) changed "
                f"exactly as a spreadsheet changes them ({kinds}; for example line "
                f"{first.line}: {first.original!r} -> {first.current!r}). They were left as "
                "they are. If the column holds codes or IDs, add it to --identifier-columns "
                "and run again."
            )
        else:
            first = changes[0]
            formatting.append(
                f"{name!r} ({len(changes)}, for example {first.original!r} -> {first.current!r})"
            )
    if formatting:
        result.warnings.append(
            "Only the formatting changed in column(s) that were not checked: "
            + ", ".join(formatting)
            + ". This is usually harmless for amounts and yes/no flags, but codes (such as "
            "customs codes) can lose their meaning. If one of these columns holds codes, add "
            "it to --identifier-columns and run again."
        )
