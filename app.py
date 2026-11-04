"""Freezer & Pantry inventory + weekly dinner planner API."""
import http.client
import json
import os
import random
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).parent
DB_PATH = Path(os.environ.get("PANTRY_DB", BASE / "data" / "pantry.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
MIN_WEEKNIGHT_PROTEIN = float(os.environ.get("MIN_WEEKNIGHT_PROTEIN", 30))
MIN_WEEKEND_PROTEIN = float(os.environ.get("MIN_WEEKEND_PROTEIN", 20))

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Mounted ASGI applications do not run their own lifespan handlers.
    async with pantry_mcp.session_manager.run():
        yield


app = FastAPI(title="Pantry Planner", lifespan=lifespan)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT DEFAULT '',
            location TEXT DEFAULT 'pantry',
            qty REAL DEFAULT 1,
            unit TEXT DEFAULT '',
            source TEXT DEFAULT '',
            added_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('weeknight','weekend')),
            minutes INTEGER DEFAULT 30,
            servings INTEGER DEFAULT 4,
            protein_per_serving REAL DEFAULT 0,
            ingredients TEXT DEFAULT '[]',
            steps TEXT DEFAULT '',
            tags TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id),
            slot TEXT DEFAULT 'dinner',
            status TEXT DEFAULT 'planned',
            cooked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id),
            thumbs INTEGER NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS consumption (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id),
            date TEXT DEFAULT (date('now'))
        );
        """
    )
    from recipe_import import migrate
    migrate(conn)
    if conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0:
        from seed_data import RECIPES
        for r in RECIPES:
            conn.execute(
                "INSERT INTO recipes(name,kind,minutes,servings,protein_per_serving,ingredients,steps,tags) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (r["name"], r["kind"], r["minutes"], r["servings"],
                 r["protein_per_serving"], json.dumps(r["ingredients"]),
                 r["steps"], ",".join(r.get("tags", []))),
            )
    conn.commit()
    conn.close()


init_db()


def norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def similar(a: str, b: str) -> bool:
    a, b = norm(a), norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    # token containment: "chicken thighs" matches "boneless chicken thighs"
    at, bt = set(a.split()), set(b.split())
    return a in b or b in a or (len(at & bt) >= min(len(at), len(bt)) and (at <= bt or bt <= at))


HEADER_RE = re.compile(r"^(for the|finishing|topping|seasoned crumb|to taste|serve with)", re.I)

PROTEIN_BUCKETS = {
    "chicken": ("chicken", "gai pad", "katsu"),
    "beef": ("beef", "steak", "short rib", "brisket", "bolognese", "burger", "chili"),
    "pork": ("pork", "sausage", "kielbasa", "carnitas", "pancetta"),
    "fish": ("salmon", "fish", "tuna", "cod", "whitefish"),
    "shellfish": ("shrimp", "scampi"),
    "turkey": ("turkey",),
    "lamb": ("lamb", "lamb shoulder"),
}


def protein_buckets(rec) -> set:
    blob = (rec["name"] + " " + " ".join(i.get("name", "") for i in rec["ingredients"])).lower()
    return {b for b, words in PROTEIN_BUCKETS.items() if any(w in blob for w in words)}


def is_ing_header(name: str) -> bool:
    """NYT-import artifacts: section headers scraped as ingredients ('For the tacos:')."""
    return bool(HEADER_RE.match(name.strip())) or name.strip().endswith(":")


# ---------------- inventory ----------------

class ItemIn(BaseModel):
    name: str
    qty: float = 1
    unit: str = ""
    location: str = "pantry"   # pantry | freezer | fridge
    category: str = ""
    source: str = ""


class ReceiptIn(BaseModel):
    source: str = "grocery run"
    items: list[ItemIn]


@app.get("/api/inventory")
def list_inventory():
    rows = db().execute("SELECT * FROM inventory ORDER BY location, name").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/inventory")
def add_item(item: ItemIn):
    conn = db()
    for r in conn.execute("SELECT * FROM inventory").fetchall():
        if similar(r["name"], item.name):
            conn.execute(
                "UPDATE inventory SET qty = qty + ?, unit = COALESCE(NULLIF(?, ''), unit), "
                "location = ?, updated_at = datetime('now') WHERE id = ?",
                (item.qty, item.unit, item.location or r["location"], r["id"]))
            conn.commit()
            conn.close()
            return {"merged": r["id"], "name": r["name"]}
    cur = conn.execute(
        "INSERT INTO inventory(name,category,location,qty,unit,source) VALUES(?,?,?,?,?,?)",
        (item.name, item.category, item.location, item.qty, item.unit, item.source))
    conn.commit()
    conn.close()
    return {"added": cur.lastrowid, "name": item.name}


@app.post("/api/receipt")
def add_receipt(rec: ReceiptIn):
    results = [add_item(ItemIn(**{**i.model_dump(), "source": i.source or rec.source})) for i in rec.items]
    return {"added_or_merged": len(results), "detail": results}


@app.patch("/api/inventory/{item_id}")
def patch_item(item_id: int, item: ItemIn):
    conn = db()
    if not conn.execute("SELECT id FROM inventory WHERE id=?", (item_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "not found")
    conn.execute(
        "UPDATE inventory SET name=?, qty=?, unit=?, location=?, category=?, updated_at=datetime('now') WHERE id=?",
        (item.name, item.qty, item.unit, item.location, item.category, item_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/inventory/{item_id}")
def delete_item(item_id: int):
    conn = db()
    conn.execute("DELETE FROM inventory WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------------- recipes ----------------

def recipe_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["ingredients"] = json.loads(d.get("ingredients") or "[]")
    d["tags"] = [t for t in (d.get("tags") or "").split(",") if t]
    d["metadata"] = json.loads(d.get("metadata") or "{}")
    return d


@app.get("/api/recipes")
def list_recipes():
    rows = db().execute("SELECT * FROM recipes ORDER BY kind, name").fetchall()
    out = []
    prefs = preference_map()
    for r in rows:
        d = recipe_dict(r)
        d["preference"] = prefs.get(r["id"], {})
        out.append(d)
    return out


@app.get('/api/recipes/options')
def recipe_options():
    conn = db()
    try:
        return [dict(r) for r in conn.execute('SELECT id,name,kind FROM recipes ORDER BY name')]
    finally:
        conn.close()


@app.get('/api/recipes/browse')
def browse_recipes(q: str = '', source: str = '', tag: str = '', category: str = '', sort: str = 'name', offset: int = 0, limit: int = 24):
    limit, offset = max(1, min(60, limit)), max(0, offset)
    clauses, params = [], []
    for expression, value in [('name', q), ("json_extract(metadata, '$.source_name')", source), ('tags', tag), ("json_extract(metadata, '$.classifications.recipeCategory')", category)]:
        if value:
            clauses.append(expression + ' LIKE ?')
            params.append('%' + value + '%')
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    order = "(json_extract(metadata,'$.rating.value') - COALESCE(json_extract(metadata,'$.rating.worst'),1)) / NULLIF(COALESCE(json_extract(metadata,'$.rating.best'),5) - COALESCE(json_extract(metadata,'$.rating.worst'),1),0) DESC, name" if sort == 'rating' else 'name'
    conn = db()
    try:
        total = conn.execute('SELECT COUNT(*) FROM recipes' + where, params).fetchone()[0]
        rows = conn.execute('SELECT * FROM recipes' + where + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', params + [limit, offset]).fetchall()
        items = []
        prefs = preference_map()
        for row in rows:
            r = recipe_dict(row)
            r['metadata'] = {k: v for k, v in r['metadata'].items() if k in ('source_name','authors','images','rating','description')}
            r.pop('ingredients', None); r.pop('steps', None)
            r['preference'] = prefs.get(r['id'], {})
            items.append(r)
        return {'items': items, 'total': total, 'offset': offset, 'limit': limit}
    finally:
        conn.close()


@app.get('/api/recipes/facets')
def recipe_facets(field: str = 'tag', q: str = '', limit: int = 15):
    """Autocomplete values for the browse filters, most-used first."""
    limit = max(1, min(50, limit))
    if field not in ('source', 'tag', 'category', 'name'):
        raise HTTPException(422, "field must be one of: source, tag, category, name")
    like, needle = '%' + q + '%', q.lower()
    conn = db()
    try:
        if field == 'tag':
            counts: dict[str, int] = {}
            for (raw,) in conn.execute('SELECT tags FROM recipes WHERE tags LIKE ?', (like,)):
                for tag in (raw or '').split(','):
                    tag = tag.strip()
                    if tag and needle in tag.lower():
                        counts[tag] = counts.get(tag, 0) + 1
            items = [{'value': v, 'count': c} for v, c in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:limit]]
        else:
            expr = {'source': "json_extract(metadata, '$.source_name')",
                    'category': "json_extract(metadata, '$.classifications.recipeCategory')",
                    'name': 'name'}[field]
            if field == 'category':
                sql = ("SELECT je.value AS v, COUNT(*) AS c FROM recipes, "
                       "json_each(" + expr + ") je WHERE je.value LIKE ? "
                       "GROUP BY je.value ORDER BY c DESC, je.value LIMIT ?")
            else:
                sql = 'SELECT ' + expr + ' AS v, COUNT(*) AS c FROM recipes WHERE ' + expr + " IS NOT NULL AND " + expr + " LIKE ? GROUP BY v ORDER BY c DESC, v LIMIT ?"
            items = [{'value': r['v'], 'count': r['c']} for r in conn.execute(sql, (like, limit))]
        return {'items': items}
    finally:
        conn.close()


@app.get('/api/recipes/{recipe_id}')
def get_recipe(recipe_id: int):
    conn = db()
    try:
        row = conn.execute('SELECT * FROM recipes WHERE id=?', (recipe_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'recipe not found')
        result = recipe_dict(row)
        result['preference'] = preference_map().get(recipe_id, {})
        return result
    finally:
        conn.close()


class RecipeIn(BaseModel):
    name: str
    kind: str = "weeknight"
    minutes: int = 30
    servings: int = 4
    protein_per_serving: float = 0
    ingredients: list[dict] = []
    steps: str = ""
    tags: list[str] = []
    metadata: dict = {}
    source_url: str = ""


class ImportIn(BaseModel):
    url: str = ""
    content: str | dict | list | None = None
    preview: bool = True


@app.post("/api/recipes/import")
def import_recipe(payload: ImportIn):
    from recipe_import import fetch_url, extract_recipe, normalize, upsert
    try:
        content, url = (payload.content, payload.url) if payload.content is not None else fetch_url(payload.url)
        recipe = normalize(extract_recipe(content), url)
        if payload.preview:
            return {"preview": True, "recipe": recipe}
        conn = db()
        try:
            result = upsert(conn, recipe)
            conn.commit()
            return {**result, "recipe": recipe}
        finally:
            conn.close()
    except (ValueError, OSError, http.client.HTTPException, RecursionError) as exc:
        raise HTTPException(400, str(exc) or 'Invalid or blocked recipe source') from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, 'Concurrent source import conflict; retry') from exc


@app.post("/api/recipes")
def add_recipe(r: RecipeIn):
    from recipe_import import ingredient, canonical_url
    try:
        for ing in r.ingredients:
            ingredient(ing.get('raw') or ing.get('name', ''))
        source_url = canonical_url(r.source_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    conn = db()
    try:
        cur = conn.execute(
            "INSERT INTO recipes(name,kind,minutes,servings,protein_per_serving,ingredients,steps,tags,source_url,metadata) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (r.name, r.kind, r.minutes, r.servings, r.protein_per_serving,
             json.dumps(r.ingredients), r.steps, ",".join(r.tags), source_url, json.dumps(r.metadata)))
        conn.commit()
        return {"added": cur.lastrowid, "name": r.name}
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, 'Recipe name or source already exists') from exc
    finally:
        conn.close()


class RecipePatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    minutes: int | None = None
    servings: int | None = None
    protein_per_serving: float | None = None
    ingredients: list[dict] | None = None
    steps: str | None = None
    tags: list[str] | None = None
    metadata: dict | None = None
    source_url: str | None = None


def merge_metadata(old, new):
    result = dict(old)
    for key, value in new.items():
        result[key] = merge_metadata(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


@app.patch("/api/recipes/{recipe_id}")
def update_recipe(recipe_id: int, r: RecipePatch):
    from recipe_import import ingredient, canonical_url
    conn = db()
    try:
        row = conn.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
        if not row:
            raise HTTPException(404, "recipe not found")
        updates = r.model_dump(exclude_unset=True)
        if any(v is None for v in updates.values()):
            raise HTTPException(422, 'Recipe fields may not be null')
        for ing in updates.get('ingredients', []):
            ingredient(ing.get('raw') or ing.get('name', ''))
        if 'source_url' in updates:
            updates['source_url'] = canonical_url(updates['source_url'])
        if 'metadata' in updates:
            updates['metadata'] = json.dumps(merge_metadata(json.loads(row['metadata'] or '{}'), updates['metadata']))
        if 'ingredients' in updates:
            updates['ingredients'] = json.dumps(updates['ingredients'])
        if 'tags' in updates:
            updates['tags'] = ','.join(updates['tags'])
        if updates:
            conn.execute('UPDATE recipes SET ' + ','.join(k+'=?' for k in updates) + ' WHERE id=?', list(updates.values()) + [recipe_id])
            conn.commit()
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, 'Recipe name/source collision or invalid kind') from exc
    finally:
        conn.close()


@app.delete("/api/recipes/{recipe_id}")
def delete_recipe(recipe_id: int):
    conn = db()
    used = conn.execute("SELECT 1 FROM plan WHERE recipe_id=? LIMIT 1", (recipe_id,)).fetchone()
    if used:
        conn.close()
        raise HTTPException(409, "recipe is in the current plan; remove that plan day first")
    conn.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))
    conn.execute("DELETE FROM feedback WHERE recipe_id=?", (recipe_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------------- feedback ----------------

class FeedbackIn(BaseModel):
    recipe_id: int
    thumbs: int  # +1 or -1
    note: str = ""


@app.post("/api/feedback")
def add_feedback(f: FeedbackIn):
    if f.thumbs not in (1, -1):
        raise HTTPException(400, "thumbs must be 1 or -1")
    conn = db()
    conn.execute("INSERT INTO feedback(recipe_id,thumbs,note) VALUES(?,?,?)",
                 (f.recipe_id, f.thumbs, f.note))
    conn.commit()
    conn.close()
    return {"ok": True}


def preference_map() -> dict:
    conn = db()
    out = {}
    for r in conn.execute(
            "SELECT recipe_id, SUM(CASE WHEN thumbs=1 THEN 1 ELSE 0 END) ups, "
            "SUM(CASE WHEN thumbs=-1 THEN 1 ELSE 0 END) downs, COUNT(*) n "
            "FROM feedback GROUP BY recipe_id"):
        score = r["ups"] - r["downs"]
        out[r["recipe_id"]] = {"ups": r["ups"], "downs": r["downs"], "score": score}
    conn.close()
    return out


@app.get("/api/preferences")
def preferences():
    return preference_map()


# ---------------- meal plan ----------------

class PlanIn(BaseModel):
    date: str  # YYYY-MM-DD
    recipe_id: int


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


@app.get("/api/plan")
def get_plan(week_start: str | None = None):
    start = date.fromisoformat(week_start) if week_start else monday_of(date.today())
    end = start + timedelta(days=6)
    conn = db()
    rows = conn.execute(
        "SELECT p.id AS plan_id, p.date, p.status, p.cooked_at, r.* "
        "FROM plan p JOIN recipes r ON r.id = p.recipe_id "
        "WHERE p.date BETWEEN ? AND ? ORDER BY p.date", (start.isoformat(), end.isoformat())).fetchall()
    conn.close()
    meals = []
    prefs = preference_map()
    for r in rows:
        rec = recipe_dict(r)
        for key in ("plan_id", "date", "status", "cooked_at"):
            rec.pop(key, None)
        rec["preference"] = prefs.get(rec["id"], {})
        meals.append({"plan_id": r["plan_id"], "date": r["date"], "status": r["status"],
                      "cooked_at": r["cooked_at"], "recipe": rec})
    return {"start": start.isoformat(), "end": end.isoformat(), "meals": meals}


@app.post("/api/plan")
def add_plan(p: PlanIn):
    conn = db()
    existing = conn.execute(
        "SELECT id, recipe_id FROM plan WHERE date=? AND slot='dinner'",
        (p.date,),
    ).fetchone()
    if existing:
        conn.close()
        if existing["recipe_id"] == p.recipe_id:
            return {"added": existing["id"], "existing": True}
        raise HTTPException(409, "a dinner is already planned for this date")
    cur = conn.execute("INSERT INTO plan(date,recipe_id) VALUES(?,?)", (p.date, p.recipe_id))
    conn.commit()
    conn.close()
    return {"added": cur.lastrowid}


@app.delete("/api/plan/{plan_id}")
def delete_plan(plan_id: int):
    conn = db()
    conn.execute("DELETE FROM plan WHERE id=?", (plan_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


def source_rating_prior(rec) -> float:
    """Weak prior from the source's published star rating. Only nudges recipes
    the family has no thumbs history for; family feedback (x3) dominates."""
    meta = rec.get("metadata") or {}
    r = meta.get("rating") or {}
    val, best, worst = r.get("value"), r.get("best"), r.get("worst")
    if val is None:
        return 0.0
    best = best if best is not None else 5
    worst = worst if worst is not None else 1
    if best <= worst:
        return 0.0
    norm = (val - worst) / (best - worst)
    norm = max(0.0, min(1.0, norm))
    confidence = min(1.0, (r.get("count") or r.get("review_count") or 0) / 50)
    return (norm - 0.6) * 1.2 * confidence


class InventoryIndex:
    """Precomputed normalized inventory for fast coverage matching (same semantics as similar())."""
    def __init__(self, names):
        self.entries = []
        for n in names:
            nn = norm(n)
            if nn:
                self.entries.append((nn, frozenset(nn.split())))

    def hit(self, name):
        nn = norm(name)
        if not nn:
            return False
        at = frozenset(nn.split())
        for n, bt in self.entries:
            if nn == n or nn in n or n in nn or at <= bt or bt <= at:
                return True
        return False


@app.post("/api/plan/generate")
def generate_week(week_start: str | None = None):
    """Fill missing dinners for the week: Mon-Thu easy 30g+ protein, Fri-Sun involved.
    Scores by thumbs feedback, pantry coverage, and variety. Idempotent: only fills gaps."""
    start = date.fromisoformat(week_start) if week_start else monday_of(date.today())
    conn = db()
    prefs = preference_map()
    planned = {r["date"] for r in conn.execute(
        "SELECT date FROM plan WHERE date BETWEEN ? AND ?", (start.isoformat(), (start + timedelta(days=6)).isoformat()))}
    # variety: dishes cooked/eaten in last 30 days
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    recent_ids = {r["recipe_id"] for r in conn.execute(
        "SELECT recipe_id FROM consumption WHERE date >= ? UNION "
        "SELECT recipe_id FROM plan WHERE date >= ? AND date < ?", (cutoff, cutoff, start.isoformat()))}
    inv = db().execute("SELECT name FROM inventory WHERE qty > 0").fetchall()
    inv_index = InventoryIndex([r["name"] for r in inv])
    # Slim projection: never parse the full raw-JSON-LD metadata blob here.
    recipes = []
    for row in conn.execute(
            "SELECT id,name,kind,minutes,servings,protein_per_serving,ingredients,tags,"
            " json_extract(metadata,'$.rating') AS rating_json FROM recipes"):
        rec = dict(row)
        rec["ingredients"] = json.loads(rec["ingredients"] or "[]")
        rec["tags"] = [t for t in (rec["tags"] or "").split(",") if t]
        rec["metadata"] = {"rating": json.loads(rec.pop("rating_json") or "{}")}
        # Inventory is fixed for the run: coverage & protein buckets computed once per recipe.
        ings = [i for i in rec["ingredients"] if i.get("name") and not is_ing_header(i["name"])]
        rec["cov"] = sum(1 for i in ings if inv_index.hit(i["name"])) / max(1, len(ings))
        rec["buckets"] = protein_buckets(rec)
        recipes.append(rec)
    added = []
    used_this_week = set()
    buckets_used = set()
    for offset in range(7):
        day = start + timedelta(days=offset)
        if day.isoformat() in planned:
            continue
        kind = "weeknight" if day.weekday() <= 3 else "weekend"  # Mon-Thu easy, Fri-Sun involved
        candidates = []
        for rec in recipes:
            if any(t.lower() in ("breakfast", "side", "side dish", "dessert", "desserts", "baking",
                                 "drinks", "non-alcoholic drinks", "alcoholic drinks", "cocktails",
                                 "appetizer", "appetizers", "salad", "salads", "snack", "snacks",
                                 "jams", "jellies and preserves", "preserves", "syrup", "syrups",
                                 "condiments", "sauces", "smoothies", "bread", "breads") for t in rec["tags"]):
                continue
            if kind == "weeknight" and rec["kind"] != "weeknight":
                continue
            if kind == "weeknight" and rec["protein_per_serving"] < MIN_WEEKNIGHT_PROTEIN:
                continue
            if kind == "weekend" and rec["protein_per_serving"] < MIN_WEEKEND_PROTEIN:
                continue  # keeps flan/pastry-class dishes out of the unbounded weekend pool
            if rec["id"] in used_this_week:
                continue
            pref = prefs.get(rec["id"], {})
            score = pref.get("score", 0) * 3.0
            if kind == "weekend" and rec["kind"] == "weekend":
                score += 2.0  # prefer involved builds on Fri-Sun
            score += rec["cov"] * 2.0
            score += source_rating_prior(rec)  # published stars as a weak prior
            if rec["id"] in recent_ids:
                score -= 4.0
            if rec["buckets"] & buckets_used:
                score -= 3.0  # protein variety within the week
            score += random.uniform(0, 0.3)
            candidates.append((score, rec["cov"], rec))
        if not candidates:
            continue
        candidates.sort(key=lambda x: -x[0])
        best_score, best_cov, best = candidates[0]
        cur = conn.execute("INSERT INTO plan(date,recipe_id) VALUES(?,?)", (day.isoformat(), best["id"]))
        added.append({"plan_id": cur.lastrowid, "date": day.isoformat(), "recipe": best["name"],
                      "score": round(best_score, 2),
                      "pantry_coverage": round(best_cov, 2)})
        used_this_week.add(best["id"])
        buckets_used |= protein_buckets(best)
    conn.commit()
    conn.close()
    return {"week_start": start.isoformat(), "filled": added}


@app.post("/api/plan/{plan_id}/cook")
def mark_cooked(plan_id: int):
    """Mark planned meal as cooked/eaten: deduct ingredients from inventory."""
    conn = db()
    row = conn.execute("SELECT p.*, r.ingredients, r.name FROM plan p JOIN recipes r ON r.id=p.recipe_id "
                       "WHERE p.id=?", (plan_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "plan entry not found")
    if row["status"] == "cooked":
        conn.close()
        raise HTTPException(400, "already cooked")
    shortages = []
    deducted = []
    for ing in json.loads(row["ingredients"]):
        need = float(ing.get("qty", 0))
        name = ing["name"]
        if is_ing_header(name) or not name:
            continue
        if need <= 0:  # "to taste": can't deduct, but flag if not in stock at all
            stocked = any(similar(name, r["name"]) for r in
                          conn.execute("SELECT name FROM inventory WHERE qty > 0").fetchall())
            if not stocked:
                shortages.append({"item": name, "still_needed": "some", "unit": ing.get("unit", "")})
            continue
        matches = [r for r in conn.execute("SELECT * FROM inventory WHERE qty > 0").fetchall()
                   if similar(name, r["name"])]
        matches.sort(key=lambda r: -r["qty"])
        for m in matches:
            if need <= 1e-9:
                break
            take = min(need, m["qty"])
            conn.execute("UPDATE inventory SET qty = qty - ?, updated_at=datetime('now') WHERE id=?",
                         (take, m["id"]))
            need -= take
            deducted.append({"used": m["name"], "qty": take, "unit": ing.get("unit", "")})
        if need > 1e-9:
            shortages.append({"item": name, "still_needed": round(need, 2), "unit": ing.get("unit", "")})
    conn.execute("UPDATE plan SET status='cooked', cooked_at=datetime('now') WHERE id=?", (plan_id,))
    conn.execute("INSERT INTO consumption(plan_id,recipe_id,date) VALUES(?,?,?)",
                 (plan_id, row["recipe_id"], row["date"]))
    conn.commit()
    conn.close()
    return {"ok": True, "recipe": row["name"], "deducted": deducted, "shortages": shortages}


@app.get("/api/shoppinglist")
def shopping_list(week_start: str | None = None):
    """Net ingredients needed for the week's planned (not yet cooked) meals, minus stock."""
    start = date.fromisoformat(week_start) if week_start else monday_of(date.today())
    end = start + timedelta(days=6)
    conn = db()
    meals = conn.execute(
        "SELECT p.status, r.ingredients FROM plan p JOIN recipes r ON r.id=p.recipe_id "
        "WHERE p.date BETWEEN ? AND ? AND p.status='planned'", (start.isoformat(), end.isoformat())).fetchall()
    inv = conn.execute("SELECT name, qty FROM inventory").fetchall()
    conn.close()
    need = []  # [(name, qty, unit)]
    for m in meals:
        for ing in json.loads(m["ingredients"]):
            name, qty = ing.get("name", ""), float(ing.get("qty", 0) or 0)
            if not name or is_ing_header(name):
                continue
            for slot in need:
                if similar(slot[0], name):
                    slot[1] += qty
                    break
            else:
                need.append([name, qty, ing.get("unit", "")])
    out = []
    for name, qty, unit in need:
        have = sum(r["qty"] for r in inv if similar(name, r["name"]))
        short = qty - have
        if qty <= 0:  # "to taste" / unspecified quantity
            if have > 0:
                continue
            out.append({"item": name, "need_total": 0, "have": 0, "buy": "some", "unit": unit})
        elif short > 1e-9:
            out.append({"item": name, "need_total": round(qty, 2), "have": round(have, 2),
                        "buy": round(short, 2), "unit": unit})
    out.sort(key=lambda x: x["item"].lower())
    return {"week_start": start.isoformat(), "meals": len(meals), "items": out}


@app.get("/api/summary")
def summary():
    conn = db()
    n_inv = conn.execute("SELECT COUNT(*) c FROM inventory WHERE qty > 0").fetchone()["c"]
    n_low = conn.execute("SELECT COUNT(*) c FROM inventory WHERE qty <= 0").fetchone()["c"]
    conn.close()
    return {"inventory_items": n_inv, "depleted_items": n_low,
            "server_time": datetime.now().isoformat()}


# ---------------- static UI ----------------

app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.middleware("http")
async def no_cache_ui(request, call_next):
    """UI files change with every deploy; force browsers to revalidate (etag-matched,
    cheap on LAN) so a stale cached recipes.js can never mismatch a new index.html."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


# MCP tools reuse the existing API's business logic and SQLite database.
# Keep the initial tool surface read-only for safe scheduled planning.
from mcp_server import make_mcp_server

pantry_mcp = make_mcp_server(
    list_inventory=list_inventory,
    list_recipes=list_recipes,
    get_plan=get_plan,
    preferences=preferences,
    shopping_list=shopping_list,
)
# The canonical MCP URL is /mcp/ (including the trailing slash).
app.mount("/mcp", pantry_mcp.streamable_http_app())
