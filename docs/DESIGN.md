# Design

This document explains how csv-safe-restore decides what to repair, and why it
refuses everything else.

## The problem in one paragraph

A spreadsheet turns `00123` into `123`. Looking only at `123`, nobody can know
whether the original was `0123`, `00123` or really `123`. The information is
gone. It can only come back from somewhere else: a clean copy of the data. So
the tool needs two things: a **reference file** that still has the original
values, and a **key** that tells it which reference row belongs to which
edited row.

## The evidence required

By default a value is restored only when all of these hold:

1. **Trusted match.** The row in the edited file is tied to exactly one row in
   the reference file through the key column(s).
2. **Confirmed record.** Another column of the row holds a value that is
   unchanged and belongs to that record alone (see
   [Strict matching](#strict-matching-the-default)).
3. **Known corruption.** The change from the reference value to the edited
   value is exactly what a spreadsheet does to that value.

The first two tell us *what the original was*. The third tells us *the change
was an accident, not a decision*. Without the third, we would undo
intentional edits. Without the first two, we would be guessing the original.
`--trust-key` drops the second requirement.

The restored value is always the reference value, copied character for
character. No value is ever computed.

## Architecture

```
reader.py     strict CSV reading; every value stays a string
matching.py   which edited row belongs to which reference row; is the key trusted?
patterns.py   forward-only corruption rules: could A have become B?
checks.py     optional GTIN check digit (warnings only)
detection.py  classify every identifier cell and every row -> Analysis
repair.py     build the repaired rows; safe, verified, atomic writing
reporting.py  text and JSON reports, exit codes
cli.py        command line
```

`detection.analyze()` produces one `Analysis` object. Both commands use the
same analysis; `repair` additionally writes a file. The report therefore always
describes exactly what `repair` does.

## When is a key trusted?

A row of the edited file is `matched` only if all of these are true:

| Check | If it fails, the row is reported as |
| --- | --- |
| Every key column is non-empty | `missing_key` |
| The key appears once in the edited file | `duplicate_key` |
| The key appears once in the reference file | `duplicate_key` |
| The key exists in the reference file | `new_record`, or `suspected_key_damage` if the key is what a spreadsheet would make of some reference key |
| No *other* reference key could have been turned into this key by a spreadsheet | `ambiguous_key` |
| The row's other columns do not belong to a different reference record | `suspected_key_reassignment` |

The `ambiguous_key` check matters when keys themselves are fragile. If the
reference has keys `007` and `7`, an edited row with key `7` might be the real
`7` or a damaged `007`. Nothing is repaired in such a row.

This check is deliberately broader than the repair rules. To *repair* a value
the change must fit a corruption rule exactly. To *distrust* a key it is
enough that two reference keys could plausibly end up looking the same:
besides the corruption rules, `matching.collision_forms` treats surrounding
spaces, a leading `=`, thousands separators, decimal commas, currency and
percent signs, the ways to write a negative number (`(7)`, `7-`, `-7`),
fractions, times and incomplete dates as things a spreadsheet may normalise
(`"1,000"` and `1000`, `" 7"` and `7`, `1:30` and `01:30:00 AM`). Not covered:
formulas that must be calculated (`=1+1` and `2`) and complete dates written
in different layouts (`Jan 5, 2024` and `1/5/2024`). Being
generous here can only cause a refusal, never a repair. Upper and lower case
are *not* merged: `a1` and `A1` are different keys in many systems and
spreadsheets do not change case.

For a key made of several columns, every combination of "same value or
possible source" over the columns is considered; the search is not cut short,
because a shortened list could hide the one combination that exists.

The `suspected_key_reassignment` check covers a different failure: the keys
are intact, but they no longer mean the same records (IDs renumbered, or rows
shifted against the key column). Restoring by key would then copy another
record's identifier.

This can only go wrong for a damaged value with more than one possible origin
in its column (`123` when the reference has both `00123` and `0123`). If a
value has a single possible origin, that origin is right whatever happened to
the keys. So for each such value the tool looks at its *rivals*: the other
reference records the value could have come from. It counts in how many
columns the edited row disagrees with the record that has its key, and with
each rival. "Disagrees" means the values are not equal and not explained by a
spreadsheet round trip (`9.90` and `9.9` agree). Ties are broken by the
columns (identifier columns included) that are not *exactly* equal, so a rival
with `1.5` beats a record with `1.50` for an edited `1.5`, and a rival with
`777` beats a record with `0777` for an edited `777`. The column being
checked is left out of both counts: that a rival really has the observed value
is what makes it a rival, not evidence that the row is that rival. Rows that
differ only in the key are therefore decided by the key, as declared. If a rival fits the row better,
nothing in the row is restored. Only when every other column is exactly as in
the row's own record is no rival looked at. (The third review found swapped
keys slipping through when the shortcut used the looser comparison.)

What this cannot catch: files with no column besides the key and the
identifier, or rows edited so heavily that no record fits better. There the
key is the only evidence, exactly as the user declared it. The report prints a
note whenever restored values had more than one possible origin, and the
README lists stable keys as a requirement.

Rows that are not `matched` are never repaired. Reference rows without a
partner are listed as `deleted_record` and are never added back.

Numbers longer than 400 characters are not analysed for damage. A key part
like that is never trusted, and neither is any key with digits in the same
column, because a collision with it could not be ruled out
(`ambiguous_key`).

A key column cannot also be an identifier column: a value cannot be both the
evidence and the thing being repaired.

## Strict matching (the default)

The key is only right if it means the same record in both files. Renumbered,
reused or shifted IDs break that silently. So by default the key alone is
never enough: the row must also *confirm* the record.

With `--trust-key` (`strict=False` in Python), the tool trusts the key as
declared by the user and only distrusts it when the row itself points
somewhere else (the `ambiguous_key` and `suspected_key_reassignment` checks
above). That mode restores more, but follows renumbered keys when nothing in
the row contradicts them, so it is an explicit opt-in. It was the default
before 0.1.0a1; it was changed because each review round found new ways for
key-only trust to restore another record's value.

After all key checks, every cell classified as `confirmed_damage` keeps
that category only if its row has a **corroborating value**. A value in column
`C` of edited row `r`, matched to reference record `R`, corroborates when all
of these hold:

| Condition | Why |
| --- | --- |
| `C` is in both files and is not a key column | the key is the thing being checked |
| `C` is not the column of the cell being repaired | a damaged value cannot vouch for itself |
| the value, after removing surrounding whitespace, is not empty | empty values are shared by many records |
| the value is byte-for-byte equal to `R`'s value in `C` | exact equality only: no collision forms, no corruption rules, no case folding, no similarity |
| the value occurs in exactly one reference record in `C` | otherwise it does not single out `R` |
| the value occurs in exactly one row of the edited file in `C` | otherwise another edited row could be `R` as well |
| no other reference value in `C` could have become this value (checked with the key guard: corruption rules and collision forms) | otherwise the value may be another record's value after damage (`1.50` shown as `1.5`). Found in the re-check of the third review |

In addition, no other identifier cell of the row may have an unexplained
change (`unexplained_change`, `suspicious_change` or `damaged_reference`):
such a change is either an edit or a sign of a different record, and strict
mode does not try to tell which.

Another identifier column counts, as long as it is unchanged. Otherwise the
cell becomes `uncorroborated`: it is listed for review with the reason and
left as it is in the edited file. It counts towards exit code 1.

Why exact equality only: a value that was changed, even by a known spreadsheet
rule, could have more than one origin, and that is exactly the uncertainty
strict matching is meant to remove. Loosening this rule to rescue more repairs
would bring back the problem.

What the default mode still cannot detect:

- **Consistent reassignment.** If the key and the corroborating column were
  both moved to another record together (for example a script renumbered IDs
  and also rewrote a unique reference code to match), the two columns agree
  and the row is trusted.
- **Intentional edits that imitate damage.** In a confirmed row, a deliberate
  `00123` → `123` is still restored. Nothing in the data can tell it apart from
  a spreadsheet doing the same.
- **A coincidental unique value.** If a reassigned row happens to carry, in
  some column, exactly the unique value of the record its key now points to,
  that is taken as confirmation. With real data this needs either a
  deliberate edit or an extraordinary coincidence.

What it costs: files with only a key and identifier columns get no repairs;
rows whose distinctive columns were edited are refused even when the key is
right. On the project benchmark the default mode restored 3,120 of 3,286
damaged cells (LibreOffice) and 4,699 of 5,019 (Excel model), against 3,199
and 4,879 with `--trust-key`, with no false repairs in either.

## Corruption rules

`patterns.explain_corruption(original, observed)` answers one question: *could
a spreadsheet have turned `original` into `observed`?* It never works in the
other direction.

| Pattern | Example | Rule |
| --- | --- | --- |
| `leading_zeros_removed` | `00123` → `123` | The original is all digits; the observed value is the same number without leading zeros. |
| `number_reformatted` | `+44` → `44`, `1.50` → `1.5`, `1E5` → `100000` | The original reads as a number; the observed value is exactly the same number in plain form. |
| `precision_lost` | `1234567890123456` → `1234567890123450` | The observed value is the original cut or rounded to 15 significant digits, or what a 64-bit float holds. |
| `scientific_notation` | `5901234123457` → `5.90123E+12` | The observed value is in scientific notation and equals the original rounded to the number of digits shown. |
| `date_conversion` | `MAR1` → `1-Mar`, `1-3` → `3-Jan` | The original can be read as an incomplete date, and the observed value is a spreadsheet's rendering of that same date. |
| `boolean_uppercased` | `true` → `TRUE` | Exactly that. |
| `formula_error` | `=A1+` → `Err:509`, `-ABC` → `#NAME?` | The original starts with `=` and the observed value is a spreadsheet error value; or it starts with `+`, `-` or `@` and the observed value is `#NAME?`. |

Notes on the number rules:

- Comparison uses `decimal.Decimal`, never floats, so the rules themselves do
  not lose precision.
- Scientific notation must be written the way spreadsheets and Python write
  it, with a signed exponent (`2E+02`, not `2E2`).
- A value that a 64-bit float turns the original into is also accepted
  (`6134327459162674864` → `6.1343274591626752e+18`), because pandas and other
  tools write floats that way.
- At 15 significant digits both neighbours are accepted (rounded down or up),
  because a real LibreOffice 26.2 rounds `321661743950674477` to
  `3.21661743950675E+017`. The first 14 digits must still match exactly. With
  shorter mantissas (for example Excel's `1.23457E+11`) the rounding must be
  correct.
- The year of a converted date is not checked when the original had no year,
  because the spreadsheet fills in the year in which the file was opened; it
  only has to lie between 2000 and the current year. The
  day and month must form a real date and must be in the same reading of the
  original (`1/2/24` is not accepted as `2/1/2024`).
- Known cost of the precision rule: a hand edit that only changes digits past
  the 15th of a long number looks exactly like precision loss and is restored.

Any identifier change that fits no rule is `unexplained_change` (or
`suspicious_change` when the new value has the shape of damage, such as
scientific notation). It is reported, never reverted.

## Cell categories

| Category | Meaning | Repaired? |
| --- | --- | --- |
| `confirmed_damage` | Trusted match and known corruption | Yes |
| `suspicious_change` | Trusted match; new value looks damaged, but the reference value does not explain it | No |
| `unexplained_change` | Trusted match; identifier changed with no known pattern (likely intentional) | No |
| `unrecoverable` | No trusted match (new record, bad key); value has the shape of damage | No |
| `invalid_gtin` | No trusted match; barcode fails its check digit | No |
| `damaged_reference` | The change fits a corruption rule, but the reference value itself has an obvious damage shape (scientific notation, error value, spreadsheet date), so it is not copied back | No |
| `uncorroborated` | Default mode only: would be `confirmed_damage`, but the record is not confirmed (no confirming value, or another identifier of the row changed by hand) | No |

Automatic identifier-column detection picks a column only when more than half
of its reference values have a fragile shape; empty cells count as not
fragile. A single `10-11` in a
column of birthdays, or one 12-digit amount, must not make the whole column a
repair target, because then a legitimate edit there (`10-11` → `2011-10-11`)
would look like damage and be reverted.

*Collapsed values* are listed separately: different reference values that now
look the same in the edited file (`00123` and `0123` both became `123`). They
are still repaired when each row has a trusted key; the list exists to show
how much information the damaged file lost.

Edits in non-identifier columns are counted per column and always kept.

## Cases that cannot be repaired, and why

| Case | Why not |
| --- | --- |
| No reference file | The original value does not exist anywhere. |
| New record with a damaged value | Same: there is no original to copy. |
| Duplicate or empty key | The row cannot be tied to one reference row. |
| Key damaged by the spreadsheet | The match itself is in doubt. |
| Row carries another record's data under this key | The keys may have been reassigned; the match is in doubt. |
| No record matched at all | Nothing was compared. This is reported as a finding (exit code 1), never as a clean result. |
| Identifier changed in an unknown way | Could be intentional; reverting would destroy an edit. |
| Reference value is itself already damaged | The tool restores what the reference says. It warns when the reference file contains values in scientific notation or error values. |

## Things that are deliberately not evidence

- **Check digits.** A valid check digit shows that a barcode is well-formed,
  not that it is the right barcode for this row. `--gtin-columns` only adds
  warnings.
- **Similarity.** `AB-0042` and `AB-0043` are similar. That proves nothing.
- **Row position.** Rows are sorted, inserted and deleted in spreadsheets.
- **Uniqueness of a candidate.** "Only one reference value ends in 123" is
  still a guess.

## Writing the output

1. The output path is checked first: not an input file (compared by file
   identity, so links are caught), not a directory, not an existing file
   unless `--overwrite` is given.
2. The repaired table is written to a temporary file in the destination
   folder, flushed and synced.
3. The temporary file is read back with the strict reader and compared with
   the intended content cell by cell.
4. The file is moved into place: with a hard link when not overwriting (this
   fails if the name has been taken in the meantime), or an atomic replace
   with `--overwrite`.

The output keeps the edited file's delimiter, line ending and BOM. Quoting is
normalised: fields are quoted only when needed. The line ending is the one
that ends the header row; a file with mixed line endings comes out uniform.

If the repaired file was written but the report could not be (for example a
full disk), the error message says that the repaired file exists.

## Strict reading

The reader refuses, with a clear message, anything it would otherwise have to
guess about: files that are not UTF-8, empty files, duplicate column names,
rows with too many or too few fields, blank lines, NUL bytes and broken
quoting. A repair tool that guesses while reading cannot be safe while
writing.

## Evidence base

- LibreOffice behaviour: real headless round trips with LibreOffice 26.2
  (see `benchmarks/make_dataset.py`).
- Excel behaviour: implemented from Microsoft's documented rules (15
  significant digits, scientific notation for long numbers in General format,
  automatic date conversion). **It was not tested against a real Excel.**
- pandas-style output (`123.0`, `1.2345678901234567e+19`) is accepted as a
  rendering of the same number.
