# Contributing

Thank you for helping. This tool changes people's data, so the bar for changes
is "provably safe", not "probably helpful".

## Set up

```console
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Python 3.11 or newer. The package itself has no runtime dependencies; please
keep it that way. Tests use pytest and Hypothesis.

## The rules that must not be broken

1. Never write to an input file.
2. A restored value is copied from the reference record with the same trusted
   key. Never compute, pad, guess or "fix up" a value.
3. Corruption rules work forwards only: given the original, could a spreadsheet
   have produced the observed value? Never the other way round.
4. Check digits, similarity, and row position are not evidence for a repair.
5. Strict matching is the default and accepts only exact, unique, unchanged
   values in another column as confirmation. Do not loosen it with similarity
   or spreadsheet rules, and do not make `--trust-key` the default; tests in
   `tests/test_strict.py` guard this.
6. When in doubt, report and leave the value alone. A missed repair is an
   inconvenience; a false repair is a release blocker.

[docs/DESIGN.md](docs/DESIGN.md) explains the reasoning.

## Adding a corruption pattern

A new pattern needs all of the following:

- **Evidence.** A reproducible round trip in a real spreadsheet (name the
  program and version), or a link to the vendor's documentation. Say which in
  the pull request.
- **An exact rule** in `src/csv_safe_restore/patterns.py`. No fuzzy matching.
- **Positive tests** (the pattern is confirmed) **and negative tests** (near
  misses are rejected) in `tests/test_patterns.py`.
- **Index support**: if the pattern could apply to key columns,
  `CorruptionIndex` must find its sources. The property tests named
  `test_corruption_index_...` in `tests/test_properties.py` check this.

The key guard (`matching.collision_forms`) is the opposite: it may be as
generous as you like, because it can only cause a refusal. If you find two
different keys that a spreadsheet makes identical and the tool still trusts
them, add the pair to `tests/test_matching.py` and widen the guard.

## Before opening a pull request

```console
.venv/bin/pytest                               # all tests
.venv/bin/python benchmarks/compare_tools.py   # must report 0 false repairs (default and --trust-key)
```

The benchmark needs the optional comparison tools
(`pip install daff csv-diff datacompy uncorrupt pandas`); regenerating its data
with `benchmarks/make_dataset.py` also needs LibreOffice. The committed data is
enough to run the comparison.

## Style

- Type hints on all functions; plain, readable code over clever code.
- Error messages say what is wrong and what the user can do about it.
- User-visible behaviour (report text, JSON keys, exit codes) is an interface.
  Changing the JSON layout requires a new `schema_version`.

## Reporting a wrong repair

If the tool ever restores a value it should not have, please open an issue
with the smallest pair of files that shows it. This is treated as the highest
priority kind of bug.
