#!/usr/bin/env python3
"""Fail if Python code imports a name rather than a module.

    uv run python -m scripts.check_imports ./app ./tests ./scripts    # run by `uv run task lint`

The AICE Python style guide (2.1.2) says to import modules or packages, never the
classes, functions or variables in them, and never relatively: write
``from app.guidance import ids`` and then ``ids.DocumentId``, so that each name says
where it comes from. Types from ``typing`` and ``collections.abc`` are the exception
(2.3.3). Ruff has no rule for this.

Whether ``from x import y`` names a module is found from the files on the path, without
running ``x`` itself.
"""

import argparse
import ast
import importlib.machinery
import importlib.util
import pathlib
import sys
from collections.abc import Iterable, Iterator

NAMES_ALLOWED_FROM = {"__future__", "collections.abc", "typing"}


def _is_module(package: str, name: str) -> bool:
    """Whether ``name`` is a module in ``package``. Raises ``ModuleNotFoundError``
    if ``package`` cannot be found."""
    spec = importlib.util.find_spec(package)
    if spec is None:
        raise ModuleNotFoundError(package)
    locations = spec.submodule_search_locations
    return (
        locations is not None
        and importlib.machinery.PathFinder.find_spec(
            f"{package}.{name}", list(locations)
        )
        is not None
    )


def _findings(node: ast.ImportFrom) -> Iterator[str]:
    if node.level:
        yield "imports relatively"
        return
    package = node.module or ""
    if package in NAMES_ALLOWED_FROM:
        return
    for alias in node.names:
        try:
            if not _is_module(package, alias.name):
                yield f"imports {alias.name} from {package}"
        except ModuleNotFoundError:
            yield f"imports from {package}, which cannot be found"
            return


def names_imported(source: str) -> Iterator[tuple[int, str]]:
    """The line, and what is wrong, of each import in ``source`` of a name rather
    than a module."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            for finding in _findings(node):
                yield node.lineno, finding


def _python_files(paths: Iterable[pathlib.Path]) -> Iterator[pathlib.Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.rglob("*.py"))
        else:
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="+",
        type=pathlib.Path,
        help="files, or folders to search for *.py",
    )
    args = parser.parse_args(argv)

    found = [
        f"{path}:{line}: {finding}; import the module (AICE Python style guide 2.1.2)\n"
        for path in _python_files(args.paths)
        for line, finding in names_imported(path.read_text())
    ]
    sys.stderr.writelines(found)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
