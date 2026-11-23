"""Build and write the repaired file.

The repaired file is the edited file with one kind of change only: cells that
were classified as confirmed damage get the exact value from the matched
reference record. Row order, column order and every other value stay as they
are in the edited file.

Writing is defensive:

* the input files are never opened for writing,
* the output is written to a temporary file first,
* the temporary file is read back and compared cell by cell,
* only then is it moved into place, and never over an existing file unless
  the caller asked for that.
"""

from __future__ import annotations

import csv
import os
import stat
import tempfile
from pathlib import Path

from .detection import Analysis
from .errors import CsvSafeRestoreError
from .reader import Table, read_csv


def repaired_rows(analysis: Analysis) -> list[list[str]]:
    """Return a copy of the edited rows with confirmed damage restored."""
    edited = analysis.edited
    rows = [list(row) for row in edited.rows]
    for finding in analysis.repairs:
        if finding.original is None:  # cannot happen; kept as a hard safety net
            raise AssertionError("confirmed damage without a reference value")
        rows[finding.row_index][edited.column_index(finding.column)] = finding.original
    return rows


def check_output_path(output: Path, inputs: list[Path], overwrite: bool, what: str) -> None:
    """Refuse output paths that would destroy something."""
    for source in inputs:
        if _same_file(output, source):
            raise CsvSafeRestoreError(
                f"The {what} path {output} is one of the input files. "
                "Input files are never overwritten; choose a different path."
            )
    if output.is_dir():
        raise CsvSafeRestoreError(f"The {what} path {output} is a directory.")
    if output.exists() and not overwrite:
        raise CsvSafeRestoreError(
            f"The {what} file {output} already exists. Choose another path or pass "
            "--overwrite to replace it."
        )
    parent = output.parent if str(output.parent) else Path(".")
    if not parent.is_dir():
        raise CsvSafeRestoreError(f"The folder for the {what} file does not exist: {parent}")


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return a.resolve() == b.resolve()


def write_csv(output: Path, header: list[str], rows: list[list[str]], like: Table) -> None:
    """Serialize rows in the style of ``like`` (delimiter, newline, BOM)."""
    encoding = "utf-8-sig" if like.has_bom else "utf-8"
    with open(output, "w", encoding=encoding, newline="") as handle:
        writer = csv.writer(
            handle, delimiter=like.delimiter, lineterminator=like.newline, quoting=csv.QUOTE_MINIMAL
        )
        writer.writerow(header)
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())


def write_text_atomically(output: Path, text: str, overwrite: bool) -> None:
    """Write a text file (used for reports) without ever leaving half a file."""

    def write(temporary: Path) -> None:
        with open(temporary, "w", encoding="utf-8", errors="backslashreplace", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

    _write_atomically(output, write, overwrite)


def write_repaired_csv(
    output: Path, header: list[str], rows: list[list[str]], like: Table, overwrite: bool
) -> None:
    """Write the repaired CSV, verify it by reading it back, then move it into place."""

    def write(temporary: Path) -> None:
        write_csv(temporary, header, rows, like)
        written = read_csv(temporary, like.delimiter)
        if written.header != header or written.rows != rows:
            raise CsvSafeRestoreError(
                "Internal safety check failed: the written file does not read back "
                "with the same values. Nothing was saved. Please report this as a bug."
            )

    _write_atomically(output, write, overwrite)


def _write_atomically(output: Path, write, overwrite: bool) -> None:
    parent = output.parent if str(output.parent) else Path(".")
    try:
        # A short fixed prefix: a long output name must not make the
        # temporary name too long for the file system.
        descriptor, name = tempfile.mkstemp(prefix=".csv-safe-restore-", suffix=".tmp", dir=parent)
    except OSError as exc:
        raise CsvSafeRestoreError(f"Cannot write to {parent}: {exc.strerror or exc}") from None
    os.close(descriptor)
    temporary = Path(name)
    try:
        write(temporary)
        mode = 0o666 & ~_current_umask()
        if overwrite and output.exists():  # keep the permissions of the file replaced
            mode = stat.S_IMODE(os.stat(output).st_mode)
        os.chmod(temporary, mode)
        if overwrite:
            os.replace(temporary, output)
        else:
            _move_without_replacing(temporary, output)
    except FileExistsError:
        raise CsvSafeRestoreError(
            f"The file {output} already exists. Choose another path or pass "
            "--overwrite to replace it."
        ) from None
    except OSError as exc:
        raise CsvSafeRestoreError(f"Cannot write {output}: {exc.strerror or exc}") from None
    finally:
        temporary.unlink(missing_ok=True)


def _move_without_replacing(temporary: Path, output: Path) -> None:
    """Give the finished file its final name, failing if that name is taken.

    A hard link fails when the target exists, which closes the gap between
    "check that the file does not exist" and "create it".
    """
    try:
        os.link(temporary, output)
    except FileExistsError:
        raise
    except OSError:
        # File systems without hard links: fall back to check-then-rename.
        if output.exists():
            raise FileExistsError(str(output)) from None
        os.replace(temporary, output)


def _current_umask() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return umask
