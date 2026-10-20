"""Display text for the reference keys a document's metadata holds.

Metadata is stored, and filtered on, by key. A reader wants the label, and the labels
are already here in config, so responses carry them rather than leaving every caller
to fetch the reference lists just to translate.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app import config

# The "not scheme-specific" choice is offered beside the configured schemes by the
# front end, and a document stores it as this key, so it has no entry in config.
NO_SCHEME = "none"
NO_SCHEME_LABEL = "Not scheme-specific"


def labelled(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of the metadata with each reference key replaced by its label.

    A key that is not in the reference data is left as it is, so nothing a document
    holds is ever hidden. Anything that is not a list of keys passes through.
    """
    reference = config.get_config()
    options = {
        "schemes": [
            *reference.reference_schemes,
            {"value": NO_SCHEME, "label": NO_SCHEME_LABEL},
        ],
        "systems": reference.reference_systems,
        "audience": reference.reference_audiences,
        "guidanceType": reference.reference_guidance_types,
    }

    result = dict(metadata)
    for field, choices in options.items():
        keys = metadata.get(field)
        if not isinstance(keys, list):
            continue
        names = {choice["value"]: choice["label"] for choice in choices}
        result[field] = [names.get(key, key) for key in keys]
    return result
