"""The pictures a document draws, and the two-step way they are named.

An image reaches the Markdown in two moves, because the two things needed to write it
are known in different places. `inline` meets the picture while rendering a run and
knows only which relationship it points at, so it writes `![](rId7)`. Only the parser
can reach the part that relationship names, and so the bytes the name is taken from -
so it is `resolved` afterwards, against the section, into `![](a3f9....png)`.

Keeping both ends here is the point of the module: the form is written once and read
once, in one file, so the two cannot drift apart. It is the same late binding
`models.py` already does for the image prefix and for cross-references, one step
earlier - and it is what lets `inline`, `lists` and `tables` stay entirely ignorant of
images.

A picture is named by the digest of its own bytes. The name it used to carry said
where it sat - `3.1_img_2.png`, the section and the position within it - which reads
far better and is wrong the moment the document is stored: a picture is immutable and
its address should be too, but inserting a paragraph above one renumbers its section
and so renames a file that has not changed. Anything holding the old address is then
holding a broken one. Naming by content also makes storing idempotent, the same
picture used twice being one object written once, and it is what lets an asset store
keep every past version of a document resolving without versioning anything.

Where a picture belongs is not lost with the old name, it just stops being encoded in
it: the section that owns an image holds it in `section.images`.

The alt text is deliberately empty. Word writes a `wp:docPr/@descr` of its own accord,
describing what it thinks a picture shows, and nothing in the file distinguishes that
from a description a person wrote - so emitting it would publish a machine's guess as
authored text, and to a screen reader the two are indistinguishable. Whoever edits the
guidance can put a real description in; saying nothing is the honest placeholder until
they do.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from docx.oxml.ns import qn

from app.guidance.parsing import models

# What `placeholder` writes and `resolved` reads back. The only `![]()` in a block is
# one this module put there, so the pattern needs no guard against document text: the
# escaping in `inline` means text the author typed cannot produce brackets bare.
_PLACEHOLDER = re.compile(r"!\[\]\(([^)\s]+)\)")

# The generated name, e.g. "a3f9...c1.png": the digest of the picture's bytes and the
# extension of the part it came from. Generated rather than taken from word/media so
# that it cannot collide with anything the document itself says, and identical bytes
# reached by different documents arrive at the same name.
_NAME = "{digest}.{extension}"


def embedded_in(run: Any) -> str:
    """The relationship id of the picture this run draws, or "" for any other run.

    Only the run's own w:drawing children are looked at, never its descendants, and
    that is what keeps a text box out: Word wraps a text box's drawing in an
    mc:AlternateContent, so it is a grandchild and `textboxes` deals with it. A
    drawing holding a shape rather than a picture has no a:blip and is no image here.
    """
    for drawing in run.findall(qn("w:drawing")):
        for blip in drawing.iter(qn("a:blip")):
            embed = blip.get(qn("r:embed"))
            if embed:
                return str(embed)
    return ""


def placeholder(embed: str) -> str:
    """A picture as `inline` can write it, knowing only its relationship."""
    return f"![]({embed})"


def resolved(block: str, section: models.MarkdownSection, part: Any) -> str:
    """Name the pictures in a block, keeping them on the section as they are met.

    Called once per section, on content that has already been filed - so a picture in
    a block ahead of the first heading is dropped with the block, and its bytes are
    never read. `part` is the document part the relationships belong to.
    """

    def named(match: re.Match[str]) -> str:
        image = _extracted(match.group(1), part)
        section.images.append(image)
        return f"![]({image.name})"

    return _PLACEHOLDER.sub(named, block)


def _extracted(embed: str, part: Any) -> models.Image:
    """One picture, named for what it holds and carrying the bytes of its part.

    Takes no section: what a picture is called used to depend on where it sat, and
    now depends on nothing but the bytes behind `embed`.
    """
    source = part.related_parts[embed]
    name = _NAME.format(
        digest=hashlib.sha256(source.blob).hexdigest(),
        extension=source.partname.ext,
    )
    return models.Image(
        name=name, content_type=str(source.content_type), data=source.blob
    )


def name_all(sections: list[models.MarkdownSection], part: Any) -> None:
    """Name the pictures of every section.

    A name depends on nothing but the picture's own bytes, so the order sections are
    walked in does not affect the answer. They are still walked in document order,
    because `section.images` records what a section holds and that list reads better
    the way the page does.
    """
    for section in sections:
        section.content = resolved(section.content, section, part)
