"""Generate the validation datasets used by compare_tools.py.

This script is deliberately independent of the csv_safe_restore package: it
does not import it, and the damage is not produced with the package's own
rules. That way the benchmark cannot simply agree with itself.

Two scenarios are produced from the same fictional product catalogue:

``libreoffice``
    The reference file is opened and re-saved as CSV by a real, headless
    LibreOffice. Whatever LibreOffice does to the values is the damage.

``excel_model``
    The damage is produced by a small model of Excel's documented behaviour
    (General format: leading zeros dropped, 15 significant digits kept, 12+
    digit numbers shown in scientific notation, MAR1 -> 1-Mar ...). Excel
    itself was NOT run; this scenario is a simulation and is labelled as such.

After the spreadsheet step, scripted "human" changes are applied: price and
description edits, intentional identifier changes, deleted and new records,
duplicated keys, blanked keys and a row shuffle. Every change is recorded in
``truth.csv`` so results can be scored cell by cell.

Usage:  python benchmarks/make_dataset.py [--rows 2000] [--seed 20261118]
"""

from __future__ import annotations

import argparse
import csv
import random
import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path

HEADER = [
    "record_id", "sku", "barcode", "postal_code", "internal_id", "description", "price", "quantity",
]
IDENTIFIER_COLUMNS = ["sku", "barcode", "postal_code", "internal_id"]
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
NOUNS = ["Widget", "Bracket", "Gasket", "Valve", "Seal kit", "Pump", "Sensor", "Cable", "Filter"]


def gtin(rng: random.Random, length: int, leading_zeros: int = 0) -> str:
    body = "0" * leading_zeros + "".join(
        rng.choice("123456789") if i == 0 else rng.choice("0123456789")
        for i in range(length - 1 - leading_zeros)
    )
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return body + str((10 - total % 10) % 10)


def make_sku(rng: random.Random) -> str:
    kind = rng.random()
    if kind < 0.40:  # zero-padded numeric
        return str(rng.randrange(1, 99999)).zfill(rng.choice([5, 6, 8]))
    if kind < 0.70:  # alphanumeric, safe in spreadsheets
        return f"{rng.choice('ABCDEFGH')}{rng.choice('ABCDEFGH')}-{rng.randrange(1, 9999):04d}"
    if kind < 0.80:  # plain short number, safe
        return str(rng.randrange(100, 99999))
    if kind < 0.88:  # looks like scientific notation
        return f"{rng.randrange(1, 99)}E{rng.randrange(1, 9)}"
    if kind < 0.94:  # month code
        return f"{rng.choice(MONTHS)}{rng.randrange(1, 28)}"
    return f"{rng.randrange(1, 12)}-{rng.randrange(1, 28)}"  # dash code


def make_reference(rows: int, rng: random.Random) -> list[list[str]]:
    table = []
    for number in range(1, rows + 1):
        kind = rng.random()
        if kind < 0.35:
            barcode = gtin(rng, 12, leading_zeros=rng.choice([0, 1, 1, 2]))  # UPC-A
        elif kind < 0.85:
            barcode = gtin(rng, 13, leading_zeros=rng.choice([0, 0, 0, 1]))  # EAN-13
        else:
            barcode = gtin(rng, 14, leading_zeros=rng.choice([0, 1, 3]))  # GTIN-14
        postal = f"{rng.randrange(501, 99950):05d}"
        internal = "".join(rng.choice("0123456789") for _ in range(rng.choice([16, 18, 20])))
        noun = rng.choice(NOUNS)
        description = rng.choice(
            [f"{noun} {number}", f"{noun}, size {rng.randrange(1, 9)}", f'{noun} "{number}" kit']
        )
        price = f"{rng.randrange(1, 500)}.{rng.choice(['00', '50', '25', '99', '10'])}"
        table.append(
            [f"R{number:06d}", make_sku(rng), barcode, postal, internal, description, price,
             str(rng.randrange(0, 500))]
        )
    return table


# ---------------------------------------------------------------- spreadsheets


def libreoffice_round_trip(header: list[str], rows: list[list[str]]) -> list[list[str]]:
    """Open the data in a real LibreOffice and save it as CSV again."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        raise SystemExit("LibreOffice (soffice) is required to build the libreoffice scenario")
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "in" / "data.csv"
        source.parent.mkdir()
        write_csv(source, header, rows)
        utf8_comma = "44,34,76,1"  # separator ',', quote '"', UTF-8, from row 1
        subprocess.run(
            [soffice, "--headless", f"-env:UserInstallation=file://{folder}/profile",
             f"--infilter=Text - txt - csv (StarCalc):{utf8_comma}",
             "--convert-to", f"csv:Text - txt - csv (StarCalc):{utf8_comma}",
             "--outdir", f"{folder}/out", str(source)],
            check=True, capture_output=True, timeout=600,
        )
        with open(Path(folder) / "out" / "data.csv", newline="", encoding="utf-8") as handle:
            result = list(csv.reader(handle))
    assert result[0] == header and len(result) == len(rows) + 1
    return result[1:]


def excel_model_cell(value: str) -> str:
    """What Excel's General format is documented to do with one typed value."""
    text = value.strip()
    for index, month in enumerate(MONTHS):  # MAR1 -> 1-Mar
        if text.upper().startswith(month) and text[3:].isdigit() and 1 <= int(text[3:]) <= 28:
            return f"{int(text[3:])}-{MONTHS[index].capitalize()}"
    parts = text.split("-")
    if len(parts) == 2 and all(p.isdigit() for p in parts):  # 1-3 -> 3-Jan (US order)
        month, day = int(parts[0]), int(parts[1])
        if 1 <= month <= 12 and 1 <= day <= 28:
            return f"{day}-{MONTHS[month - 1].capitalize()}"
    try:
        number = Decimal(text)
    except ArithmeticError:
        return value
    if not text[0].isdigit() or not number.is_finite():
        return value
    typed_exponent = "E" in text.upper()
    digits = number.as_tuple().digits
    if len(digits) > 15:  # only 15 significant digits are stored
        number = number.quantize(Decimal(1).scaleb(number.adjusted() - 14), rounding="ROUND_DOWN")
    if typed_exponent:
        return f"{number:.2E}"  # Excel shows 1.20E+04
    if number == number.to_integral_value() and number.adjusted() >= 11:
        return f"{number:.5E}"  # 12+ digits: 1.23457E+11
    plain = format(number.normalize(), "f")
    return plain


def excel_model(rows: list[list[str]]) -> list[list[str]]:
    return [[excel_model_cell(value) for value in row] for row in rows]


# ---------------------------------------------------------------- human edits


def apply_human_changes(
    reference: list[list[str]], damaged: list[list[str]], rng: random.Random
) -> tuple[list[list[str]], list[dict[str, str]]]:
    """Apply scripted edits and return (edited rows, truth records)."""
    column = {name: i for i, name in enumerate(HEADER)}
    # Each working record remembers which reference row it truly came from.
    records = [
        {"row": list(row), "source": index, "state": "matched", "intentional": set()}
        for index, row in enumerate(damaged)
    ]

    for record in records:
        row = record["row"]
        if rng.random() < 0.10:
            row[column["price"]] = f"{rng.randrange(1, 500)}.{rng.choice(['49', '95', '75'])}"
        if rng.random() < 0.05:
            row[column["description"]] += " (updated)"
        if rng.random() < 0.03:
            row[column["quantity"]] = str(rng.randrange(500, 900))
        if rng.random() < 0.02:  # the user really changes an identifier
            name = rng.choice(["sku", "barcode", "postal_code"])
            if name == "sku":
                new = f"ZZ-{rng.randrange(1, 9999):04d}-V2"
            elif name == "barcode":
                new = gtin(rng, 13)
            else:
                new = f"{rng.randrange(10000, 99950)}"
            if new != reference[record["source"]][column[name]]:
                row[column[name]] = new
                record["intentional"].add(name)

    records = [r for r in records if rng.random() >= 0.03]  # deleted records

    extra = []
    for record in records:
        chance = rng.random()
        if chance < 0.01:  # row copied and pasted: same key twice
            record["state"] = "duplicate_key"
            copy = {**record, "row": list(record["row"]), "intentional": set(record["intentional"])}
            copy["row"][column["sku"]] = f"COPY-{rng.randrange(1, 9999):04d}"
            copy["intentional"].add("sku")
            extra.append(copy)
        elif chance < 0.015:
            record["state"] = "blank_key"
            record["row"][column["record_id"]] = ""
    records += extra

    # New records typed in the spreadsheet: some of their identifiers are
    # already damaged, and there is nothing to restore them from.
    first_new = len(reference) + 1
    for number in range(first_new, first_new + max(1, len(reference) * 3 // 100)):
        barcode = gtin(rng, 13, leading_zeros=rng.choice([0, 1]))
        row = [f"R{number:06d}", f"NEW-{number}", barcode.lstrip("0"),
               f"{rng.randrange(501, 99950)}", str(rng.randrange(10**15, 10**16)) ,
               "New item", "9.99", "1"]
        records.append({"row": row, "source": None, "state": "new", "intentional": set()})

    rng.shuffle(records)

    truth = []
    for position, record in enumerate(records):
        for name in IDENTIFIER_COLUMNS:
            current = record["row"][column[name]]
            original = "" if record["source"] is None else reference[record["source"]][column[name]]
            if record["source"] is None:
                label = "new_record"
            elif name in record["intentional"]:
                label = "intentional_edit"
            elif current == original:
                label = "unchanged"
            else:
                label = "damaged"
            truth.append(
                {"row": str(position), "record_id": record["row"][0], "column": name,
                 "row_state": record["state"], "label": label,
                 "true_original": original, "edited_value": current}
            )
    return [record["row"] for record in records], truth


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20261118)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "data")
    args = parser.parse_args()

    reference = make_reference(args.rows, random.Random(args.seed))
    scenarios = {"libreoffice": libreoffice_round_trip(HEADER, reference),
                 "excel_model": excel_model(reference)}
    for name, damaged in scenarios.items():
        folder = args.out / name
        folder.mkdir(parents=True, exist_ok=True)
        edited, truth = apply_human_changes(reference, damaged, random.Random(args.seed + 1))
        write_csv(folder / "reference.csv", HEADER, reference)
        write_csv(folder / "edited.csv", HEADER, edited)
        with open(folder / "truth.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(truth[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(truth)
        labels = {}
        for item in truth:
            labels[item["label"]] = labels.get(item["label"], 0) + 1
        print(f"{name}: {len(edited)} edited rows, identifier cells by truth label: {labels}")


if __name__ == "__main__":
    main()
