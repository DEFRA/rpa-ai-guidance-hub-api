"""A user's cases and the sections of guides bookmarked to them, changed by JSON Patch
(RFC 6902) over the whole structure: an ordered list of cases, each
`{key, type, ref, bookmarks}`, each bookmark `{guideId, sectionId, section, heading,
createdAt}` with its section at its anchor, so a `test` can name it by either.

`add`, `remove`, `move` and `test`, at any depth (`json_patch`), so one patch can
bookmark a section to a new case or an existing one, remove a bookmark, or move several
bookmarks or cases at once, and a `test` keeps a patch made on cases that have changed
since from applying. A case left with no bookmarks stays, for the UI to remove in the
same patch.

What the patch makes is then read as the cases are, so a reference that is not one, a
case or a key twice, a section bookmarked twice to one case, or anything the structure
does not hold is refused whichever operation made it. A patch applies whole or not at
all: it is applied here to a copy of the cases as read, and they are written only if
they are still what was read (`records.patch_cases`).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pydantic

from app.guidance import ids as guidance_ids
from app.users import json_patch, models, schemas

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Mapping, Sequence

_CASES = pydantic.TypeAdapter(list[schemas.Case])


class InvalidCasesError(ValueError):
    """Raised for a patch whose cases would not be valid ones."""


def applied(
    read: Sequence[Mapping[str, Any]],
    operations: Sequence[schemas.PatchOperation],
    now: dt.datetime,
    sections: Mapping[guidance_ids.DocumentId, models.GuideSections],
    *,
    sbis_allowed: bool,
) -> list[dict[str, Any]]:
    """The cases, as stored, once the patch is applied to them as answered. A
    bookmark added without when is added `now`. `sections` has, for every guide the
    cases and the patch name, its sections by anchor. A new case of SBI is refused
    unless `sbis_allowed`; one already kept stays.

    Raises:
        InvalidCasesError: if the cases the patch makes are not valid ones.
        json_patch.PatchRefusedError: if an operation cannot apply.
        json_patch.PatchTestFailedError: if a `test` fails.
    """
    before = [models.Case.from_document(case) for case in read]
    answered = [
        schemas.Case.from_model(case, sections).model_dump(by_alias=True, mode="json")
        for case in before
    ]
    patched = json_patch.applied(answered, operations)
    try:
        cases = [
            case.to_model(now, sections) for case in _CASES.validate_python(patched)
        ]
    except (pydantic.ValidationError, schemas.UnknownSectionError) as invalid:
        raise InvalidCasesError(str(invalid)) from invalid

    _refuse_repeats(cases)
    if not sbis_allowed:
        _refuse_new_sbis(cases, {case.key for case in before})
    return [case.to_document() for case in cases]


def guides_in(read: Sequence[Mapping[str, Any]]) -> set[guidance_ids.DocumentId]:
    """Every guide bookmarked to the cases, as stored."""
    return {
        bookmark.document_id
        for case in read
        for bookmark in models.Case.from_document(case).bookmarks
    }


def guides_added(
    operations: Sequence[schemas.PatchOperation],
) -> set[guidance_ids.DocumentId]:
    """Every guide an `add` puts in, at whatever depth, so the caller can check
    they exist.

    Raises:
        InvalidCasesError: if a guide id is not one.
    """
    added = (
        operation.value
        for operation in operations
        if isinstance(operation, schemas.AddOperation)
    )
    return {_guide_id(text) for value in added for text in _guide_ids_in(value)}


def _guide_ids_in(value: Any) -> list[Any]:
    """Every `guideId` in a JSON value, at any depth."""
    found: list[Any] = []
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if "guideId" in current:
                found.append(current["guideId"])
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return found


def _guide_id(text: Any) -> guidance_ids.DocumentId:
    try:
        return guidance_ids.DocumentId(uuid.UUID(str(text)))
    except ValueError as invalid:
        message = f"{text!r} is not a guide id"
        raise InvalidCasesError(message) from invalid


def _refuse_repeats(cases: list[models.Case]) -> None:
    """Refuse a case or a key twice, or the same section of a guide bookmarked twice
    to one case."""
    references = [(case.type, case.ref) for case in cases]
    if len(set(references)) != len(references):
        message = "A case can be there only once"
        raise InvalidCasesError(message)

    keys = [case.key for case in cases]
    if len(set(keys)) != len(keys):
        message = "A case's key can be there only once"
        raise InvalidCasesError(message)

    for case in cases:
        places = [(b.document_id, b.section_id) for b in case.bookmarks]
        if len(set(places)) != len(places):
            message = f"A section of a guide is bookmarked to {case.ref} twice"
            raise InvalidCasesError(message)


def _refuse_new_sbis(cases: list[models.Case], kept: set[Any]) -> None:
    """Refuse a case of SBI that was not kept before the patch."""
    if any(case.type == "sbi" and case.key not in kept for case in cases):
        message = "SBIs cannot be bookmarked to"
        raise InvalidCasesError(message)
