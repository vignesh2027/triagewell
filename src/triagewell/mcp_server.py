"""Triagewell as an MCP server, so a maintainer can triage from their editor.

Run `triagewell mcp` as a stdio server from any MCP client (Claude Desktop,
VS Code, Zed, Cursor). The tools are read-only: they never post to GitHub.
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .config import load_settings
from .render import issue_ref, maintainer_summary
from .service import budget_status, list_samples, triage
from .triage import load_report, load_reports

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def build(env_file: str | None = None) -> MCPServer:
    settings = load_settings(env_file)
    server = MCPServer(
        name="triagewell",
        instructions=(
            "Triagewell triages GitHub issues through the Otari gateway on open-weight models. "
            "It never posts to GitHub; drafted replies are returned for the maintainer to review."
        ),
        version="0.1.0",
    )

    @server.tool(annotations=READ_ONLY)
    def triage_issue(issue: str, maintainer: str) -> str:
        """Triage one GitHub issue. `issue` is OWNER/REPO#NUMBER or an issue URL; `maintainer`
        is who the run is billed to. Returns a Markdown report with a draft reply."""
        return maintainer_summary(triage(settings, ref=issue, maintainer=maintainer))

    @server.tool(annotations=READ_ONLY)
    def triage_sample(sample: str, maintainer: str) -> str:
        """Triage one of the bundled sample issues (see list_samples)."""
        return maintainer_summary(triage(settings, sample=sample, maintainer=maintainer))

    @server.tool(name="list_samples", annotations=ToolAnnotations(readOnlyHint=True))
    def list_samples_tool() -> list[str]:
        """Names of the bundled sample issues."""
        return list_samples()

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def recent_reports(limit: int = 10) -> str:
        """The most recent triage reports, one line each."""
        lines = []
        for r in load_reports(settings.home, limit=max(1, min(limit, 50))):
            issue = r["issue"]
            lines.append(f"{r['id']}  {r['status']:<16} {issue_ref(issue)}  {issue['title'][:70]}  ${r.get('cost_usd', 0):.6f}  ({r['maintainer']})")
        return "\n".join(lines) or "No reports yet."

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def get_report(report_id: str) -> str:
        """A full triage report by id."""
        report = load_report(settings.home, report_id)
        return maintainer_summary(report) if report else f"No report {report_id!r}."

    @server.tool(annotations=READ_ONLY)
    def maintainer_budget(maintainer: str) -> str:
        """What a maintainer has spent today, from Triagewell's ledger and Otari's end-user record."""
        return json.dumps(budget_status(settings, maintainer), indent=2)

    return server


def run(env_file: str | None = None) -> None:
    build(env_file).run("stdio")
