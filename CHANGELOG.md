# Changelog

All notable changes to this project are listed here. Versions follow
[PEP 440](https://peps.python.org/pep-0440/): `a` marks an alpha release.

## Unreleased

Reports explain more; what is restored has not changed. Exit codes and the
JSON format are unchanged (new information is added to `warnings`).

- *NOTES* says how many restorations put back a change a person could also
  have made on purpose (leading zeros, plus sign or decimal zeros removed,
  `TRUE` for `true`). If matched values in the same column kept their leading
  zeros, the column is named as the place to check first.
- *NOTES* names columns that were not checked but changed exactly like
  spreadsheet damage (`00501` → `501`, `+44…` → `44…`). Formatting-only
  changes (`4.50` → `4.5`, `true` → `TRUE`) are summed up in one shorter note.
  These columns are never repaired. This replaces the older note about
  skipped columns when both would name the same column.
- The "reference file itself looks damaged" warning no longer fires for
  genuine long numbers that end in `0` by chance. Cut-off numbers are
  reported only when a column has more of them than chance explains. Values
  in scientific notation, error values and spreadsheet dates are reported as
  before.
- Real-world validation with real LibreOffice round trips of a realistic
  product file and order-line file (`validation/`,
  [docs/REAL_WORLD_VALIDATION.md](docs/REAL_WORLD_VALIDATION.md)): 0 incorrect
  restorations; 15 deliberate leading-zero removals were undone (the known
  limitation, counted separately). New documented limitation: order-line
  files are mostly left for review in the default mode. Manual steps for a
  real Excel test. The scorer also judges every warning against the known
  truth.

## 0.1.0a1 — first public alpha (2026-11-23)

First public release, for testing and feedback. **Not ready for unattended
production use.** Keep your original files and read the report before you use
a repaired file.

### What it does

- `check` compares a clean reference CSV with an edited copy and reports
  identifier values (SKUs, barcodes, postal codes, long IDs) that a
  spreadsheet damaged. It writes nothing.
- `repair` writes a new file in which only confirmed damage is restored, copied
  exactly from the reference file. Input files are never modified.
- Recognised damage: leading zeros removed, 15-digit precision loss,
  scientific notation, values read as dates, `TRUE`/`FALSE` uppercasing, and
  formula errors.
- Text and JSON reports; exit codes 0 (nothing to do), 1 (findings), 2 (error),
  130 (interrupted).

### Safety model

- A value is restored only when the row is matched to one reference record by
  a unique, undamaged key, **another column confirms that it is the same
  record**, and the change is exactly what a spreadsheet does.
- This strict matching is the default. `--trust-key` lets the key alone decide
  and is less conservative: it can restore another record's value if record
  IDs were renumbered or reused.
- Everything else is reported for review and left unchanged.

### Known limitations

See [README.md](README.md#safety-limitations). In short: intentional edits that
look exactly like damage are restored; a key and a confirming column that were
moved together cannot be detected; real Excel has not been tested yet.

### Tested on

- Linux, Windows and macOS with Python 3.11, 3.12, 3.13 and 3.14, by the
  automated tests in GitHub Actions.
