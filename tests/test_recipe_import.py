"""Lossless recipe migration/import, network guards and real archive integration."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import recipe_import as ri
from import_nyt import run
import test_planner
pantry = test_planner.pantry


def sample(**updates):
    return {'@type':['Recipe'],'name':'Soup','url':'//cooking.nytimes.com/recipes/42-soup?x=1#top','recipeIngredient':['1 (28-ounce) can tomatoes','1½ cups stock','Salt to taste'], 'recipeInstructions':{'@type':'HowToSection','name':'Soup','itemListElement':{'@type':'HowToStep','text':'Simmer.'}},'aggregateRating':{'ratingValue':'4.8','ratingCount':'1,234'},'recipeCategory':'Dinner, Main','recipeCuisine':['American'],'keywords':'easy, soup','nutrition':{'proteinContent':'32 g','sodiumContent':'2 mg'},'author':{'name':'Chef'},'totalTime':'PT35M',**updates}


class ParserTests(unittest.TestCase):
    def test_jsonld_variants(self):
        raw=sample()
        for obj in (raw,[raw],{'@graph':[{'@type':'Organization'},raw]}, {'wrapper':{'@graph':[raw]}}):
            self.assertEqual(ri.extract_recipe(json.dumps(obj)),raw)
        for tag in ('script','div'):
            for quote in ('"',"'"):
                content=f'<{tag} class="x" type={quote}application/ld+json{quote}>'+json.dumps({'@graph':[raw]})+f'</{tag}>'
                self.assertEqual(ri.extract_recipe(content),raw)
        for invalid in ('{}','<html>Access denied</html>','broken'):
            with self.assertRaisesRegex(ValueError,'No Recipe'):
                ri.extract_recipe(invalid)
    def test_rich_fields_and_singletons(self):
        raw=sample(recipeIngredient='1 cup rice',review={'reviewBody':'Good'},video={'name':'Video'},description='x'*600)
        r=ri.normalize(raw)
        self.assertEqual(len(r['ingredients']),1)
        self.assertEqual(r['ingredients'][0]['name'],'rice')
        self.assertEqual(r['source_url'],'https://cooking.nytimes.com/recipes/42-soup')
        self.assertEqual(r['metadata']['raw_jsonld'],raw)
        self.assertEqual(r['metadata']['rating']['count'],1234)
        self.assertEqual(len(r['metadata']['description']),600)
        self.assertEqual(r['protein_per_serving'],32)
        self.assertEqual(r['minutes'],35)
        self.assertEqual(r['kind'],'weeknight')
        self.assertEqual(r['metadata']['instructions'][0]['steps'][0]['text'],'Simmer.')
        self.assertEqual(r['metadata']['reviews'][0]['reviewBody'],'Good')
        self.assertEqual(set(r['tags']),{'Dinner','Main','American','easy','soup'})
        self.assertEqual(r['metadata']['nutrition']['sodiumContent'],'2 mg')
    def test_nested_instructions(self):
        r=ri.normalize(sample(recipeInstructions=[{'@type':'HowToSection','name':'A','itemListElement':[{'@type':'HowToSection','name':'B','itemListElement':{'@type':'HowToStep','text':'Nested'}}]},'Finish']))
        self.assertIn('Nested',r['steps']); self.assertIn('Finish',r['steps'])
    def test_rating_scale_and_missing(self):
        for rating in (None,{}, {'ratingValue':None},{'ratingValue':'oops'}):
            self.assertIsNone(ri.normalize(sample(aggregateRating=rating))['metadata']['rating']['value'])
        r=ri.normalize(sample(aggregateRating={'ratingValue':'90','bestRating':'100','worstRating':'0','reviewCount':0}))
        self.assertEqual(r['metadata']['rating'],{'value':90,'best':100,'worst':0,'count':None,'review_count':0})
    def test_quantities(self):
        for text,qty,unit,name in [('1 (28-ounce) can tomatoes',1,'can','tomatoes'),('1½ cups rice',1.5,'cups','rice'),('½ cup rice',.5,'cup','rice'),('1 1/2 cups rice',1.5,'cups','rice'),
                                   ('1/4 tsp salt',.25,'tsp','salt'),('0 cups stock',0,'cups','stock'),('salt to taste',0,'','salt to taste'),
                                   ('1–2 cups rice',0,'cups','rice'),  # range: qty stays 0, but unit+name are extracted (raw keeps full text)
                                   ('1/0 cups stock',0,'','1/0 cups stock')]:  # unparseable qty keeps full text as name
            with self.subTest(text=text):
                parsed=ri.ingredient(text)
                self.assertEqual((parsed['qty'],parsed['unit'],parsed['name']),(qty,unit,name))
                self.assertEqual(parsed['raw'],text)
    def test_peanut_policy_matches_household(self):
        # Household rule (user-stated 2026-11-04): refined peanut oil is tolerated;
        # alternates like "peanuts or cashews" are fine (pick the other nut).
        allowed = ri.normalize(sample(recipeIngredient=['1 tbsp peanut oil','oil, or peanut oil','groundnut oil']))
        self.assertTrue(all(i['allergy_note'].startswith('peanut oil') for i in allowed['ingredients']))
        alt = ri.normalize(sample(recipeIngredient=['⅓ cup peanuts or cashews']))
        self.assertIn('non-peanut', alt['ingredients'][0]['allergy_note'])
        # Real NYT phrasings that previously false-rejected (2026-11-04 user report):
        for line in ('1 tablespoon peanut or safflower oil',
                     '2 tablespoons peanut, canola, rice bran, sunflower, or grape seed oil',
                     'Safflower, peanut or vegetable oil, for frying',
                     '2 tablespoons neutral oil, like peanut or grapeseed',
                     '½ cup peanut or other neutral oil'):
            with self.subTest(line=line):
                got = ri.normalize(sample(recipeIngredient=[line]))
                self.assertEqual(got['ingredients'][0]['allergy_note'],
                                 'peanut oil (refined — tolerated in this household)')
        for line in ('roasted peanuts','1 cup peanut butter','½ cup peanuts, crushed',
                     '½ cup peanut sauce, plus 1 tbsp oil'):
            with self.subTest(line=line):
                with self.assertRaisesRegex(ValueError,'Peanut ingredient rejected'):
                    ri.normalize(sample(recipeIngredient=[line]))
        with self.assertRaisesRegex(ValueError,'Peanut ingredient rejected'):
            ri.normalize(sample(recipeIngredient=['2 tbsp peanut oil','chopped peanuts']))
        ri.normalize(sample(recipeIngredient=['1 cup peas']))
    def test_sections(self):
        r=ri.normalize(sample(recipeIngredient=['For the soup:','2 cups stock',{'name':'Garnish','itemListElement':['1 cup herbs']}]))
        self.assertEqual(len(r['ingredients']),2)
        self.assertEqual(len(r['metadata']['ingredient_sections']),2)


class MigrationTests(unittest.TestCase):
    def test_legacy_additive_and_upsert_preserve_relationships(self):
        conn=sqlite3.connect(':memory:')
        conn.executescript("CREATE TABLE recipes(id INTEGER PRIMARY KEY,name TEXT UNIQUE,kind TEXT,minutes INTEGER,servings INTEGER,protein_per_serving REAL,ingredients TEXT,steps TEXT,tags TEXT); INSERT INTO recipes VALUES(91,'Soup','weeknight',30,4,32,'[]','Legacy','dinner'); CREATE TABLE plan(id INTEGER PRIMARY KEY,recipe_id INTEGER REFERENCES recipes(id));INSERT INTO plan VALUES(12,91);CREATE TABLE feedback(recipe_id INTEGER,thumbs INTEGER);INSERT INTO feedback VALUES(91,1);")
        ri.migrate(conn); ri.migrate(conn)
        self.assertEqual(conn.execute('SELECT id,steps FROM recipes').fetchone(),(91,'Legacy'))
        r=ri.normalize(sample()); first=ri.upsert(conn,r); second=ri.upsert(conn,r)
        self.assertNotEqual(first['id'],91); self.assertEqual(first['id'],second['id']);self.assertTrue(second['updated'])
        self.assertEqual(conn.execute('SELECT recipe_id FROM plan').fetchone()[0],91)
        self.assertEqual(conn.execute('SELECT thumbs FROM feedback').fetchone()[0],1)
        other=ri.upsert(conn,ri.normalize(sample(url='https://example.com/soup')))
        self.assertNotEqual(first['id'],other['id'])
        conn.close()


class APITests(unittest.TestCase):
    setUp = test_planner.PlannerTests.setUp
    tearDown = test_planner.PlannerTests.tearDown
    def test_preview_persist_patch_and_upsert(self):
        raw=sample()
        before=len(self.client.get('/api/recipes').json())
        preview=self.client.post('/api/recipes/import',json={'content':raw})
        self.assertEqual(preview.status_code,200)
        self.assertEqual(len(self.client.get('/api/recipes').json()),before)
        payload={'content':raw,'preview':False}
        result=self.client.post('/api/recipes/import',json=payload).json();rid=result['id']
        self.client.post('/api/plan',json={'date':'2026-11-02','recipe_id':rid})
        self.client.post('/api/feedback',json={'recipe_id':rid,'thumbs':1})
        self.assertEqual(self.client.post('/api/recipes/import',json=payload).json()['id'],rid)
        result=self.client.patch(f'/api/recipes/{rid}',json={'minutes':99,'metadata':{'rating':{'count':9}}})
        self.assertEqual(result.status_code,200)
        recipe=self.client.get(f'/api/recipes/{rid}').json()
        self.assertEqual(recipe['minutes'],99)
        self.assertEqual(recipe['metadata']['rating']['value'],4.8)
        self.assertEqual(recipe['metadata']['rating']['count'],9)
        self.assertEqual(recipe['metadata']['raw_jsonld'],raw)
        self.assertEqual(recipe['preference']['ups'],1)
        self.assertEqual(self.client.get('/api/plan?week_start=2026-11-02').json()['meals'][0]['recipe']['id'],rid)
    def test_errors_and_pagination(self):
        for payload in ({'content':'blocked'}, {'content':sample(recipeIngredient=['½ cup peanut butter'])}):
            self.assertEqual(self.client.post('/api/recipes/import',json=payload).status_code,400)
        with patch('recipe_import.fetch_url',side_effect=OSError('network blocked')):
            self.assertEqual(self.client.post('/api/recipes/import',json={'url':'https://example.com'}).status_code,400)
        self.client.post('/api/recipes/import',json={'content':sample(),'preview':False})
        result=self.client.get('/api/recipes/browse?source=NYT&tag=easy&category=Dinner&sort=rating&limit=1').json()
        self.assertEqual(result['total'],1); self.assertEqual(len(result['items']),1)
        self.assertNotIn('raw_jsonld',result['items'][0]['metadata'])
        self.assertEqual(self.client.get('/api/recipes/browse?limit=999').json()['limit'],60)
    def test_facets_autocomplete(self):
        self.client.post('/api/recipes/import',json={'content':sample(),'preview':False})
        self.assertEqual(self.client.get('/api/recipes/facets?field=bogus').status_code,422)
        srcs=self.client.get('/api/recipes/facets?field=source&q=nyt').json()['items']
        self.assertEqual(srcs[0]['value'],'NYT Cooking'); self.assertEqual(srcs[0]['count'],1)
        self.assertEqual(self.client.get('/api/recipes/facets?field=source&q=zzz').json()['items'],[])
        tags=self.client.get('/api/recipes/facets?field=tag&q=ea').json()['items']
        self.assertIn('easy',[t['value'] for t in tags])
        cats=self.client.get('/api/recipes/facets?field=category&q=din').json()['items']
        self.assertTrue(any('Dinner' in c['value'] for c in cats))
        self.assertTrue(self.client.get('/api/recipes/facets?field=name&q=sou').json()['items'])


class NetworkTests(unittest.TestCase):
    def test_private_and_unsafe_targets(self):
        for url in ('file:///etc/passwd','javascript:alert(1)','http://user:pw@example.com','http://127.0.0.1','http://[::1]','http://169.254.169.254','http://192.168.1.2','http://0.0.0.0'):
            with self.subTest(url=url), self.assertRaises((ValueError,OSError)):
                ri.public_addresses(url)
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('10.0.0.1',80))]):
            with self.assertRaises(ValueError): ri.public_addresses('https://example.com')
    def test_redirect_target_validated_and_ip_pinned(self):
        sock=MagicMock(); connection=MagicMock();response=connection.getresponse.return_value
        response.status=302;response.getheader.return_value='http://127.0.0.1/private'
        public=(ri.urlsplit('http://example.com'),[(2,1,6,'',('93.184.216.34',80))])
        with patch('recipe_import.public_addresses',side_effect=[public,ValueError('Blocked non-public')]),patch('recipe_import.socket.socket',return_value=sock),patch('recipe_import.http.client.HTTPConnection',return_value=connection):
            with self.assertRaisesRegex(ValueError,'Blocked'): ri.fetch_url('http://example.com')
            sock.connect.assert_called_once_with(('93.184.216.34',80))
    def test_http_error_and_size(self):
        public=(ri.urlsplit('http://example.com'),[(2,1,6,'',('93.184.216.34',80))])
        for status in (403,429,500):
            connection=MagicMock();connection.getresponse.return_value.status=status
            with patch('recipe_import.public_addresses',return_value=public),patch('recipe_import.socket.socket'),patch('recipe_import.http.client.HTTPConnection',return_value=connection):
                with self.assertRaisesRegex(ValueError,str(status)):ri.fetch_url('http://example.com')
        connection=MagicMock();response=connection.getresponse.return_value;response.status=200;response.read1.return_value=b'x'*65536
        with patch('recipe_import.public_addresses',return_value=public),patch('recipe_import.socket.socket'),patch('recipe_import.http.client.HTTPConnection',return_value=connection):
            with self.assertRaisesRegex(ValueError,'3 MB'):ri.fetch_url('http://example.com')


class UITests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node unavailable for dependency-free UI checks')
    def test_xss_safe_rendering(self):
        result = subprocess.run(['node', 'tests/test_recipes_ui.js'], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ArchiveTests(unittest.TestCase):
    def test_real_archive_readonly_bounded_temp_import(self):
        archive=Path('/home/hermesadmin/nytcooking/nytcooking.db')
        if not archive.exists():self.skipTest('private archive unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'target.db'
            with contextlib.redirect_stdout(io.StringIO()):
                first=run(archive,target,limit=30,batch_size=7)
                second=run(archive,target,limit=30,batch_size=7)
            self.assertEqual(first['processed'],30)
            self.assertEqual(first['errors'],0)
            self.assertEqual(second['updated'],first['added'])
            self.assertEqual(second['added'],0)
            before=target.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):run(archive,target,limit=5,dry_run=True)
            self.assertEqual(before,target.read_bytes())
            conn=sqlite3.connect(target)
            raw=json.loads(conn.execute('SELECT metadata FROM recipes LIMIT 1').fetchone()[0])
            self.assertIn('raw_jsonld',raw);self.assertIn('rating',raw);conn.close()
    def test_cli_reject_report_and_dryrun(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/'archive.db';target=Path(tmp)/'target.db'
            conn=sqlite3.connect(archive);conn.execute('CREATE TABLE recipes(url TEXT,state TEXT,ldjson TEXT)')
            for i,raw in enumerate((sample(),sample(recipeIngredient=['peanuts']),{})):
                conn.execute('INSERT INTO recipes VALUES(?,?,?)',(str(i),'fetched',json.dumps(raw)))
            conn.commit();conn.close()
            with contextlib.redirect_stdout(io.StringIO()):report=run(archive,target,limit=10,dry_run=True)
            self.assertFalse(target.exists());self.assertEqual(report['added'],1);self.assertEqual(report['rejected'],1);self.assertEqual(report['errors'],1)
