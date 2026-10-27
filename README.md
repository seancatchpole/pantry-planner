# Freezer & Pantry Meal Planner

FastAPI + SQLite + vanilla-JS single page. Tracks pantry/freezer/fridge inventory,
auto-generates weekly dinner plans from stock + family thumbs feedback, and deducts
ingredients when you mark a meal as cooked.

## Run locally

    pip install -r requirements.txt
    python -m uvicorn app:app --host 0.0.0.0 --port 8017
    # open http://<host>:8017

DB lives at `./data/pantry.db` (override with `PANTRY_DB=/path/to.db`).

## Deploy on Unraid (Docker, from this repo)

One-time setup in the Unraid web terminal (host port 61400, container port
8080, joined to the `proxynet` network so the reverse proxy can reach it at
`http://pantry-planner:8080`):

    mkdir -p /mnt/user/appdata/pantry-planner
    cd /mnt/user/appdata/pantry-planner
    git clone https://github.com/seancatchpole/pantry-planner src
    cd src && docker build -t pantry-planner .
    docker network ls | grep -q proxynet || docker network create proxynet
    docker run -d --name pantry-planner \
      --network proxynet \
      -p 61400:8080 \
      -v /mnt/user/appdata/pantry-planner/data:/data \
      --restart unless-stopped \
      pantry-planner

Direct UI: `http://192.168.1.2:61400` (or via your reverse proxy pointing at
`pantry-planner:8080`). The SQLite DB lives in
`/mnt/user/appdata/pantry-planner/data/pantry.db` and survives rebuilds.

Upgrade cycle (after the agent pushes changes):

    cd /mnt/user/appdata/pantry-planner/src && git pull
    docker rm -f pantry-planner
    docker build -t pantry-planner . && docker run -d --name pantry-planner \
      --network proxynet -p 61400:8080 \
      -v /mnt/user/appdata/pantry-planner/data:/data \
      --restart unless-stopped pantry-planner

Unraid UI alternative: Docker > Add New Container from the `pantry-planner`
image, Web UI port 61400 (container 8080), extra args `--network proxynet`,
map `/data` to `/mnt/user/appdata/pantry-planner/data`.

## API cheat sheet (used by the Hermes agent)

- `POST /api/receipt` — `{source, items:[{name, qty, unit, location}]}` (agent fills this from receipt photos)
- `GET/POST /api/inventory`, `PATCH/DELETE /api/inventory/{id}`
- `POST /api/plan/generate?week_start=YYYY-MM-DD` — fills missing dinners (Mon–Thu: weeknight recipes, protein ≥ 30g; Fri–Sun: weekend). Scores: 👍−👎 ×3 + pantry coverage ×2 − recently-cooked penalty
- `POST /api/plan/{id}/cook` — deducts ingredients, logs consumption, returns shortages
- `POST /api/feedback` — `{recipe_id, thumbs: 1|-1, note}`
- `POST /api/recipes` — add new recipes (agent generates these when the rotation gets stale)

## ChatGPT MCP (read-only initial integration)

The existing FastAPI container also serves a Streamable HTTP MCP endpoint at
`http://pantry-planner:8080/mcp/` on the Unraid Docker network. It exposes:

- `get_planning_context(week_start)`: stock, recipes with preference scores,
  the previous week's meal statuses, and the target week's saved plan.
- `get_inventory()`: current on-hand inventory.
- `get_weekly_plan(week_start)`: a saved Monday–Sunday plan and cooked statuses.
- `get_shopping_list(week_start)`: shortages for a saved plan.

`week_start` uses `YYYY-MM-DD` (Monday for planning context). All exposed tools
are read-only, so ChatGPT cannot modify inventory or plans through MCP yet.
Existing UI and REST API write operations are unchanged.

For private ChatGPT access, run OpenAI Secure MCP Tunnel from an Unraid host or
container that can reach `http://pantry-planner:8080/mcp/`. Associate the tunnel
with the intended ChatGPT workspace and add it via **ChatGPT Plugins →
Add custom MCP server → Tunnel**. Keep the REST API on the LAN; do not
port-forward it to the public Internet. The tunnel client and ChatGPT plugin
are configured separately and are not included in this repository.

To verify locally, run MCP Inspector and select **Streamable HTTP** with
`http://localhost:61400/mcp/` (for the README's Unraid port mapping).
Confirm the tools are read-only and test a representative week before
connecting the scheduled Wednesday meal-planning task.

The MCP transport checks the HTTP Host header to prevent DNS rebinding.
By default it allows `localhost`, `127.0.0.1`, and the Docker service name
`pantry-planner` (with or without a port). To connect by LAN IP or a
reverse-proxy hostname, set the container environment variable
`MCP_ALLOWED_HOSTS` to a comma-separated list of *all* allowed hosts,
including the defaults you still need. For example:
`MCP_ALLOWED_HOSTS=pantry-planner,pantry-planner:*,192.168.1.2:61400`.

Run regression tests with `python -m pytest` after installing
`pip install -r requirements.txt pytest httpx`.

## Notes

- `protein_per_serving` values in `seed_data.py` are estimates; tune via UI/API.
- Recipes measure in cooking units (tbsp/cloves), receipts track purchase units
  (bottle/head/oz) — shortages reported at cook time double as shopping hints.
- Pantry staples (oil, spices) are assumed; add them to inventory to track properly.
