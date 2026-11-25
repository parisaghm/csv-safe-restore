# Manual test with real Microsoft Excel

**Status: not done yet.** Excel was not available when the Phase 2 validation
was run (Linux, no Microsoft Office). Every Excel number in this project so far
comes from a *simulation* of Excel's documented rules
(`benchmarks/make_dataset.py`, scenario `excel_model`). This page explains how
someone with Excel can produce real results. It takes about 15 minutes.

You do not need to understand the code. You only open two files in Excel, save
them, and run one command.

## What you need

- Microsoft Excel on Windows or macOS (any recent version; Microsoft 365 is
  ideal). Excel on the web is a separate case; see the end of this page.
- Python 3.11 or newer with csv-safe-restore installed, and a copy of this
  repository (the files below are in `validation/data/`).

## Step 1: write down your setup

Before you start, note:

1. **Excel version.** Windows: *File → Account → About Excel*. Mac: *Excel →
   About Microsoft Excel*.
2. **Operating system** and version.
3. **Region / language setting** of the computer (for example "English
   (United States)", "Finnish (Finland)", "German (Germany)"). It decides how
   Excel reads dates and decimal numbers, so it changes the results.

## Step 2: open and save the product file

1. Copy `validation/data/products/intended.csv` to your Desktop. Do not open
   the original inside the repository.
2. **Double-click** the copy so that it opens in Excel. Do not use any import
   wizard; double-clicking is what most people do, and it is the case to test.
3. **Do not edit anything.** The person's deliberate changes are already in the
   file; Excel's own changes are what we want to measure.
4. *File → Save As*. Choose the format **CSV UTF-8 (Comma delimited) (\*.csv)**.
   Name the file `excel-products.csv`.
5. If Excel warns that some features might be lost, choose **Keep Current
   Format** / **Yes**.
6. Close Excel.

## Step 3: open and save the order file

Repeat step 2 with `validation/data/orders/intended.csv` and save it as
`excel-orders.csv`.

## Step 4: score the result

Open a terminal in the repository folder and run (adjust the paths):

```console
python validation/score.py --table products --name excel-products --edited ~/Desktop/excel-products.csv
python validation/score.py --table orders --name excel-orders --edited ~/Desktop/excel-orders.csv
```

If your Excel saved the file with semicolons (common on computers set to a
European region), add `--delimiter ";"` to each command. The script converts
the separator before scoring; no value is changed.

Each command prints one line per mode (default, `--trust-key`, and automatic
column detection). The columns mean:

| Column | Meaning | What we hope for |
| --- | --- | --- |
| Damaged | identifier cells Excel changed | anything; this measures Excel |
| Restored | put back exactly as intended | as many as possible |
| Review | left unchanged and reported for a person | fewer is better |
| Incorrect | the tool wrote a wrong value | **must be 0** |
| Undone | a deliberate change was put back (known limitation) | 15 for products, 0 for orders |
| Other cols | a non-identifier cell was changed | **must be 0** |
| Inputs same | the input files were left untouched | **must be yes** |
| False warnings | a warning that says something untrue | 0 |
| Not mentioned | columns Excel changed that the report neither checked nor named | none |

For the full breakdown, add `--markdown excel-results.md` and open that file.

## Step 5: share the results

Open an issue in the repository with:

- your notes from step 1;
- the printed lines from step 4 (or the `excel-results.md` file);
- the two saved CSV files, if you are willing to share them. They contain only
  made-up data.

**If "Incorrect" is not 0, that is the most important result.** Please attach
the files, because they reproduce the problem exactly.

## What to expect

These are expectations from Microsoft's documentation, **not measured
results**. Excel is expected to:

- remove leading zeros (`00123` → `123`);
- show numbers with 12 or more digits in scientific notation
  (`5901234123457` → `5.90123E+12`);
- keep only 15 significant digits of long numbers (`1234567890123456789` →
  `1234567890123450000`);
- turn some codes into dates. This depends on the region: with US settings,
  `MAR1` → `1-Mar`, `1-7` → `7-Jan`, `7/2` → `2-Jul`;
- turn `true` / `false` into `TRUE` / `FALSE`;
- turn `12E3` into `1.20E+04`, and `+447700900835` into `447700900835`
  (shown in scientific notation).

LibreOffice left `MAR1` and `1-7` unchanged in the Phase 2 round trips. If
Excel changes them, that is damage the project has not yet seen from a real
spreadsheet program, which makes this test especially useful.

## Other ways of opening files (optional, later)

- **Data → From Text/CSV** (Power Query) detects column types itself and can
  give different damage. Test it separately if you can: load the file, choose
  *Load*, then save as above, naming the files `excel-pq-products.csv` and
  `excel-pq-orders.csv`.
- **Excel on the web** (Office.com): upload, open, then *File → Save a Copy*
  or *Download as CSV*.
- Recent Microsoft 365 versions have an option *File → Options → Data →
  Automatic Data Conversion* that can stop the removal of leading zeros and
  other conversions. Note whether it was on or off.
