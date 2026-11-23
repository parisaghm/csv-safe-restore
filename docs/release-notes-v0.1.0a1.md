**First public alpha.** This release is for testing and feedback. It is not
ready for unattended production use: keep your original files, run `check`
first, and read the report before you use a repaired file.

## What it does

When a CSV file is opened in Excel or LibreOffice and saved again, values that
look like numbers or dates are silently rewritten:

| Original | After a spreadsheet | What happened |
| --- | --- | --- |
| `00123` | `123` | leading zeros removed |
| `5901234123457` | `5.90123E+12` | scientific notation |
| `1234567890123456` | `1234567890123450` | only 15 digits kept |
| `MAR1` | `1-Mar` | read as a date |

`csv-safe-restore` compares the damaged file with a clean copy of the same
data (an export, a backup, the file before it was shared) and puts the original
identifiers back. **It never guesses.** A value is restored only when all of
these are true:

1. the row is matched to exactly one record of the clean file by a unique,
   undamaged **key** column (for example an order number);
2. **another column** of the row confirms that it is the same record;
3. the change is **exactly what a spreadsheet does** to that value.

Everything else is left unchanged and listed in a report, with the reason, so
a person can decide. Other edits in the file (new prices, fixed descriptions,
new rows) are kept. Your input files are never modified.

## Install

You need Python 3.11 or newer. The alpha is not on PyPI.

```console
# With Git installed:
python -m pip install "git+https://github.com/parisaghm/csv-safe-restore@v0.1.0a1"

# Without Git: the wheel file attached to this release:
python -m pip install "https://github.com/parisaghm/csv-safe-restore/releases/download/v0.1.0a1/csv_safe_restore-0.1.0a1-py3-none-any.whl"
```

On Windows, use `py -m pip` if `python` is not found. To keep the tool apart
from your other Python packages, install it in a virtual environment or with
[pipx](https://pipx.pypa.io/). Check that it works with
`csv-safe-restore --version`.

## Quick start

```console
# 1. Look only. Nothing is written.
csv-safe-restore check original.csv edited.csv --key order_id

# 2. When the report looks right, write a repaired copy.
csv-safe-restore repair original.csv edited.csv --key order_id --output repaired.csv
```

`original.csv` is the clean file, `edited.csv` the damaged one, and `order_id`
the column that names each record. The
[README](https://github.com/parisaghm/csv-safe-restore#readme) has a full
example, all options, and how to review the result.

The default is the cautious mode described above. The `--trust-key` option
lets the key alone decide and repairs more, but it restores **wrong** values if
record IDs were renumbered or reused. Use it only if you are sure the key
column was never changed.

## Known limitations

These are the most important ones. The full list is in the README under
[Safety limitations](https://github.com/parisaghm/csv-safe-restore#safety-limitations).

- **An intentional change that looks exactly like spreadsheet damage is
  restored.** If someone deliberately changed `00123` to `123`, the tool cannot
  tell. Every restored value is listed in the report so you can check.
- **Renumbered records can fool it** if the key and the confirming column were
  moved to another record together.
- **It often refuses.** Files with only a key and identifier columns get no
  repairs in the default mode, rows whose distinctive columns were edited
  (for example a renamed customer) are not repaired, and new rows can only be
  flagged, never repaired.
- **The clean file must really be clean.** Values are copied from it exactly.
- **UTF-8 files only**, and both files must use the same delimiter.
- **Tested:** the automated tests pass on Linux, Windows and macOS with
  Python 3.11–3.14. **Not tested:** a real Excel. Excel's behaviour was built
  from Microsoft's documentation and a simulation.

## Feedback

Please [open an issue](https://github.com/parisaghm/csv-safe-restore/issues)
with anything that goes wrong. If the tool ever restores a value it should not
have, that is the most serious kind of bug: please include the smallest pair of
files that shows it (with made-up data, never real personal data).

Full list of changes: [CHANGELOG.md](https://github.com/parisaghm/csv-safe-restore/blob/main/CHANGELOG.md).
