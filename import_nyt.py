#!/usr/bin/env python3
"""Import a bounded page of an old NYT archive into a local Pantry DB."""
import argparse
import json
import re
import sqlite3
from pathlib import Path
from recipe_import import extract_recipe, normalize, migrate, upsert

NON_DINNER_CATS = {
    'dessert', 'desserts', 'breakfast', 'brunch', 'drinks', 'cocktail', 'cocktails',
    'non-alcoholic drinks', 'alcoholic drinks', 'beverages', 'smoothie', 'smoothies',
    'appetizer', 'appetizers', 'side dish', 'side dishes', 'salad', 'salads',
    'snack', 'snacks', 'bread', 'breads', 'sauce', 'sauces', 'condiment', 'condiments',
    'candy', 'jam', 'jams', 'jellies and preserves', 'preserves', 'syrup', 'syrups',
    'leftovers', 'pickup meal', 'punch', 'cocktail food',
}


def curated(ldjson, min_rating, min_count, exclude_cats):
    """Cheap pre-JSON parse gate; returns skip-reason string or None."""
    if min_rating or min_count or exclude_cats:
        obj = json.loads(ldjson)
        r = obj.get('aggregateRating') or {}
        if min_rating or min_count:
            try:
                if float(r.get('ratingValue', 0)) < min_rating or int(r.get('ratingCount', 0)) < min_count:
                    return 'below-rating-floor'
            except (TypeError, ValueError):
                return 'no-rating'
        if exclude_cats:
            cats = {x.strip().lower() for x in re.split(r'[,/]', str(obj.get('recipeCategory') or '')) if x.strip()}
            if cats & NON_DINNER_CATS:
                return 'non-dinner-category'
    return None


def run(archive, target, limit=100, offset=0, batch_size=25, dry_run=False,
        min_rating=0.0, min_count=0, exclude_cats=True):
    if limit < 1 or offset < 0 or batch_size < 1 or batch_size > 1000:
        raise ValueError('limit/batch-size must be positive (batch <=1000); offset nonnegative')
    if Path(archive).resolve() == Path(target).resolve():
        raise ValueError('Archive and target must differ')
    source = sqlite3.connect(Path(archive).resolve().as_uri() + '?mode=ro', uri=True)
    dest = sqlite3.connect(':memory:' if dry_run else target)
    # No application import: do not seed or touch the default app DB.
    dest.execute("CREATE TABLE IF NOT EXISTS recipes(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,minutes INTEGER,servings INTEGER,protein_per_serving REAL,ingredients TEXT,steps TEXT,tags TEXT)")
    if dry_run and Path(target).exists():
        existing = sqlite3.connect(Path(target).resolve().as_uri() + '?mode=ro', uri=True)
        existing.backup(dest); existing.close()
    migrate(dest)
    report = dict(processed=0, added=0, updated=0, rejected=0, errors=0, skipped=0,
                  dry_run=dry_run, offset=offset, limit=limit,
                  min_rating=min_rating, min_count=min_count)
    cursor = source.execute("SELECT url,ldjson FROM recipes WHERE state='fetched' AND ldjson IS NOT NULL ORDER BY url LIMIT ? OFFSET ?", (limit, offset))
    try:
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            for url, ldjson in rows:
                report['processed'] += 1
                skip = curated(ldjson, min_rating, min_count, exclude_cats)
                if skip:
                    report['skipped'] += 1
                    continue
                try:
                    recipe = normalize(extract_recipe(ldjson), url)
                    result = upsert(dest, recipe)
                    report['updated' if result['updated'] else 'added'] += 1
                except (ValueError, TypeError, sqlite3.Error) as exc:
                    key = 'rejected' if 'Peanut ingredient rejected' in str(exc) else 'errors'
                    report[key] += 1
                    print(json.dumps({'url': url, 'status': key, 'error': str(exc)}), flush=True)
            dest.commit()
            print(json.dumps({'progress': report}), flush=True)
    finally:
        source.close(); dest.close()
    print(json.dumps({'report': report}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--db', required=True, help='Explicit local destination DB (no network writes)')
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--offset', type=int, default=0, help='Offset in fetched rows sorted by URL')
    parser.add_argument('--batch-size', type=int, default=25)
    parser.add_argument('--dry-run', action='store_true', help='Simulate against an in-memory backup of target')
    parser.add_argument('--min-rating', type=float, default=0.0, help='Skip recipes below this source star value')
    parser.add_argument('--min-count', type=int, default=0, help='Skip recipes with fewer source ratings than this')
    parser.add_argument('--all-categories', action='store_true', help='Import desserts/breakfast/etc too (planner excludes them by tag)')
    args = parser.parse_args()
    result = run(args.archive, args.db, args.limit, args.offset, args.batch_size, args.dry_run,
                 min_rating=args.min_rating, min_count=args.min_count, exclude_cats=not args.all_categories)
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
