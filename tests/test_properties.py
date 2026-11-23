"""Property-based tests (Hypothesis) for the safety guarantees.

Instead of hand-picked examples, these tests generate thousands of random
tables and random edits and check rules that must hold for every one of them.
The most important rule: a repaired cell is always either the edited value or
the reference value of a record matched by a unique key. Nothing else, ever.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from csv_safe_restore.errors import CsvSafeRestoreError
from csv_safe_restore.patterns import CorruptionIndex, explain_corruption, looks_damaged
from csv_safe_restore.reader import read_csv
from csv_safe_restore.repair import repaired_rows, write_csv

from conftest import analyze_trusting_key as analyze
from conftest import make_table

HEADER = ["id", "code", "price"]

# Small alphabets on purpose: they make collisions, duplicates and
# number-like values common instead of rare.
keys = st.one_of(
    st.sampled_from(["", "1", "01", "001", "7", "007", "A", "B", "1E2", "100"]),
    st.text(alphabet="01A", max_size=3),
)
codes = st.one_of(
    st.text(alphabet="0123456789", max_size=20),
    st.text(alphabet="019E+-.", max_size=8),
    st.sampled_from(["MAR1", "1-Mar", "1-3", "3-Jan", "true", "TRUE", "-A", "#NAME?", "AB-01"]),
    st.text(max_size=5),
)
prices = st.sampled_from(["1.00", "1", "2.50", ""])
tables = st.lists(st.tuples(keys, codes, prices).map(list), max_size=8)


def spreadsheet(value: str) -> str:
    """A small model of what a spreadsheet does to digit strings."""
    if not (value.isascii() and value.isdigit()):
        return value
    number = int(value)
    digits = str(number)
    if len(digits) > 15:  # keep 15 digits, zero the rest
        digits = digits[:15] + "0" * (len(digits) - 15)
    if len(digits) >= 12:  # General format switches to scientific notation
        return f"{Decimal(digits):.5E}"  # e.g. 1.23457E+11
    return digits


@st.composite
def related_tables(draw: st.DrawFn) -> tuple[list[list[str]], list[list[str]]]:
    """A reference table and an edited copy of it, the way a real file goes.

    Independent random tables almost never contain a row that strict mode may
    repair (its price would have to stay the record's unique price by
    chance). Here the edited table is derived from the reference: identifiers
    get spreadsheet damage, prices are edited, keys are renumbered, rows are
    deleted, added and shuffled.
    """
    # Mostly well-behaved keys and codes that spreadsheets really damage,
    # mixed with the hostile ones above, so that repairs actually happen.
    related_keys = st.one_of(st.sampled_from([f"K{i}" for i in range(1, 9)]), keys)
    damageable = st.one_of(
        st.from_regex(r"0{1,3}[1-9][0-9]{0,3}", fullmatch=True),
        st.from_regex(r"[1-9][0-9]{11,17}", fullmatch=True),
        codes,
    )
    original = draw(
        st.lists(
            st.tuples(
                related_keys,
                damageable,
                st.one_of(  # mostly distinct prices, sometimes shared or empty
                    st.from_regex(r"[1-9][0-9]?\.[0-9]{2}", fullmatch=True),
                    st.sampled_from(["1.00", "2.50", ""]),
                ),
            ),
            max_size=8,
            unique_by=lambda row: row[0],  # duplicates are covered by `tables`
        ).map(lambda rows: [list(row) for row in rows])
    )
    edited = []
    for key, code, price in original:
        action = draw(st.sampled_from(["keep", "damage", "damage", "edit price", "drop"]))
        if action == "drop":
            continue
        if action == "damage":
            code = spreadsheet(code)
        if action == "edit price":
            price = draw(prices)
        edited.append([key, code, price])
    if len(edited) >= 2 and draw(st.booleans()):  # renumber: two rows swap keys
        first, second = draw(st.lists(st.integers(0, len(edited) - 1), min_size=2, max_size=2))
        edited[first][0], edited[second][0] = edited[second][0], edited[first][0]
    edited += draw(st.lists(st.tuples(keys, codes, prices).map(list), max_size=2))
    return original, draw(st.permutations(edited))


@pytest.mark.parametrize("strict", [True, False], ids=["default-strict", "trust-key"])
@given(st.one_of(st.tuples(tables, tables), related_tables()))
@settings(max_examples=600, deadline=None)
def test_repair_never_invents_or_misplaces_a_value(strict, pair) -> None:
    original_rows, edited_rows = pair
    original = make_table(HEADER, original_rows)
    edited = make_table(HEADER, edited_rows)
    analysis = analyze(original, edited, ("id",), ("code",), strict=strict)
    output = repaired_rows(analysis)

    original_key_counts = Counter(row[0] for row in original_rows)
    edited_key_counts = Counter(row[0] for row in edited_rows)
    reference = {row[0]: row[1] for row in original_rows}
    reference_price = {row[0]: row[2] for row in original_rows}
    original_price_counts = Counter(row[2] for row in original_rows)
    edited_price_counts = Counter(row[2] for row in edited_rows)

    assert len(output) == len(edited_rows)
    for before, after in zip(edited_rows, output):
        # Key and ordinary columns are never changed.
        assert after[0] == before[0] and after[2] == before[2]
        if after[1] != before[1]:
            key = before[0]
            assert key.strip() != ""
            assert original_key_counts[key] == 1 and edited_key_counts[key] == 1
            assert after[1] == reference[key]
            assert explain_corruption(reference[key], before[1]) is not None
            # No other reference key could have become this key.
            others = [k for k in original_key_counts if k != key]
            assert not any(explain_corruption(other, key) for other in others)
            if strict:
                # The only other column, price, must confirm the record: not
                # empty, unchanged, and held by this record alone in both files.
                price = before[2]
                assert price.strip() != ""
                assert price == reference_price[key]
                assert original_price_counts[price] == 1 and edited_price_counts[price] == 1
    # The inputs were not modified in memory either.
    assert original.rows == original_rows and edited.rows == edited_rows


@pytest.mark.parametrize("strict", [True, False], ids=["default-strict", "trust-key"])
@given(st.one_of(st.tuples(tables, tables), related_tables()))
@settings(max_examples=300, deadline=None)
def test_repair_is_idempotent(strict, pair) -> None:
    original_rows, edited_rows = pair
    original = make_table(HEADER, original_rows)

    def repair(rows: list[list[str]]) -> list[list[str]]:
        analysis = analyze(original, make_table(HEADER, rows), ("id",), ("code",), strict=strict)
        return repaired_rows(analysis)

    once = repair(edited_rows)
    assert repair(once) == once


@given(st.lists(st.text(alphabet="0123456789", min_size=1, max_size=20), max_size=8), st.randoms())
@settings(max_examples=200, deadline=None)
def test_simulated_spreadsheet_damage_is_fully_repaired_with_a_good_key(codes_list, random) -> None:
    original_rows = [[f"K{i}", code, "1.00"] for i, code in enumerate(codes_list)]
    edited_rows = [[key, spreadsheet(code), "9.99"] for key, code, _ in original_rows]
    random.shuffle(edited_rows)
    analysis = analyze(
        make_table(HEADER, original_rows), make_table(HEADER, edited_rows), ("id",), ("code",)
    )
    assert analysis.review_count == 0
    restored = {row[0]: row[1] for row in repaired_rows(analysis)}
    assert restored == {row[0]: row[1] for row in original_rows}
    assert all(row[2] == "9.99" for row in repaired_rows(analysis))


@given(st.lists(st.text(alphabet="0123456789", min_size=1, max_size=20), max_size=8), st.randoms())
@settings(max_examples=200, deadline=None)
def test_without_a_usable_key_nothing_is_repaired(codes_list, random) -> None:
    """The damaged identifier used as its own key: there must be no repairs at all."""
    original_rows = [[code, "x"] for code in codes_list]
    edited_rows = [[spreadsheet(code), "x"] for code in codes_list]
    random.shuffle(edited_rows)
    analysis = analyze(
        make_table(["code", "other"], original_rows),
        make_table(["code", "other"], edited_rows),
        ("code",),
        ("other",),
    )
    assert repaired_rows(analysis) == edited_rows


@given(st.text(max_size=30), st.text(max_size=30))
@settings(max_examples=500, deadline=None)
def test_rules_never_crash_on_arbitrary_text(original: str, observed: str) -> None:
    explain_corruption(original, observed)
    looks_damaged(observed)
    assert explain_corruption(original, original) is None


@given(st.lists(codes, max_size=12), codes)
@settings(max_examples=300, deadline=None)
def test_corruption_index_matches_plain_search(originals, observed) -> None:
    expected = {o for o in originals if o != observed and explain_corruption(o, observed)}
    assert set(CorruptionIndex(originals).sources_of(observed, limit=1000)) == expected


def _float_forms(number: int) -> list[str]:
    value = float(number)
    return [repr(value), f"{value:.15g}", f"{value:.14E}", f"{value:.5E}", str(int(value))]


@given(
    st.lists(st.integers(min_value=10**14, max_value=10**20), min_size=1, max_size=8).flatmap(
        lambda numbers: st.tuples(
            st.just([str(n) for n in numbers]),
            st.sampled_from([form for n in numbers for form in _float_forms(n)]),
        )
    )
)
@settings(max_examples=500, deadline=None)
def test_corruption_index_is_complete_for_long_numbers(data) -> None:
    """Long IDs as floats: the index must find every source the rules accept."""
    originals, observed = data
    expected = {o for o in originals if o != observed and explain_corruption(o, observed)}
    assert set(CorruptionIndex(originals).sources_of(observed, limit=None)) == expected


@given(
    st.integers(min_value=10**15, max_value=10**19),
    st.lists(st.integers(min_value=-30, max_value=30), min_size=1, max_size=8),
    st.sampled_from([0, 1, 2, 3, 4]),
)
@settings(max_examples=500, deadline=None)
def test_corruption_index_is_complete_for_neighbouring_numbers(base, offsets, form) -> None:
    originals = [str(base + offset) for offset in offsets]
    observed = _float_forms(base)[form]
    expected = {o for o in originals if o != observed and explain_corruption(o, observed)}
    assert set(CorruptionIndex(originals).sources_of(observed, limit=None)) == expected


csv_text = st.text(
    alphabet=st.characters(blacklist_characters="\x00", blacklist_categories=("Cs",)), max_size=12
)


@given(
    st.integers(min_value=1, max_value=4).flatmap(
        lambda width: st.tuples(
            st.lists(csv_text, min_size=width, max_size=width, unique=True),
            st.lists(st.lists(csv_text, min_size=width, max_size=width), max_size=6),
        )
    ),
    st.sampled_from(["\n", "\r\n"]),
    st.sampled_from([",", ";", "\t"]),
    st.booleans(),
)
@settings(max_examples=300, deadline=None)
def test_written_files_read_back_identically(tmp_path_factory, data, newline, delimiter, bom) -> None:
    header, rows = data
    path: Path = tmp_path_factory.mktemp("roundtrip") / "file.csv"
    style = make_table(header, rows)
    style = type(style)(
        path=path, header=header, rows=rows, line_numbers=[], has_bom=bom,
        newline=newline, delimiter=delimiter,
    )
    write_csv(path, header, rows, style)
    try:
        table = read_csv(path, delimiter)
    except CsvSafeRestoreError as error:
        # The strict reader may refuse odd-but-writable files (for example a
        # file whose only content is an empty header). Refusing is safe;
        # reading back different values would not be.
        assert "empty" in str(error) or "only one column" in str(error)
        return
    assert (table.header, table.rows) == (header, rows)
    assert (table.has_bom, table.newline) == (bom, newline) or not rows
