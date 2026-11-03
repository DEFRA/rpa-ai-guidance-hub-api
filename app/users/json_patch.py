"""JSON Patch (RFC 6902) on a JSON document held in memory: `add`, `remove`, `move`
and `test`, the four a user's own lists are changed by, at any depth, each at the
place its JSON Pointer (RFC 6901) names.

A patch applies whole or not at all: it is applied to a copy, so one that is refused
or fails a `test` part way leaves the document as it was. What the document may hold
once patched is not this module's question; the caller validates the result.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

from app.users import schemas

# A position at the end of a list, for `add`.
_END = "-"


class PatchRefusedError(ValueError):
    """Raised for an operation that cannot apply: a path that is not a JSON Pointer,
    or that names nothing the operation can act on."""


class PatchTestFailedError(LookupError):
    """Raised when a `test` finds another value at its path, or nothing."""


def applied(document: Any, operations: Sequence[schemas.PatchOperation]) -> Any:
    """The document once the patch is applied; `document` itself is left as it is.

    Raises:
        PatchRefusedError: if an operation cannot apply.
        PatchTestFailedError: if a `test` fails.
    """
    patched = copy.deepcopy(document)
    for operation in operations:
        patched = _apply(patched, operation)
    return patched


def _apply(document: Any, operation: schemas.PatchOperation) -> Any:
    """`document` with one operation applied, changed in place where it can be."""
    match operation:
        case schemas.AddOperation(path=path, value=value):
            return _add(document, _tokens(path), copy.deepcopy(value))
        case schemas.RemoveOperation(path=path):
            _remove(document, _tokens(path))
            return document
        case schemas.MoveOperation(from_=from_, path=path):
            return _move(document, _tokens(from_), _tokens(path))
        case schemas.TestOperation(path=path, value=value):
            _test(document, _tokens(path), value)
            return document


def _tokens(pointer: str) -> list[str]:
    """The reference tokens of a JSON Pointer: `/a~1b/0` is `["a/b", "0"]`, and the
    empty pointer, the whole document, is none."""
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        message = f"{pointer!r} is not a JSON Pointer"
        raise PatchRefusedError(message)
    return [_unescaped(token) for token in pointer[1:].split("/")]


def _unescaped(token: str) -> str:
    """A reference token with `~1` read as `/` and `~0` as `~`."""
    parts = token.split("~")
    escapes = {"0": "~", "1": "/"}
    if any(part[:1] not in escapes for part in parts[1:]):
        message = f"{token!r} has a `~` that escapes nothing"
        raise PatchRefusedError(message)
    return parts[0] + "".join(escapes[part[0]] + part[1:] for part in parts[1:])


def _index(part: str, size: int, *, end: bool = False) -> int:
    """The position in a list of `size` that `part` names: `0`, `1` … with no
    leading zeros, below `size` - or at it, for `end`, which `-` names too."""
    if end and part == _END:
        return size
    is_index = part.isdigit() and part.isascii() and (part == "0" or part[0] != "0")
    limit = size + 1 if end else size
    if not is_index or int(part) >= limit:
        message = f"{part!r} is not a position in a list of {size}"
        raise PatchRefusedError(message)
    return int(part)


def _child(container: Any, token: str) -> Any:
    """What `token` names in `container`, which it must name."""
    if isinstance(container, list):
        return container[_index(token, len(container))]
    if isinstance(container, dict) and token in container:
        return container[token]
    message = f"{token!r} names nothing here"
    raise PatchRefusedError(message)


def _resolved(document: Any, tokens: list[str]) -> Any:
    """What the tokens name in `document`."""
    found = document
    for token in tokens:
        found = _child(found, token)
    return found


def _add(document: Any, tokens: list[str], value: Any) -> Any:
    if not tokens:
        return value

    *above, last = tokens
    container = _resolved(document, above)
    if isinstance(container, list):
        container.insert(_index(last, len(container), end=True), value)
    elif isinstance(container, dict):
        container[last] = value
    else:
        message = f"Nothing can be added beneath {last!r}"
        raise PatchRefusedError(message)
    return document


def _remove(document: Any, tokens: list[str]) -> Any:
    """Take out what the tokens name, and answer it."""
    if not tokens:
        message = "The whole document cannot be removed"
        raise PatchRefusedError(message)

    *above, last = tokens
    container = _resolved(document, above)
    if isinstance(container, list):
        return container.pop(_index(last, len(container)))
    if isinstance(container, dict) and last in container:
        return container.pop(last)
    message = f"{last!r} names nothing to remove"
    raise PatchRefusedError(message)


def _move(document: Any, source: list[str], target: list[str]) -> Any:
    if target[: len(source)] == source and target != source:
        message = "Nothing can be moved into itself"
        raise PatchRefusedError(message)
    return _add(document, target, _remove(document, source))


def _test(document: Any, tokens: list[str], value: Any) -> None:
    try:
        found = _resolved(document, tokens)
    except PatchRefusedError as missing:
        raise PatchTestFailedError(str(missing)) from missing
    if not _same(found, value):
        message = f"{'/'.join(tokens)!r} holds something else"
        raise PatchTestFailedError(message)


def _same(one: Any, other: Any) -> bool:
    """Whether two JSON values are equal as JSON sees them: `true` is not `1`, and a
    list or an object is equal member by member."""
    if isinstance(one, bool) or isinstance(other, bool):
        return type(one) is type(other) and one == other
    if isinstance(one, list) and isinstance(other, list):
        return len(one) == len(other) and all(
            _same(a, b) for a, b in zip(one, other, strict=True)
        )
    if isinstance(one, dict) and isinstance(other, dict):
        return one.keys() == other.keys() and all(_same(one[k], other[k]) for k in one)
    if isinstance(one, (list, dict)) or isinstance(other, (list, dict)):
        return False
    return bool(one == other)
