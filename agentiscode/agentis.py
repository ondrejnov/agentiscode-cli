from __future__ import annotations

from typing import Any
from uuid import uuid4

import requests

AUTH_HEADER = "X-Auth-Token"
SERVICE_AUTH_HEADER = "X-Service-Token"
SERVICE_TOKEN_METHODS = frozenset(
    {
        "task.add_agent_comment",
        "task.upload_artifact",
        "task.add_question",
        "task.get_question_result",
        "task.add_approve",
        "task.get_approve_result",
        "session.session_created",
        "session.session_update",
        "session.store_activity_log",
        "session.session_error",
    }
)


class AgentisJsonRpcError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


class AgentisJsonRpcClient:
    def __init__(
        self,
        endpoint: str,
        token: str | None = None,
        service_token: str | None = None,
        timeout: float = 15.0,
        session: requests.Session | None = None,
    ) -> None:
        self.endpoint = self._normalize_endpoint(endpoint)
        self.token = token
        self.service_token = service_token
        self.timeout = timeout
        self.session = session or requests.Session()
        self._owns_session = session is None

    def __enter__(self) -> AgentisJsonRpcClient:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    @staticmethod
    def _normalize_endpoint(endpoint: str) -> str:
        value = endpoint.strip().rstrip("/")
        if not value:
            raise ValueError("endpoint must not be empty")
        if value.endswith("/api"):
            return value
        return f"{value}/api"

    def call(
        self,
        method: str,
        params: Any = None,
        *,
        request_id: Any | None = None,
        timeout: float | None = None,
    ) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id if request_id is not None else f"agentis-{uuid4().hex}",
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        headers = self._auth_headers(method)

        try:
            response = self.session.post(
                self.endpoint,
                json=payload,
                timeout=timeout if timeout is not None else self.timeout,
                headers=headers or None,
            )
        except requests.RequestException as exc:
            raise AgentisJsonRpcError(f"Agentis JSON-RPC request failed: {exc}") from exc

        try:
            body = response.json()
        except ValueError:
            body = response.text

        if response.status_code >= 400:
            raise AgentisJsonRpcError(
                f"Agentis returned HTTP {response.status_code}",
                status_code=response.status_code,
                details=body,
            )

        if not isinstance(body, dict):
            raise AgentisJsonRpcError(
                "Agentis returned an invalid JSON-RPC response",
                status_code=response.status_code,
                details=body,
            )

        error = body.get("error")
        if error is not None:
            message = "Agentis returned a JSON-RPC error"
            if isinstance(error, dict) and error.get("message"):
                message = str(error["message"])
            raise AgentisJsonRpcError(message, status_code=response.status_code, details=error)

        return body.get("result")

    def _auth_headers(self, method: str) -> dict[str, str]:
        if method in SERVICE_TOKEN_METHODS:
            return {SERVICE_AUTH_HEADER: str(self.service_token)} if self.service_token else {}
        return {AUTH_HEADER: str(self.token)} if self.token else {}
