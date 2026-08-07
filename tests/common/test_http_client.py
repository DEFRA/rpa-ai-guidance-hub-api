import httpx

from app.common import http_client, tracing


def mock_handler(request):
    request_id = request.headers.get("x-cdp-request-id", "")
    return httpx.Response(200, text=request_id)


def test_trace_id_missing():
    tracing.ctx_trace_id.set("")
    client = httpx.Client(
        event_hooks={"request": [http_client.hook_request_tracing]},
        transport=httpx.MockTransport(mock_handler),
    )
    resp = client.get("http://localhost:1234/test")
    assert resp.text == ""


def test_trace_id_set():
    tracing.ctx_trace_id.set("trace-id-value")
    client = httpx.Client(
        event_hooks={"request": [http_client.hook_request_tracing]},
        transport=httpx.MockTransport(mock_handler),
    )
    resp = client.get("http://localhost:1234/test")
    assert resp.text == "trace-id-value"


async def test_async_trace_id_missing():
    tracing.ctx_trace_id.set("")
    client = httpx.AsyncClient(
        event_hooks={"request": [http_client.async_hook_request_tracing]},
        transport=httpx.MockTransport(mock_handler),
    )
    resp = await client.get("http://localhost:1234/test")
    assert resp.text == ""


async def test_async_trace_id_set():
    tracing.ctx_trace_id.set("async-trace-id-value")
    client = httpx.AsyncClient(
        event_hooks={"request": [http_client.async_hook_request_tracing]},
        transport=httpx.MockTransport(mock_handler),
    )
    resp = await client.get("http://localhost:1234/test")
    assert resp.text == "async-trace-id-value"


def test_create_client_returns_httpx_client():
    client = http_client.create_client()
    assert isinstance(client, httpx.Client)


def test_create_client_default_timeout():
    client = http_client.create_client()
    assert client.timeout.read == 30


def test_create_client_custom_timeout():
    client = http_client.create_client(request_timeout=60)
    assert client.timeout.read == 60


def test_create_client_tracing_hook_registered():
    client = http_client.create_client()
    assert http_client.hook_request_tracing in client.event_hooks["request"]


def test_create_async_client_returns_httpx_async_client():
    client = http_client.create_async_client()
    assert isinstance(client, httpx.AsyncClient)


def test_create_async_client_default_timeout():
    client = http_client.create_async_client()
    assert client.timeout.read == 30


def test_create_async_client_custom_timeout():
    client = http_client.create_async_client(request_timeout=60)
    assert client.timeout.read == 60


def test_create_async_client_tracing_hook_registered():
    client = http_client.create_async_client()
    assert http_client.async_hook_request_tracing in client.event_hooks["request"]
