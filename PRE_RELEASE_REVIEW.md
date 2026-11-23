# Pre-release review — csv-safe-restore 0.1.0 (alpha)

Date: 2026-11-18. Nothing has been published. There is no remote repository.

> **Later change (2026-11-23, version 0.1.0a1):** strict matching is now the
> default. What this report calls "normal mode" is now the opt-in
> `--trust-key` option, and `--strict` only names the default. See
> [CHANGELOG.md](CHANGELOG.md) and the README. The findings below are kept as
> they were written. The publishing decisions listed at the end have since
> been settled: the copyright holder stays neutral, no package author is
> named, and the install commands use the repository address. The GitHub
> Actions workflow has since run: all tests pass on Linux, Windows and macOS
> with Python 3.11–3.14, after one fix (a folder given as an input file is now
> reported as a folder on Windows too, instead of "Permission denied").

This review had five parts: an independent adversarial review (the third so
far), a focused re-check of the fixes from that review, a new `--strict` mode,
tests on several Python versions, and a documentation rewrite.

**Passing tests do not prove the tool is safe.** The tests and the benchmark
only show that the cases tried did not produce a wrong value. Two rounds of
review in this pass still found wrong values that earlier tests had missed.

## 1. Problems discovered

Each problem was found by running the tool on files built to break it, and was
reproduced before it was fixed. "Wrong value" means the repaired file contained
a value that differed from the known truth.

### Third independent review

| # | Severity | Problem |
| --- | --- | --- |
| F1 | **Wrong value** | Swapped keys were trusted when another column differed only loosely (`1.50` vs `1.5`, `5%` vs `5`, `" Widget"` vs `Widget`, `"1,000"` vs `1`). The row-fit check skipped any row that matched its own record *after normalisation*, even when another record matched it *exactly*. |
| F2 | **Wrong value** | For numbers with more than 28 significant digits, the search for other possible originals and the key-ambiguity check silently found nothing. Python's `Decimal` keeps only 28 digits by default. |
| F3 | Legitimate edits reverted | Automatic column detection picked a whole column because of a single fragile-looking value. A deliberate birthday correction `10-11` → `2011-10-11`, or `1500` → `1500.00` in an amount column, was then "restored". |
| F4 | Bad value copied | A reference value that itself looked damaged (`5.90123E+12`) was copied over a hand-corrected value in the edited file. |
| F5 | Format | A `"` inside an unquoted header field (`5" pipe`) fooled line-ending detection, so the output used LF instead of CRLF. Cell values were correct. |
| F6 | Crash | A file name that is not valid UTF-8, combined with `--report`, crashed with a traceback after the output had been written. |
| F7 | Crash / leftovers | `kill` (SIGTERM) during the write left a hidden temporary file. Ctrl+C printed a traceback. |
| F8 | Permissions | `--overwrite` gave the replaced file default permissions, for example making a private `0600` file readable by others. |
| F9 | Error | A long but legal output file name failed, because the temporary file name was the output name plus extra characters. |
| F10 | Crash | With standard output closed (`>&-`), the tool crashed after writing the output, with exit code 1. CI would read that as "findings". |
| F11 | Rules / messages | `0` → `-0` was accepted as spreadsheet damage. A UTF-32 file was reported as UTF-16. |
| F12 | Wrong value (key reuse) | Fuzzing found a row with an unexplained change to one identifier whose other identifier was still repaired, although the key had been reused for a new record. Fixed in strict mode only (see section 4). In normal mode this is the documented key-trust limitation. |

### Re-check of the fixes

A second, independent agent attacked only the newly changed code.

| # | Severity | Problem |
| --- | --- | --- |
| R1 | **Wrong value, strict mode** | A corroborating value could be another record's value after spreadsheet damage. For example, Bob's `0777` became `777`, which is exactly Ann's value. After a renumbering, that "confirmed" the wrong record. A random test found 21 wrong repairs in 20,000 renumbered cases. |
| R2 | **Wrong value** | The F1 fix still skipped the comparison when *identifier* columns matched only loosely (`777` vs `0777`). |
| R3 | Legitimate edits reverted | The F3 fix counted only non-empty cells, so a sparse column with one fragile value was still picked. |
| R4 | **Wrong value** | Numeric keys longer than 400 characters were never checked for ambiguity. |
| R5 | Correct repair refused | Found by Hypothesis during the final run on Python 3.12 and 3.13 (random data, not a version difference). The R2 fix counted the column being checked as evidence for a rival that really has the observed value. That is circular: matching the observed value is what makes it a rival. Rows identical apart from the key were then refused, although the key is the only evidence there, by design. |

## 2. Fixes implemented

| # | Fix | Where |
| --- | --- | --- |
| F1, R2 | The row-fit check now compares two things in order: first the columns a spreadsheet cannot explain, then all columns that are not exactly equal. A row skips the comparison only when every column is exactly as in its own record. | `detection._flag_reassigned_keys` |
| F2 | Exact decimal arithmetic in the corruption index and the key guard. Unary minus and division now use an explicit high-precision context. | `patterns.CorruptionIndex._candidates`, `matching.collision_forms` |
| R4 | Number-like key parts over 400 characters are never trusted. Neither is any key with digits in a key column that contains such a value. The reason is shown in the report. | `matching.match_records` |
| F3, R3 | A column is detected automatically only when more than half of **all** its reference values look fragile; empty cells count as not fragile. Columns that were skipped despite having some fragile values are listed in the report. Repairs in auto-detected columns add a note. | `detection._detect_identifier_columns`, `_collect_warnings` |
| F4 | New category `damaged_reference`. A reference value with an obvious damage shape (scientific notation, error value, spreadsheet date) is never copied back. Zeros after the 15th digit are deliberately not treated as an obvious shape: one real 16-digit number in ten looks like that, and such a value can only "explain" itself. | `detection._collect_cell_findings` |
| F5 | The header's line ending is found by the `csv` parser itself, so it treats quotes exactly as when the data is read. | `reader._detect_newline` |
| F6 | File names in reports show undecodable bytes as `\xff`. The report writer cannot fail on them. | `reporting.path_text`, `repair.write_text_atomically` |
| F7 | SIGTERM is treated like Ctrl+C. Both remove the temporary file, print one line, and exit with 130. | `cli.main` |
| F8 | `--overwrite` keeps the permissions of the file it replaces. It replaces a symbolic link itself, not the file the link points to (documented). | `repair._write_atomically` |
| F9 | The temporary file uses a short fixed prefix. | `repair._write_atomically` |
| F10 | A closed standard output is detected before any work: exit code 2, nothing written. | `cli._run` |
| F11 | `0` → `-0` is rejected (spreadsheets never add a minus sign to zero; `-0` → `0` is still accepted). UTF-32 byte-order marks are checked before UTF-16. | `patterns._explain_number`, `reader._OTHER_BOMS` |
| R5 | The row-fit comparison leaves out the column being checked, and skips rivals when the rest of the row exactly matches its own record. | `detection._flag_reassigned_keys` |
| R1 | Strict corroboration also requires that no other reference value in that column could have become the corroborating value. This is checked with the generous key guard (corruption rules plus collision forms). | `detection._apply_strict` |

How the fixes were checked:

- All reviewer repro files were rerun against the fixed code. Every one is now refused or correct.
- The reviewer's own random test now finds **0** wrong strict-mode repairs in 20,000 renumbered cases (it found 21 before).
- The property tests were run with 12 different random seeds after the last fix.
- Kill and Ctrl+C were sent to a real 400,000-row run in the middle of writing. No temporary file was left and no output was created.

Not fixed, judged acceptable and documented:

- `0-5` → `May-00` is accepted as a date conversion. Excel may really read `0-5` as May 2000; this was not verified.
- The text report shows 50 lines per section by default (`--max-details 0` shows all; the JSON report always has everything).

## 3. New tests added

The suite went from 241 to **286 tests**:

| File | Tests | What it covers |
| --- | --- | --- |
| `tests/test_strict.py` | 14 | Strict-mode rules, one by one: unique unchanged column confirms; key-only files are never repaired; renumbered and reused keys; shared, repeated, edited, damaged, look-alike and empty values do not confirm; another identifier column can confirm; intentional edit that looks like damage (documented limitation); normal mode unchanged; CLI, JSON and exit codes. |
| `tests/test_preservation.py` | 2 | End-to-end ground truth through the real CLI and real files, with random quoting, embedded newlines, commas and quotes, LF/CRLF, BOM, deleted, added and shuffled rows, intentional edits and renumbered keys (Hypothesis, 300 examples per run). It checks that inputs are byte-identical, that every changed cell equals the truth (always in strict mode; in normal mode when keys were not renumbered), that every other cell, including every legitimate edit, equals the edited file, that rows keep their order, and that the number of changed cells equals the report. |
| `tests/test_regressions.py` | 29 | The smallest reproduction of every problem in section 1 that was fixed (F1–F12, R1–R5). |

One existing test changed: `test_identifier_columns_are_detected_from_the_reference_file` now expects that a column where only 2 of 4 values look fragile is *not* picked, and that the report says so.

The benchmark now also scores `--strict`, and it fails if either mode makes a false repair.

## 4. Strict-mode behaviour

`--strict` works on both `check` and `repair`. A value is restored only if
everything the normal mode requires holds **and**:

1. no other identifier cell of the row changed in a way that is not
   spreadsheet damage, and
2. the row has a **corroborating value**: in a column that is not a key column
   and not the cell being repaired, a value that is
   - not empty,
   - byte-for-byte equal to the matched reference record's value,
   - held by exactly one reference record and exactly one edited row, and
   - not something a spreadsheet could have made of another reference value
     in that column.

No similarity, row position or check digit is ever used. A cell that fails
becomes `uncorroborated`: it is left unchanged, listed under *NEEDS REVIEW*
with the reason, and counted towards exit code 1.

Measured results:

| Data | Normal mode | Strict mode |
| --- | --- | --- |
| Benchmark, LibreOffice round trip (3,286 damaged cells) | 3,199 restored, 0 false | 3,120 restored, 0 false |
| Benchmark, Excel model (5,019 damaged cells) | 4,879 restored, 0 false | 4,699 restored, 0 false |
| Reviewer's random test, keys renumbered in every case (20,000 cases) | 1,124 wrong rows (expected: normal mode trusts the key when the row cannot contradict it) | 0 wrong |
| `examples/` | 17 restored | 16 restored (P006 had other identifiers changed by hand) |
| 200,000 rows, one laptop | about 17 s | about 23 s |

The benchmark data never renumbers keys, so it does not exercise the case that
strict mode exists for. The renumbering results come from the random tests.

What strict mode cannot do:

- It cannot detect a consistent reassignment, where the key and the
  corroborating column were moved to another record together.
- It cannot tell an intentional edit that looks exactly like damage (a
  deliberate `00123` → `123`) from real damage. Such an edit is restored in a
  confirmed row. This holds in both modes, and no tool can decide it from the
  data alone.
- It cannot use a coincidental unique value: a reassigned row that happens to
  carry exactly the unique value of the record its key points to is trusted.
- It repairs nothing in files that have only a key and identifier columns.

## 5. Python versions and operating systems actually tested

| Operating system | Python | How | Result |
| --- | --- | --- | --- |
| Linux (kernel 7.0, this laptop) | 3.14.4 | local virtual environment | 286 passed (property tests also with 12 extra seeds); benchmark 0 false repairs; wheel built, installed in a clean venv and run end to end |
| Linux (Debian, Docker `python:3.11-slim`) | 3.11.12 | container, run as root | 286 passed; wheel built, installed in a clean venv, output identical to the example ¹ |
| Linux (Debian, Docker `python:3.12-slim`) | 3.12.16 | container, run as root | 286 passed; same wheel check ¹ |
| Linux (Debian, Docker `python:3.13-slim`) | 3.13.18 | container, run as root | 286 passed; same wheel check ¹ |
| **Windows** | — | **not available here** | **Not tested.** No claim of Windows compatibility is made. |
| **macOS** | — | **not available here** | **Not tested.** No claim of macOS compatibility is made. |

¹ The containers ran as root, which ignores most file permission checks. The
permission test (F8) checks file modes, which root does not change, so it is
still meaningful there. The reviewer's read-only-folder case was run by hand
as a normal user only.

Real Excel was never run. Excel behaviour comes from Microsoft's documented
rules and a simulation. LibreOffice 26.2 was tested for real.

GitHub Actions (`.github/workflows/tests.yml`) is prepared to test, after
publication, on Ubuntu, Windows and macOS with Python 3.11, 3.12, 3.13 and
3.14 (12 jobs). Each job runs the tests, a normal and a strict smoke run, and
checks that the example input is unchanged. A further job per operating system
builds the wheel and runs it from a clean environment. GitHub Actions is free
for public repositories. **The workflow has never run**: it needs a remote
repository, which was not created.

## 6. Remaining safety limitations

1. **Normal mode trusts the key.** If records were renumbered or a key was
   reused and nothing else in the row contradicts it, the wrong original is
   restored. The report notes when restored values had more than one possible
   original. Use `--strict` when keys may not be stable.
2. **Intentional edits that look exactly like damage are restored**, in both
   modes. Every restored value is listed in the report.
3. **Strict mode cannot detect a key and a corroborating column moved
   together**, or a coincidental unique value.
4. **Excel, Windows and macOS are untested.** Windows-specific behaviour (file
   replacement, permissions, CRLF, file names) is the most likely place for
   surprises.
5. **The reference file must be clean.** Values with an obvious damage shape
   are no longer copied back, but a reference value that lost its leading
   zeros cannot be recognised.
6. **Automatic column detection** may now miss identifier columns where at
   most half of the values look fragile. Those columns are listed in the
   report, and naming the columns with `--identifier-columns` is recommended.
7. **Limits:** numeric keys over 400 characters are never matched; files are
   loaded into memory; English month names only; one composite-key shape is
   slow (about 95 s at 200,000 rows, from the previous report).
8. **Review coverage:** the fixes from the re-check (R1–R5) were verified with
   tests, the reviewer's repro files and the reviewer's random test, but not
   by a further independent review.

## 7. Is it ready for a public GitHub alpha release?

**Yes, as an alpha, once you have made the decisions below.** It is not ready
to be called stable or production-ready, and the README says so.

Reasons it is reasonable to publish as an alpha:

- The two core safety properties are tested hard on Linux and were not broken
  in the final runs. Input files are never changed. Every value not restored
  stays exactly as in the edited file.
- Every reproduced wrong-value case was fixed and has a regression test.
- Strict mode gives users who cannot guarantee stable keys a safe option.
- The documentation says plainly when not to trust the output.

Reasons to stay cautious:

- Each of the last three review rounds found new wrong-value cases, the most
  recent one inside the brand-new strict mode. More will probably be found.
  An alpha release with clear warnings is the honest way to find them.
- Windows, macOS and real Excel are untested. Let CI run on all platforms
  before announcing the project.

Before publishing, you need to decide:

1. **Copyright holder.** `LICENSE` now says *"Copyright (c) 2026 The
   csv-safe-restore contributors"*. The MIT terms are unchanged. Replace this
   with your name (or organisation) if you want to be named. It was not
   inferred from your account.
2. **Package author.** `pyproject.toml` has no `authors` field. Add one if you
   want PyPI to show an author.
3. **Repository URL.** The README still contains `<repository-url>`.

Suggested order after you decide: create the repository yourself, push, let
GitHub Actions run on all 12 combinations, fix anything Windows or macOS
reveals, and only then announce the alpha.
