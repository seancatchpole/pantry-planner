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
- `POST /api/recipes` — add a legacy/manual recipe; prefer direct internet imports, then fallback sources. Generated recipes are allowed but less preferred.

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

Run regression tests (including the mounted read-only MCP transport):

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt httpx
python3 -m unittest discover -s tests -v
```

If system Python has no pip, use `uv venv .venv` and
`uv pip install --python .venv/bin/python -r requirements.txt httpx`, then activate.
The global Hermes environment may contain MCP **2.x**; use the project environment
with the declared `mcp>=1.30,<2` requirement rather than changing MCP APIs.
All tests redirect app initialization and writes to disposable databases.
The private-archive integration test skips when its external fixture is unavailable.

## Notes

- `protein_per_serving` values in `seed_data.py` are estimates; tune via UI/API.
- Recipes measure in cooking units (tbsp/cloves), receipts track purchase units
  (bottle/head/oz) — shortages reported at cook time double as shopping hints.
- Pantry staples (oil, spices) are assumed; add them to inventory to track properly.

## Rich recipes and direct imports

The Recipes tab browses 24 cards per page (API caps pages at 60), with name search,
source/tag/category filters and source-rating sorting. Details load on demand;
legacy `/api/recipes` and MCP retain their original list contract. Cards distinguish
**source stars/count** from **family thumbs**. Missing stars are not zero stars.
Details include authors, images, descriptions, raw ingredient lines, ingredient
sections, numbered nested instruction sections, source nutrition/provenance,
yield, durations, embedded reviews/video/tips and publication/modification dates.
Only embedded source information is available; this does not scrape review APIs.
Scraped values are rendered as text, not executable HTML; links/images accept
HTTP(S) only. Image loading contacts the publisher; no embedded video execution.

API examples (use your local development server):

```sh
curl -s http://localhost:8017/api/recipes/import \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://cooking.nytimes.com/recipes/10008-chicken-soup-with-tiny-veal-meatballs","preview":true}'
# Persist explicitly with preview:false. Preview never writes the recipe.
# Alternative payload: {"content":"<script type=...>...</script>","url":"https://...","preview":true}
# content can also be a parsed JSON object/list or a JSON-LD string.
```

The browser saves the **exact previewed JSON-LD**, without refetching it. Imports
upsert by canonical source URL without replacing recipe IDs, family feedback or
plan references. Same-name recipes from different sources get a collision suffix
rather than clobbering the old dish. NYT URLs use HTTPS and drop query/fragment;
other sources retain meaningful query parameters, dropping only common tracking
parameters and fragments. Recipes without a source URL cannot be URL-deduplicated.
Reimport replaces the source-derived recipe data; it retains the existing display
name. `PATCH /api/recipes/{id}` updates only supplied fields and recursively merges
metadata objects; nested arrays are replaced only when explicitly supplied.

- `GET /api/recipes/browse?q=&source=&tag=&category=&sort=rating&offset=0&limit=24`
  returns `{items,total,offset,limit}`; cards omit full raw JSON-LD/steps/ingredients.
- `GET /api/recipes/{id}` returns rich detail; `/api/recipes/options` is a lightweight
  name/ID/kind list for the legacy plan picker.
- Source metadata lives in additive `recipes.metadata` JSON; `source_url` has a
  partial unique index. Original schema columns remain available to planner/MCP.
  Full original Recipe JSON-LD is retained under `metadata.raw_jsonld`, including
  fields not mapped into the display model. Schema migrations are idempotent ALTERs,
  never DROP/recreate, and existing recipes keep their primary keys.
- Any peanut/groundnut/arachis ingredient, including peanut oil and optional peanut
  alternatives, rejects the entire import. No allergen is silently stripped.
  This is a text guard, not an allergen certification or cross-contamination check.
- Conservative quantities support decimals, fractions, Unicode fractions and mixed
  numbers. Package sizes remain raw: `1 (28-ounce) can` means **1 can**, not 28.
  Missing/ambiguous ranges have qty 0 and keep the raw line; the legacy planner
  flags unknown quantities as `some` rather than subtracting fabricated numbers.
- Protein comes from source `nutrition.proteinContent`, not a fabricated estimate;
  source nutrition can itself be inaccurate. Missing protein remains 0 and fails
  the Mon–Thu 30g gate. Known total duration <=45 minutes is weeknight; unknown
  duration is weekend. This is an ease heuristic, not a verified difficulty label.

### Fetch security and blocked sources

Server imports allow public HTTP(S) standard ports only, reject credentials,
resolve/check **every address** and **every redirect target**, and pin the outbound
socket to a vetted IP while retaining HTTPS hostname verification. Environment
proxy settings are not used. Private, loopback, link-local, multicast, reserved
and other non-global targets are blocked. Fetches have a 3 MB body limit, a 20s
network deadline, per-operation timeouts and at most five followed redirects.
OS DNS resolver latency is not itself deadline-interruptible. No cookies, logged-in
browser scraping, paywall bypass, proxy fallback or arbitrary file reading occurs.
HTTP errors or absent Recipe JSON-LD return actionable import errors; paste HTML
or JSON-LD you have permission to use instead. Keep the app LAN-only: imports are
not a substitute for authentication, authorization or rate limiting.

### Archive transfer / bounded direct CLI (no Tandoor)

`import_nyt.py` uses the same shared importer as the API, opens the old crawler
SQLite archive with `mode=ro`, reads only `state='fetched'` rows with JSON-LD,
and imports directly into an **explicit** local target DB. It never imports app.py,
seeds recipes, calls Tandoor, or contacts a network service. Default limit is 100,
batch size 25; no default whole-archive scan. Progress and final report are JSON
lines with added/updated/rejected/errors counts; individual errors include URLs.
Exit status is 1 for parse/database errors (allergen rejections are separately
reported). Dry-run uses an in-memory backup to simulate duplicate upserts without
changing or creating the target. Concurrent imports should be avoided.

```sh
# Read-only source, disposable development target; safe dry-run first
python3 import_nyt.py --archive /home/hermesadmin/nytcooking/nytcooking.db \
  --db ./data/nyt-development.db --limit 100 --offset 0 --dry-run
python3 import_nyt.py --archive /home/hermesadmin/nytcooking/nytcooking.db \
  --db ./data/nyt-development.db --limit 100 --offset 0 --batch-size 25
# Repeat identical command: existing source URLs update in place.
# Advance --offset 100 etc. for explicit bounded pages.
PANTRY_DB=$PWD/data/nyt-development.db python3 -m uvicorn app:app --port 8017
```

Curation flags (recommended for the planner pool): `--min-rating 4.5 --min-count 100`
import only well-rated recipes; non-dinner categories (desserts, drinks, jams,
appetizers…) are skipped unless `--all-categories` is passed. The whole curated
pass over ~18k archived recipes takes ~17 s and adds ~1.9k dinners.

Import into a running Unraid container (archive placed in the data volume):

```sh
docker exec pantry-planner python import_nyt.py \
  --archive /data/nytcooking.db --db /data/pantry.db \
  --limit 20000 --min-rating 4.5 --min-count 100 --batch-size 500
```

For transfer, create a consistent SQLite backup (not a live `cp` of a WAL DB),
then transfer it privately. Example on the archive host:

```sh
sqlite3 /home/hermesadmin/nytcooking/nytcooking.db ".backup '/path/to/private/nyt-snapshot.db'"
scp /path/to/private/nyt-snapshot.db your-development-host:/private/archive/
```

Use a fixed snapshot while paging: the crawler may keep growing, so OFFSET against
a live archive can drift. Keep archives, reports and DBs outside version control
(`data/`, `archive/`, and `*.db*` are ignored). No personal archive content is
bundled with this repository. The real-archive test reads at most 30 external rows,
imports twice into a temporary DB, checks duplicate updates and dry-run immutability.

### Archive audit and migration limitations

The old crawler preserves the first Recipe JSON-LD, not full HTML/Next.js data.
Its prior Tandoor `push_v2` path lost nested HowToSection instructions, truncated
names/descriptions, omitted source aggregateRating and did not carry the full tag
set. The direct importer avoids that conversion. Archive Recipe variants include
single-string ingredients, dict/list instructions, dict/list reviews and videos,
object/list authors/images and protocol-relative URLs. Nested section children can
be either dicts or lists; this importer handles both recursively. A single archived
ingredient string is a single line, never an iterable of characters. Missing HTML-
only tips/reviews cannot be recovered from an archive that never retained them.

### Upgrade safety

Before an operator deploys, stop writes and take a consistent SQLite backup; rebuild
the Docker image using the existing deployment instructions (Dockerfile includes
both importer modules). First app startup performs only additive schema/index
migration and retains plan/feedback/recipe IDs. Review and test locally before
copying data or deploying; **development work does not update the live instance**.
For rollback, restore the backup with the previous image if necessary; never
replace production data with a development import database. A read-only archive
mount can be supplied to a one-off Docker CLI run, but use an explicit disposable
local `/data/...` destination until the import report has been reviewed.
