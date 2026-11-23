"""Strict CSV reading.

Every value is kept as a string exactly as it appears in the file. Nothing is
converted to a number or a date, and nothing is trimmed.

The reader refuses anything it cannot interpret with certainty (bad encoding,
duplicate headers, rows with the wrong number of fields) instead of trying to
fix it. A tool that restores data must not start from a guess.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .errors import CsvSafeRestoreError

_UTF8_BOM = b"\xef\xbb\xbf"
_OTHER_BOMS = {  # longest first: the UTF-32 LE mark starts like UTF-16 LE
    b"\xff\xfe\x00\x00": "UTF-32 (little endian)",
    b"\x00\x00\xfe\xff": "UTF-32 (big endian)",
    b"\xff\xfe": "UTF-16 (little endian)",
    b"\xfe\xff": "UTF-16 (big endian)",
}

# The csv module limits field size to 128 KB by default. Long descriptions are
# legitimate data, so allow much larger fields.
csv.field_size_limit(2**31 - 1)


@dataclass(frozen=True)
class Table:
    """The contents of one CSV file."""

    path: Path
    header: list[str]
    rows: list[list[str]]
    # line_numbers[i] is the line of the file where rows[i] starts (1-based).
    line_numbers: list[int]
    has_bom: bool
    newline: str
    delimiter: str

    def column_index(self, name: str) -> int:
        return self.header.index(name)


def parse_delimiter(text: str) -> str:
    """Turn the --delimiter option into a single character."""
    names = {"tab": "\t", "\\t": "\t", "comma": ",", "semicolon": ";", "pipe": "|"}
    delimiter = names.get(text.lower(), text)
    if len(delimiter) != 1 or delimiter in '"\r\n':
        raise CsvSafeRestoreError(
            f"Invalid delimiter {text!r}. Use one character, or the word 'tab'."
        )
    return delimiter


def read_csv(path: str | Path, delimiter: str = ",") -> Table:
    """Read a CSV file strictly, or raise CsvSafeRestoreError explaining why not."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise CsvSafeRestoreError(f"File not found: {path}") from None
    except IsADirectoryError:
        raise CsvSafeRestoreError(f"Expected a CSV file but got a directory: {path}") from None
    except OSError as exc:
        raise CsvSafeRestoreError(f"Cannot read {path}: {exc.strerror or exc}") from None

    for bom, name in _OTHER_BOMS.items():
        if data.startswith(bom):
            raise CsvSafeRestoreError(
                f"{path}: the file is encoded as {name}. Only UTF-8 is supported; "
                "re-save the file as 'CSV UTF-8'."
            )
    has_bom = data.startswith(_UTF8_BOM)
    if has_bom:
        data = data[len(_UTF8_BOM) :]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CsvSafeRestoreError(
            f"{path}: not valid UTF-8 (problem at byte {exc.start}). Only UTF-8 is "
            "supported; re-save the file as 'CSV UTF-8'."
        ) from None
    if "\x00" in text:
        raise CsvSafeRestoreError(
            f"{path}: contains NUL characters, so it is not a plain-text CSV file."
        )
    if not text.strip():
        raise CsvSafeRestoreError(f"{path}: the file is empty (no header row).")

    # newline="" hands line endings to the csv module untouched, which is what
    # keeps newlines inside quoted fields exactly as they were.
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    records: list[list[str]] = []
    line_numbers: list[int] = []
    start_line = 1
    try:
        for record in reader:
            records.append(record)
            line_numbers.append(start_line)
            start_line = reader.line_num + 1
    except csv.Error as exc:
        raise CsvSafeRestoreError(
            f"{path}: malformed CSV in the record starting at line {start_line}: {exc}"
        ) from None

    header = records[0]
    _check_header(path, header, delimiter)

    rows = records[1:]
    row_lines = line_numbers[1:]
    for row, line in zip(rows, row_lines):
        if len(row) != len(header):
            what = "is blank" if not row else f"has {len(row)} field(s)"
            raise CsvSafeRestoreError(
                f"{path}: line {line} {what}, but the header has {len(header)} "
                "column(s). Refusing to guess how the values line up."
            )

    return Table(
        path=path,
        header=header,
        rows=rows,
        line_numbers=row_lines,
        has_bom=has_bom,
        newline=_detect_newline(text, delimiter),
        delimiter=delimiter,
    )


def _check_header(path: Path, header: list[str], delimiter: str) -> None:
    if not header:
        raise CsvSafeRestoreError(f"{path}: the first line is blank (no header row).")
    duplicates = [name for name, count in Counter(header).items() if count > 1]
    if duplicates:
        shown = ", ".join(repr(name) for name in duplicates)
        raise CsvSafeRestoreError(
            f"{path}: duplicate column name(s) in the header: {shown}. Columns are "
            "matched by name, so each name must be unique. Rename them and try again."
        )
    if len(header) == 1:
        for other, label in ((";", "semicolon"), ("\t", "tab"), (",", "comma")):
            if other != delimiter and other in header[0]:
                raise CsvSafeRestoreError(
                    f"{path}: only one column was found, and the header contains a "
                    f"{label}. If the file is {label}-separated, pass "
                    f"--delimiter {label}."
                )


def _detect_newline(text: str, delimiter: str) -> str:
    """Return the line ending that ends the header row.

    The csv module reads the header record, so line breaks inside quoted
    fields are skipped exactly as when the data is read. The physical line
    it consumed last ends the record. A file without any line ending is
    treated as using \\n.
    """
    last = ""

    def lines():
        nonlocal last
        for line in io.StringIO(text, newline=""):
            last = line
            yield line

    try:
        next(csv.reader(lines(), delimiter=delimiter, strict=True))
    except (StopIteration, csv.Error):
        return "\n"
    for ending in ("\r\n", "\n", "\r"):
        if last.endswith(ending):
            return ending
    return "\n"
