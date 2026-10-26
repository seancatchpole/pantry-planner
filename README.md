# Freezer & Pantry Meal Planner

FastAPI + SQLite + vanilla-JS single page. Tracks pantry/freezer/fridge inventory,
auto-generates weekly dinner plans from stock + family thumbs feedback, and deducts
ingredients when you mark a meal as cooked.

## Run locally

    pip install -r requirements.txt
    python -m uvicorn app:app --host 0.0.0.0 --port 8017
    # open http://<host>:8017

DB lives at `./data/pantry.db` (override with `PANTRY_DB=/path/to.db`).

## Deploy on Unraid (Docker)

Build the image once (or build on Unraid directly):

    docker build -t pantry-planner .

Run with a persistent appdata volume so the DB survives container rebuilds:

    docker run -d --name pantry-planner \
      -p 8017:8000 \
      -v /mnt/user/appdata/pantry-planner:/data \
      --restart unless-stopped \
      pantry-planner

Unraid UI: Docker > Add New Container > template from this image, Web UI port 8017,
map container path `/data` to `/mnt/user/appdata/pantry-planner`.

To carry the prototype DB over, just copy `data/pantry.db` into the appdata folder.

## API cheat sheet (used by the Hermes agent)

- `POST /api/receipt` — `{source, items:[{name, qty, unit, location}]}` (agent fills this from receipt photos)
- `GET/POST /api/inventory`, `PATCH/DELETE /api/inventory/{id}`
- `POST /api/plan/generate?week_start=YYYY-MM-DD` — fills missing dinners (Mon–Thu: weeknight recipes, protein ≥ 30g; Fri–Sun: weekend). Scores: 👍−👎 ×3 + pantry coverage ×2 − recently-cooked penalty
- `POST /api/plan/{id}/cook` — deducts ingredients, logs consumption, returns shortages
- `POST /api/feedback` — `{recipe_id, thumbs: 1|-1, note}`
- `POST /api/recipes` — add new recipes (agent generates these when the rotation gets stale)

## Notes

- `protein_per_serving` values in `seed_data.py` are estimates; tune via UI/API.
- Recipes measure in cooking units (tbsp/cloves), receipts track purchase units
  (bottle/head/oz) — shortages reported at cook time double as shopping hints.
- Pantry staples (oil, spices) are assumed; add them to inventory to track properly.
