"""End-to-end tests through the command line, including file safety."""

from __future__ import annotations

import json
import subprocess
import sys

from csv_safe_restore import __version__

from conftest import Workspace, digest

ORIGINAL = (
    "product_id,sku,barcode,description,price\n"
    'P1,00123,0036000291452,"Widget, small",4.50\n'
    "P2,0123,4006381333931,Widget medium,6.00\n"
    'P3,123,5901234123457,"Two\nlines",8.25\n'
    "P4,AB-0042,123456789012,Seal kit,2.10\n"
)
EDITED = (
    "product_id,sku,barcode,description,price\n"
    'P3,123,5.90123E+12,"Two\nlines",8.25\n'
    'P1,123,36000291452,"Widget, small",4.75\n'
    "P2,123,4006381333931,Widget medium (new),6.00\n"
    "P4,AB-0043,1.23457E+11,Seal kit,2.10\n"
    "P5,0500,9780306406157,New item,1.00\n"
)
# Default (strict) mode. P4's barcode is damaged, but P4's sku was changed by
# hand in a way no spreadsheet does, so the row might be a different record:
# its barcode is reported, not restored.
REPAIRED = (
    "product_id,sku,barcode,description,price\n"
    'P3,123,5901234123457,"Two\nlines",8.25\n'
    'P1,00123,0036000291452,"Widget, small",4.75\n'
    "P2,0123,4006381333931,Widget medium (new),6.00\n"
    "P4,AB-0043,1.23457E+11,Seal kit,2.10\n"
    "P5,0500,9780306406157,New item,1.00\n"
)
# With --trust-key the key alone decides, so P4's barcode is restored too.
REPAIRED_TRUSTING_KEY = REPAIRED.replace("P4,AB-0043,1.23457E+11", "P4,AB-0043,123456789012")
OPTIONS = ("--key", "product_id", "--identifier-columns", "sku,barcode")


def files(workspace: Workspace):
    return workspace.write("original.csv", ORIGINAL), workspace.write("edited.csv", EDITED)


def test_version_and_help(workspace: Workspace) -> None:
    code, out, _ = workspace.run("--version")
    assert code == 0 and out.strip() == f"csv-safe-restore {__version__}"
    code, out, _ = workspace.run("--help")
    assert code == 0 and "check" in out and "repair" in out and "exit codes" in out
    code, out, _ = workspace.run("repair", "--help")
    assert code == 0 and "--output" in out


def test_installed_as_a_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "csv_safe_restore", "--version"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0 and __version__ in result.stdout


def test_check_reports_and_changes_nothing(workspace: Workspace) -> None:
    original, edited = files(workspace)
    before = sorted(p.name for p in workspace.folder.iterdir())
    hashes = digest(original), digest(edited)

    code, out, err = workspace.run("check", original, edited, *OPTIONS)

    assert code == 1 and err == ""
    assert "CONFIRMED DAMAGE" in out and "'123' -> '00123'" in out
    assert "NEEDS REVIEW" in out and "'AB-0043'" in out
    assert "NEW RECORDS" in out and "'P5'" in out
    assert "price: 1" in out
    assert (digest(original), digest(edited)) == hashes
    assert sorted(p.name for p in workspace.folder.iterdir()) == before


def test_check_exit_code_zero_when_clean(workspace: Workspace) -> None:
    original = workspace.write("original.csv", ORIGINAL)
    same = workspace.write("same.csv", ORIGINAL)
    code, out, _ = workspace.run("check", original, same, *OPTIONS)
    assert code == 0 and "No identifier damage found in 4 matched record(s)." in out


def test_new_and_deleted_records_alone_do_not_fail_the_check(workspace: Workspace) -> None:
    original = workspace.write("original.csv", ORIGINAL)
    lines = ORIGINAL.splitlines(keepends=True)
    edited = workspace.write("edited.csv", "".join(lines[:2]) + "P9,0900,4006381333931,New,1.00\n")
    code, out, _ = workspace.run("check", original, edited, *OPTIONS)
    assert code == 0
    assert "NEW RECORDS" in out and "DELETED RECORDS" in out


def test_nothing_matched_is_never_reported_as_clean(workspace: Workspace) -> None:
    original = workspace.write("original.csv", ORIGINAL)
    other = workspace.write("other.csv", ORIGINAL.replace("\nP", "\nQ"))
    code, out, _ = workspace.run("check", original, other, *OPTIONS)
    assert code == 1
    assert "No records could be matched by key, so nothing was checked" in out
    output = workspace.folder / "out.csv"
    code, out, _ = workspace.run("repair", original, other, *OPTIONS, "--output", output)
    assert code == 1 and "nothing was checked" in out


def test_renamed_identifier_column_is_pointed_out(workspace: Workspace) -> None:
    original = workspace.write("original.csv", ORIGINAL)
    edited = workspace.write("edited.csv", EDITED.replace("sku,", "SKU code,", 1))
    code, out, _ = workspace.run("check", original, edited, "--key", "product_id")
    assert "'sku' looks like an identifier column" in out and "Was it renamed?" in out


def test_closed_pipe_gives_an_error_not_a_traceback(workspace: Workspace) -> None:
    original, edited = files(workspace)
    command = [sys.executable, "-m", "csv_safe_restore", "check", str(original), str(edited)]
    # /dev/full refuses every write, like a full disk (Linux only).
    sink_path = "/dev/full" if sys.platform == "linux" else workspace.folder / "x"
    with open(sink_path, "w", encoding="utf-8") as sink:
        result = subprocess.run(
            [*command, *OPTIONS],
            stdout=sink,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )
    if sys.platform == "linux":
        assert result.returncode == 2
        assert "could not write the report" in result.stderr
    assert "Traceback" not in result.stderr


def test_repair_writes_only_confirmed_repairs(workspace: Workspace) -> None:
    original, edited = files(workspace)
    hashes = digest(original), digest(edited)
    output = workspace.folder / "repaired.csv"

    code, out, err = workspace.run("repair", original, edited, *OPTIONS, "--output", output)

    assert err == ""
    assert code == 1  # the intentional edit AB-0043 still needs a human
    assert output.read_text(encoding="utf-8") == REPAIRED
    assert "REPAIRED" in out and "4 restored value(s)" in out
    assert "Mode               : strict (default" in out
    assert (digest(original), digest(edited)) == hashes
    # No temporary files are left behind.
    assert sorted(p.name for p in workspace.folder.iterdir()) == [
        "edited.csv",
        "original.csv",
        "repaired.csv",
    ]


def test_trust_key_is_an_explicit_opt_in(workspace: Workspace) -> None:
    original, edited = files(workspace)
    output = workspace.folder / "repaired.csv"
    code, out, err = workspace.run(
        "repair", original, edited, *OPTIONS, "--trust-key", "--output", output
    )
    assert err == "" and code == 1
    assert output.read_text(encoding="utf-8") == REPAIRED_TRUSTING_KEY
    assert "5 restored value(s)" in out
    assert "Mode               : --trust-key" in out
    report = json.loads(
        workspace.run("check", original, edited, *OPTIONS, "--trust-key", "--format", "json")[1]
    )
    assert report["strict"] is False


def test_strict_is_the_default_and_can_be_named(workspace: Workspace) -> None:
    original, edited = files(workspace)
    default = workspace.run("check", original, edited, *OPTIONS, "--format", "json")
    named = workspace.run("check", original, edited, *OPTIONS, "--strict", "--format", "json")
    assert default == named
    assert json.loads(default[1])["strict"] is True


def test_strict_and_trust_key_cannot_be_combined(workspace: Workspace) -> None:
    original, edited = files(workspace)
    code, _, err = workspace.run("check", original, edited, *OPTIONS, "--strict", "--trust-key")
    assert code == 2 and "not allowed with argument" in err


def test_repair_exit_code_zero_when_everything_was_resolved(workspace: Workspace) -> None:
    original = workspace.write("original.csv", ORIGINAL)
    edited = workspace.write("edited.csv", ORIGINAL.replace("00123", "123"))
    output = workspace.folder / "repaired.csv"
    code, _, _ = workspace.run("repair", original, edited, *OPTIONS, "--output", output)
    assert code == 0
    assert output.read_text(encoding="utf-8") == ORIGINAL


def test_repair_is_repeatable_and_idempotent(workspace: Workspace) -> None:
    original, edited = files(workspace)
    first, second, third = (workspace.folder / f"r{i}.csv" for i in (1, 2, 3))
    workspace.run("repair", original, edited, *OPTIONS, "--output", first)
    workspace.run("repair", original, edited, *OPTIONS, "--output", second)
    assert first.read_bytes() == second.read_bytes()
    # Repairing the repaired file changes nothing more.
    workspace.run("repair", original, first, *OPTIONS, "--output", third)
    assert third.read_bytes() == first.read_bytes()
    report = json.loads(workspace.run("check", original, first, *OPTIONS, "--format", "json")[1])
    assert report["summary"]["confirmed_damage"] == 0


def test_output_is_never_overwritten_without_permission(workspace: Workspace) -> None:
    original, edited = files(workspace)
    output = workspace.write("repaired.csv", "precious\n")

    code, _, err = workspace.run("repair", original, edited, *OPTIONS, "--output", output)
    assert code == 2 and "already exists" in err and "--overwrite" in err
    assert output.read_text(encoding="utf-8") == "precious\n"

    code, _, _ = workspace.run(
        "repair", original, edited, *OPTIONS, "--output", output, "--overwrite"
    )
    assert code == 1 and output.read_text(encoding="utf-8") == REPAIRED


def test_input_files_can_never_be_the_output(workspace: Workspace) -> None:
    original, edited = files(workspace)
    hashes = digest(original), digest(edited)
    link = workspace.folder / "link.csv"
    link.symlink_to(edited)
    for target in (original, edited, link, workspace.folder / "." / "edited.csv"):
        for flag in ("--output", "--report"):
            code, _, err = workspace.run(
                "repair", original, edited, *OPTIONS, flag, target, "--overwrite",
                *(() if flag == "--output" else ("--output", workspace.folder / "out.csv")),
            )
            assert code == 2 and "input files" in err
    assert (digest(original), digest(edited)) == hashes
    assert not (workspace.folder / "out.csv").exists()


def test_errors_leave_no_output_behind(workspace: Workspace) -> None:
    original = workspace.write("original.csv", ORIGINAL)
    broken = workspace.write("edited.csv", 'product_id,sku,barcode\nP1,"oops\n')
    output = workspace.folder / "repaired.csv"
    code, out, err = workspace.run("repair", original, broken, *OPTIONS, "--output", output)
    assert code == 2 and out == "" and "malformed CSV" in err
    assert not output.exists()
    assert sorted(p.name for p in workspace.folder.iterdir()) == ["edited.csv", "original.csv"]


def test_a_directory_as_input_gets_the_same_clear_error_everywhere(workspace: Workspace) -> None:
    # Windows reports reading a directory as "Permission denied"; the message
    # must still say what is wrong, on every platform.
    original, edited = files(workspace)
    folder = workspace.folder / "not-a-file.csv"
    folder.mkdir()
    output = workspace.folder / "repaired.csv"
    for inputs in ((folder, edited), (original, folder)):
        code, out, err = workspace.run("repair", *inputs, *OPTIONS, "--output", output)
        assert code == 2 and out == ""
        assert f"Expected a CSV file but got a directory: {folder}" in err
        assert "Permission denied" not in err
        assert not output.exists()


def test_usage_errors_exit_with_2(workspace: Workspace) -> None:
    original, edited = files(workspace)
    assert workspace.run("check", original, edited)[0] == 2  # --key is required
    assert workspace.run("repair", original, edited, *OPTIONS)[0] == 2  # --output is required
    assert workspace.run("check", original, workspace.folder / "missing.csv", *OPTIONS)[0] == 2
    code, _, err = workspace.run("check", original, edited, "--key", "nope")
    assert code == 2 and "Key column(s) 'nope' not found" in err
    code, _, err = workspace.run(
        "repair", original, edited, *OPTIONS, "--output", workspace.folder / "no" / "dir.csv"
    )
    assert code == 2 and "does not exist" in err


def test_json_report(workspace: Workspace) -> None:
    original, edited = files(workspace)
    code, out, _ = workspace.run(
        "check", original, edited, *OPTIONS, "--gtin-columns", "barcode", "--format", "json"
    )
    report = json.loads(out)
    assert code == report["exit_code"] == 1
    assert report["schema_version"] == 1
    assert report["summary"]["confirmed_damage"] == 4
    assert report["summary"]["damage_not_restored_unconfirmed"] == 1
    assert report["summary"]["new_records"] == 1
    assert report["repairs_applied"] is False
    first = report["confirmed_damage"][0]
    assert first == {
        "category": "confirmed_damage",
        "line": 2,
        "key": {"product_id": "P3"},
        "column": "barcode",
        "reference_value": "5901234123457",
        "edited_value": "5.90123E+12",
        "pattern": "scientific_notation",
        "detail": "rewritten in scientific notation; record confirmed by column 'description'",
    }
    assert [(c["key"]["product_id"], c["category"]) for c in report["review_cells"]] == [
        ("P4", "unexplained_change"),
        ("P4", "uncorroborated"),
    ]


def test_report_file(workspace: Workspace) -> None:
    original, edited = files(workspace)
    report = workspace.folder / "report.json"
    output = workspace.folder / "repaired.csv"
    code, out, _ = workspace.run(
        "repair", original, edited, *OPTIONS, "--output", output, "--report", report,
        "--format", "json",
    )
    assert code == 1 and "Report written to" in out
    assert json.loads(report.read_text(encoding="utf-8"))["repairs_applied"] is True
    # The report file is protected like the output file.
    code, _, err = workspace.run("check", original, edited, *OPTIONS, "--report", report)
    assert code == 2 and "already exists" in err


def test_bom_newlines_and_delimiter_are_preserved(workspace: Workspace) -> None:
    original = workspace.write("original.csv", 'id;code;note\r\n1;00123;café\r\n2;0456;"b;c"\r\n')
    edited = workspace.write(
        "edited.csv", b"\xef\xbb\xbf" + 'id;code;note\r\n2;456;"b;c"\r\n1;123;café\r\n'.encode()
    )
    output = workspace.folder / "repaired.csv"
    code, _, _ = workspace.run(
        "repair", original, edited, "--key", "id", "--delimiter", ";", "--output", output
    )
    assert code == 0
    assert output.read_bytes() == (
        b"\xef\xbb\xbf" + 'id;code;note\r\n2;0456;"b;c"\r\n1;00123;café\r\n'.encode()
    )


def test_duplicate_headers_and_bad_encoding_are_refused(workspace: Workspace) -> None:
    good = workspace.write("good.csv", ORIGINAL)
    duplicate = workspace.write("dup.csv", "product_id,sku,sku\nP1,1,2\n")
    latin = workspace.write("latin.csv", b"product_id,sku\nP1,caf\xe9\n")
    for bad, message in ((duplicate, "duplicate column name"), (latin, "not valid UTF-8")):
        for arguments in ((good, bad), (bad, good)):
            code, _, err = workspace.run("check", *arguments, *OPTIONS)
            assert code == 2 and message in err


def test_long_reports_are_shortened_in_text_only(workspace: Workspace) -> None:
    rows = [f"K{i},{i:05d},name {i}" for i in range(1, 201)]
    original = workspace.write("original.csv", "id,code,name\n" + "\n".join(rows) + "\n")
    edited = workspace.write(
        "edited.csv",
        "id,code,name\n" + "\n".join(f"K{i},{i},name {i}" for i in range(1, 201)) + "\n",
    )
    code, out, _ = workspace.run("check", original, edited, "--key", "id", "--max-details", "5")
    assert code == 1 and "... and 195 more" in out
    out = workspace.run("check", original, edited, "--key", "id", "--format", "json")[1]
    assert len(json.loads(out)["confirmed_damage"]) == 200
