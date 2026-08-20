from __future__ import annotations

from typing import Any, cast

import pytest

from agentiscode.agentis import AUTH_HEADER, SERVICE_AUTH_HEADER, AgentisJsonRpcClient, AgentisJsonRpcError


class FakeResponse:
    def __init__(self, body: Any, status_code: int = 200, text: str = "") -> None:
        self.body = body
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        if isinstance(self.body, BaseException):
            raise self.body
        return self.body


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
        return self.response


def test_client_posts_normalized_authenticated_request() -> None:
    session = FakeSession(FakeResponse({"jsonrpc": "2.0", "id": "req-1", "result": {"ok": True}}))
    client = AgentisJsonRpcClient("http://agentis.local/", token="secret", timeout=12.0, session=cast(Any, session))

    result = client.call("run.adapter_event", {"kind": "deploy"}, request_id="req-1")

    assert result == {"ok": True}
    assert client.endpoint == "http://agentis.local/api"
    assert session.calls[0]["headers"] == {AUTH_HEADER: "secret"}


def test_client_uses_service_token_only_for_service_methods() -> None:
    session = FakeSession(FakeResponse({"jsonrpc": "2.0", "id": "req-1", "result": {"ok": True}}))
    client = AgentisJsonRpcClient(
        "http://agentis.local/",
        token="user-secret",
        service_token="service-secret",
        session=cast(Any, session),
    )

    client.call("session.store_activity_log", {"session_id": "ses-1"}, request_id="req-1")
    client.call("run.adapter_event", {"kind": "workflow"}, request_id="req-2")

    assert session.calls[0]["headers"] == {SERVICE_AUTH_HEADER: "service-secret"}
    assert session.calls[1]["headers"] == {AUTH_HEADER: "user-secret"}


def test_client_raises_shared_error_for_rpc_error() -> None:
    error = {"code": -32000, "message": "boom"}
    session = FakeSession(FakeResponse({"jsonrpc": "2.0", "id": "req-1", "error": error}))
    client = AgentisJsonRpcClient("http://agentis.local/api", session=cast(Any, session))

    with pytest.raises(AgentisJsonRpcError) as exc_info:
        client.call("run.adapter_event", request_id="req-1")

    assert str(exc_info.value) == "boom"
    assert exc_info.value.status_code == 200
    assert exc_info.value.details == error
