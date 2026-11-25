"""Open each intended.csv in a REAL LibreOffice and save it as CSV again.

This is the real round trip of Phase 2: nothing here models or simulates a
spreadsheet. Whatever LibreOffice writes is the damaged ``edited.csv``.

Each profile is one way a person might open the file:

``lo-default``
    Default CSV import (same as benchmarks/make_dataset.py).
``lo-special-numbers-off``
    "Detect special numbers" explicitly switched off. (In a command-line
    round trip the default already converts some dates, e.g. ``7/2``; with
    this option off they should stay as typed.)
``lo-de-DE``
    Imported with German (Germany) number rules, as on a German-language
    computer: comma is the decimal separator, dot the thousands separator.
    Saved with commas between fields so both files keep the same delimiter.

For every profile the exact command and the LibreOffice version are written
to ``run.txt`` next to ``edited.csv``. Re-running in another year gives
different dates in the output: LibreOffice fills in the current year when it
reads a code such as ``7/2`` as a date (``07/02/26``).

Usage:  python validation/run_libreoffice.py [--data validation/data]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

# CSV filter tokens: separator, text quote, character set (76 = UTF-8), first
# line, column formats, language, quoted field as text, detect special numbers.
PROFILES = {
    "lo-default": ("44,34,76,1", "44,34,76,1"),
    "lo-special-numbers-off": ("44,34,76,1,,1033,false,false", "44,34,76,1"),
    "lo-de-DE": ("44,34,76,1,,1031,false,true", "44,34,76,1"),
}
FILTER = "Text - txt - csv (StarCalc)"


def round_trip(soffice: str, source: Path, target: Path, import_options: str,
               export_options: str) -> str:
    """Run LibreOffice once; return the command line that was used."""
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        (work / "in").mkdir()
        shutil.copyfile(source, work / "in" / "data.csv")
        command = [
            soffice, "--headless", f"-env:UserInstallation=file://{work}/profile",
            f"--infilter={FILTER}:{import_options}",
            "--convert-to", f"csv:{FILTER}:{export_options}",
            "--outdir", str(work / "out"), str(work / "in" / "data.csv"),
        ]
        subprocess.run(command, check=True, capture_output=True, timeout=600)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(work / "out" / "data.csv", target)
    shown = [part.replace(str(work), "<temp>") for part in command]
    return " ".join(f'"{part}"' if " " in part else part for part in shown)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path(__file__).parent / "data")
    args = parser.parse_args()

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        raise SystemExit("LibreOffice (soffice) is required")
    version = subprocess.run([soffice, "--version"], capture_output=True, text=True,
                             check=True).stdout.strip()
    print(version)
    for table in sorted(p for p in args.data.iterdir() if (p / "intended.csv").exists()):
        for profile, (import_options, export_options) in PROFILES.items():
            target = table / profile / "edited.csv"
            command = round_trip(soffice, table / "intended.csv", target,
                                 import_options, export_options)
            (table / profile / "run.txt").write_text(
                "Produced by a real LibreOffice round trip (not a simulation).\n"
                f"Version: {version}\nCommand: {command}\n", encoding="utf-8")
            print(f"{table.name}/{profile}: done")


if __name__ == "__main__":
    main()
