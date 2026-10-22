import pydantic


class ReferenceOption(pydantic.BaseModel):
    """A reference option as returned on the wire.

    `label` is the option's identity: it is what documents store and filters
    match on. `short_label` is display-only (e.g. for chips and table columns) and
    is never stored or filtered by; it is omitted from the response when unset.
    """

    label: str
    short_label: str | None = pydantic.Field(default=None, alias="shortLabel")

    model_config = pydantic.ConfigDict(populate_by_name=True)
