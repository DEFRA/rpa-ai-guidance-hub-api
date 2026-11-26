"""What a link in a guide points at: its kind, and, for a SharePoint item, its
canonical link.

The same document is linked in many forms: through a sharing prefix (`/:w:/r/`), by
its id or by its path, with its spaces as `%20` or `+`, in any case, behind an
Outlook safe link, and with query noise that changes from one copy to the next. Its
canonical link is what is left when all that is evened out: the address in lower
case, encoded one way, with only what identifies the target - a document's id and
name (`sourcedoc`, `file`), a library view's folder or a Document ID (`id`), or nothing
for a path. Two links to one document in the same form have the same canonical link;
a document linked in two forms has two, since only SharePoint itself could say for
sure that two forms are one document, and so each form is remapped on its own.

A link is a **named Word document** when it is marked as Word, or not marked at all,
and names a `.docx` file: by `file=`, by the path in `sourcedoc` or `id`, or by
its own path. Any other SharePoint link - a folder, a page, a sharing link that names
nothing, another kind of file - is another SharePoint item, except a **Document ID
link** (`/_layouts/15/DocIdRedir.aspx?ID=…`), which names a document by the id the
Document ID service gave it, kept when it moves. A link to any other site, or one
whose address cannot be read, is external, and a `file:` link names a network share;
neither is canonicalised.
"""

from __future__ import annotations

import dataclasses
import enum
import urllib.parse


class Kind(enum.StrEnum):
    WORD_DOCUMENT = "word-document"
    DOCUMENT_ID = "document-id"
    SHAREPOINT_OTHER = "sharepoint-other"
    EXTERNAL = "external"
    INTERNAL = "internal"
    NETWORK_SHARE = "network-share"


@dataclasses.dataclass(frozen=True)
class Target:
    """What a link points at. Only a SharePoint item has a canonical link, and only
    a named Word document a file name, as the link gives it."""

    kind: Kind
    canonical_link: str | None = None
    file_name: str | None = None


_SHAREPOINT = ".sharepoint.com"
_WEB = ("http", "https")
_SAFE_LINKS = ".safelinks.protection.outlook.com"

# Of a SharePoint link's query, what identifies the target, in the order kept.
_IDENTIFYING = ("sourcedoc", "file", "id")
# Of those, the ones that may hold the path of the file a link names.
_HOLDING_A_PATH = ("sourcedoc", "id")

# The mark a sharing link puts first on a Word document's path, and the mode that
# opens the target by the path after it; another mode (`t`) is an opaque token.
_WORD = ":w:"
_BY_PATH = "r"

_DOCX = ".docx"
_PAGE = ".aspx"
_DOCUMENT_ID_PAGE = "docidredir.aspx"

_UNREADABLE = urllib.parse.urlsplit("")


def target_of(link: str) -> Target:
    """What `link`, as written in a guide, points at. Only a link reached by http or
    https can be SharePoint's or a safe link: any other with such a host is external."""
    if link.startswith("#"):
        return Target(Kind.INTERNAL)

    parts = _unwrapped(_split(link))
    if parts.scheme.lower() == "file":
        return Target(Kind.NETWORK_SHARE)
    if not _is_web_link_under(parts, _SHAREPOINT):
        return Target(Kind.EXTERNAL)
    return _sharepoint(parts)


def _is_web_link_under(parts: urllib.parse.SplitResult, domain: str) -> bool:
    return parts.scheme.lower() in _WEB and (parts.hostname or "").endswith(domain)


def _unwrapped(parts: urllib.parse.SplitResult) -> urllib.parse.SplitResult:
    """The link an Outlook safe link wraps, or the link itself."""
    if not _is_web_link_under(parts, _SAFE_LINKS):
        return parts
    wrapped = dict(urllib.parse.parse_qsl(parts.query)).get("url")
    return parts if wrapped is None else _split(wrapped)


def _split(link: str) -> urllib.parse.SplitResult:
    """`link`'s parts, or none for an address that cannot be read as a browser reads
    it: a malformed one (`https://[x`), or one with a backslash in its host part,
    which a browser reads as a slash, so that it goes to another host."""
    try:
        parts = urllib.parse.urlsplit(link.strip())
    except ValueError:
        return _UNREADABLE
    return _UNREADABLE if "\\" in parts.netloc else parts


def _sharepoint(parts: urllib.parse.SplitResult) -> Target:
    segments = urllib.parse.unquote(parts.path).split("/")[1:]
    marker = segments[0] if segments and _is_marker(segments[0]) else ""
    by_path = marker and segments[1:2] == [_BY_PATH]
    path = segments[2:] if by_path else segments
    query = _identifying(parts.query)

    canonical = _canonical(parts.hostname or "", path, query)
    if path and path[-1].lower() == _DOCUMENT_ID_PAGE and query.get("id"):
        return Target(Kind.DOCUMENT_ID, canonical)

    named = _file_name(path, query)
    is_word = marker in ("", _WORD) and named is not None and _is_docx(named)
    return Target(
        Kind.WORD_DOCUMENT if is_word else Kind.SHAREPOINT_OTHER,
        canonical,
        named if is_word else None,
    )


def _is_marker(segment: str) -> bool:
    """Whether a path's first segment is a sharing link's mark of what it is."""
    return len(segment) > 2 and segment.startswith(":") and segment.endswith(":")  # noqa: PLR2004 - a colon either side


def _identifying(query: str) -> dict[str, str]:
    """What identifies the target, read from the query: each name's first value,
    running no further than a `?` (a viewer frame runs its target's query into its
    own)."""
    found: dict[str, str] = {}
    for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True):
        name = key.lower()
        if name in _IDENTIFYING and name not in found:
            found[name] = value.partition("?")[0]
    return found


def _file_name(path: list[str], query: dict[str, str]) -> str | None:
    """The file a link names, or the last part of a folder's path: by `file=`, by the
    path in `sourcedoc` or `id`, or by its own path; None for a page or an opaque
    sharing link."""
    if query.get("file"):
        return query["file"]
    for name in _HOLDING_A_PATH:
        if query.get(name, "").startswith("/"):
            return query[name].rsplit("/", 1)[-1]
    last = path[-1] if path else ""
    return last if "." in last and not last.lower().endswith(_PAGE) else None


def _is_docx(name: str) -> bool:
    return name.lower().endswith(_DOCX)


def _canonical(host: str, path: list[str], query: dict[str, str]) -> str:
    """The address in lower case, encoded one way, with only what identifies the
    target."""
    address = (
        f"https://{host}{urllib.parse.quote('/' + '/'.join(path).lower(), safe='/:')}"
    )
    kept = [
        f"{name}={urllib.parse.quote(query[name].lower(), safe='/{}')}"
        for name in _IDENTIFYING
        if name in query
    ]
    return f"{address}?{'&'.join(kept)}" if kept else address
