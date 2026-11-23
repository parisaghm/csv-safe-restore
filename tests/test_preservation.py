"""End-to-end ground-truth tests: real files, the real command line, random data.

Each example builds a reference file, a "true" edited version (deleted, added
and shuffled rows, intentional edits, sometimes renumbered keys) and then the
file a spreadsheet would have saved. Because we know the truth for every
cell, we can check the three promises that matter most:

1. the input files are byte-for-byte unchanged,
2. every value the tool changed is changed to the true value,
3. every other value (including every intentional edit) is exactly as in the
   edited file, in the same rows and the same order.

Quoting and line endings in the output may differ from the edited file; the
cell values may not. Passing these tests does not prove the tool is safe;
they only fail to find a counterexample.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from csv_safe_restore.cli import main

HEADER = ["id", "sku", "name", "price"]
NAMES = ["Ada", "Ben", "Cy", 'Dee "D"', "Eve, Jr.", "Fay\nline two", "Gus", "Hal"]


def damage(value: str) -> str:
    """What a spreadsheet does to a digit string: drop leading zeros."""
    if value.isdigit() and value.lstrip("0"):
        return value.lstrip("0")
    return value


def to_bytes(rows: list[list[str]], newline: str, bom: bool) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator=newline)
    writer.writerow(HEADER)
    writer.writerows(rows)
    return (b"\xef\xbb\xbf" if bom else b"") + buffer.getvalue().encode("utf-8")


def parse(path: Path) -> list[list[str]]:
    text = path.read_bytes().decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text, newline="")))[1:]


@st.composite
def scenarios(draw):
    count = draw(st.integers(1, 12))
    skus = draw(
        st.lists(
            st.text(alphabet="0123", min_size=1, max_size=5), min_size=count, max_size=count
        )
    )
    reference = [
        [f"K{i}", skus[i], draw(st.sampled_from(NAMES)), draw(st.sampled_from(["1", "2.50"]))]
        for i in range(count)
    ]
    # Truth: which reference record each edited row really is, and its true values.
    kept = [i for i in range(count) if not draw(st.booleans()) or i == 0]
    truth = []
    for i in kept:
        key, sku, name, price = reference[i]
        if draw(st.integers(0, 9)) == 0:
            sku = sku + "X"  # an intentional identifier edit (not damage-shaped)
        if draw(st.integers(0, 4)) == 0:
            name = name + " (renamed)"  # never equal to another reference name
        if draw(st.integers(0, 3)) == 0:
            price = "9.99"
        truth.append([key, sku, name, price])
    for n in range(draw(st.integers(0, 2))):
        truth.append([f"N{n}", draw(st.sampled_from(["0001", "42", "007"])), "New", "1"])
    renumbered = False
    if draw(st.booleans()) and len(truth) > 1:
        # Renumbering: the keys of some records were swapped around.
        keys = [row[0] for row in truth]
        shuffled = draw(st.permutations(keys))
        renumbered = keys != shuffled
        for row, key in zip(truth, shuffled):
            row[0] = key
    truth = draw(st.permutations(truth))
    damaged = [[k, damage(s) if draw(st.booleans()) else s, n, p] for k, s, n, p in truth]
    return reference, [list(row) for row in truth], damaged, renumbered


@given(
    scenarios(),
    st.sampled_from(["\n", "\r\n"]),
    st.booleans(),
    st.booleans(),
)
@settings(max_examples=300, deadline=None)
def test_repair_preserves_everything_it_does_not_restore(scenario, newline, bom, strict) -> None:
    reference_rows, truth, damaged, renumbered = scenario
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        reference = folder / "reference.csv"
        edited = folder / "edited.csv"
        output = folder / "repaired.csv"
        report = folder / "report.json"
        reference.write_bytes(to_bytes(reference_rows, newline, bom))
        edited.write_bytes(to_bytes(damaged, newline, bom))
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (reference, edited)]

        args = ["repair", str(reference), str(edited), "--key", "id"]
        args += ["--identifier-columns", "sku", "--output", str(output)]
        args += ["--format", "json", "--report", str(report)]
        # The default is strict; the less conservative mode must be asked for.
        code = main(args if strict else args + ["--trust-key"])

        assert code in (0, 1)
        assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in (reference, edited)] == before
        result = parse(output)
        restored = json.loads(report.read_text(encoding="utf-8"))["confirmed_damage"]

    assert len(result) == len(damaged)
    changed = 0
    for out_row, in_row, true_row in zip(result, damaged, truth):
        # Only the sku column may ever change.
        assert [out_row[0], *out_row[2:]] == [in_row[0], *in_row[2:]]
        if out_row[1] != in_row[1]:
            changed += 1
            # With renumbered keys, --trust-key can restore the wrong record's
            # value (a documented limitation); the default strict mode must not.
            if strict or not renumbered:
                assert out_row[1] == true_row[1], (in_row, out_row, true_row)
    assert changed == len(restored)


def test_default_never_follows_renumbered_keys_even_with_shared_names() -> None:
    """A fixed version of the most dangerous scenario for --trust-key."""
    reference = [["K1", "00123", "Ada", "1"], ["K2", "0123", "Ada", "1"]]
    damaged = [["K1", "123", "Ada", "1"], ["K2", "123", "Ada", "1"]]  # keys swapped
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        (folder / "r.csv").write_bytes(to_bytes(reference, "\n", False))
        (folder / "e.csv").write_bytes(to_bytes(damaged, "\n", False))
        code = main(
            [
                "repair", str(folder / "r.csv"), str(folder / "e.csv"), "--key", "id",
                "--identifier-columns", "sku", "--output", str(folder / "o.csv"),
                "--report", str(folder / "report.txt"),
            ]
        )  # fmt: skip
        assert code == 1
        assert parse(folder / "o.csv") == damaged
