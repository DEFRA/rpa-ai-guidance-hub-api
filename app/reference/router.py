from fastapi import APIRouter

from app import config as app_config
from app.reference import models

router = APIRouter(prefix="/reference")


@router.get("/schemes")
async def get_schemes() -> list[models.ReferenceOption]:
    """Schemes for the "Which scheme does this guidance relate to?" radios."""
    return app_config.get_config().reference_schemes


@router.get("/audiences")
async def get_audiences() -> list[models.ReferenceOption]:
    """Audience options for the "Who is this for?" checkboxes."""
    return app_config.get_config().reference_audiences


@router.get("/systems")
async def get_systems() -> list[models.ReferenceOption]:
    """System options for the "What systems does this guidance relate to?" checkboxes."""
    return app_config.get_config().reference_systems


@router.get("/guidance-types")
async def get_guidance_types() -> list[models.ReferenceOption]:
    """Guidance type options for the metadata capture journey."""
    return app_config.get_config().reference_guidance_types
