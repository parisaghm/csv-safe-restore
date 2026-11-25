"""Real LibreOffice round trips from validation/ (Phase 2) must stay safe.

The edited files in validation/data/<table>/<profile>/ were written by a real
LibreOffice (see each run.txt), not by a model. Every identifier cell of the
tool's output is compared with what the person intended to save, and every
warning is judged against what is known to be true.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

VALIDATION = Path(__file__).resolve().parent.parent / "validation"
EDITED = sorted((VALIDATION / "data").glob("*/*/edited.csv"))

if not EDITED:  # e.g. when testing from the source archive, which omits validation/
    pytest.skip("validation data not present", allow_module_level=True)

spec = importlib.util.spec_from_file_location("validation_score", VALIDATION / "score.py")
score = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = score  # dataclasses look the module up while it loads
spec.loader.exec_module(score)


@pytest.mark.parametrize(
    "flags", [[], ["--trust-key"], ["--auto-detect"]], ids=["default", "trust-key", "auto-detect"]
)
@pytest.mark.parametrize("edited", EDITED, ids=lambda p: f"{p.parent.parent.name}-{p.parent.name}")
def test_real_round_trip_has_no_incorrect_restoration(edited: Path, flags: list[str]) -> None:
    table = edited.parent.parent.name
    result = score.score(table, edited.parent.parent, edited, flags)

    assert result.outcomes["incorrect"] == 0, result.problems
    assert result.other_column_changed == 0
    assert result.inputs_unchanged
    # Real damage was present and the tool really restored some of it.
    assert result.outcomes["restored"] > 0
    # The only undone changes are the deliberate "remove the leading zeros"
    # edits, which look exactly like damage (a documented limitation).
    assert {p["scenario"] for p in result.problems} <= {"intentional_edit_like_damage"}

    # Every deliberate change that was undone is covered by the warning that
    # some restorations may undo a deliberate edit.
    assert result.undone_flagged == result.outcomes["intentional_undone"]
    # No warning says something false (the reference files are clean) ...
    assert [text for verdict, text in result.warnings if verdict.startswith("FALSE")] == []
    # ... and every column the spreadsheet damaged is either checked or named.
    assert result.unwarned == []
