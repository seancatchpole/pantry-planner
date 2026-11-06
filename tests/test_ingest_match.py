"""Ingredient name cleanup (prep/purpose clause split) and the alias table."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BOOT = tempfile.TemporaryDirectory()
os.environ['PANTRY_DB'] = str(Path(_BOOT.name) / 'boot.db')
import app as pantry
from recipe_import import ingredient
from fastapi.testclient import TestClient


class IngredientSurgeryTests(unittest.TestCase):
    def test_prep_clause_split(self):
        cases = [
            ("2 tablespoons olive oil, divided", 'olive oil', 'divided'),
            ("3 tablespoons butter, plus more for serving", 'butter', 'plus more for serving'),
            ("1 (14-oz.) can chicken broth, low-sodium", 'chicken broth', 'low-sodium'),
            ("1 cup heavy cream (preferably not ultra-pasteurized)", 'heavy cream', 'preferably not ultra-pasteurized'),
            ("4 scallions, green parts only", 'scallions, green parts only', ''),  # clause kept: 'green' is not a prep cue; inclusion matching still works
            ("Kosher salt", 'Kosher salt', ''),
            ("Boneless skinless chicken thighs, cut into 1-inch pieces", 'Boneless skinless chicken thighs', 'cut into 1-inch pieces'),
            ("boneless, skinless chicken breasts, trimmed", 'boneless, skinless chicken breasts', 'trimmed'),
            ("2 1/2 lbs. of raw chicken wings", 'raw chicken wings', ''),
            ("2 slices country bread", 'country bread', ''),
            ("12 to 16 fresh flour or corn tortillas, hot", 'fresh flour or corn tortillas', 'hot'),
            ("1 4-to-6-pound duck", 'duck', ''),
            ("4 (5- to 6-ounce) filet mignon steaks, patted dry", 'filet mignon steaks', 'patted dry'),
            ("6-ounce halibut fillets, skin removed", 'halibut fillets', 'skin removed'),
            ("Chopped parsley, for garnish, optional", 'Chopped parsley', 'for garnish, optional'),
            ("(30 g) vegetable oil", 'vegetable oil', ''),
        ]
        for raw, name, prep in cases:
            out = ingredient(raw)
            self.assertEqual(out['name'], name, raw)
            self.assertEqual(out.get('prep', ''), prep, raw)
            self.assertEqual(out['raw'], raw)  # raw always preserved

    def test_qty_survives_size_parenthetical(self):
        out = ingredient("4 (5- to 6-ounce) filet mignon steaks, patted dry")
        self.assertEqual(out['qty'], 4)          # clean qty kept despite range INSIDE parens
        out2 = ingredient("12 to 16 fresh flour or corn tortillas, hot")
        self.assertEqual(out2['qty'], 0)         # real qty range stays conservative

    def test_ranges_still_conservative(self):
        out = ingredient("1 1/2 to 2 pounds chuck roast, cut into chunks")
        self.assertEqual(out['qty'], 0)  # ambiguous range keeps qty 0
        self.assertEqual(out['name'], 'chuck roast')


class AliasTableTests(unittest.TestCase):
    def test_similar_via_alias(self):
        for a, b in [("scallions", "green onions"), ("cilantro", "fresh coriander"),
                     ("red pepper flakes", "chili flakes"), ("cannellini beans", "white beans"),
                     ("heavy whipping cream", "heavy cream"), ("eggplant", "aubergine")]:
            self.assertTrue(pantry.similar(a, b), (a, b))

    def test_no_overmerge(self):
        # NOTE: token-inclusion (pre-existing) intentionally matches "cream" against
        # "cream cheese"; these pairs guard the ALIAS table only, not inclusion.
        for a, b in [("heavy cream", "milk"), ("heavy whipping cream", "whole milk"),
                     ("green beans", "coffee beans")]:
            self.assertFalse(pantry.similar(a, b), (a, b))

    def test_gap_matching_uses_aliases(self):
        tmp = tempfile.TemporaryDirectory()
        with patch.object(pantry, 'DB_PATH', Path(tmp.name) / 'test.db'):
            pantry.init_db()
            client = TestClient(pantry.app, base_url='http://localhost:8080')
            try:
                rid = client.post('/api/recipes', json={
                    'name': 'Alias Test Stew', 'kind': 'weeknight', 'minutes': 30, 'servings': 4,
                    'protein_per_serving': 32,
                    'ingredients': [{'name': 'scallions', 'qty': 2, 'unit': 'each'},
                                    {'name': 'cannellini beans', 'qty': 1, 'unit': 'can'}],
                    'steps': 'cook'}).json()['added']
                client.post('/api/inventory', json={'name': 'green onions', 'qty': 3})
                # cannellini NOT stocked -> only beans show as gap
                client.post('/api/plan', json={'date': '2026-11-16', 'recipe_id': rid})
                items = client.get('/api/shoppinglist?week_start=2026-11-16').json()['items']
                names = {i['item'] for i in items}
                self.assertEqual(names, {'cannellini beans'})
            finally:
                client.close()
        tmp.cleanup()


if __name__ == '__main__':
    unittest.main()
