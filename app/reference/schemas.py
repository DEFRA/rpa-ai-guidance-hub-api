import pydantic


class ReferenceOption(pydantic.BaseModel):
    value: str
    label: str
