"""Command line interface.

Exit codes (stable, meant for CI):

* 0 - nothing needs attention
* 1 - findings: damage found (``check``) or unresolved findings left (``repair``),
  or no record could be matched at all
* 2 - the tool could not run: bad options, unreadable or malformed input,
  or an output file that already exists
* 130 - interrupted (Ctrl+C or SIGTERM); no partly written file is left behind
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, reporting
from .detection import analyze
from .errors import CsvSafeRestoreError
from .reader import parse_delimiter, read_csv
from .repair import check_output_path, repaired_rows, write_repaired_csv, write_text_atomically

_DESCRIPTION = """\
Restore identifiers (SKUs, barcodes, postal codes, long IDs) that a spreadsheet
damaged in a CSV file, using a clean reference copy of the same data.

Values are restored only when a record is matched by a unique, undamaged key,
another column confirms that it is the same record, and the change is exactly
what a spreadsheet does (for example 00123 -> 123). Everything else is
reported and left untouched. The tool never guesses.

Start with "check": it writes nothing and shows what "repair" would do.
"""

_EPILOG = """\
exit codes:
  0    nothing needs attention
  1    findings (check: any damage; repair: findings that were not fixed),
       or no record could be matched at all
  2    error (bad options, unreadable input, output file already exists)
  130  interrupted; no partly written file is left behind

examples:
  csv-safe-restore check original.csv edited.csv --key product_id
  csv-safe-restore repair original.csv edited.csv --key product_id \\
      --identifier-columns sku,barcode --output repaired.csv
"""


def _column_list(text: str) -> tuple[str, ...]:
    names = tuple(name.strip() for name in text.split(","))
    if any(name == "" for name in names):
        raise argparse.ArgumentTypeError("column names must not be empty")
    return names


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("original", type=Path, help="clean reference CSV (never modified)")
    parser.add_argument("edited", type=Path, help="CSV that may be damaged (never modified)")
    parser.add_argument(
        "--key",
        required=True,
        type=_column_list,
        metavar="COLUMNS",
        help="column(s) that identify a record, comma-separated. Must be unique, "
        "non-empty and not one of the identifier columns",
    )
    parser.add_argument(
        "--identifier-columns",
        type=_column_list,
        metavar="COLUMNS",
        help="columns to check, comma-separated (default: detect columns with "
        "leading zeros, long numbers and similar in the reference file)",
    )
    parser.add_argument(
        "--gtin-columns",
        type=_column_list,
        default=(),
        metavar="COLUMNS",
        help="identifier columns holding UPC/EAN/GTIN barcodes; adds check-digit "
        "warnings (warnings only, never a reason to repair)",
    )
    parser.add_argument(
        "--delimiter",
        default=",",
        metavar="CHAR",
        help="field separator used by both files: one character or 'tab' (default: comma)",
    )
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="report format (default: text)"
    )
    parser.add_argument(
        "--report",
        type=Path,
        metavar="PATH",
        help="write the report to this file instead of the screen",
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=50,
        metavar="N",
        help="lines shown per section of the text report; 0 = no limit (default: 50)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=True,
        help="the default: restore a value only when, besides the key, another "
        "column holds an unchanged value that belongs to that record alone. "
        "Everything else is reported for review",
    )
    mode.add_argument(
        "--trust-key",
        dest="strict",
        action="store_false",
        help="less conservative: let the key alone decide which record a row is. "
        "Restores more values, but restores the WRONG value if record IDs were "
        "renumbered or reused between the two files and nothing else in the row "
        "shows it. Use only if you are sure the key column was never changed",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="allow replacing an existing output or report file (never an input file)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="csv-safe-restore",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="{check,repair}", required=True)

    check = commands.add_parser(
        "check",
        help="compare the files and report; changes nothing",
        description="Compare the edited file with the reference file and report what "
        "was found. No file is modified.",
    )
    _add_common_options(check)

    repair = commands.add_parser(
        "repair",
        help="write a repaired copy with confirmed damage restored",
        description="Write a copy of the edited file in which confirmed damage is "
        "restored from the reference file. Everything else is kept as it is in the "
        "edited file. Neither input file is modified.",
    )
    _add_common_options(repair)
    repair.add_argument(
        "--output", required=True, type=Path, metavar="PATH", help="where to write the repaired CSV"
    )
    return parser


def _run(args: argparse.Namespace) -> int:
    if sys.stdout is None:  # started with standard output closed
        raise CsvSafeRestoreError(
            "standard output is closed, so no result could be shown. Nothing was done."
        )
    if args.max_details < 0:
        raise CsvSafeRestoreError("--max-details must be 0 or more.")
    delimiter = parse_delimiter(args.delimiter)
    output: Path | None = getattr(args, "output", None)
    inputs = [args.original, args.edited]

    # Check destinations before doing any work, so mistakes fail fast.
    if output is not None:
        check_output_path(output, inputs, args.overwrite, "output")
    if args.report is not None:
        check_output_path(args.report, inputs, args.overwrite, "report")
        if output is not None and args.report.resolve() == output.resolve():
            raise CsvSafeRestoreError("--report and --output must be different files.")

    original = read_csv(args.original, delimiter)
    edited = read_csv(args.edited, delimiter)
    analysis = analyze(
        original, edited, args.key, args.identifier_columns, args.gtin_columns, args.strict
    )

    if output is not None:
        write_repaired_csv(output, edited.header, repaired_rows(analysis), edited, args.overwrite)

    if args.format == "json":
        report = reporting.to_json(analysis, args.command, output)
    else:
        report = reporting.to_text(analysis, args.command, output, args.max_details)
    try:
        if args.report is not None:
            write_text_atomically(args.report, report, args.overwrite)
            print(reporting.result_line(analysis, args.command, output))
            print(f"Report written to {reporting.path_text(args.report)}")
        else:
            sys.stdout.write(report)
        sys.stdout.flush()
    except (CsvSafeRestoreError, OSError) as exc:
        if output is None:
            raise
        # The user must know the repaired file exists even though the run failed.
        raise CsvSafeRestoreError(
            f"The repaired file {output} was written, but the report could not be: {exc}"
        ) from exc
    return reporting.exit_code(analysis, args.command)


def _silence_closed_stdout() -> None:
    """After a failed write, stop Python from failing again when it exits."""
    if sys.stdout is None:
        return
    try:
        sys.stdout.flush()
    except OSError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass


def _interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Reports quote values from the files; never crash on a terminal that
    # cannot display one of them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    # Treat "kill" like Ctrl+C, so temporary files are cleaned up either way.
    previous = None
    try:
        previous = signal.signal(signal.SIGTERM, _interrupt)
    except (ValueError, OSError):  # not the main thread, or not supported
        pass
    try:
        return _run(args)
    except KeyboardInterrupt:
        _silence_closed_stdout()
        print(
            "csv-safe-restore: interrupted. No partly written file was left behind; "
            "an output file exists only if it was completely written.",
            file=sys.stderr,
        )
        return 130
    except CsvSafeRestoreError as exc:
        _silence_closed_stdout()
        print(f"csv-safe-restore: error: {exc}", file=sys.stderr)
        return reporting.EXIT_ERROR
    except OSError as exc:  # for example the report was piped into a closed program
        _silence_closed_stdout()
        print(f"csv-safe-restore: error: could not write the report: {exc}", file=sys.stderr)
        return reporting.EXIT_ERROR
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    sys.exit(main())
