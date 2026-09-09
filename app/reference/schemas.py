import pydantic


class ReferenceOption(pydantic.BaseModel):
    """A single {value, label} pair for a GOV.UK radios/checkboxes option list."""

    value: str
    label: str
