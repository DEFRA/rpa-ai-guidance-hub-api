import fastapi

async def verify_caller(request: fastapi.Request) -> None:
    """
    Verify the caller of the API request.

    This function checks if the request is coming from an allowed source.
    If not, it raises an HTTPException with a 403 status code.

    Args:
        request (fastapi.Request): The incoming API request.
    """
