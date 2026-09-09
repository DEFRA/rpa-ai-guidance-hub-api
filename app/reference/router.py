from fastapi import APIRouter

from app import config as app_config
from app.reference import schemas

router = APIRouter(prefix="/reference")


@router.get("/schemes", response_model=list[schemas.ReferenceOption])
async def get_schemes() -> list[dict[str, str]]:
    """Schemes for the "Which scheme does this guidance relate to?" radios."""
    return app_config.get_config().reference_schemes


@router.get("/audiences", response_model=list[schemas.ReferenceOption])
async def get_audiences() -> list[dict[str, str]]:
    """Audience options for the "Who is this for?" checkboxes."""
    return app_config.get_config().reference_audiences


@router.get("/systems", response_model=list[schemas.ReferenceOption])
async def get_systems() -> list[dict[str, str]]:
    """System options for the "What systems does this guidance relate to?" checkboxes."""
    return app_config.get_config().reference_systems


@router.get("/guidance-types", response_model=list[schemas.ReferenceOption])
async def get_guidance_types() -> list[dict[str, str]]:
    """Guidance type options for the metadata capture journey."""
    return app_config.get_config().reference_guidance_types
