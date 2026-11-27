"""The tests must check the source in this folder, not an older installed copy."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import csv_safe_restore

SOURCE = Path(__file__).resolve().parent.parent / "src"


def test_tests_import_the_working_source() -> None:
    assert Path(csv_safe_restore.__file__).resolve().parent == SOURCE / "csv_safe_restore"


def test_the_validation_scorer_runs_the_working_source() -> None:
    validation = SOURCE.parent / "validation"
    if not (validation / "score.py").exists():  # the source archive omits validation/
        return
    sys.path.insert(0, str(validation))
    try:
        import score  # noqa: PLC0415
    finally:
        sys.path.remove(str(validation))
    found = subprocess.run(
        [sys.executable, "-c", "import csv_safe_restore; print(csv_safe_restore.__file__)"],
        capture_output=True, text=True, encoding="utf-8", env=score.tool_environment(),
        check=True,
    ).stdout.strip()
    assert Path(found).resolve().parent == SOURCE / "csv_safe_restore"
