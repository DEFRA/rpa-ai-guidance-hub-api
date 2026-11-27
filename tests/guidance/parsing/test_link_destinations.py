"""How the hub writes a link's destination in Markdown.

All fixture text is invented.
"""

from __future__ import annotations

import pytest

from app.guidance.parsing import link_destinations


class TestALinksDestination:
    @pytest.mark.parametrize(
        "target",
        [
            "https://example.sharepoint.com/Claims/Form.docx",
            "https://example.sharepoint.com/Claims/Form%20(2026).docx",
            "#2-checks",
        ],
    )
    def test_is_written_bare_when_bare_reads_as_itself(self, target):
        assert link_destinations.written(target) == target

    @pytest.mark.parametrize(
        "target",
        [
            "https://example.sharepoint.com/Claims/Claim Form.docx",
            "https://example.sharepoint.com/Claims/Form (2026.docx",
            "https://example.sharepoint.com/Claims/Form 2026).docx",
            "https://example.sharepoint.com/Claims/Form)(2026.docx",
        ],
    )
    def test_is_written_in_angle_brackets_when_bare_would_be_misread(self, target):
        assert link_destinations.written(target) == f"<{target}>"
