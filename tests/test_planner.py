"""Legacy API and mounted read-only MCP regressions (stdlib unittest)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# App import initializes a DB; force a disposable one before import.
_BOOT = tempfile.TemporaryDirectory()
os.environ['PANTRY_DB'] = str(Path(_BOOT.name) / 'boot.db')
import app as pantry
from fastapi.testclient import TestClient


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pantry, 'DB_PATH', Path(self.tmp.name) / 'test.db')
        self.patch.start(); pantry.init_db()
        self.client = TestClient(pantry.app, base_url='http://localhost:8080')
    def tearDown(self):
        self.client.close(); self.patch.stop(); self.tmp.cleanup()
    def test_plan_actual_id_feedback_and_duplicate(self):
        recipes = self.client.get('/api/recipes').json()
        rid = recipes[-1]['id']
        payload = {'date':'2026-11-02','recipe_id':rid}
        result = self.client.post('/api/plan', json=payload)
        self.assertEqual(result.status_code, 200)
        pid = result.json()['added']
        self.assertEqual(self.client.post('/api/plan',json=payload).json(), {'added':pid,'existing':True})
        self.assertEqual(self.client.post('/api/plan',json={**payload,'recipe_id':recipes[0]['id']}).status_code,409)
        self.client.post('/api/feedback',json={'recipe_id':rid,'thumbs':1})
        meals = self.client.get('/api/plan?week_start=2026-11-02').json()['meals']
        self.assertEqual(len(meals),1)
        self.assertEqual(meals[0]['recipe']['id'],rid)
        self.assertEqual(meals[0]['recipe']['preference']['ups'],1)
        self.assertNotIn('plan_id',meals[0]['recipe'])
    def test_mcp_context_and_readonly(self):
        manager = pantry.pantry_mcp._tool_manager
        context = manager.get_tool('get_planning_context').fn('2026-11-02')
        self.assertEqual(context['target_week']['start'],'2026-11-02')
        self.assertEqual(context['previous_week']['start'],'2026-10-26')
        for tool in manager.list_tools():
            self.assertTrue(tool.annotations.readOnlyHint)
    def test_mcp_http_endpoint(self):
        headers={'content-type':'application/json','accept':'application/json, text/event-stream'}
        with self.client:
            payload={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'test','version':'1'}}}
            self.assertEqual(self.client.post('/mcp/',json=payload,headers=headers).status_code,200)
            payload.update(id=2,method='tools/list',params={})
            result=self.client.post('/mcp/',json=payload,headers=headers)
            self.assertEqual(result.status_code,200)
            self.assertIn('get_planning_context',result.text)
            payload.update(id=3,method='tools/call',params={'name':'get_planning_context','arguments':{'week_start':'2026-11-02'}})
            result=self.client.post('/mcp/',json=payload,headers=headers)
            self.assertEqual(result.status_code,200)
            self.assertIn('2026-11-02',result.text)
