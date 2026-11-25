# csv-safe-restore

Restore identifiers that a spreadsheet damaged in a CSV file, using a clean
reference copy of the same data — and never guess.

> **Status: alpha (0.1.0a1).** The tool has been tested carefully, but it is
> new, its automated tests run on Linux, Windows and macOS, and it has not
> yet been tested against a real Excel. Always keep your original files and read the report before you use a
> repaired file. Passing tests do not prove that it is safe for your data.
> Please read [Safety limitations](#safety-limitations) before you rely on it.

## What the tool does

When someone opens a CSV file in Excel or LibreOffice and saves it again, values
that look like numbers or dates are silently rewritten:

| In the original file | After a spreadsheet round trip | What happened |
| --- | --- | --- |
| `00123` | `123` | leading zeros removed |
| `0036000291452` | `36000291452` | leading zeros removed |
| `5901234123457` | `5.90123E+12` | scientific notation |
| `1234567890123456` | `1234567890123450` | only 15 digits are kept |
| `1E5` | `1.00E+05` or `100000` | read as a number |
| `MAR1` | `1-Mar` | read as a date |

The damaged file usually also contains real work: new prices, fixed
descriptions, new rows. You cannot simply throw it away, and you cannot simply
copy the old identifiers back, because some identifiers were changed on purpose.

`csv-safe-restore` compares the edited file with a clean reference file and

- **restores** an identifier only when three things are all true: the row is
  matched to one reference record by a unique, undamaged **key**; **another
  column** of the row confirms that it is the same record; and the change is
  **exactly what a spreadsheet does** to that value;
- **keeps** every edit in other columns, every new row and the row order;
- **reports** everything it will not touch, with the reason, so a person can
  decide.

It has no runtime dependencies and needs Python 3.11 or newer.

## Who should use it

It is for you if:

- you have a **clean copy** of a CSV file (an export from a database, a backup,
  the file before you sent it to a colleague), **and**
- you have a **newer copy** of the same file that went through a spreadsheet
  and has lost leading zeros or has numbers in scientific notation, **and**
- both files have a **key column** that names each record (for example an
  order number like `ORD-1001` or a database ID).

It is **not** for you if:

- you only have the damaged file (see
  [why recovery needs evidence](#why-recovery-is-impossible-without-evidence));
- you want a general "diff" of two CSV files. Tools such as daff, csv-diff or
  datacompy do that well. This tool only deals with identifier damage.

## Install

The alpha is not on PyPI. Install it from GitHub:

```console
# The released alpha (recommended for testing):
python -m pip install "git+https://github.com/parisaghm/csv-safe-restore@v0.1.0a1"

# Without Git installed: the wheel file attached to the GitHub release:
python -m pip install "https://github.com/parisaghm/csv-safe-restore/releases/download/v0.1.0a1/csv_safe_restore-0.1.0a1-py3-none-any.whl"

# Or, from a downloaded copy of the source:
cd csv-safe-restore
python -m pip install .
```

The first command needs [Git](https://git-scm.com/) on your computer; the
others do not.

On Windows, use `py -m pip` if `python` is not on your PATH. To keep the tool
separate from your other Python packages, install it in a virtual environment
or with [pipx](https://pipx.pypa.io/) (`pipx install "git+https://..."`).

Check that it works:

```console
csv-safe-restore --version
csv-safe-restore --help
```

## Start with `check`

There are two commands. **Always run `check` first.** It writes nothing and
shows exactly what `repair` would do.

```console
# 1. Look only. Nothing is written.
csv-safe-restore check original.csv edited.csv --key order_id

# 2. When the report looks right, write a repaired copy.
#    The input files are never modified.
csv-safe-restore repair original.csv edited.csv --key order_id --output repaired.csv
```

| | `check` | `repair` |
| --- | --- | --- |
| Reads both files | yes | yes |
| Writes a file | no | only the new file given with `--output` |
| Changes the input files | never | never |
| Report | what *would* be restored, and what needs review | what *was* restored, and what still needs review |

Both commands run exactly the same analysis, so the `check` report is a
reliable preview of what `repair` will do.

`--key` names the column (or columns, comma-separated) that identifies a
record. It must be unique and non-empty, and it must not be one of the columns
you want repaired.

## A small example

A fictional bookshop keeps its orders in `orders.csv`:

```csv
order_id,customer,postcode,isbn,price
ORD-1001,Mira Lind,00150,9780306406157,12.00
ORD-1002,Tom Berg,02100,0306406152,8.50
ORD-1003,Sara Kivi,0150,0012345678905,22.00
```

A colleague opened it in a spreadsheet, fixed a price and saved it as
`orders-edited.csv`:

```csv
order_id,customer,postcode,isbn,price
ORD-1001,Mira Lind,150,9.78031E+12,12.00
ORD-1002,Tom Berg,2100,306406152,9.00
ORD-1003,Sara Kivi,150,12345678905,22.00
```

First, check:

```console
csv-safe-restore check orders.csv orders-edited.csv --key order_id \
    --identifier-columns postcode,isbn
```

The report lists six values under *CONFIRMED DAMAGE*, for example:

```text
  line 2  order_id='ORD-1001'  postcode: '150' -> '00150'  [leading zeros removed
  (value was read as a number); record confirmed by column 'customer']
```

(Lines are wrapped here for reading.) Then repair:

```console
csv-safe-restore repair orders.csv orders-edited.csv --key order_id \
    --identifier-columns postcode,isbn --output orders-repaired.csv
```

`orders-repaired.csv`:

```csv
order_id,customer,postcode,isbn,price
ORD-1001,Mira Lind,00150,9780306406157,12.00
ORD-1002,Tom Berg,02100,0306406152,9.00
ORD-1003,Sara Kivi,0150,0012345678905,22.00
```

The postcodes and ISBNs are back, and the new price `9.00` is kept. Two
different postcodes, `00150` and `0150`, both became `150`. Each row got its
own original back because the row was matched by `order_id` **and** confirmed
by the customer name, which is unchanged and belongs to that order alone.

A longer example is in [examples/](examples/): the input files
[original.csv](examples/original.csv) and [edited.csv](examples/edited.csv),
the [check report](examples/check_report.txt), and the
[repaired file](examples/repaired.csv) with its
[repair report](examples/repair_report.txt). It restores 16 values and leaves
4 findings for review. For comparison,
[check_report_trust_key.txt](examples/check_report_trust_key.txt) shows the
less conservative `--trust-key` mode, which restores one value more.

## How a row is matched to its record

The damaged value alone cannot tell you where it came from: in the example,
Mira's `00150` and Sara's `0150` both became `150`. The tool has to know
*which record* a row is, to know which original to copy back. It uses two
independent pieces of evidence.

**1. The key.** A key is trusted only if it is non-empty, unique in both
files, and could not be confused with another key after a spreadsheet round
trip (`007` and `7`, or `"1,000"` and `1000`). If the rest of a row fits a
different reference record better than the one with its key, the row is
reported as a possible key reassignment. In all these cases nothing in the
row is restored.

**2. A confirming value (strict matching, the default).** The key is only
right if it means the same record in both files. If IDs were renumbered,
reused after a delete, or rows shifted against the ID column, `ORD-1001`
could be Mira's order in one file and Tom's in the other. So by default a
damaged value is restored only if the row also has a **confirming value**:
a value in another column that is

1. **not a key column** and not the column being repaired,
2. **not empty**,
3. **exactly the same** as in the matched reference record, character for
   character (no "looks similar", no spreadsheet rules, no case folding),
4. **unique**: no other record in the reference file and no other row in the
   edited file has that value in that column, and
5. **not a possible look-alike**: no other reference value in that column
   could have been turned into it by a spreadsheet (another record's `1.50`
   shown as `1.5`, or `0777` shown as `777`).

Such a value names the same record independently of the key. Every repair in
the report says which column confirmed it.

A repair is also refused when another identifier column of the same row was
changed in a way that is not spreadsheet damage. That change may be an edit,
or a sign that the row is a different record.

When there is no confirming value, the damaged value is **left unchanged**
and listed under *NEEDS REVIEW* with the reason. If the bookshop file had only
the `order_id` and `postcode` columns, the report would say:

```text
  line 2  order_id='ORD-1001'  postcode: reference '00150', now '150'
      spreadsheet damage, but not restored because the record is not confirmed:
      leading zeros removed (value was read as a number); no other column of
      this row has a non-empty value that is unchanged and belongs to this
      record only, so the key is the only evidence that the two rows are the
      same record
```

## The `--trust-key` option

`--trust-key` drops the confirming-value requirement and lets the key alone
decide which record a row is. Everything else stays the same: the key must
still be unique and unambiguous, and the change must still fit a spreadsheet
rule exactly.

Use it **only** when you are certain that the key column was never changed
between the two files: no renumbering, no reused IDs, no re-sorting against
the ID column. It restores more values, for example in files that have only a
key and identifier columns.

**The risk:** if keys were renumbered and nothing else in the row shows it,
`--trust-key` restores **another record's value**, and the report cannot tell
you. When that may have happened, do not use `--trust-key`.

On the project's benchmark (one synthetic dataset; your data will differ):

| Scenario | Damaged cells | Default (strict) restored | `--trust-key` restored | Wrong repairs |
| --- | --- | --- | --- | --- |
| Real LibreOffice round trip | 3,286 | 3,120 | 3,199 | 0 in both modes |
| Model of Excel's documented rules | 5,019 | 4,699 | 4,879 | 0 in both modes |

The benchmark never renumbers keys. In separate random tests where it did,
`--trust-key` restored wrong values and the default mode did not. See
[benchmarks/](benchmarks/README.md) and
[PRE_RELEASE_REVIEW.md](PRE_RELEASE_REVIEW.md).

## Reviewing the result

1. **Run `check`.** It writes nothing and lists, under *CONFIRMED DAMAGE*,
   every value that `repair` would restore: the row's line number, its key,
   the current value, the value that would be written, and the column that
   confirmed the record.
2. **Read *NEEDS REVIEW*.** These are left exactly as they are. Each line says
   why (no known damage pattern, duplicate key, record not confirmed, and so
   on).
3. **Read *COLLAPSED VALUES* and *NOTES*.** Collapsed values are different
   originals that now look the same; for those, the match decides which
   original is restored.
4. **Save the full report** with `--report report.txt`, or as JSON for a
   script: `--format json --report report.json`. The JSON lists every finding
   with no limit; the text report shows 50 lines per section unless you pass
   `--max-details 0`.
5. **Run `repair`** with `--output` set to a new file name. Compare a few
   restored rows with the report.
6. **Fix the review items by hand** in the repaired file, using the report as
   a checklist.

## Options

| Option | Meaning |
| --- | --- |
| `--key COLUMNS` | Required. Column(s) that identify a record, comma-separated. |
| `--identifier-columns sku,barcode` | Columns to check. Default: columns of the reference file in which more than half of the values (empty cells count as not fragile) have leading zeros, 12+ digits, or look like `1E5` or `MAR1`. Naming the columns yourself is safer. |
| `--trust-key` | Let the key alone decide which record a row is. Less conservative; see [The `--trust-key` option](#the---trust-key-option). |
| `--strict` | The default. Can be given explicitly, for example in scripts. Cannot be combined with `--trust-key`. |
| `--gtin-columns barcode` | Also verify UPC/EAN/GTIN check digits in these columns. Warnings only; a check digit is never a reason to repair. |
| `--delimiter ';'` or `--delimiter tab` | Field separator used by both files. |
| `--format json` | Machine-readable report (stable `schema_version`). |
| `--report report.txt` | Write the report to a file instead of the screen. |
| `--max-details 0` | Show every finding in the text report (default: 50 per section). |
| `--overwrite` | Allow replacing an existing output or report file. The replaced file's permissions are kept; if the path is a symbolic link, the link itself is replaced, not the file it points to. Input files can never be replaced. |

Exit codes, for scripts and CI:

| Code | Meaning |
| --- | --- |
| 0 | Nothing needs attention. |
| 1 | `check`: damage or review items were found. `repair`: the file was written, but some findings could not be fixed and need review. Also when not a single record could be matched by key, because then nothing was checked. |
| 2 | The tool could not run: bad options, unreadable or malformed input, or an output file that already exists. |
| 130 | Interrupted (Ctrl+C or `kill`). No partly written file is left behind. |

New and deleted records alone are reported but do not cause exit code 1.

## Safety rules

1. **Input files are never written to.** The output path must be given, must
   differ from both inputs, and an existing file is not replaced without
   `--overwrite`.
2. **A restored value is always copied exactly from the reference file**, from
   the matched record. The tool never computes, pads or invents a value.
3. **A value is restored only with three pieces of evidence:** a trusted key
   match, a confirming value in another column, and a change that fits a
   known spreadsheet corruption pattern exactly. `--trust-key` drops the
   second.
4. **Only identifier columns can change.** Other columns, new rows, row order
   and column order are written as they are in the edited file.
5. **Anything unclear is reported, not repaired.**
6. **Writing is atomic and verified.** The output is written to a temporary
   file, read back and compared cell by cell, then moved into place. If the
   run is interrupted, the temporary file is removed.

These rules are covered by the test suite, including property-based tests that
check them against randomly generated data with known ground truth, and by a
benchmark (see [benchmarks/](benchmarks/README.md)). Tests can only show that
no counterexample was found; they do not prove the rules hold for every file.

## Safety limitations

These are known, documented ways the tool can still go wrong or fall short.
Each one was found by testing or review; none of them is hidden.

### It can restore a value that is wrong for your purpose

- **An intentional edit that looks exactly like damage is restored, in both
  modes.** If someone deliberately changed `00123` to `123`, or edited only
  the digits after the 15th of a long number, the change cannot be told apart
  from spreadsheet damage. Every restored value is listed in the report so you
  can check.
- **The default mode cannot detect a key and a confirming column that moved
  together.** If renumbering moved both the ID and, say, the customer name to
  another record in the same way, the two columns agree with each other.
- **A coincidence can confirm the wrong record.** If a re-keyed row happens to
  carry the unique, unchanged value of the record its key now points to, the
  default mode trusts it.
- **`--trust-key` follows renumbered or reused keys** when nothing else in the
  row contradicts them, and restores another record's value. That is why it
  is not the default.
- **The reference file must be clean.** Values are copied exactly from it.
  Reference values with an obvious damage shape (`5.90123E+12`, `#NAME?`,
  `1-Mar`) are never copied back, but a reference value that already lost its
  leading zeros looks like any other number.

### It can refuse more than you expect

- **Files with only a key and identifier columns get no repairs** in the
  default mode: there is nothing to confirm the record. Use `--trust-key` only
  if you are certain the keys are stable.
- **Rows whose distinctive columns were edited** (for example a renamed
  customer) are not repaired, even if the key is right.
- **Transaction files are often left for review.** In order lines or invoices,
  customers and products repeat, so few columns are unique to one row. In a
  real LibreOffice test only about a quarter of the damaged order-line values
  were restored in the default mode; the rest were reported for review. See
  [docs/REAL_WORLD_VALIDATION.md](docs/REAL_WORLD_VALIDATION.md).
- **New records cannot be repaired**, only flagged when a value has an obvious
  damage shape (scientific notation, error values, `1-Mar`). There is no
  original to copy.
- **A damaged key column** makes those rows unmatched. Use a key that
  spreadsheets do not alter (`ORD-1001`, UUIDs, or numbers without leading
  zeros and with fewer than 12 digits).
- **Numeric keys longer than 400 characters** are never matched, because they
  are too long to check for spreadsheet damage.
- **Automatic column detection may miss columns.** A column is picked only when
  more than half of its values have a fragile shape. Columns with fewer, or
  with values fragile in other ways (`1.10`, `+44123`, `true`), are checked
  only if you name them with `--identifier-columns`. The report names some
  skipped columns (for example postal codes with leading zeros), but not
  all: in a real LibreOffice test it said nothing about customs codes
  (`8471.30` → `8471.3`), `true`/`false` flags or `+44…` phone numbers, and
  their damage stayed in the repaired file.

### What has and has not been tested

- **Tested:** the full test suite and the command-line examples pass on
  Linux, Windows and macOS with Python 3.11, 3.12, 3.13 and 3.14 (GitHub
  Actions); the built package installs and runs on all three. Real
  LibreOffice 26.2 round trips on Linux, including a realistic product file and
  order-line file with renumbered, swapped and duplicated records: 0 incorrect
  restorations. 15 leading zeros that a person had removed on purpose were put
  back, as described under *Safety limitations* (see
  [docs/REAL_WORLD_VALIDATION.md](docs/REAL_WORLD_VALIDATION.md)).
- **Not tested yet:** real Excel. Excel behaviour was implemented from
  Microsoft's documented rules and a simulation. Windows and macOS were tested
  only by the automated tests, not by people using real files.
- **Review:** the code went through three independent adversarial reviews and
  a re-check. Each round found new wrong-repair cases, all fixed and covered by
  tests. More may exist; please [report them](CONTRIBUTING.md#reporting-a-wrong-repair).

### Format and size

- **UTF-8 only** (with or without BOM). Both files must use the same delimiter.
- **Strict input.** Duplicate column names, rows with the wrong number of
  fields and broken quoting are errors; the tool stops instead of guessing.
- **The output is re-serialized.** Cell values are identical to the edited
  file except for restored values, and the edited file's delimiter, line
  ending and BOM are kept, but quoting may differ (quotes are written only
  where needed) and the file always ends with a line ending.
- **Column names containing a comma** cannot be passed on the command line,
  and spaces around names in `--key a, b` are removed.
- **Date patterns cover English month names and common numeric layouts.**
  Other locales are reported for review instead of being repaired.
- **Files are loaded into memory.** It is not a streaming tool. A file of
  200,000 rows took 17–23 seconds and about 400 MB of memory on one Linux
  laptop (a single measurement, not a performance promise).

More detail on the rules: [docs/DESIGN.md](docs/DESIGN.md).

## Why recovery is impossible without evidence

`123` could have been `123`, `0123`, `00123` or `000123`. `1.23457E+11` could
have been any of about a million different 12-digit numbers. The information
is gone from the damaged file, so nobody and no tool can get it back from that
file alone.

The original can only come from somewhere else: here, the clean reference
file. And the reference file only helps if we know *which* reference record a
row belongs to. That is why the tool needs a trusted match and a change that
fits a known spreadsheet rule, and why it refuses when either is missing
instead of guessing. Similar-looking values, check digits and row order are
never used to fill in a value.

## Use from Python

```python
from pathlib import Path
from csv_safe_restore.reader import read_csv
from csv_safe_restore.detection import analyze

analysis = analyze(
    read_csv(Path("original.csv")),
    read_csv(Path("edited.csv")),
    key_columns=("order_id",),
)  # strict by default; strict=False is the same as --trust-key
for finding in analysis.repairs:
    print(finding.line, finding.column, finding.current, "->", finding.original)
```

The command line is the supported interface for 0.x; the Python API may change.

## Development

```console
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

On Windows, use `.venv\Scripts\pip` and `.venv\Scripts\pytest`. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
