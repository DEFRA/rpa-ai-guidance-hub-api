from logging import getLogger

from fastapi import APIRouter

router = APIRouter(prefix="/review")
logger = getLogger(__name__)


# basic endpoint example
@router.get("/assets")
async def root() -> dict[str, bool]:
    logger.info("TEST ENDPOINT")
    return {"ok": True}
