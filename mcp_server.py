"""Read-only Model Context Protocol tools for Pantry Planner."""

import os
from collections.abc import Callable
from datetime import date, timedelta

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)


def make_mcp_server(
    *,
    list_inventory: Callable[[], list[dict]],
    list_recipes: Callable[[], list[dict]],
    get_plan: Callable[[str | None], dict],
    preferences: Callable[[], dict],
    shopping_list: Callable[[str | None], dict],
) -> FastMCP:
    """Bind MCP tools to the app's existing data-access functions."""
    allowed_hosts = [
        host.strip()
        for host in os.getenv(
            "MCP_ALLOWED_HOSTS",
            "localhost,localhost:*,127.0.0.1,127.0.0.1:*,"
            "pantry-planner,pantry-planner:*",
        ).split(",")
        if host.strip()
    ]
    server = FastMCP(
        "pantry-planner",
        stateless_http=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=allowed_hosts,
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
        ),
    )

    @server.tool(annotations=READ_ONLY)
    def get_planning_context(week_start: str) -> dict:
        """Read inventory, recipes, ratings, and plans for the requested week.

        Use before creating a weekly dinner plan. week_start must be a
        Monday in YYYY-MM-DD format. Previous week's meal statuses are
        included to avoid repeating recently planned meals.
        """
        start = date.fromisoformat(week_start)
        if start.weekday() != 0:
            raise ValueError("week_start must be a Monday")
        return {
            "week_start": week_start,
            "inventory": list_inventory(),
            "recipes": list_recipes(),
            "preferences": preferences(),
            "previous_week": get_plan((start - timedelta(days=7)).isoformat()),
            "target_week": get_plan(week_start),
        }

    @server.tool(annotations=READ_ONLY)
    def get_inventory() -> list[dict]:
        """Read current pantry, refrigerator, and freezer stock and quantities."""
        return list_inventory()

    @server.tool(annotations=READ_ONLY)
    def get_weekly_plan(week_start: str) -> dict:
        """Read the saved dinner plan and cooked statuses for a given Monday."""
        return get_plan(week_start)

    @server.tool(annotations=READ_ONLY)
    def get_shopping_list(week_start: str) -> dict:
        """Read ingredients missing from stock for a saved week's dinners."""
        return shopping_list(week_start)

    return server
