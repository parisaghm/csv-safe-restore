"""Known spreadsheet corruption patterns.

The central function is :func:`explain_corruption`. Given the value from the
clean reference file and the value now in the edited file, it answers one
question: "Is the new value exactly what a spreadsheet would have produced from
the old one?" It returns the matching pattern, or None.

Important: this module never works backwards. It cannot turn ``123`` into
``00123``; nobody can, because ``0123`` and ``000123`` give the same result.
It only confirms that a known original could have become the observed value.
Finding the right original is the job of key matching (see ``matching.py``).

All rules are exact and deterministic. There is no fuzzy matching.
"""

from __future__ import annotations

import bisect
import datetime
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP, Context, Decimal

# --------------------------------------------------------------------------
# Pattern names (used in reports and JSON output)
# --------------------------------------------------------------------------

LEADING_ZEROS = "leading_zeros_removed"
NUMBER_REFORMATTED = "number_reformatted"
PRECISION_LOST = "precision_lost"
SCIENTIFIC_NOTATION = "scientific_notation"
DATE_CONVERSION = "date_conversion"
BOOLEAN_UPPERCASED = "boolean_uppercased"
FORMULA_ERROR = "formula_error"


@dataclass(frozen=True)
class Corruption:
    """A confirmed explanation of how an original value became the observed one."""

    pattern: str
    description: str


_DESCRIPTIONS = {
    LEADING_ZEROS: "leading zeros removed (value was read as a number)",
    NUMBER_REFORMATTED: "rewritten as a plain number (sign, zeros or exponent changed)",
    PRECISION_LOST: "digits lost (numbers keep only about 15 significant digits)",
    SCIENTIFIC_NOTATION: "rewritten in scientific notation",
    DATE_CONVERSION: "converted to a date",
    BOOLEAN_UPPERCASED: "converted to a spreadsheet boolean",
    FORMULA_ERROR: "treated as a formula and replaced by an error value",
}


def _corruption(pattern: str) -> Corruption:
    return Corruption(pattern, _DESCRIPTIONS[pattern])


def short_description(pattern: str) -> str:
    """The description of a pattern without its explanation in brackets."""
    return _DESCRIPTIONS[pattern].split(" (")[0]


# --------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------

# Text that a spreadsheet reads as a number: 00123, +44, 1.50, 1E5, 12e-3 ...
_NUMERIC_INPUT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d+))?")
# A number written back in plain form: 123, -4.5, 0.25 (no leading zeros or plus).
_PLAIN_OUTPUT = re.compile(r"-?(?:0|[1-9]\d*)(?:\.(\d+))?")
# A number written back in scientific notation: 1.23457E+11, 1.2345678901234567e+19.
# Spreadsheets and Python always write the sign of the exponent; "2E2" without
# a sign is more likely a product code than a number written by a program.
_SCI_OUTPUT = re.compile(r"-?([1-9](?:\.\d+)?)[eE]([+-]\d+)")

_MAX_NUMERIC_LENGTH = 400
_MAX_EXPONENT = 400
# Excel and LibreOffice store numbers as 64-bit floats and show at most 15 digits.
_SPREADSHEET_DIGITS = 15
# Display rounding (1.23457E+11) is always correct rounding.
_DISPLAY_ROUNDING = (ROUND_HALF_UP, ROUND_HALF_EVEN)
# At the 15-digit limit of float precision the last digit is not reliable:
# Excel cuts digits off, and a real LibreOffice turned 321661743950674477 into
# 3.21661743950675E+017. Both neighbours are accepted there; the other 14
# digits must still match exactly.
_PRECISION_ROUNDING = (ROUND_DOWN, ROUND_UP)
_CONTEXT = Context(prec=_MAX_NUMERIC_LENGTH + _MAX_EXPONENT + 50)


def _numeric_value(text: str) -> Decimal | None:
    """Return the exact numeric value of ``text`` if a spreadsheet would read it as a number."""
    if len(text) > _MAX_NUMERIC_LENGTH or not text.isascii():
        return None
    match = _NUMERIC_INPUT.fullmatch(text)
    if match is None:
        return None
    if match.group(1) is not None and abs(int(match.group(1))) > _MAX_EXPONENT:
        return None
    return Decimal(text)


def _observed_number(text: str) -> tuple[Decimal, int | None] | None:
    """Parse a number as a spreadsheet would have written it.

    Returns (value, digits). ``digits`` is the number of mantissa digits for
    scientific notation and None for plain notation.
    """
    if len(text) > _MAX_NUMERIC_LENGTH + _MAX_EXPONENT or not text.isascii():
        return None
    match = _PLAIN_OUTPUT.fullmatch(text)
    if match is not None:
        fraction = match.group(1)
        # Spreadsheets drop trailing zeros. "123.0" is allowed because that is
        # how pandas writes a float column.
        if fraction is not None and fraction.endswith("0") and fraction != "0":
            return None
        return Decimal(text), None
    match = _SCI_OUTPUT.fullmatch(text)
    if match is not None and abs(int(match.group(2))) <= _MAX_EXPONENT:
        return Decimal(text), len(match.group(1).replace(".", ""))
    return None


def _round_significant(value: Decimal, digits: int, rounding: str) -> Decimal:
    if value.is_zero():
        return value
    quantum = Decimal((0, (1,), value.adjusted() - digits + 1))
    return value.quantize(quantum, rounding=rounding, context=_CONTEXT)


def _lossy_results(original_text: str, value: Decimal, digits: int) -> set[Decimal]:
    """Values that ``value`` can turn into when squeezed into a spreadsheet number."""
    modes = _PRECISION_ROUNDING if digits >= _SPREADSHEET_DIGITS else _DISPLAY_ROUNDING
    results = {_round_significant(value, digits, mode) for mode in modes}
    as_float = _as_float(original_text)
    if as_float is not None:
        results.add(Decimal(repr(as_float)))  # shortest text for the float
        results.add(Decimal(as_float))  # the float's exact value, e.g. int(float(x))
    return results


def _as_float(text: str) -> float | None:
    try:
        number = float(text)
    except (ValueError, OverflowError):
        return None
    if number != number or abs(number) == float("inf"):
        return None
    return number


def _explain_number(original: str, value: Decimal, observed: str) -> Corruption | None:
    parsed = _observed_number(observed)
    if parsed is None:
        return None
    observed_value, mantissa_digits = parsed
    if mantissa_digits is None:
        if observed_value == value:
            if value.is_zero() and observed.startswith("-") and not original.startswith("-"):
                return None  # spreadsheets never add a minus sign to zero
            if original.isdigit() and observed == (original.lstrip("0") or "0"):
                return _corruption(LEADING_ZEROS)
            return _corruption(NUMBER_REFORMATTED)
        if observed_value in _lossy_results(original, value, _SPREADSHEET_DIGITS):
            return _corruption(PRECISION_LOST)
        return None
    if observed_value == value or observed_value in _lossy_results(
        original, value, mantissa_digits
    ):
        return _corruption(SCIENTIFIC_NOTATION)
    return None


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_MONTH_NAMES = (
    "january february march april may june july august september october november december"
).split()
_MONTHS = {name: number for number, name in enumerate(_MONTH_NAMES, start=1)}
_MONTHS.update({name[:3]: number for name, number in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_MONTH_ABBREVIATIONS = {name[:3] for name in _MONTH_NAMES}
_DAYS_IN_MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

# A date guess is (year, month, day). Year or day is None when not stated.
_DateGuess = tuple[int | None, int, int | None]

_IN_TWO_NUMBERS = re.compile(r"(\d{1,4})[-/](\d{1,4})")
_IN_THREE_NUMBERS = re.compile(r"(\d{1,4})([-/.])(\d{1,4})\2(\d{1,4})")
_IN_NAME_NUMBER = re.compile(r"([A-Za-z]{3,9})[-/. ]?(\d{1,4})")
_IN_NUMBER_NAME = re.compile(r"(\d{1,4})[-/. ]?([A-Za-z]{3,9})")

# Date renderings that spreadsheets (and pandas) write. Each has a shape name.
_OUT_DAY_MONTH = re.compile(r"(\d{1,2})-([A-Za-z]{3})")  # 3-Jan
_OUT_MONTH_YEAR = re.compile(r"([A-Za-z]{3})-(\d{2})")  # Mar-24
_OUT_DAY_MONTH_YEAR = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{2}|\d{4})")  # 3-Jan-26
_OUT_SLASH_LONG = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")  # 1/3/2026
_OUT_SLASH_SHORT = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2})")  # 01/03/26
_OUT_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[ T]00:00:00)?")  # 2026-01-03
_OUT_DOTTED = re.compile(r"(\d{2})\.(\d{2})\.(\d{2}|\d{4})")  # 03.01.2026


def _years(text: str) -> list[int]:
    """Possible years for a year written with 2 or 4 digits."""
    number = int(text)
    if len(text) == 4:
        return [number] if number >= 1900 else []
    if len(text) <= 2:
        return [1900 + number, 2000 + number]
    return []


def _valid_day(month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= _DAYS_IN_MONTH[month - 1]


def _full_dates(year_text: str, month: int, day: int) -> set[_DateGuess]:
    if not _valid_day(month, day):
        return set()
    return {(year, month, day) for year in _years(year_text) if _exists(year, month, day)}


def _exists(year: int, month: int, day: int) -> bool:
    try:
        datetime.date(year, month, day)
    except ValueError:
        return False
    return True


def _month_number(name: str, abbreviations_only: bool = False) -> int | None:
    name = name.lower()
    if abbreviations_only and name not in _MONTH_ABBREVIATIONS:
        return None
    return _MONTHS.get(name)


def _date_inputs(text: str) -> set[_DateGuess]:
    """Every date a spreadsheet might see in a typed value such as 1-3 or MAR1."""
    guesses: set[_DateGuess] = set()
    if not text.isascii():
        return guesses
    if match := _IN_TWO_NUMBERS.fullmatch(text):
        first, second = match.group(1), match.group(2)
        a, b = int(first), int(second)
        if _valid_day(a, b):
            guesses.add((None, a, b))
        if _valid_day(b, a):
            guesses.add((None, b, a))
        # A number is read as a year only when it cannot be a day: 1-3 is a
        # day and a month, 3-2024 and 1-32 are a month and a year.
        if 1 <= a <= 12 and not _valid_day(a, b):
            guesses.update((year, a, None) for year in _years(second))
        if 1 <= b <= 12 and not _valid_day(b, a):
            guesses.update((year, b, None) for year in _years(first))
    elif match := _IN_THREE_NUMBERS.fullmatch(text):
        first, second, third = match.group(1), match.group(3), match.group(4)
        a, b, c = int(first), int(second), int(third)
        guesses |= _full_dates(first, b, c)  # year-month-day
        guesses |= _full_dates(third, a, b)  # month-day-year
        guesses |= _full_dates(third, b, a)  # day-month-year
    else:
        name = number = None
        if match := _IN_NAME_NUMBER.fullmatch(text):
            name, number = match.group(1), match.group(2)
        elif match := _IN_NUMBER_NAME.fullmatch(text):
            number, name = match.group(1), match.group(2)
        month = _month_number(name) if name else None
        if month is not None and number is not None:
            if _valid_day(month, int(number)):
                guesses.add((None, month, int(number)))
            else:
                guesses.update((year, month, None) for year in _years(number))
    return guesses


def _date_outputs(text: str) -> tuple[str, set[_DateGuess]] | None:
    """Parse a date as a spreadsheet writes it. Returns (shape name, guesses)."""
    if not text.isascii():
        return None
    guesses: set[_DateGuess] = set()
    shape = ""
    if match := _OUT_DAY_MONTH.fullmatch(text):
        shape = "day-month"
        month = _month_number(match.group(2), abbreviations_only=True)
        if month and _valid_day(month, int(match.group(1))):
            guesses.add((None, month, int(match.group(1))))
    elif match := _OUT_MONTH_YEAR.fullmatch(text):
        shape = "month-year"
        month = _month_number(match.group(1), abbreviations_only=True)
        if month:
            guesses.update((year, month, None) for year in _years(match.group(2)))
    elif match := _OUT_DAY_MONTH_YEAR.fullmatch(text):
        shape = "day-month-year"
        month = _month_number(match.group(2), abbreviations_only=True)
        if month:
            guesses |= _full_dates(match.group(3), month, int(match.group(1)))
    elif match := (_OUT_SLASH_LONG.fullmatch(text) or _OUT_SLASH_SHORT.fullmatch(text)):
        shape = "slashes"
        a, b = int(match.group(1)), int(match.group(2))
        guesses |= _full_dates(match.group(3), a, b)  # month/day/year
        guesses |= _full_dates(match.group(3), b, a)  # day/month/year
    elif match := _OUT_ISO.fullmatch(text):
        shape = "iso"
        guesses |= _full_dates(match.group(1), int(match.group(2)), int(match.group(3)))
    elif match := _OUT_DOTTED.fullmatch(text):
        shape = "dotted"
        guesses |= _full_dates(match.group(3), int(match.group(2)), int(match.group(1)))
    if not guesses:
        return None
    return shape, guesses


def _same_date(typed: _DateGuess, written: _DateGuess) -> bool:
    typed_year, typed_month, typed_day = typed
    written_year, written_month, written_day = written
    if typed_month != written_month:
        return False
    if typed_day is None:
        # "03-2024" means March 2024; spreadsheets store it as the 1st.
        if written_day not in (None, 1):
            return False
    elif written_day != typed_day:
        return False
    # When a year was typed, it must match. When none was typed, the
    # spreadsheet fills in the year in which the file was opened: unknown, but
    # not in the future and not from before these programs wrote such files.
    if typed_year is not None:
        return typed_year == written_year
    return written_year is None or _EARLIEST_FILLED_YEAR <= written_year <= _this_year()


_EARLIEST_FILLED_YEAR = 2000


def _this_year() -> int:
    return datetime.date.today().year


def _explain_date(original: str, observed: str) -> Corruption | None:
    written = _date_outputs(observed)
    if written is None:
        return None
    shape, written_guesses = written
    # If the original was already a date in the same layout, a different date in
    # that layout is an edit (for example day and month swapped by hand), not a
    # conversion.
    original_as_output = _date_outputs(original)
    if original_as_output is not None and original_as_output[0] == shape:
        return None
    typed_guesses = _date_inputs(original)
    if any(_same_date(t, w) for t in typed_guesses for w in written_guesses):
        return _corruption(DATE_CONVERSION)
    return None


# --------------------------------------------------------------------------
# Booleans and formula errors
# --------------------------------------------------------------------------

_BOOLEANS = {"TRUE", "FALSE"}
_ERROR_VALUES = {"#NAME?", "#VALUE!", "#DIV/0!", "#REF!", "#NUM!", "#N/A", "#NULL!"}
_LIBREOFFICE_ERROR = re.compile(r"Err:\d{3}")
_FORMULA_STARTERS = "=+-@"


def _is_error_value(text: str) -> bool:
    return text in _ERROR_VALUES or _LIBREOFFICE_ERROR.fullmatch(text) is not None


def _is_formula_like(text: str) -> bool:
    # A lone "-" or "=" is a placeholder, not a formula.
    return len(text) >= 2 and text[0] in _FORMULA_STARTERS and _numeric_value(text) is None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def explain_corruption(original: str, observed: str) -> Corruption | None:
    """Return the known corruption pattern that turns ``original`` into ``observed``.

    Returns None when the values are equal or when no known pattern explains
    the change. None does not mean the change is fine; it means there is no
    proof, so the tool must not revert it automatically.
    """
    if original == observed:
        return None
    value = _numeric_value(original)
    if value is not None:
        return _explain_number(original, value, observed)
    if original.upper() in _BOOLEANS and observed == original.upper():
        return _corruption(BOOLEAN_UPPERCASED)
    if _is_formula_like(original) and _is_error_value(observed):
        # Text after "=" is a formula and can fail in any way. Text after
        # "+", "-" or "@" is only read as an unknown name; other error values
        # (such as a hand-typed #N/A) do not come from it.
        if original[0] == "=" or observed == "#NAME?":
            return _corruption(FORMULA_ERROR)
    return _explain_date(original, observed)


def incomplete_dates(text: str) -> set[tuple[int, int | None, int | None]]:
    """Dates without a year or day that ``text`` may stand for, as (month, day, year).

    Covers both typed forms (``1-3``, ``MAR1``) and written forms (``3-Jan``).
    Used by the key guard to see that such values can be the same date.
    """
    guesses = set(_date_inputs(text))
    written = _date_outputs(text)
    if written is not None:
        guesses |= written[1]
    return {(month, day, year) for year, month, day in guesses if year is None or day is None}


_LOOKS_SCIENTIFIC = re.compile(r"-?\d(?:\.\d+)?E[+-]\d+")
_LOOKS_TRUNCATED = re.compile(r"[1-9]\d{14}0+")


def looks_damaged(value: str, include_truncation: bool = True) -> str | None:
    """Say why a value looks like spreadsheet damage, judging by its shape alone.

    This is never used as a reason to repair, only as a hint or a reason not
    to. ``include_truncation=False`` leaves out the weakest sign (zeros after
    the 15th digit), which one real 16-digit number in ten has by chance.
    """
    if not value.isascii():
        return None
    if _LOOKS_SCIENTIFIC.fullmatch(value):
        return "scientific notation"
    if _is_error_value(value):
        return "spreadsheet error value"
    if _OUT_DAY_MONTH.fullmatch(value) or _OUT_MONTH_YEAR.fullmatch(value):
        if _date_outputs(value) is not None:
            return "looks like a date written by a spreadsheet"
    if include_truncation and _LOOKS_TRUNCATED.fullmatch(value):
        return "every digit after the 15th is zero (typical precision loss)"
    return None


def looks_truncated(value: str) -> bool:
    """True for a 16+ digit number whose digits after the 15th are all zero."""
    return value.isascii() and _LOOKS_TRUNCATED.fullmatch(value) is not None


def chance_of_trailing_zeros(value: str) -> float:
    """How often a genuine number of this length ends like :func:`looks_truncated`.

    One 16-digit number in 10 ends in 0 by chance, one 17-digit number in 100
    ends in 00, and so on. 0.0 for values that cannot look truncated.
    """
    if not (value.isascii() and value.isdigit()) or len(value) <= _SPREADSHEET_DIGITS:
        return 0.0
    if value[0] == "0":
        return 0.0
    return 10.0 ** (_SPREADSHEET_DIGITS - len(value))


def is_spreadsheet_vulnerable(value: str) -> bool:
    """True for identifier shapes that spreadsheets are known to alter.

    Used only to suggest identifier columns and to warn about risky key
    columns: digit strings with leading zeros, digit strings of 12+ digits,
    codes such as 1E5 that read as scientific notation, and codes such as
    MAR1 or 1-3 that read as an incomplete date. Complete dates such as
    2024-01-05 do not count; a column of real dates is not an identifier.
    """
    if not value.isascii():
        return False
    if value.isdigit():
        return (len(value) > 1 and value[0] == "0") or len(value) >= 12
    if re.fullmatch(r"\d+[eE]\d+", value):
        return True
    return any(year is None or day is None for year, _, day in _date_inputs(value))


class CorruptionIndex:
    """Answers: which of these original values could have turned into X?

    A plain loop over all originals would be too slow for large files, so the
    originals are grouped by kind (numbers sorted by value, dates by month and
    day). Every candidate is still confirmed with :func:`explain_corruption`,
    so the index can never report a source that the rules do not accept.
    """

    def __init__(self, originals: Iterable[str]) -> None:
        numbers: list[tuple[Decimal, str]] = []
        self._dates: dict[tuple[int, int | None], list[str]] = defaultdict(list)
        self._booleans: dict[str, list[str]] = defaultdict(list)
        self._formulas: list[str] = []
        # Originals by the float they are stored as; finds float round-trip
        # sources (int(float(x)), repr(float(x))) with one lookup.
        self._floats: dict[float, list[str]] = defaultdict(list)
        for original in set(originals):
            value = _numeric_value(original)
            if value is not None:
                numbers.append((value, original))
                as_float = _as_float(original)
                if as_float is not None:
                    self._floats[as_float].append(original)
                continue
            if original.upper() in _BOOLEANS:
                self._booleans[original.upper()].append(original)
            if _is_formula_like(original):
                self._formulas.append(original)
            for _, month, day in _date_inputs(original):
                self._dates[(month, day)].append(original)
        numbers.sort()
        self._number_values = [value for value, _ in numbers]
        self._number_texts = [text for _, text in numbers]

    def sources_of(self, observed: str, limit: int | None = 5) -> list[str]:
        """Original values (other than ``observed`` itself) that explain ``observed``.

        ``limit`` stops the search early; pass None to get every source.
        """
        found: dict[str, None] = {}
        for candidate in self._candidates(observed):
            if candidate != observed and candidate not in found:
                if explain_corruption(candidate, observed) is not None:
                    found[candidate] = None
                    if limit is not None and len(found) >= limit:
                        break
        return list(found)

    def _candidates(self, observed: str) -> Iterable[str]:
        parsed = _observed_number(observed)
        if parsed is not None:
            value, mantissa_digits = parsed
            digits = mantissa_digits or _SPREADSHEET_DIGITS
            # Sources through float storage, however far away they are. The
            # observed value must itself be something a float is written as.
            as_float = _as_float(observed)
            if as_float is not None and value in (Decimal(as_float), Decimal(repr(as_float))):
                yield from self._floats.get(as_float, ())
            # Sources through rounding lie within one unit of the last kept
            # digit. A value with more digits than that is not a rounding
            # result at all; then only originals with the same value qualify.
            margin = Decimal(0)
            # Exact arithmetic: the default context keeps only 28 digits.
            if not value.is_zero() and len(value.normalize(_CONTEXT).as_tuple().digits) <= digits:
                margin = Decimal((0, (1,), value.adjusted() - digits + 1))
            low = bisect.bisect_left(self._number_values, _CONTEXT.subtract(value, margin))
            high = bisect.bisect_right(self._number_values, _CONTEXT.add(value, margin))
            yield from self._number_texts[low:high]
        if observed in _BOOLEANS:
            yield from self._booleans.get(observed, ())
        if _is_error_value(observed):
            yield from self._formulas
        written = _date_outputs(observed)
        if written is not None:
            for _, month, day in written[1]:
                yield from self._dates.get((month, day), ())
                if day == 1:
                    yield from self._dates.get((month, None), ())
