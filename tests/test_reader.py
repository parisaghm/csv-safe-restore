"""Strict reading: keep every value exactly, refuse anything unclear."""

from __future__ import annotations

from pathlib import Path

import pytest

from csv_safe_restore.errors import CsvSafeRestoreError
from csv_safe_restore.reader import parse_delimiter, read_csv


def write(tmp_path: Path, content: str | bytes, name: str = "data.csv") -> Path:
    path = tmp_path / name
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return path


def test_values_stay_strings_exactly_as_written(tmp_path: Path) -> None:
    table = read_csv(write(tmp_path, "id,code,note\n1,00123, padded \n2,1E5,\n"))
    assert table.header == ["id", "code", "note"]
    assert table.rows == [["1", "00123", " padded "], ["2", "1E5", ""]]
    assert table.line_numbers == [2, 3]


def test_utf8_bom_is_detected_and_not_part_of_the_header(tmp_path: Path) -> None:
    table = read_csv(write(tmp_path, b"\xef\xbb\xbfid,name\n1,Caf\xc3\xa9\n"))
    assert table.has_bom is True
    assert table.header == ["id", "name"]
    assert table.rows == [["1", "Café"]]


def test_quotes_commas_and_embedded_newlines(tmp_path: Path) -> None:
    text = 'id,description\r\n1,"Widget, small"\r\n2,"line one\nline two"\r\n3,"say ""hi"""\r\n'
    table = read_csv(write(tmp_path, text))
    assert table.rows == [["1", "Widget, small"], ["2", "line one\nline two"], ["3", 'say "hi"']]
    assert table.newline == "\r\n"
    # Line numbers point at where each record starts, even after a multi-line record.
    assert table.line_numbers == [2, 3, 5]


@pytest.mark.parametrize(("ending", "expected"), [("\n", "\n"), ("\r\n", "\r\n"), ("\r", "\r")])
def test_newline_styles(tmp_path: Path, ending: str, expected: str) -> None:
    table = read_csv(write(tmp_path, ending.join(["id,code", "1,001", "2,002", ""])))
    assert table.rows == [["1", "001"], ["2", "002"]]
    assert table.newline == expected


def test_line_breaks_inside_quoted_fields_do_not_decide_the_newline_style(tmp_path: Path) -> None:
    table = read_csv(write(tmp_path, '"a\rb",c\n"x\r\ny","z\r"\n'))
    assert table.header == ["a\rb", "c"]
    assert table.newline == "\n"


def test_file_without_final_newline(tmp_path: Path) -> None:
    assert read_csv(write(tmp_path, "id,code\n1,001")).rows == [["1", "001"]]


def test_header_only_file_is_valid(tmp_path: Path) -> None:
    assert read_csv(write(tmp_path, "id,code\n")).rows == []


def test_other_delimiters(tmp_path: Path) -> None:
    table = read_csv(write(tmp_path, "id;code\n1;00,5\n"), delimiter=";")
    assert table.rows == [["1", "00,5"]]
    assert parse_delimiter("tab") == "\t"
    assert parse_delimiter(";") == ";"
    with pytest.raises(CsvSafeRestoreError):
        parse_delimiter(";;")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "empty"),
        (b"\n\n", "empty"),
        (b"id,code,id\n1,2,3\n", "duplicate column name"),
        (b"id,,\n1,2,3\n", "duplicate column name"),  # two unnamed columns
        (b"id,code\n1,001,extra\n", "line 2 has 3 field(s)"),
        (b"id,code\n1\n", "line 2 has 1 field(s)"),
        (b"id,code\n1,001\n\n2,002\n", "line 3 is blank"),
        (b'id,code\n1,"unclosed\n', "malformed CSV"),
        (b'id,code\n1,"bad"quote\n', "malformed CSV"),
        (b"id,name\n1,Caf\xe9\n", "not valid UTF-8"),  # Latin-1 / Windows-1252
        (b"\xff\xfei\x00d\x00\n\x00", "UTF-16"),
        (b"id,code\n1,a\x00b\n", "NUL"),
        (b"id;code\n1;001\n", "--delimiter semicolon"),
    ],
)
def test_unclear_input_is_refused_with_a_clear_message(
    tmp_path: Path, content: bytes, message: str
) -> None:
    with pytest.raises(CsvSafeRestoreError) as error:
        read_csv(write(tmp_path, content))
    assert message in str(error.value)


def test_missing_file_and_directory(tmp_path: Path) -> None:
    with pytest.raises(CsvSafeRestoreError, match="File not found"):
        read_csv(tmp_path / "nope.csv")
    with pytest.raises(CsvSafeRestoreError, match="directory"):
        read_csv(tmp_path)
