"""Shared helpers for the test suite."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from csv_safe_restore.cli import main
from csv_safe_restore.detection import Analysis, analyze
from csv_safe_restore.reader import Table


def make_table(header: list[str], rows: list[list[str]], name: str = "memory.csv") -> Table:
    """Build a Table in memory, as if it had been read from a file."""
    return Table(
        path=Path(name),
        header=header,
        rows=rows,
        line_numbers=list(range(2, len(rows) + 2)),
        has_bom=False,
        newline="\n",
        delimiter=",",
    )


def table_from_text(text: str, name: str = "memory.csv") -> Table:
    """Build a Table from simple CSV text (no quoting) for readable tests."""
    lines = [line.split(",") for line in text.strip().splitlines()]
    return make_table(lines[0], lines[1:], name)


def analyze_trusting_key(*args, **kwargs) -> Analysis:
    """``analyze`` in --trust-key mode (``strict=False``).

    The tests of the key-matching and classification rules use this. Those
    rules are shared by both modes, and strict mode (the default) would hold
    back the repairs that show whether a rule works. Strict mode itself is
    tested in test_strict.py, test_preservation.py and test_regressions.py.
    """
    kwargs.setdefault("strict", False)
    return analyze(*args, **kwargs)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Workspace:
    """Writes CSV files into a temporary folder and runs the CLI on them."""

    def __init__(self, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.folder = folder
        self.capsys = capsys

    def write(self, name: str, content: str | bytes) -> Path:
        path = self.folder / name
        if isinstance(content, str):
            content = content.encode("utf-8")
        path.write_bytes(content)
        return path

    def run(self, *args: object) -> tuple[int, str, str]:
        """Run the CLI. Returns (exit code, stdout, stderr)."""
        self.capsys.readouterr()
        try:
            code = main([str(arg) for arg in args])
        except SystemExit as exc:  # argparse exits by itself on usage errors
            code = exc.code if isinstance(exc.code, int) else 1
        captured = self.capsys.readouterr()
        return code, captured.out, captured.err


@pytest.fixture
def workspace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Workspace:
    return Workspace(tmp_path, capsys)
