"""Errors that stop the tool with a clear message (exit code 2)."""


class CsvSafeRestoreError(Exception):
    """A problem with the input files or options that the user must fix."""
