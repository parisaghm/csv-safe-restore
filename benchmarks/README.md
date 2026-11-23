# Benchmark

A validation dataset with ground truth, and a script that scores
csv-safe-restore and four other tools on it.

```console
pip install daff csv-diff datacompy uncorrupt pandas   # comparison tools
python benchmarks/make_dataset.py      # optional: rebuild data (needs LibreOffice)
python benchmarks/compare_tools.py --markdown benchmarks/results.md
```

`compare_tools.py` exits with code 1 if csv-safe-restore makes even one false
repair, in the default (strict) mode or with `--trust-key`.

## The data

`make_dataset.py` does not import csv-safe-restore. It builds a fictional
catalogue of 2,000 products (zero-padded SKUs, codes such as `12E3`, `MAR1`
and `1-3`, UPC/EAN/GTIN barcodes, postal codes, 16–20 digit IDs) and damages
it in two independent ways:

| Scenario | How the damage is made |
| --- | --- |
| `libreoffice` | The file is opened and saved again by a real, headless LibreOffice (26.2 for the committed data). |
| `excel_model` | A small model of Excel's documented General-format rules. **Excel was not run; this is a simulation.** |

Then scripted "human" changes are applied to the damaged file: about 10 % of
prices, 5 % of descriptions and 3 % of quantities are edited; 2 % of rows get
an intentional identifier change; 3 % of rows are deleted; 60 new rows are
added (some typed with already-damaged identifiers); 1 % of rows are
duplicated with the same key; 0.5 % of keys are blanked; all rows are
shuffled. `truth.csv` records, for every identifier cell, what it truly is.

## Results

Measured on 2026-11-18 on one Linux laptop with Python 3.14, daff 1.4.2,
csv-diff 1.2, datacompy 1.2.0 and UnCorrupt 1.0.2. Times are the median of
three runs including process start-up; they are small-file timings on one
machine, not a performance claim. The tables are in
[results.md](results.md).

Summary for csv-safe-restore:

| Scenario | Mode | Truly damaged cells | Restored correctly | False repairs | Left for review |
| --- | --- | --- | --- | --- | --- |
| `libreoffice` | default (strict) | 3,286 | 3,120 | 0 | 166 |
| `libreoffice` | `--trust-key` | 3,286 | 3,199 | 0 | 87 |
| `excel_model` | default (strict) | 5,019 | 4,699 | 0 | 320 |
| `excel_model` | `--trust-key` | 5,019 | 4,879 | 0 | 140 |

With `--trust-key`, every cell left for review is in a row whose key was
duplicated or blanked, where a safe match is impossible. The default mode also
leaves cells whose row has no unique, unchanged value in another column, or
whose row has an unexplained change in another identifier column. None of the
65 intentional identifier edits was reverted in either mode.

The dataset never renumbers keys, so it does not show the case the default
mode exists for. That case is covered by `tests/test_strict.py`,
`tests/test_preservation.py` and the property tests.

## How to read the comparison fairly

- **daff, csv-diff and datacompy are diff tools.** They show what changed and
  do that well: between them they list essentially every damaged cell. They
  are not designed to decide whether a change is damage or an intentional
  edit, and they do not repair. "n/a" in the repairs column is not a failure.
  The useful finding is narrower: a diff lists the 65 intentional identifier
  edits and the 1,387 legitimate edits in other columns in the same way as the
  damage, so the judgement is left to the reader.
- **csv-diff** keeps one row per key, so some cells in rows with duplicated
  keys are not listed. Duplicate keys are outside what it promises to handle.
- **datacompy** is a pandas/Polars comparison library. It was given text
  columns (`dtype=str`); with pandas' default type inference the leading zeros
  would be lost on reading, in both files alike.
- **UnCorrupt** scans one file for Excel damage to gene symbols and similar
  scientific identifiers, with no reference file. It raised no suspicion on
  this product data. That is a different job from the one measured here, not
  a defect.
- **naive revert** is not a tool. It is the obvious script ("copy every
  differing identifier back from the reference by key"). It repairs more cells
  than csv-safe-restore, because it also writes into rows with duplicated
  keys, and it reverts all 65 intentional edits. It shows why a diff plus a
  blind copy is not a safe repair.

## Limits of this benchmark

- The data is synthetic. Real files have messier edits.
- The Excel scenario is a model, not Excel.
- One dataset size (2,000 rows). No large-file or memory measurements.
- The human edits never imitate spreadsheet damage. An intentional
  `00123` → `123` would be restored by csv-safe-restore (a documented
  limitation) and is not exercised here.
