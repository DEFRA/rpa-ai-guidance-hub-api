import fastapi

router = fastapi.APIRouter(include_in_schema=False)


# Do not remove - used for health checks. CDP restarts a container that fails this, so
# it asks only whether this process is alive, whatever else has a problem.
@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/queue")
async def queue_health(
    request: fastapi.Request, response: fastapi.Response
) -> dict[str, str]:
    """Whether uploads can be converted: 503 for as long as the queue has a problem.
    Nothing restarts on this one, so the API can recover by itself."""
    consumer = request.app.state.queue_consumer
    if consumer is None:
        response.status_code = fastapi.status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "no queue configured"}
    if not consumer.healthy:
        response.status_code = fastapi.status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "queue unavailable"}
    return {"status": "ok"}
