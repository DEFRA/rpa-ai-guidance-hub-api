#!/usr/bin/env python3
"""Fail if a ``with pytest.raises(...)`` block makes more than one call.

    uv run python -m scripts.check_raises ./tests    # run by `uv run task lint`

The block holds the one call under test. Anything else called in it, even inside that
call's arguments, could raise the expected exception itself, and the test would pass
for the wrong reason. Ruff's PT012 finds a second statement, but not a call nested in
the first, such as ``_patched([], _new_case(ref))``. SonarCloud's python:S5778 finds
both, but only once a pull request is raised.
"""

import argparse
import ast
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path


def _is_raises(item: ast.withitem) -> bool:
    expression = item.context_expr
    return (
        isinstance(expression, ast.Call)
        and ast.unparse(expression.func) == "pytest.raises"
    )


def _calls(statements: list[ast.stmt]) -> int:
    return sum(
        isinstance(node, ast.Call)
        for statement in statements
        for node in ast.walk(statement)
    )


def blocks_with_more_than_one_call(source: str) -> Iterator[int]:
    """The line of each ``with pytest.raises(...)`` block in ``source`` whose body
    makes more than one call."""
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.With | ast.AsyncWith)
            and any(_is_raises(item) for item in node.items)
            and _calls(node.body) > 1
        ):
            yield node.lineno


def _python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.rglob("*.py"))
        else:
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="+", type=Path, help="files, or folders to search for *.py"
    )
    args = parser.parse_args(argv)

    found = [
        f"{path}:{line}: this pytest.raises block makes more than one call; "
        "make the others before it\n"
        for path in _python_files(args.paths)
        for line in blocks_with_more_than_one_call(path.read_text())
    ]
    sys.stderr.writelines(found)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
