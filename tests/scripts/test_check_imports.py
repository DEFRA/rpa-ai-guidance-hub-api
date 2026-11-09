"""Finding an import of a name rather than a module, so that every name in the code
says which module it comes from (AICE Python style guide 2.1.2)."""

import textwrap

import check_imports


def _found(source: str) -> list[tuple[int, str]]:
    return list(check_imports.names_imported(textwrap.dedent(source)))


class TestNamesImported:
    def test_a_module_is_allowed(self):
        assert not _found("""
            import logging
            import importlib.util
            from unittest import mock
        """)

    def test_a_name_from_a_module_is_found(self):
        assert _found("""
            import logging
            from logging import getLogger
        """) == [(3, "imports getLogger from logging")]

    def test_each_name_is_found(self):
        assert _found("""
            from unittest.mock import AsyncMock, MagicMock
        """) == [
            (2, "imports AsyncMock from unittest.mock"),
            (2, "imports MagicMock from unittest.mock"),
        ]

    def test_a_module_and_a_name_from_one_package_are_told_apart(self):
        assert _found("""
            from importlib import machinery, import_module
        """) == [(2, "imports import_module from importlib")]

    def test_types_from_typing_and_collections_abc_are_allowed(self):
        assert not _found("""
            from __future__ import annotations
            from collections.abc import Iterator
            from typing import Any
        """)

    def test_a_relative_import_is_found(self):
        assert _found("""
            from . import sibling
        """) == [(2, "imports relatively")]

    def test_a_module_that_cannot_be_found_is_reported(self):
        assert _found("""
            from no_such_package import anything
        """) == [(2, "imports from no_such_package, which cannot be found")]

    def test_an_import_inside_a_function_is_found(self):
        assert _found("""
            def handler():
                from logging import getLogger
        """) == [(3, "imports getLogger from logging")]


class TestMain:
    def test_a_folder_with_a_name_import_fails_naming_the_file_line_and_name(
        self, tmp_path, capsys
    ):
        (tmp_path / "one.py").write_text("from logging import getLogger\n")

        failed = check_imports.main([str(tmp_path)])

        assert failed == 1
        assert (
            f"{tmp_path / 'one.py'}:1: imports getLogger from logging"
            in capsys.readouterr().err
        )

    def test_a_folder_of_module_imports_passes(self, tmp_path):
        (tmp_path / "one.py").write_text("import logging\n")

        assert check_imports.main([str(tmp_path)]) == 0
