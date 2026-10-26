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

One-time setup in the Unraid web terminal:

    mkdir -p /mnt/user/appdata/pantry-planner
    cd /mnt/user/appdata/pantry-planner
    git clone https://github.com/seancatchpole/pantry-planner src
    cd src && docker build -t pantry-planner .
    docker run -d --name pantry-planner \
      -p 8017:8000 \
      -v /mnt/user/appdata/pantry-planner/data:/data \
      --restart unless-stopped \
      pantry-planner

UI at `http://<unraid>:8017`. The SQLite DB lives in
`/mnt/user/appdata/pantry-planner/data/pantry.db` and survives rebuilds.
To carry the prototype DB over, copy `data/pantry.db` into that data folder
before first start.

Upgrade cycle (after the agent pushes changes):

    cd /mnt/user/appdata/pantry-planner/src && git pull
    docker rm -f pantry-planner
    docker build -t pantry-planner . && docker run -d --name pantry-planner \
      -p 8017:8000 -v /mnt/user/appdata/pantry-planner/data:/data \
      --restart unless-stopped pantry-planner

Unraid UI alternative: Docker > Add New Container from the `pantry-planner`
image, Web UI port 8017, map `/data` to `/mnt/user/appdata/pantry-planner/data`.

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
