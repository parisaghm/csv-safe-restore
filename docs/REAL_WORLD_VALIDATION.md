# Real-world validation (Phase 2)

Date: 2026-11-24. Version tested: 0.1.0a1 (application code unchanged).

This page answers one question: **what happens when realistic business files
go through a real spreadsheet program, and the tool is then asked to repair
them?** It separates three kinds of evidence:

| Evidence | Source | Status |
| --- | --- | --- |
| **Real** | LibreOffice 26.2.6.3 on Linux, run headless | done, results below |
| **Simulated** | a model of Excel's documented rules (`benchmarks/`, `excel_model`) | done earlier; *not* evidence about real Excel |
| **Real Excel** | Microsoft Excel on Windows or macOS | **not done**: Excel was not available. Steps: [validation/EXCEL_MANUAL_TEST.md](../validation/EXCEL_MANUAL_TEST.md) |

Everything needed to repeat the test is in [`validation/`](../validation/).

## Summary in plain words

- **No incorrect restorations.** In 12 real LibreOffice runs (2 files × 3 ways
  of opening × 2 modes), the tool never wrote a wrong value: 0 incorrect cells,
  0 changed cells outside the identifier columns, and the input files were
  never modified. This includes renumbered records, swapped keys, duplicated
  keys, blank keys and new rows.
- **But 15 intentional changes were wrongly undone, in every run.** A person
  removed the leading zeros of 15 product codes *on purpose*
  (`00092045` → `92045`). The tool put the zeros back, because that change is
  identical to spreadsheet damage. This is the known, documented limitation.
  These 15 cells are **not** in the "incorrect" count, because the tool wrote
  the record's own earlier value and not a wrong one, but the user's change was
  still lost. They are counted on their own as "intentional change undone",
  and they are errors from the user's point of view.
- **Product master files are repaired well.** In ordinary product rows every
  damaged cell was restored, in every run.
- **Order-line files are mostly left for review in the default mode.** Only
  25% of damaged cells were restored (62% with German settings). The default
  mode needs another column that is unique to the record and unchanged; in
  order lines, customers and products repeat, so usually only the unit price
  happened to qualify. `--trust-key` restored 95%, still with 0 incorrect
  values, but this data has no renumbered order lines, which is exactly the
  case `--trust-key` cannot catch.
- **Automatic column detection misses damaged columns.** Customs codes
  (`8471.30` → `8471.3`), `true/false` flags and phone numbers (`+44…` →
  `44…`) were not checked unless named with `--identifier-columns`, and the
  report gave no hint for them.

## What was tested

Two fictional tables, made by
[`validation/make_dataset.py`](../validation/make_dataset.py) (fixed seed,
byte-identical on every run):

- **products** (1,500 rows): item ID (key), SKU with leading zeros, EAN-13 and
  UPC-A barcodes, part numbers (`PN-4471-B`, `MAR1`, `4-12`, `12E3`, `3/8`,
  `0042`), customs (HS) codes, 16–18 digit supplier accounts, `true`/`false`,
  a unique product name, price, stock.
- **orders** (1,500 order lines): order line ID (key), date, 16–18 digit
  customer account, customer name, postal code (Finnish `00100` and US
  `02134` styles), phone (`+447700900835`; all numbers are from the UK range
  reserved for fiction, so none belongs to a real person), SKU, barcode,
  quantity, unit price.
  Customers and products repeat across lines.

A person's deliberate work was applied first. The result is
`intended.csv`, the "right answer" for every cell:

| Scenario | products | orders |
| --- | --- | --- |
| normal rows (with price, stock or quantity edits) | 1,279 | 1,429 |
| product renamed (the name no longer confirms the record) | 62 | — |
| an identifier changed on purpose | 30 | 15 (postal code) |
| leading zeros removed on purpose (looks exactly like damage) | 15 | — |
| two records swapped keys | 16 | 16 |
| records renumbered, key only | 24 | — |
| records renumbered, key **and** name moved together | 24 | — |
| a row pasted twice (duplicate key) | 20 | 20 |
| blank key | 10 | — |
| new rows | 30 | 30 |
| deleted rows | 30 | 30 |

Then `intended.csv` was opened and saved by a **real LibreOffice** in three
ways ([`validation/run_libreoffice.py`](../validation/run_libreoffice.py); the
exact command is stored in each `run.txt`):

- **lo-default**: default CSV import.
- **lo-special-numbers-off**: "detect special numbers" switched off.
- **lo-de-DE**: German number rules (decimal comma).

[`validation/score.py`](../validation/score.py) ran the tool on each result
and compared every identifier cell with `intended.csv`.

## What LibreOffice really did to the data

| Damage | lo-default | special numbers off | lo-de-DE |
| --- | --- | --- | --- |
| leading zeros removed (`00093984` → `93984`) | yes | yes | yes |
| long numbers in scientific notation (`7.20654874707309E+017`) | yes | yes | yes |
| `+` removed from phone numbers (`+447700900835` → `447700900835`) | yes | yes | yes |
| codes read as numbers (`86E1` → `8.60E+02`) | yes | yes | yes |
| decimal codes reformatted (`8471.30` → `8471.3`) | yes | yes | no |
| `true` → `TRUE` | yes | no | no |
| `7/2` → `07/02/26` (a date, with the current year) | yes | no | no |
| `MAR1`, `4-12` changed | no | no | no |

Two lessons:

- **In a command-line round trip, LibreOffice's default already turns `7/2`
  into a date.** Switching "detect special numbers" on made no difference;
  switching it off stopped the date and `TRUE` changes.
- **German settings damaged less:** values with a dot (`8471.30`, prices) and
  `true` stayed text. Leading zeros and long numbers were damaged the same way.

## Results

"Damaged" counts identifier cells the spreadsheet changed. "Incorrect" means
the tool wrote a value that is wrong. "Undone" is counted separately: a
deliberate change was put back to the record's earlier value (the documented
limitation; not a wrong value, but the person's change is lost). Full tables per
column and per scenario: [`validation/results.md`](../validation/results.md).

| Table / way of opening / mode | Damaged | Restored | Left for review | **Incorrect** | Undone | Other columns changed |
| --- | --- | --- | --- | --- | --- | --- |
| products / lo-default / default | 5,922 | 5,323 | 599 | **0** | 15 | 0 |
| products / lo-default / `--trust-key` | 5,922 | 5,464 | 458 | **0** | 15 | 0 |
| products / special numbers off / default | 4,311 | 3,874 | 437 | **0** | 15 | 0 |
| products / special numbers off / `--trust-key` | 4,311 | 3,956 | 355 | **0** | 15 | 0 |
| products / lo-de-DE / default | 3,040 | 2,740 | 300 | **0** | 15 | 0 |
| products / lo-de-DE / `--trust-key` | 3,040 | 2,787 | 253 | **0** | 15 | 0 |
| orders / lo-default / default | 5,064 | 1,267 | 3,797 | **0** | 0 | 0 |
| orders / lo-default / `--trust-key` | 5,064 | 4,826 | 238 | **0** | 0 | 0 |
| orders / special numbers off / default | 5,064 | 1,267 | 3,797 | **0** | 0 | 0 |
| orders / special numbers off / `--trust-key` | 5,064 | 4,826 | 238 | **0** | 0 | 0 |
| orders / lo-de-DE / default | 5,064 | 3,156 | 1,908 | **0** | 0 | 0 |
| orders / lo-de-DE / `--trust-key` | 5,064 | 4,826 | 238 | **0** | 0 | 0 |

### Where the cells left for review come from

- **Products, default mode:** all of them are in rows where refusing is the
  correct answer (renumbered and swapped keys, duplicate and blank keys, new
  rows, rows with a deliberate identifier change), plus a handful of renamed
  products. In ordinary rows nothing was left: 5,048 of 5,048 damaged cells
  were restored (lo-default).
- **Orders, default mode:** 3,509 cells in ordinary rows were refused because
  no other column was unique and unchanged. The confirming column, when one
  was found, was the unit price (365 rows) or the order date (16 rows). In
  lo-default many prices were rewritten too (`11.50` → `11.5`), so they could
  no longer confirm anything. With German settings prices stayed text, and the
  restoration rate rose from 25% to 62%.

### Dangerous scenarios

| Scenario | Default mode | `--trust-key` |
| --- | --- | --- |
| renumbered, key only | all refused | 0 incorrect |
| renumbered, key and name moved together | all refused | 0 incorrect |
| swapped keys | all refused | 0 incorrect |
| duplicate key | all refused | all refused |
| blank key | all refused | all refused |
| deliberate different identifier | left as the person wrote it | left as the person wrote it |
| deliberate `00123` → `123` | **put back (15 of 15)** | **put back (15 of 15)** |

The renumbered cases were caught for a simple reason: the moved records'
*other* identifiers did not match the record the new key pointed to, so the
change did not look like spreadsheet damage. If two renumbered records had
identical damaged values in every identifier column, `--trust-key` would
restore the wrong record's value (see the README's *Safety limitations*).
This test did not contain such a case.

## Other observations

- **No warning for some skipped columns.** Without `--identifier-columns`, the
  tool picked `sku, ean, part_number, supplier_account` and
  `customer_account, sku, ean`. For `postal_code` it said why it was skipped.
  For `hs_code`, `active` and `phone` it said nothing, although LibreOffice
  changed 1,271, 1,510 and 1,510 of their values in exactly the way
  spreadsheets do. The repair left that damage in place.
- **A noisy warning.** The report said 64 supplier accounts (products) and 24
  customer accounts (orders) in the *clean* file "look damaged" (for example
  `6725461496159140`). They are genuine; about one long number in ten ends in
  `0` by chance. Nothing was
  mis-restored because of it, but a user may lose trust in their reference.
- **A real Excel will damage more.** LibreOffice left `MAR1` and `4-12` alone.
  Excel is documented to turn them into dates. This is covered only by the
  simulation so far.
- **Different separators.** Excel in many European regions saves CSV files with
  semicolons. The tool needs both files to use the same separator, so such a
  file must be converted first. `validation/score.py --delimiter ";"` does
  this for the Excel test; the tool itself does not.

## Recommended next priorities

In order of importance:

1. **Run the real Excel test** ([steps](../validation/EXCEL_MANUAL_TEST.md))
   on Windows, and on macOS if possible. It is the largest gap between what the
   project claims and what has been measured. It needs a person with Excel.
2. **Make the default mode useful for transaction files** (order lines,
   invoices) without weakening it. One option to study: accept a
   *combination* of columns (for example customer + date + quantity) as
   confirmation when the combination is unique and unchanged. Any change must
   pass a new adversarial review, and the 0-incorrect results here must stay 0.
   Until then, the README should say plainly that the default mode often
   refuses on such files.
3. **Warn about skipped columns that look damaged.** When a column outside the
   identifier list has many changes of a known spreadsheet kind (`8471.30` →
   `8471.3`, `true` → `TRUE`, `+44…` → `44…`), name it in the report and
   suggest `--identifier-columns`. This only adds a message; it repairs
   nothing by itself.
4. **Reduce the false "reference looks damaged" warning** for long numbers
   that end in `0`, or word it as "may be damaged".
5. **Decide whether to support a different separator in the edited file**
   (semicolon from European Excel). This is a real-world need; it must keep
   every value byte-for-byte as it is.

Items 2–5 change behaviour or messages and should be discussed before any code
is written.

## How to repeat

```console
python validation/make_dataset.py        # same files every time
python validation/run_libreoffice.py     # needs LibreOffice; dates may show a different year
python validation/score.py --markdown validation/results.md
```

`tests/test_real_world.py` re-scores the committed LibreOffice files on every
test run and fails on any incorrect restoration.
