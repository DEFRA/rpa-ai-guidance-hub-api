"""A user's pinned guides, changed by JSON Patch (RFC 6902) on the ordered list of
their ids: `add` pins (at `/0`, the top, as the hub does), `move` reorders, `remove`
unpins, and `test` checks a position still holds the guide the page showed there, so
a patch made on a list that has changed since applies nothing.

Only those four, and only on top-level positions. The patch is applied by
`json_patch` to the ids as answered, and what it makes is then read as pins are: guide
ids, each once. A patch applies whole or not at all: it is applied here to a copy of
the list as read, and the list is then written only if it is still the list that was
read (`records.patch_pins`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pydantic

from app.guidance import ids as guidance_ids
from app.users import json_patch, schemas

if TYPE_CHECKING:
    from collections.abc import Sequence

_PINS = pydantic.TypeAdapter(list[guidance_ids.DocumentId])


class InvalidPinsError(ValueError):
    """Raised for a patch below the top of the list, or one whose pins would not be
    valid: something that is not a guide id, or a guide pinned twice."""


def applied(
    pins: Sequence[guidance_ids.DocumentId],
    operations: Sequence[schemas.PatchOperation],
) -> list[guidance_ids.DocumentId]:
    """The pins once the patch is applied; `pins` itself is left as it is.

    Raises:
        InvalidPinsError: if the patch reaches below the top of the list, or makes
            something other than pins.
        json_patch.PatchRefusedError: if an operation cannot apply.
        json_patch.PatchTestFailedError: if a `test` fails.
    """
    _refuse_below_the_top(operations)
    patched = json_patch.applied([str(pin) for pin in pins], operations)
    try:
        kept = _PINS.validate_python(patched)
    except pydantic.ValidationError as invalid:
        raise InvalidPinsError(str(invalid)) from invalid

    if len(set(kept)) != len(kept):
        message = "A guide can be pinned only once"
        raise InvalidPinsError(message)
    return kept


def guides_added(
    operations: Sequence[schemas.PatchOperation],
) -> set[guidance_ids.DocumentId]:
    """Every guide an `add` pins, so the caller can check they exist.

    Raises:
        InvalidPinsError: if what an `add` pins is not a guide id.
    """
    added = [
        operation.value
        for operation in operations
        if isinstance(operation, schemas.AddOperation)
    ]
    try:
        return set(_PINS.validate_python(added))
    except pydantic.ValidationError as invalid:
        raise InvalidPinsError(str(invalid)) from invalid


def _refuse_below_the_top(operations: Sequence[schemas.PatchOperation]) -> None:
    """Refuse an operation on anything but a position in the list itself."""
    for operation in operations:
        pointers = [operation.path]
        if isinstance(operation, schemas.MoveOperation):
            pointers.append(operation.from_)
        if any(pointer.count("/") != 1 for pointer in pointers):
            message = f"{operation.path!r} is not a position in the pins"
            raise InvalidPinsError(message)
