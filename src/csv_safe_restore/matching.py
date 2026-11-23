"""Match records between the reference file and the edited file by key.

A record in the edited file is only trusted as "the same record" when all of
these hold:

* its key is not empty,
* its key appears exactly once in the edited file,
* the same key appears exactly once in the reference file,
* no *other* reference key could have been turned into this key by a
  spreadsheet (for example reference keys ``007`` and ``7``: an edited row
  with key ``7`` might really be the damaged ``007`` row).

Anything else is reported and left alone. Row order is never used.

The last check is deliberately broader than the repair rules. To *repair* a
value the change must fit a known pattern exactly; to *distrust* a key it is
enough that two reference keys could plausibly end up looking the same (for
example ``"1,000"`` and ``1000``, or ``" 7"`` and ``7``).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Context, Decimal, InvalidOperation

from .patterns import CorruptionIndex, incomplete_dates

# Exact decimal arithmetic for long numbers (the default keeps 28 digits).
_EXACT = Context(prec=1000)
from .reader import Table

Key = tuple[str, ...]

MATCHED = "matched"
NEW_RECORD = "new_record"
MISSING_KEY = "missing_key"
DUPLICATE_KEY = "duplicate_key"
AMBIGUOUS_KEY = "ambiguous_key"
SUSPECTED_KEY_DAMAGE = "suspected_key_damage"
KEY_REASSIGNED = "suspected_key_reassignment"  # set by detection.py

_SHOWN = 5  # how many keys or line numbers a message lists

# Longer numbers are not analysed for spreadsheet damage. Keys like that are
# never trusted (see _too_long), because a collision could not be ruled out.
_LONGEST_CHECKED = 400
_NUMBER_LIKE = re.compile(r"[\d\s.,+\-eE]+")


@dataclass(frozen=True)
class RowMatch:
    """What we know about one row of the edited file."""

    status: str
    key: Key
    original_index: int | None = None  # set only when status is MATCHED
    detail: str = ""


@dataclass
class MatchResult:
    rows: list[RowMatch]  # one entry per edited row, in file order
    # Reference rows whose key does not appear in the edited file.
    deleted: list[tuple[int, Key, str]] = field(default_factory=list)
    # Reference rows that can never be used: empty or duplicated key.
    reference_missing_key: list[int] = field(default_factory=list)
    reference_duplicates: dict[Key, list[int]] = field(default_factory=dict)


def format_key(key_columns: tuple[str, ...], key: Key) -> str:
    return ", ".join(f"{name}={value!r}" for name, value in zip(key_columns, key))


def _listed(items: list[str]) -> str:
    """Join at most a few items, saying how many were left out."""
    text = ", ".join(items[:_SHOWN])
    if len(items) > _SHOWN:
        text += f" and {len(items) - _SHOWN} more"
    return text


def _is_missing(key: Key) -> bool:
    return any(part.strip() == "" for part in key)


def _keys_of(table: Table, key_columns: tuple[str, ...]) -> list[Key]:
    indexes = [table.column_index(name) for name in key_columns]
    return [tuple(row[i] for i in indexes) for row in table.rows]


_SPACES = re.compile(r"[\s  ']")
_CURRENCY = "$€£¥"
_DECIMAL = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?")
_FRACTION = re.compile(r"(-?)(?:(\d{1,9}) )?(\d{1,9})/([1-9]\d{0,8})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?: ?([AaPp])[Mm])?")


def _too_long(part: str) -> bool:
    """A number-like key part too long to check for spreadsheet damage."""
    stripped = part.strip()
    return len(stripped) > _LONGEST_CHECKED and _NUMBER_LIKE.fullmatch(stripped) is not None


def collision_forms(text: str) -> set[str]:
    """Forms under which two different keys could end up looking the same.

    Used only to *distrust* keys, never to repair anything, so it is generous:
    surrounding spaces, a leading "=", thousands separators, decimal commas,
    currency and percent signs, and the various ways to write a negative
    number, a fraction, a time or an incomplete date are all treated as
    things a spreadsheet might normalise.
    """
    stripped = text.strip().strip(" ")
    forms = {"text:" + stripped}
    if len(stripped) > _LONGEST_CHECKED:
        return forms
    if stripped.startswith("="):  # =7 and ="007" are shown as 7 and 007
        stripped = stripped[1:].strip()
        if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
            stripped = stripped[1:-1]
        forms.add("text:" + stripped)
    if stripped.upper() in ("TRUE", "FALSE"):
        forms.add("boolean:" + stripped.upper())
    if match := _TIME.fullmatch(stripped):
        hours = int(match.group(1))
        if match.group(4):
            hours = hours % 12 + (12 if match.group(4) in "Pp" else 0)
        forms.add(f"time:{hours * 3600 + int(match.group(2)) * 60 + int(match.group(3) or 0)}")
    if match := _FRACTION.fullmatch(stripped):
        number = int(match.group(2) or 0) + Decimal(int(match.group(3))) / int(match.group(4))
        forms.add("number:" + _canonical(number.copy_negate() if match.group(1) else number))
    forms.update(f"date:{month}-{day}-{year}" for month, day, year in incomplete_dates(stripped))

    compact = _SPACES.sub("", stripped).replace("−", "-")
    negative = False
    if compact.startswith("(") and compact.endswith(")"):  # accounting style
        compact, negative = compact[1:-1], True
    elif compact.endswith("-"):
        compact, negative = compact[:-1], True
    percent = compact.endswith("%")
    compact = compact.rstrip("%").strip(_CURRENCY)
    if compact[:1] in "+-" and compact[1:2] and compact[1] in _CURRENCY:  # -$7
        compact = compact[0] + compact[2:]
    readings = {compact.replace(",", ""), compact.replace(".", "").replace(",", ".")}
    for reading in readings:
        if _DECIMAL.fullmatch(reading):
            try:
                number = Decimal(reading)
            except InvalidOperation:
                continue
            if negative:
                number = number.copy_negate()
            forms.add("number:" + _canonical(number))
            if percent:
                forms.add("number:" + _canonical(_EXACT.divide(number, 100)))
    return forms


def _canonical(number: Decimal) -> str:
    return "0" if number.is_zero() else str(number.normalize(_EXACT))


class KeyPartIndex:
    """Reference values of one key column, searchable by "could become X"."""

    def __init__(self, values: set[str]) -> None:
        self._corruption = CorruptionIndex(values)
        self._by_form: dict[str, list[str]] = defaultdict(list)
        for value in sorted(values):
            for form in collision_forms(value):
                self._by_form[form].append(value)
        self._cache: dict[tuple[str, int | None], list[str]] = {}

    def sources(self, observed: str, limit: int | None) -> list[str]:
        """Other reference values that might be the origin of ``observed``."""
        cached = self._cache.get((observed, limit))
        if cached is None:
            found = dict.fromkeys(self._corruption.sources_of(observed, limit))
            for form in sorted(collision_forms(observed)):
                found.update(dict.fromkeys(self._by_form.get(form, ())))
            found.pop(observed, None)
            cached = self._cache[(observed, limit)] = list(found)
        return cached


def match_records(original: Table, edited: Table, key_columns: tuple[str, ...]) -> MatchResult:
    original_keys = _keys_of(original, key_columns)
    edited_keys = _keys_of(edited, key_columns)

    original_rows: dict[Key, list[int]] = defaultdict(list)
    reference_missing_key: list[int] = []
    for index, key in enumerate(original_keys):
        if _is_missing(key):
            reference_missing_key.append(index)
        else:
            original_rows[key].append(index)

    edited_rows: dict[Key, list[int]] = defaultdict(list)
    for index, key in enumerate(edited_keys):
        if not _is_missing(key):
            edited_rows[key].append(index)

    # One index per key column, to find reference keys that a spreadsheet could
    # have turned into a given edited key.
    width = len(key_columns)
    indexes = [KeyPartIndex({key[position] for key in original_rows}) for position in range(width)]
    keys_with_part: list[dict[str, list[Key]]] = [defaultdict(list) for _ in range(width)]
    if width > 1:
        for key in original_rows:
            for position, part in enumerate(key):
                keys_with_part[position][part].append(key)

    def possible_sources(key: Key) -> list[Key]:
        """Other reference keys that this key might really be."""
        if width == 1:
            return [(source,) for source in indexes[0].sources(key[0], _SHOWN + 1)]
        # Every part must be the same or a possible source. All sources are
        # needed here: cutting the list short could hide the one combination
        # that exists. Start from the key column with the fewest candidates.
        allowed = [{part, *indexes[i].sources(part, None)} for i, part in enumerate(key)]
        if all(len(options) == 1 for options in allowed):
            return []

        def bucket_size(position: int) -> int:
            return sum(len(keys_with_part[position].get(v, ())) for v in allowed[position])

        start = min(range(width), key=bucket_size)
        found = []
        for value in sorted(allowed[start]):
            for candidate in keys_with_part[start].get(value, ()):
                if candidate != key and all(candidate[i] in allowed[i] for i in range(width)):
                    found.append(candidate)
        return found

    duplicate_details: dict[Key, str] = {}

    def duplicate_detail(key: Key) -> str:
        if key not in duplicate_details:
            lines = [str(edited.line_numbers[i]) for i in edited_rows[key]]
            duplicate_details[key] = (
                f"the same key is on {len(lines)} lines of the edited file ({_listed(lines)})"
            )
        return duplicate_details[key]

    # Key columns in which the reference has a number too long to check: any
    # number in that column might be its damaged form.
    long_columns = {
        position for key in original_rows for position, part in enumerate(key) if _too_long(part)
    }
    too_long_detail = (
        f"this key, or a reference key in the same column, is a number longer than "
        f"{_LONGEST_CHECKED} characters, too long to check whether a spreadsheet "
        "changed it, so this row cannot be tied to one reference record"
    )

    suspected_sources: dict[Key, list[int]] = defaultdict(list)
    rows: list[RowMatch] = []
    for index, key in enumerate(edited_keys):
        line = edited.line_numbers[index]
        if _is_missing(key):
            rows.append(RowMatch(MISSING_KEY, key, detail="the key is empty"))
            continue
        if len(edited_rows[key]) > 1:
            rows.append(RowMatch(DUPLICATE_KEY, key, detail=duplicate_detail(key)))
            continue
        if any(
            _too_long(part) or (position in long_columns and any(c.isdigit() for c in part))
            for position, part in enumerate(key)
        ):
            rows.append(RowMatch(AMBIGUOUS_KEY, key, detail=too_long_detail))
            continue
        sources = possible_sources(key)
        shown = _listed([format_key(key_columns, source) for source in sources])
        if key not in original_rows:
            if sources:
                for source in sources[:_SHOWN]:
                    suspected_sources[source].append(line)
                rows.append(
                    RowMatch(
                        SUSPECTED_KEY_DAMAGE,
                        key,
                        detail="this key is not in the reference file, but it may be the "
                        f"damaged form of reference key {shown}",
                    )
                )
            else:
                rows.append(RowMatch(NEW_RECORD, key, detail="not in the reference file"))
            continue
        if len(original_rows[key]) > 1:
            lines = _listed([str(original.line_numbers[i]) for i in original_rows[key]])
            rows.append(
                RowMatch(
                    DUPLICATE_KEY,
                    key,
                    detail=f"the key appears more than once in the reference file (lines {lines})",
                )
            )
            continue
        if sources:
            rows.append(
                RowMatch(
                    AMBIGUOUS_KEY,
                    key,
                    detail=f"the reference file also has key {shown}, which a spreadsheet "
                    "could turn into this key, so this row cannot be tied to one "
                    "reference record",
                )
            )
            continue
        rows.append(RowMatch(MATCHED, key, original_index=original_rows[key][0]))

    result = MatchResult(rows=rows, reference_missing_key=reference_missing_key)
    result.reference_duplicates = {
        key: found for key, found in original_rows.items() if len(found) > 1
    }
    for key, found in original_rows.items():
        if key in edited_rows:
            continue
        detail = "not in the edited file"
        if key in suspected_sources:
            lines = _listed([str(number) for number in suspected_sources[key]])
            detail += f"; possibly the record on line {lines} whose key looks damaged"
        result.deleted.extend((index, key, detail) for index in found)
    result.deleted.sort()
    return result
