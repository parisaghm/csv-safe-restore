"""Generate the realistic validation dataset (Phase 2: real-world validation).

Like ``benchmarks/make_dataset.py``, this script does not import
csv_safe_restore: the data and the expected answers are produced independently
of the tool's own rules, so the validation cannot simply agree with itself.

Two fictional business tables are produced:

``products``
    A product master file: one row per item, with a unique product name.
``orders``
    Order lines: customers and products repeat on many lines, so apart from the
    key no column is unique. This is common in real exports.

For each table three files are written:

``reference.csv``
    The clean export (the file the user still has).
``intended.csv``
    The same data after a person's deliberate work: price edits, renamed
    products, new and deleted rows, intentional identifier changes and the
    dangerous cases (renumbered records, swapped keys, duplicated keys, blank
    keys, an intentional ``00123`` -> ``123``). This is what the person meant
    to save. It has NOT been through a spreadsheet yet.
``truth.csv``
    One row per row of ``intended.csv``: which reference record it really came
    from, which scenario it belongs to, and which identifier columns were
    changed on purpose.

``run_libreoffice.py`` then opens ``intended.csv`` in a real LibreOffice and
saves it again; the result is the damaged ``edited.csv``. ``score.py``
compares the tool's repair with ``intended.csv``.

The output is fully determined by ``--seed``: running the script twice gives
byte-identical files.

Usage:  python validation/make_dataset.py [--seed 20261124] [--out validation/data]
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

PRODUCT_HEADER = [
    "item_id", "sku", "ean", "part_number", "hs_code", "supplier_account",
    "product_name", "price", "active", "stock",
]
PRODUCT_IDENTIFIERS = ["sku", "ean", "part_number", "hs_code", "supplier_account", "active"]

ORDER_HEADER = [
    "order_line_id", "order_date", "customer_account", "customer_name", "postal_code",
    "phone", "sku", "ean", "quantity", "unit_price",
]
ORDER_IDENTIFIERS = ["customer_account", "postal_code", "phone", "sku", "ean"]

TABLES = {
    "products": {"key": "item_id", "header": PRODUCT_HEADER, "identifiers": PRODUCT_IDENTIFIERS},
    "orders": {"key": "order_line_id", "header": ORDER_HEADER, "identifiers": ORDER_IDENTIFIERS},
}

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
ADJECTIVES = ["Steel", "Brass", "Compact", "Heavy", "Marine", "Mini", "Pro", "Basic", "Outdoor"]
NOUNS = ["Valve", "Bracket", "Gasket", "Pump", "Sensor", "Cable", "Filter", "Hinge", "Clamp"]
FIRST = ["Aino", "Mika", "Laura", "Omar", "Sofia", "Jonas", "Lea", "Ravi", "Emma", "Noah", "Mei"]
LAST = ["Virtanen", "Korhonen", "Nieminen", "Smith", "Garcia", "Kim", "Novak", "Silva", "Berg"]
COMPANY = ["Oy", "Ltd", "AB", "GmbH", "Inc"]


def gtin(rng: random.Random, length: int, leading_zeros: int = 0) -> str:
    """A valid GTIN (UPC-A = 12, EAN-13 = 13) with the given leading zeros."""
    body = "0" * leading_zeros + "".join(
        rng.choice("123456789") if i == 0 else rng.choice("0123456789")
        for i in range(length - 1 - leading_zeros)
    )
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return body + str((10 - total % 10) % 10)


def barcode(rng: random.Random) -> str:
    if rng.random() < 0.4:
        return gtin(rng, 12, leading_zeros=rng.choice([0, 1, 1]))  # UPC-A
    return gtin(rng, 13, leading_zeros=rng.choice([0, 0, 1, 2]))  # EAN-13


def sku(rng: random.Random) -> str:
    kind = rng.random()
    if kind < 0.55:  # zero-padded numeric, the classic victim
        return str(rng.randrange(1, 99999)).zfill(rng.choice([6, 8]))
    if kind < 0.80:
        return f"{rng.choice('ABCDEFGH')}{rng.choice('KLMNPRST')}-{rng.randrange(1, 9999):04d}"
    return str(rng.randrange(10000, 99999))  # plain number, survives


def part_number(rng: random.Random) -> str:
    kind = rng.random()
    if kind < 0.40:
        return f"PN-{rng.randrange(1000, 9999)}-{rng.choice('ABC')}"
    if kind < 0.55:  # month code, e.g. MAR1
        return f"{rng.choice(MONTHS)}{rng.randrange(1, 28)}"
    if kind < 0.70:  # dash code, e.g. 4-12
        return f"{rng.randrange(1, 12)}-{rng.randrange(1, 28)}"
    if kind < 0.85:  # looks like scientific notation, e.g. 12E3
        return f"{rng.randrange(1, 99)}E{rng.randrange(1, 9)}"
    if kind < 0.92:  # fraction-like code, e.g. 3/8
        return f"{rng.randrange(1, 9)}/{rng.choice([2, 4, 8, 16])}"
    return f"{rng.randrange(1, 999):04d}"  # zero-padded


def hs_code(rng: random.Random) -> str:
    """Customs tariff code: 4 digits, a dot, 2 digits (e.g. 0101.21, 8471.30)."""
    return f"{rng.randrange(1, 97):02d}{rng.randrange(1, 99):02d}.{rng.choice(['10', '20', '30', '00', '21', '90'])}"


def long_account(rng: random.Random) -> str:
    return str(rng.randrange(1, 10)) + "".join(
        rng.choice("0123456789") for _ in range(rng.choice([15, 16, 17]))
    )


def phone(rng: random.Random) -> str:
    """A number from the UK range reserved for fiction (+44 7700 900000-900999),
    so no real person's number is published."""
    return f"+447700900{rng.randrange(0, 1000):03d}"


def price(rng: random.Random) -> str:
    return f"{rng.randrange(1, 900)}.{rng.choice(['00', '50', '95', '10', '25'])}"


# ------------------------------------------------------------------ products


def make_products(rows: int, rng: random.Random) -> list[list[str]]:
    table = []
    for number in range(1, rows + 1):
        name = f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)} {number:05d}"  # unique
        table.append([
            f"ITM-{number:06d}", sku(rng), barcode(rng), part_number(rng), hs_code(rng),
            long_account(rng), name, price(rng), rng.choice(["true", "true", "false"]),
            str(rng.randrange(0, 900)),
        ])
    return table


# -------------------------------------------------------------------- orders


def make_orders(lines: int, rng: random.Random) -> list[list[str]]:
    customers = []
    for _ in range(80):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)} {rng.choice(COMPANY)}"
        postal = (f"{rng.randrange(0, 999):05d}" if rng.random() < 0.5  # Finnish, e.g. 00100
                  else f"{rng.randrange(501, 99950):05d}")  # US ZIP, e.g. 02134
        customers.append((long_account(rng), name, postal, phone(rng)))
    catalogue = [(sku(rng), barcode(rng)) for _ in range(150)]
    table = []
    order = 0
    while len(table) < lines:
        order += 1
        account, name, postal, telephone = rng.choice(customers)
        day = f"2026-{rng.randrange(1, 12):02d}-{rng.randrange(1, 28):02d}"
        for line in range(1, rng.randrange(2, 6)):
            item_sku, item_ean = rng.choice(catalogue)
            table.append([
                f"SO-{order:06d}-{line:02d}", day, account, name, postal, telephone,
                item_sku, item_ean, str(rng.randrange(1, 40)), price(rng),
            ])
    return table[:lines]


# --------------------------------------------------------------- human edits


class Row:
    """One row of the intended file and where it really came from."""

    def __init__(self, values: list[str], source: int | None, scenario: str) -> None:
        self.values = values
        self.source = source  # index into the reference table, None for new rows
        self.scenario = scenario
        self.intentional: set[str] = set()


def rotate(rows: list[Row], group: list[int], columns: list[int], scenario: str) -> None:
    """Move the given columns one step around a group of rows (renumbering)."""
    moved = [[rows[i].values[c] for c in columns] for i in group]
    for position, index in enumerate(group):
        for column, value in zip(columns, moved[(position + 1) % len(group)]):
            rows[index].values[column] = value
        rows[index].scenario = scenario


def intended_products(reference: list[list[str]], rng: random.Random) -> list[Row]:
    col = {name: i for i, name in enumerate(PRODUCT_HEADER)}
    rows = [Row(list(values), index, "normal") for index, values in enumerate(reference)]

    for row in rows:
        v = row.values
        if rng.random() < 0.12:
            v[col["price"]] = price(rng)
        if rng.random() < 0.04:
            v[col["stock"]] = str(rng.randrange(0, 900))
        if rng.random() < 0.03:  # renamed product: the name no longer confirms the record
            v[col["product_name"]] += " (new model)"
            row.scenario = "product_renamed"

    untouched = [i for i, r in enumerate(rows) if r.scenario == "normal"]
    rng.shuffle(untouched)
    take = lambda n: [untouched.pop() for _ in range(n)]  # noqa: E731

    for i in take(30):  # a deliberate, different identifier value
        name = rng.choice(["sku", "ean", "active"])
        new = {"sku": f"ZZ-{rng.randrange(1, 9999):04d}",
               "ean": gtin(rng, 13, leading_zeros=rng.choice([0, 1])),
               "active": "false" if rows[i].values[col["active"]] == "true" else "true"}[name]
        rows[i].values[col[name]] = new
        rows[i].intentional.add(name)
        rows[i].scenario = "intentional_edit"

    looks_like_damage = [i for i in untouched if rows[i].values[col["sku"]].startswith("00")]
    for i in looks_like_damage[:15]:  # someone deliberately removes the zeros
        untouched.remove(i)
        rows[i].values[col["sku"]] = rows[i].values[col["sku"]].lstrip("0")
        rows[i].intentional.add("sku")
        rows[i].scenario = "intentional_edit_like_damage"

    for _ in range(8):  # two records swap keys; everything else stays
        rotate(rows, take(2), [col["item_id"]], "swapped_keys")
    for _ in range(8):  # records renumbered: key moves, name stays with the data
        rotate(rows, take(3), [col["item_id"]], "renumbered_key_only")
    for _ in range(8):  # key AND name moved together: the documented blind spot
        rotate(rows, take(3), [col["item_id"], col["product_name"]], "renumbered_key_and_name")

    for i in take(10):  # a row pasted twice
        rows[i].scenario = "duplicate_key"
        copy = Row(list(rows[i].values), rows[i].source, "duplicate_key")
        rows.append(copy)
    for i in take(10):
        rows[i].values[col["item_id"]] = ""
        rows[i].scenario = "blank_key"
    deleted = set(take(30))
    rows = [r for i, r in enumerate(rows) if i not in deleted]

    for number in range(900001, 900031):  # new items typed in by hand
        rows.append(Row([
            f"ITM-{number:06d}", sku(rng), barcode(rng), part_number(rng), hs_code(rng),
            long_account(rng), f"New item {number}", price(rng), "true", "0",
        ], None, "new_record"))

    rng.shuffle(rows)
    return rows


def intended_orders(reference: list[list[str]], rng: random.Random) -> list[Row]:
    col = {name: i for i, name in enumerate(ORDER_HEADER)}
    rows = [Row(list(values), index, "normal") for index, values in enumerate(reference)]
    for row in rows:
        if rng.random() < 0.10:
            row.values[col["quantity"]] = str(rng.randrange(1, 40))
        if rng.random() < 0.05:
            row.values[col["unit_price"]] = price(rng)

    untouched = list(range(len(rows)))
    rng.shuffle(untouched)
    take = lambda n: [untouched.pop() for _ in range(n)]  # noqa: E731

    for i in take(15):  # the customer moved: a deliberate new postal code
        rows[i].values[col["postal_code"]] = f"{rng.randrange(0, 999):05d}"
        rows[i].intentional.add("postal_code")
        rows[i].scenario = "intentional_edit"
    for _ in range(8):
        rotate(rows, take(2), [col["order_line_id"]], "swapped_keys")
    for i in take(10):
        rows[i].scenario = "duplicate_key"
        rows.append(Row(list(rows[i].values), rows[i].source, "duplicate_key"))
    deleted = set(take(30))
    rows = [r for i, r in enumerate(rows) if i not in deleted]
    for number in range(1, 31):
        account = long_account(rng)
        rows.append(Row([
            f"SO-900000-{number:02d}", "2026-11-20", account, "New customer Oy",
            f"{rng.randrange(0, 999):05d}", phone(rng),
            sku(rng), barcode(rng), "1", price(rng),
        ], None, "new_record"))
    rng.shuffle(rows)
    return rows


# --------------------------------------------------------------------- files


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def write_table(folder: Path, name: str, reference: list[list[str]], rows: list[Row]) -> None:
    spec = TABLES[name]
    folder.mkdir(parents=True, exist_ok=True)
    key = spec["header"].index(spec["key"])
    write_csv(folder / "reference.csv", spec["header"], reference)
    write_csv(folder / "intended.csv", spec["header"], [r.values for r in rows])
    truth = [
        [str(position), r.values[key], "" if r.source is None else reference[r.source][key],
         r.scenario, ";".join(sorted(r.intentional))]
        for position, r in enumerate(rows)
    ]
    write_csv(folder / "truth.csv",
              ["row", "key", "true_source_key", "scenario", "intentional_columns"], truth)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.scenario] = counts.get(r.scenario, 0) + 1
    print(f"{name}: {len(reference)} reference rows, {len(rows)} intended rows; {counts}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=20261124)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "data")
    args = parser.parse_args()

    products = make_products(1500, random.Random(args.seed))
    write_table(args.out / "products", "products", products,
                intended_products(products, random.Random(args.seed + 1)))
    orders = make_orders(1500, random.Random(args.seed + 2))
    write_table(args.out / "orders", "orders", orders,
                intended_orders(orders, random.Random(args.seed + 3)))


if __name__ == "__main__":
    main()
