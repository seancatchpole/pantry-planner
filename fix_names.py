"""One-shot, idempotent: re-parse stored ingredient rows with the current parser.

Usage (inside the container, or anywhere with recipe_import.py importable):
    python fix_names.py            # PANTRY_DB env or ./data/pantry.db
Dry run:  python fix_names.py --dry-run
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.getcwd())
from recipe_import import ingredient


def fix(db_path, dry=False):
    conn = sqlite3.connect(db_path)
    changed = rejected = skipped = 0
    samples = []
    for rid, raw_json in conn.execute("SELECT id, ingredients FROM recipes"):
        try:
            ings = json.loads(raw_json or "[]")
        except (ValueError, TypeError):
            skipped += 1
            continue
        out, row_changed = [], False
        for ing in ings:
            src = ing.get("raw") or ing.get("name") or ""
            if not src:
                out.append(ing)
                continue
            try:
                new = ingredient(src)
            except ValueError:
                rejected += 1          # recipe was accepted once; never lose rows
                out.append(ing)
                continue
            if ing.get("qty", 0) > 0 and new.get("qty", 0) == 0:
                new["qty"] = ing["qty"]  # never regress an already-parsed quantity
            if ing.get("hint"):
                new["hint"] = ing["hint"]  # preserve manual storage hints
            if (new.get("name") != ing.get("name") or new.get("unit") != ing.get("unit")
                    or new.get("qty") != ing.get("qty")):
                row_changed = True
                if len(samples) < 12 and new.get("name") != ing.get("name"):
                    samples.append((src, ing.get("name"), new["name"]))
            out.append(new)
        if row_changed:
            changed += 1
            if not dry:
                conn.execute("UPDATE recipes SET ingredients = ? WHERE id = ?",
                             (json.dumps(out), rid))
    conn.commit()
    conn.close()
    print(f"{db_path}: recipes updated={changed} unparseable-kept={rejected} bad-json-skipped={skipped} dry={dry}")
    for src, old, new in samples:
        print(f"  {src!r}: {old!r} -> {new!r}")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    fix(os.environ.get("PANTRY_DB", "data/pantry.db"), dry=dry)
