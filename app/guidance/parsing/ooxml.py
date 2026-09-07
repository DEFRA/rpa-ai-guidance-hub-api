"""The bits of WordprocessingML that more than one part of the parser reads."""

from __future__ import annotations

from typing import Any

from docx.oxml.ns import qn

W_VAL = "w:val"

# A paragraph's own properties, read by every module that asks anything about the
# paragraph rather than about its runs: its borders, its numbering, its indent.
W_PPR = "w:pPr"


def is_toggle_on(element: Any) -> bool:
    """Whether an OOXML boolean toggle is present and not explicitly disabled.

    Toggle properties are 'on' by their presence alone; they are turned off with
    w:val="false" or w:val="0".
    """
    if element is None:
        return False
    return element.get(qn(W_VAL)) not in ("false", "0")
