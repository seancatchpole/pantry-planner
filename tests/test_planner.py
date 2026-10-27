"""Regression tests for meal IDs and duplicate dinner planning."""

import app as pantry
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pantry, "DB_PATH", tmp_path / "test-pantry.db")
    pantry.init_db()
    # HTTP-only tests do not need the mounted MCP server lifespan.
    return TestClient(pantry.app, base_url="http://localhost:8080")


def test_plan_returns_actual_recipe_id_and_preference(client):
    recipes = client.get("/api/recipes").json()
    recipe_id = recipes[-1]["id"]
    result = client.post(
        "/api/plan", json={"date": "2026-11-02", "recipe_id": recipe_id}
    )
    assert result.status_code == 200
    plan_id = result.json()["added"]

    result = client.post("/api/feedback", json={"recipe_id": recipe_id, "thumbs": 1})
    assert result.status_code == 200

    plan = client.get("/api/plan?week_start=2026-11-02").json()
    assert len(plan["meals"]) == 1
    meal = plan["meals"][0]
    assert meal["plan_id"] == plan_id
    assert meal["recipe"]["id"] == recipe_id
    assert meal["recipe"]["preference"]["ups"] == 1
    assert "plan_id" not in meal["recipe"]


def test_readding_same_dinner_is_idempotent_and_conflicts_are_rejected(client):
    recipes = client.get("/api/recipes").json()
    first_id, second_id = recipes[0]["id"], recipes[1]["id"]
    payload = {"date": "2026-11-03", "recipe_id": first_id}

    first = client.post("/api/plan", json=payload)
    same = client.post("/api/plan", json=payload)
    different = client.post(
        "/api/plan", json={"date": "2026-11-03", "recipe_id": second_id}
    )

    assert first.status_code == 200
    assert same.status_code == 200
    assert same.json() == {"added": first.json()["added"], "existing": True}
    assert different.status_code == 409
    assert len(client.get("/api/plan?week_start=2026-11-02").json()["meals"]) == 1


def test_mcp_planning_context_uses_app_data(client):
    tool = pantry.pantry_mcp._tool_manager.get_tool("get_planning_context")
    context = tool.fn("2026-11-02")
    assert context["week_start"] == "2026-11-02"
    assert isinstance(context["inventory"], list)
    assert isinstance(context["recipes"], list)
    assert context["target_week"]["start"] == "2026-11-02"
    assert context["previous_week"]["start"] == "2026-10-26"


def test_mcp_http_endpoint_initializes(client):
    # The parent FastAPI lifespan must start the mounted MCP session manager.
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }
    with client:
        result = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pantry-test", "version": "1.0"},
                },
            },
            headers=headers,
        )
        tools = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )
        context = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "get_planning_context",
                    "arguments": {"week_start": "2026-11-02"},
                },
            },
            headers=headers,
        )
    assert result.status_code == 200
    assert "pantry-planner" in result.text
    assert tools.status_code == 200
    assert "get_planning_context" in tools.text
    assert "get_inventory" in tools.text
    assert context.status_code == 200
    assert "2026-11-02" in context.text
