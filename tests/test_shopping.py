"""Shopping list: gap generation, manual rows, check-off, purchase loop."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BOOT = tempfile.TemporaryDirectory()
os.environ['PANTRY_DB'] = str(Path(_BOOT.name) / 'boot.db')
import app as pantry
from fastapi.testclient import TestClient


class ShoppingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(pantry, 'DB_PATH', Path(self.tmp.name) / 'test.db')
        self.patcher.start(); pantry.init_db()
        self.client = TestClient(pantry.app, base_url='http://localhost:8080')
        recipes = self.client.get('/api/recipes').json()
        self.chicken = next(r for r in recipes if r['name'] == 'Honey-Garlic Chicken Thighs & Rice')

    def tearDown(self):
        self.client.close(); self.patcher.stop(); self.tmp.cleanup(); _BOOT.cleanup()

    def test_aisle_guessing(self):
        cases = [('chicken broth', 'Pantry'), ('egg noodles', 'Pantry'),
                 ('frozen broccoli', 'Frozen'), ('ground turkey', 'Meat & Seafood'),
                 ('bell peppers', 'Produce'), ('greek yogurt', 'Dairy & Eggs'),
                 ('burger buns', 'Bakery'), ('soy sauce', 'Spices & Condiments'),
                 ('mystery item', 'Pantry')]
        for name, aisle in cases:
            self.assertEqual(pantry.aisle_of(name), aisle, name)

    def test_generate_checkoff_purchase_loop(self):
        week = '2026-11-02'
        self.client.post('/api/plan', json={'date': week, 'recipe_id': self.chicken['id']})
        gen = self.client.post(f'/api/shopping/generate?week_start={week}').json()
        self.assertGreater(gen['generated'], 0)
        items = self.client.get(f'/api/shopping?week_start={week}').json()['items']
        names = {i['name'] for i in items}
        self.assertIn('boneless skinless chicken thighs', names)  # nothing in stock
        self.assertTrue(all('aisle' in i for i in items))

        # manual add survives a rebuild
        added = self.client.post(f'/api/shopping?week_start={week}',
                                 json={'name': 'milk', 'qty': 1, 'unit': 'gal', 'hint': 'fridge'}).json()
        self.client.post(f'/api/shopping?week_start={week}', json={'name': 'ground beef'})  # no hint -> fridge
        self.client.post(f'/api/shopping/generate?week_start={week}')
        items = self.client.get(f'/api/shopping?week_start={week}').json()['items']
        milk = next(i for i in items if i['name'] == 'milk')
        self.assertEqual(milk['source'], 'manual')
        self.assertEqual(milk['aisle'], 'Dairy & Eggs')

        # check one off, rebuild keeps it checked
        chicken_row = next(i for i in items if 'chicken thighs' in i['name'])
        self.client.patch(f"/api/shopping/{chicken_row['id']}", json={'checked': True})
        self.client.post(f'/api/shopping/generate?week_start={week}')
        items = self.client.get(f'/api/shopping?week_start={week}').json()['items']
        still = next(i for i in items if i['id'] == chicken_row['id'])
        self.assertEqual(still['checked'], 1)

        # purchase moves checked rows into inventory with the right location
        beef = next(i for i in items if i['name'] == 'ground beef')
        self.client.patch(f"/api/shopping/{milk['id']}", json={'checked': True})
        self.client.patch(f"/api/shopping/{beef['id']}", json={'checked': True})
        res = self.client.post(f'/api/shopping/purchase?week_start={week}').json()
        self.assertEqual(len(res['added']), 3)
        inv = self.client.get('/api/inventory').json()
        milk_row = next(r for r in inv if r['name'] == 'milk')
        self.assertEqual(milk_row['location'], 'fridge')
        self.assertEqual(next(r for r in inv if r['name'] == 'ground beef')['location'], 'fridge')
        self.assertEqual(milk_row['source'], 'shopping list')
        self.assertTrue(any('chicken thighs' in r['name'] for r in inv))
        left = self.client.get(f'/api/shopping?week_start={week}').json()['items']
        self.assertNotIn('milk', {i['name'] for i in left})

    def test_cooked_meals_leave_no_gaps(self):
        week = '2026-11-02'
        self.client.post('/api/plan', json={'date': week, 'recipe_id': self.chicken['id']})
        plan = self.client.get(f'/api/plan?week_start={week}').json()['meals'][0]
        self.client.post(f"/api/plan/{plan['plan_id']}/cook")
        self.assertEqual(self.client.get(f'/api/shoppinglist?week_start={week}').json()['items'], [])

    def test_purchase_keeps_fuzzy_names_distinct(self):
        week = '2026-11-02'
        self.client.post('/api/inventory', json={'name': 'sweet onion & sour cream kettle chips', 'qty': 2})
        self.client.post(f'/api/shopping?week_start={week}', json={'name': 'sour cream', 'qty': 1, 'hint': 'fridge'})
        items = self.client.get(f'/api/shopping?week_start={week}').json()['items']
        sc = next(i for i in items if i['name'] == 'sour cream')
        self.client.patch(f"/api/shopping/{sc['id']}", json={'checked': True})
        self.client.post(f'/api/shopping/purchase?week_start={week}')
        inv = self.client.get('/api/inventory').json()
        self.assertEqual(next(r for r in inv if 'kettle chips' in r['name'])['qty'], 2)
        sc_row = next(r for r in inv if r['name'] == 'sour cream')
        self.assertEqual(sc_row['location'], 'fridge')

    def test_shoppinglist_shape_unchanged(self):
        week = '2026-11-02'
        self.client.post('/api/plan', json={'date': week, 'recipe_id': self.chicken['id']})
        data = self.client.get(f'/api/shoppinglist?week_start={week}').json()
        self.assertEqual(set(data), {'week_start', 'meals', 'items'})
        for item in data['items']:
            self.assertEqual(set(item), {'item', 'need_total', 'have', 'buy', 'unit'})


if __name__ == '__main__':
    unittest.main()
