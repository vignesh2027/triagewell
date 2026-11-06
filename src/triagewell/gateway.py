"""A small client for the Otari gateway.

Otari speaks the OpenAI Chat Completions dialect, so any OpenAI SDK works. This
client uses httpx directly because Triagewell reads things the SDKs hide: the
`otari_tool_calls` field (what the gateway's own fetch, sandbox and MCP calls
really returned), and the routing, guardrail, cost and budget headers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class GatewayResult:
    content: str
    model_requested: str
    provider: str | None = None
    model_served: str | None = None
    fallback: bool = False
    attempt_count: int = 1
    cost_usd: float = 0.0
    request_id: str | None = None
    guardrails: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    end_user_budget: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    duration_ms: int | None = None

    @property
    def guardrail_flagged(self) -> bool:
        return any(entry.get("valid") is False for entry in self.guardrails)

    def summary(self) -> dict[str, Any]:
        return {
            "requested": self.model_requested,
            "provider": self.provider,
            "model": self.model_served,
            "fallback": self.fallback,
            "attempts": self.attempt_count,
            "cost_usd": round(self.cost_usd, 6),
            "request_id": self.request_id,
            "duration_ms": self.duration_ms,
        }


@dataclass
class CodeRun:
    stdout: str
    stderr: str
    return_code: int
    cost_usd: float = 0.0
    request_id: str | None = None


class GatewayError(Exception):
    def __init__(self, status: int, code: str | None, detail: str, headers: httpx.Headers | None = None):
        super().__init__(f"{status} {code or ''} {detail}".strip())
        self.status = status
        self.code = code
        self.detail = detail
        self.headers = headers or httpx.Headers()


class BudgetExceeded(GatewayError):
    @property
    def scope(self) -> str | None:
        return self.headers.get("Otari-Budget-Scope")

    @property
    def retry_after(self) -> int | None:
        value = self.headers.get("Retry-After")
        return int(value) if value and value.isdigit() else None


class GuardrailBlocked(GatewayError):
    pass


class UnknownModel(GatewayError):
    pass


class UserMismatch(GatewayError):
    """An ordinary (non-service) key sent a `user` that is not its own owner."""


_USER_MISMATCH = "does not match the authenticated API key's user"


def _error_from_response(response: httpx.Response) -> GatewayError:
    code = response.headers.get("Otari-Error-Code")
    detail = response.text
    if detail.lstrip().startswith("<"):
        # An HTML error page from the CDN in front of the gateway, not Otari itself.
        detail = f"HTTP {response.status_code} from the edge in front of the gateway (no JSON body)"
    try:
        body = response.json()
        if isinstance(body, dict):
            code = code or body.get("code")
            raw = body.get("detail", body)
            detail = raw if isinstance(raw, str) else json.dumps(raw)
    except ValueError:
        pass
    if code == "budget_exceeded":
        cls: type[GatewayError] = BudgetExceeded
    elif code == "guardrail_violation":
        cls = GuardrailBlocked
    elif code in {"invalid_model", "model_not_found"}:
        cls = UnknownModel
    elif response.status_code == 403 and _USER_MISMATCH in detail:
        cls = UserMismatch
    else:
        cls = GatewayError
    return cls(response.status_code, code, detail, response.headers)


def _parse_guardrails(header: str | None) -> list[dict[str, Any]]:
    if not header:
        return []
    try:
        value = json.loads(header)
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _cost(usage: dict[str, Any]) -> float:
    try:
        return float(usage.get("cost_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


class OtariClient:
    """Thin wrapper over the gateway's REST API."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        *,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("OTARI_API_KEY is not set. Create a key on otari.ai and put it in .env.")
        self.base_url = base_url.rstrip("/")
        self._service_key: bool | None = None
        self._tool_availability: dict[str, bool] | None = None
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": "triagewell/0.1"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        user: str | None = None,
        end_user_budget: str | None = None,
        max_tokens: int = 800,
        temperature: float | None = 0.1,
        tools: list[dict[str, Any]] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        mcp_server_ids: list[str] | None = None,
        guardrails: list[dict[str, Any]] | None = None,
        max_tool_iterations: int | None = None,
        response_format: dict[str, Any] | None = None,
        metadata: dict[str, str] | None = None,
        session_label: str | None = None,
        reasoning_effort: str | None = None,
    ) -> GatewayResult:
        body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        if user:
            body["user"] = user
        if tools:
            body["tools"] = tools
        if mcp_servers:
            body["mcp_servers"] = mcp_servers
        if mcp_server_ids:
            body["mcp_server_ids"] = mcp_server_ids
        if guardrails:
            body["guardrails"] = guardrails
        if max_tool_iterations:
            body["max_tool_iterations"] = max_tool_iterations
        if response_format:
            body["response_format"] = response_format
        if metadata:
            body["metadata"] = metadata
        if session_label:
            body["session_label"] = session_label[:200]
        headers = {}
        if end_user_budget:
            headers["Otari-End-User-Budget"] = end_user_budget

        response = self._http.post("/chat/completions", json=body, headers=headers)
        if response.status_code >= 400:
            raise _error_from_response(response)
        data = response.json()
        choices = data.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        usage = data.get("usage") or {}
        h = response.headers
        return GatewayResult(
            content=message.get("content") or "",
            model_requested=model,
            provider=h.get("Otari-Provider"),
            model_served=h.get("Otari-Model"),
            fallback=h.get("Otari-Fallback", "false").lower() == "true",
            attempt_count=int(h.get("Otari-Attempt-Count", "1") or 1),
            cost_usd=_cost(usage),
            request_id=h.get("Otari-Request-ID"),
            guardrails=_parse_guardrails(h.get("Otari-Guardrails")),
            tool_calls=list(data.get("otari_tool_calls") or []),
            end_user_budget=h.get("Otari-End-User-Budget"),
            usage=usage,
            duration_ms=int(h["Otari-Response-Duration-Ms"]) if (h.get("Otari-Response-Duration-Ms") or "").isdigit() else None,
        )

    def execute_code(self, code: str, *, user: str | None = None, timeout_seconds: int = 60) -> CodeRun:
        body: dict[str, Any] = {"code": code, "timeout_seconds": timeout_seconds}
        if user:
            body["user"] = user
        response = self._http.post("/code/execute", json=body)
        if response.status_code >= 400:
            raise _error_from_response(response)
        data = response.json()
        return CodeRun(
            stdout=data.get("stdout") or "",
            stderr=data.get("stderr") or "",
            return_code=int(data.get("return_code") or 0),
            cost_usd=_cost(data.get("usage") or {}),
            request_id=response.headers.get("Otari-Request-ID"),
        )

    def settled_cost(self, request_id: str | None, *, tries: int = 3, wait_s: float = 1.5) -> float | None:
        """The cost Otari settled for one of this key's requests, by its request id.

        `/code/execute` answers without a cost, but the sandbox run is billed, so
        the report reads the settled amount back. Rows are written in the
        background, so a lookup right after the call can 404 for a moment."""
        if not request_id:
            return None
        import time

        for attempt in range(tries):
            response = self._http.get(f"/usage/requests/{request_id}")
            if response.status_code == 200:
                try:
                    return float(response.json().get("cost_usd") or 0.0)
                except (TypeError, ValueError):
                    return None
            if response.status_code != 404:
                return None
            if attempt + 1 < tries:
                time.sleep(wait_s)
        return None

    def tools(self) -> list[dict[str, Any]]:
        response = self._http.get("/tools")
        if response.status_code >= 400:
            raise _error_from_response(response)
        data = response.json()
        if isinstance(data, dict):
            return list(data.get("tools") or data.get("data") or [])
        return list(data)

    def tool_availability(self) -> dict[str, bool]:
        """Which of Otari's own tools this workspace may use, read once per client.

        A request that names a tool the workspace has turned off is refused as a
        whole (403), so callers drop those tools instead of losing the call."""
        if self._tool_availability is None:
            try:
                self._tool_availability = {
                    str(t.get("id") or t.get("name") or t.get("type")): t.get("available") is not False for t in self.tools()
                }
            except (GatewayError, httpx.HTTPError):
                self._tool_availability = {}
        return self._tool_availability

    def is_service_key(self) -> bool:
        """True unless Otari says this key is not a service key, read once per client.

        Only a service key may name other people in `user`; an ordinary key that
        does is refused with 403, so callers leave `user` out for it."""
        if self._service_key is None:
            try:
                response = self._http.get("/end-user-budgets")
                self._service_key = not (response.status_code == 403 and "service key" in response.text)
            except httpx.HTTPError:
                self._service_key = True
        return self._service_key

    def guardrail_profiles(self) -> dict[str, Any]:
        response = self._http.get("/tool-settings/guardrails/profiles")
        if response.status_code >= 400:
            raise _error_from_response(response)
        return response.json()

    def end_user(self, external_id: str) -> dict[str, Any] | None:
        """Read one end user's budget and counters. Needs a service key."""
        response = self._http.get("/end-users", params={"external_id": external_id})
        if response.status_code >= 400:
            raise _error_from_response(response)
        data = response.json()
        items = data if isinstance(data, list) else data.get("data") or data.get("items") or []
        return items[0] if items else None

    def end_user_budgets(self) -> list[dict[str, Any]]:
        response = self._http.get("/end-user-budgets")
        if response.status_code >= 400:
            raise _error_from_response(response)
        data = response.json()
        return data if isinstance(data, list) else list(data.get("data") or data.get("items") or [])
