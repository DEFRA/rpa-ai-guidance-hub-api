"""A user's pins: guides, and sections of guides, changed by JSON Patch (RFC 6902) on
the ordered list of them, each `{guideId, sectionId, section, heading}`, a whole guide
with no section. `add` pins (at `/0`, the top, as the hub does), `move` reorders,
`remove` unpins, and `test` checks that a position, or a field of the pin there, still
holds what the page showed, so a patch made on a list that has changed since applies
nothing.

Only those four, and `add`, `remove` and `move` only on top-level positions. The patch
is applied by `json_patch` to the pins as answered, and what it makes is then read as
pins are: a section kept by its id, one added by its anchor the section the guide has
there, and each guide or section once. A patch applies whole or not at all: it is
applied here to a copy of the pins as read, and they are then written only if they
are still what was read (`records.patch_pins`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pydantic

from app.users import json_patch, models, schemas

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.guidance import ids as guidance_ids

_PINS = pydantic.TypeAdapter(list[schemas.Pin])


class InvalidPinsError(ValueError):
    """Raised for a patch below a position in the list, or one whose pins would not
    be valid: something that is not a pin, a section the guide does not have, or a
    guide or a section pinned twice."""


def applied(
    pins: Sequence[models.Pin],
    operations: Sequence[schemas.PatchOperation],
    sections: Mapping[guidance_ids.DocumentId, models.GuideSections],
) -> list[models.Pin]:
    """The pins once the patch is applied; `pins` itself is left as it is.
    `sections` has, for the guide of each pinned section and of each section the
    patch pins (`guides_with_sections`), its sections by anchor.

    Raises:
        InvalidPinsError: if the patch reaches below a position in the list, or
            makes something other than pins.
        json_patch.PatchRefusedError: if an operation cannot apply.
        json_patch.PatchTestFailedError: if a `test` fails.
    """
    _refuse_below_a_position(operations)
    answered = [
        schemas.Pin.from_model(pin, sections.get(pin.document_id)).model_dump(
            by_alias=True, mode="json"
        )
        for pin in pins
    ]
    patched = json_patch.applied(answered, operations)
    try:
        kept = [
            pin.to_model(sections.get(pin.guide_id))
            for pin in _PINS.validate_python(patched)
        ]
    except (pydantic.ValidationError, schemas.UnknownSectionError) as invalid:
        raise InvalidPinsError(str(invalid)) from invalid

    places = [(pin.document_id, pin.section_id) for pin in kept]
    if len(set(places)) != len(places):
        message = "A guide, or a section of one, can be pinned only once"
        raise InvalidPinsError(message)
    return kept


def guides_added(
    operations: Sequence[schemas.PatchOperation],
) -> set[guidance_ids.DocumentId]:
    """Every guide an `add` pins, or pins a section of, so the caller can check they
    exist.

    Raises:
        InvalidPinsError: if what an `add` pins is not a pin.
    """
    return {pin.guide_id for pin in _added(operations)}


def guides_with_sections(
    pins: Sequence[models.Pin], operations: Sequence[schemas.PatchOperation]
) -> set[guidance_ids.DocumentId]:
    """The guides whose sections a patch of the pins needs: those of the pinned
    sections, and of each section an `add` pins.

    Raises:
        InvalidPinsError: if what an `add` pins is not a pin.
    """
    pinned = {pin.document_id for pin in pins if pin.section_id is not None}
    added = {
        pin.guide_id
        for pin in _added(operations)
        if pin.section_id is not None or pin.section is not None
    }
    return pinned | added


def _added(operations: Sequence[schemas.PatchOperation]) -> list[schemas.Pin]:
    added = [
        operation.value
        for operation in operations
        if isinstance(operation, schemas.AddOperation)
    ]
    try:
        return _PINS.validate_python(added)
    except pydantic.ValidationError as invalid:
        raise InvalidPinsError(str(invalid)) from invalid


def _refuse_below_a_position(operations: Sequence[schemas.PatchOperation]) -> None:
    """Refuse an operation on anything but a position in the list itself, or, for a
    `test`, a field of the pin there."""
    for operation in operations:
        pointers = [operation.path]
        if isinstance(operation, schemas.MoveOperation):
            pointers.append(operation.from_)
        deepest = 2 if isinstance(operation, schemas.TestOperation) else 1
        if any(not 1 <= pointer.count("/") <= deepest for pointer in pointers):
            message = f"{operation.path!r} is not a position in the pins"
            raise InvalidPinsError(message)
