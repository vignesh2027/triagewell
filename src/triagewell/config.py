"""Settings, read from the environment and an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Role:
    """One model role. `model` is tried first; `fallback` is used only when the
    gateway says the first name is unknown (for example the routing policy has
    not been created yet). Provider failover itself is Otari's job."""

    name: str
    model: str
    fallback: str | None
    max_tokens: int


@dataclass(frozen=True)
class Settings:
    otari_base_url: str
    otari_api_key: str | None
    sorter: Role
    investigator: Role
    screen: Role | None
    injection_profile: str | None
    injection_mode: str
    mcp_url: str | None
    mcp_server_id: str | None
    mcp_allowed_tools: list[str]
    end_user_budget: str | None
    daily_limit_usd: float
    issue_limit_usd: float
    github_token: str | None
    allow_post: bool
    post_repos: list[str]
    home: Path
    code_execution: bool
    web_fetch: bool
    max_tool_iterations: int
    timeout_s: float = 120.0

    @property
    def outbox(self) -> Path:
        return self.home / "outbox"


def load_settings(env_file: str | os.PathLike[str] | None = None) -> Settings:
    if env_file is not None:
        load_dotenv(env_file, override=False)
    else:
        load_dotenv(Path.cwd() / ".env", override=False)
    env = os.environ

    screen_model = env.get("TRIAGEWELL_SCREEN_MODEL", "meta/llama-guard-4-12b")
    screen = None
    if screen_model and screen_model.lower() != "off":
        screen = Role("screen", screen_model, None, 20)

    profile = env.get("TRIAGEWELL_INJECTION_PROFILE", "prompt-injection")
    mcp_url = env.get("TRIAGEWELL_MCP_URL", "https://mcp.deepwiki.com/mcp")

    return Settings(
        otari_base_url=env.get("OTARI_BASE_URL", "https://api.otari.ai/api/v1").rstrip("/"),
        otari_api_key=env.get("OTARI_API_KEY") or None,
        sorter=Role(
            "sorter",
            env.get("TRIAGEWELL_SORTER_MODEL", "triagewell-sorter"),
            env.get("TRIAGEWELL_SORTER_FALLBACK", "nvidia/nemotron-3.5-lightning-30b-a3b") or None,
            int(env.get("TRIAGEWELL_SORTER_MAX_TOKENS", "600")),
        ),
        investigator=Role(
            "investigator",
            env.get("TRIAGEWELL_INVESTIGATOR_MODEL", "triagewell-investigator"),
            env.get("TRIAGEWELL_INVESTIGATOR_FALLBACK", "openai/gpt-oss-120b") or None,
            int(env.get("TRIAGEWELL_INVESTIGATOR_MAX_TOKENS", "1800")),
        ),
        screen=screen,
        injection_profile=None if profile.lower() == "off" else profile,
        injection_mode=env.get("TRIAGEWELL_INJECTION_MODE", "monitor"),
        mcp_url=None if mcp_url.lower() == "off" else mcp_url,
        mcp_server_id=env.get("TRIAGEWELL_MCP_SERVER_ID") or None,
        mcp_allowed_tools=_list(env.get("TRIAGEWELL_MCP_ALLOWED_TOOLS", "read_wiki_structure,ask_wiki_question")),
        end_user_budget=env.get("TRIAGEWELL_END_USER_BUDGET") or None,
        daily_limit_usd=float(env.get("TRIAGEWELL_DAILY_LIMIT_USD", "0.50")),
        issue_limit_usd=float(env.get("TRIAGEWELL_ISSUE_LIMIT_USD", "0.05")),
        github_token=env.get("GITHUB_TOKEN") or None,
        allow_post=_bool(env.get("TRIAGEWELL_ALLOW_POST")),
        post_repos=_list(env.get("TRIAGEWELL_POST_REPOS")),
        home=Path(env.get("TRIAGEWELL_HOME", ".triagewell")),
        code_execution=_bool(env.get("TRIAGEWELL_CODE_EXECUTION"), default=True),
        web_fetch=_bool(env.get("TRIAGEWELL_WEB_FETCH"), default=True),
        max_tool_iterations=int(env.get("TRIAGEWELL_MAX_TOOL_ITERATIONS", "6")),
        timeout_s=float(env.get("TRIAGEWELL_TIMEOUT_S", "120")),
    )
