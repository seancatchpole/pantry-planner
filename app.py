"""Freezer & Pantry inventory + weekly dinner planner API."""
import json
import os
import random
import re
import sqlite3
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

app = FastAPI(title="Pantry Planner")


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


class RecipeIn(BaseModel):
    name: str
    kind: str = "weeknight"
    minutes: int = 30
    servings: int = 4
    protein_per_serving: float = 0
    ingredients: list[dict] = []
    steps: str = ""
    tags: list[str] = []


@app.post("/api/recipes")
def add_recipe(r: RecipeIn):
    conn = db()
    cur = conn.execute(
        "INSERT INTO recipes(name,kind,minutes,servings,protein_per_serving,ingredients,steps,tags) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (r.name, r.kind, r.minutes, r.servings, r.protein_per_serving,
         json.dumps(r.ingredients), r.steps, ",".join(r.tags)))
    conn.commit()
    conn.close()
    return {"added": cur.lastrowid, "name": r.name}


@app.patch("/api/recipes/{recipe_id}")
def update_recipe(recipe_id: int, r: RecipeIn):
    conn = db()
    if not conn.execute("SELECT 1 FROM recipes WHERE id=?", (recipe_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "recipe not found")
    conn.execute(
        "UPDATE recipes SET name=?,kind=?,minutes=?,servings=?,protein_per_serving=?,"
        "ingredients=?,steps=?,tags=? WHERE id=?",
        (r.name, r.kind, r.minutes, r.servings, r.protein_per_serving,
         json.dumps(r.ingredients), r.steps, ",".join(r.tags), recipe_id))
    conn.commit()
    conn.close()
    return {"ok": True}


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
        "SELECT p.id, p.date, p.status, p.cooked_at, r.* "
        "FROM plan p JOIN recipes r ON r.id = p.recipe_id "
        "WHERE p.date BETWEEN ? AND ? ORDER BY p.date", (start.isoformat(), end.isoformat())).fetchall()
    conn.close()
    meals = []
    for r in rows:
        rec = recipe_dict(r)
        meals.append({"plan_id": r["id"], "date": r["date"], "status": r["status"],
                      "cooked_at": r["cooked_at"], "recipe": rec})
    return {"start": start.isoformat(), "end": end.isoformat(), "meals": meals}


@app.post("/api/plan")
def add_plan(p: PlanIn):
    conn = db()
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
    inv_names = [r["name"] for r in inv]
    recipes = [recipe_dict(r) for r in conn.execute("SELECT * FROM recipes").fetchall()]
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
            if any(t in ("breakfast", "side", "dessert", "baking") for t in rec["tags"]):
                continue
            if kind == "weeknight" and rec["kind"] != "weeknight":
                continue
            if kind == "weeknight" and rec["protein_per_serving"] < MIN_WEEKNIGHT_PROTEIN:
                continue
            if rec["id"] in used_this_week:
                continue
            pref = prefs.get(rec["id"], {})
            score = pref.get("score", 0) * 3.0
            if kind == "weekend" and rec["kind"] == "weekend":
                score += 2.0  # prefer involved builds on Fri-Sun
            # pantry coverage: fraction of ingredients already in stock
            hits = sum(1 for ing in rec["ingredients"] if any(similar(ing["name"], n) for n in inv_names))
            cov = hits / max(1, len(rec["ingredients"]))
            score += cov * 2.0
            if rec["id"] in recent_ids:
                score -= 4.0
            if protein_buckets(rec) & buckets_used:
                score -= 3.0  # protein variety within the week
            score += random.uniform(0, 0.3)
            candidates.append((score, cov, rec))
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


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")
