"""A user's cases and the sections of guides bookmarked to them, changed by JSON Patch
(RFC 6902) over the whole structure: an ordered list of cases, each
`{key, type, ref, bookmarks}`, each bookmark `{guideId, sectionId, section, heading,
createdAt}` with its section at its anchor, so a `test` can name it by either.

`add`, `remove`, `move` and `test`, at any depth (`json_patch`), so one patch can
bookmark a section to a new case or an existing one, remove a bookmark, or move several
bookmarks or cases at once, and a `test` keeps a patch made on cases that have changed
since from applying. A case left with no bookmarks stays, for the UI to remove in the
same patch.

What the patch makes is then read as the cases are, whichever operations made it. A
reference that is not one, a case or a key twice, a section bookmarked twice to one
case, a guide there is not, or anything the structure does not hold is refused. A patch
applies whole or not at all: it is applied here to a copy of the cases as read, and they
are written only if they are still what was read (`records.patch_cases`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pydantic

from app.users import json_patch, models, schemas

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from app.guidance import ids as guidance_ids

    type SectionsOf = Callable[
        [set[guidance_ids.DocumentId]],
        Awaitable[Mapping[guidance_ids.DocumentId, models.GuideSections]],
    ]

_CASES = pydantic.TypeAdapter(list[schemas.Case])


class InvalidCasesError(ValueError):
    """Raised for a patch whose cases would not be valid ones."""


async def applied(
    read: Sequence[Mapping[str, Any]],
    operations: Sequence[schemas.PatchOperation],
    now: dt.datetime,
    sections_of: SectionsOf,
    *,
    sbis_allowed: bool,
) -> list[dict[str, Any]]:
    """The cases, as stored, once the patch is applied to them as answered. A
    bookmark added without when is added `now`. `sections_of` gives guides' sections
    by anchor, leaving out a guide with no version: first for the guides the cases
    name, to answer them, then for any other guide the patched cases name, which must
    be one there is. A new case of SBI, or a new reference for one, is refused unless
    `sbis_allowed`; one already kept stays.

    Raises:
        InvalidCasesError: if the cases the patch makes are not valid ones.
        json_patch.PatchRefusedError: if an operation cannot apply.
        json_patch.PatchTestFailedError: if a `test` fails.
    """
    before = [models.Case.from_document(case) for case in read]
    named_before = {b.document_id for case in before for b in case.bookmarks}
    sections = dict(await sections_of(named_before))
    answered = [
        schemas.Case.from_model(case, sections).model_dump(by_alias=True, mode="json")
        for case in before
    ]
    proposed = _validated(json_patch.applied(answered, operations))

    added = {b.guide_id for case in proposed for b in case.bookmarks} - named_before
    sections |= await sections_of(added)
    if not added <= sections.keys():
        message = "A guide to bookmark does not exist"
        raise InvalidCasesError(message)

    try:
        cases = [case.to_model(now, sections) for case in proposed]
    except schemas.UnknownSectionError as unknown:
        raise InvalidCasesError(str(unknown)) from unknown

    _refuse_repeats(cases)
    if not sbis_allowed:
        _refuse_new_sbis(cases, before)
    return [case.to_document() for case in cases]


def _validated(patched: Any) -> list[schemas.Case]:
    try:
        return _CASES.validate_python(patched)
    except pydantic.ValidationError as invalid:
        raise InvalidCasesError(str(invalid)) from invalid


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


def _refuse_new_sbis(cases: list[models.Case], before: list[models.Case]) -> None:
    """Refuse a case of SBI that was not that SBI, under the same key, before the
    patch: a new one, a case made an SBI, or an SBI given another reference."""
    kept = {(case.key, case.ref) for case in before if case.type == "sbi"}
    if any(case.type == "sbi" and (case.key, case.ref) not in kept for case in cases):
        message = "SBIs cannot be bookmarked to"
        raise InvalidCasesError(message)
