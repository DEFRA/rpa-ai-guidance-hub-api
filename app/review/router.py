import logging

import fastapi

router = fastapi.APIRouter(prefix="/review")
logger = logging.getLogger(__name__)


# basic endpoint example
@router.get("/assets")
async def root() -> dict[str, bool]:
    logger.info("TEST ENDPOINT")
    return {"ok": True}
