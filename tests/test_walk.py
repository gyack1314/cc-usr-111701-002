"""核对清单走查、授权边界、版本闭包、缺口留存与重试回放。"""
import json, unittest
from pathlib import Path
from capture_data.io import load
from capture_data.fetch import TokenStore, ScriptedClient, Walker, VersionDrift, replay_client
from capture_data.journal import connect
from capture_data.projection import closure_view
from capture_data.cli import walk_command

DATA = Path(__file__).resolve().parents[1] / 'data'
MUSEUM = 'https://museum.example.test'
CDN = 'https://cdn.mirror.test'

def plan():
    p, _ = load(DATA / 'walk-case.json')
    return p

def run_walk(p, db, refresher=None):
    tokens = TokenStore()
    for t in p.get('tokens', []): tokens.register(t['label'], t['token'], t['origins'])
    client = ScriptedClient(p['session'])
    walker = Walker(client, tokens, db, p['collection'], refresher=refresher)
    return walker, client, walker.walk(p['manifest_url'])

class WalkTests(unittest.TestCase):
    def test_full_walk_closure_and_evidence(self):
        db = connect(':memory:')
        _, _, summary = run_walk(plan(), db)
        self.assertTrue(summary['complete'])
        self.assertEqual(summary['manifest_version'], 'etag:exhibit-12-v3')
        view = closure_view(db, 'exhibit-12')
        kinds = {}
        for it in view['items']: kinds[it['kind']] = kinds.get(it['kind'], 0) + 1
        self.assertEqual(kinds, {'manifest': 1, 'canvas': 2, 'page': 3, 'annotation': 4, 'resource': 4})
        for it in view['items']:
            self.assertEqual(it['status'], 'captured')
            self.assertEqual(it['manifest_version'], 'etag:exhibit-12-v3')
            self.assertTrue(it['digest'])
        manifest = next(i for i in view['items'] if i['kind'] == 'manifest')
        self.assertEqual(manifest['final_url'], MUSEUM + '/iiif/manifests/12')
        self.assertEqual(manifest['evidence']['chain'][0]['status'], 301)
        self.assertEqual(manifest['evidence']['status'], 200)
        self.assertTrue(view['business']['publishable'])
        self.assertTrue(view['business']['sealed'])
        self.assertEqual(view['verify'], {'duplicates': [], 'version_mixing': []})
        db.close()

    def test_token_scope_boundary(self):
        db = connect(':memory:')
        _, client, _ = run_walk(plan(), db)
        sent = {r['url']: r['headers'] for r in client.requests}
        self.assertEqual(sent[CDN + '/img/2.jpg'], {})
        self.assertEqual(sent[CDN + '/img/1.jpg'], {})
        self.assertEqual(sent[MUSEUM + '/media/1.jpg'].get('Authorization'), 'Bearer tok-exhibit-1')
        self.assertEqual(sent[MUSEUM + '/media/3.jpg'].get('Authorization'), 'Bearer tok-exhibit-1')
        view = closure_view(db, 'exhibit-12')
        media1 = next(i for i in view['items'] if i['id'] == MUSEUM + '/media/1.jpg')
        self.assertEqual(media1['final_url'], CDN + '/img/1.jpg')
        self.assertEqual(media1['auth_scope'], '')
        self.assertEqual(media1['evidence']['chain'][0]['auth_scope'], 'museum')
        media3 = next(i for i in view['items'] if i['id'] == MUSEUM + '/media/3.jpg')
        self.assertEqual(media3['auth_scope'], 'museum')
        db.close()

    def test_relative_redirect_pagination_bases(self):
        db = connect(':memory:')
        run_walk(plan(), db)
        view = closure_view(db, 'exhibit-12')
        ids = {i['id'] for i in view['items']}
        self.assertIn(MUSEUM + '/iiif/canvas/1', ids)
        self.assertIn(MUSEUM + '/iiif/pages/1', ids)
        self.assertIn(MUSEUM + '/iiif/pages/2', ids)
        self.assertIn(MUSEUM + '/iiif/pages/anno/1', ids)
        self.assertIn(MUSEUM + '/iiif/manifests/p/1', ids)
        self.assertIn(MUSEUM + '/media/1.jpg', ids)
        inline = next(i for i in view['items'] if i['id'].endswith('#body'))
        self.assertTrue(inline['payload']['inline'])
        self.assertTrue(inline['digest'])
        db.close()

    def test_gaps_keep_originals_without_sealing(self):
        p, _ = load(DATA / 'walk-gap-case.json')
        db = connect(':memory:')
        _, _, summary = run_walk(p, db)
        self.assertFalse(summary['complete'])
        reasons = {g['id']: g['reason'] for g in summary['gaps']}
        self.assertEqual(reasons[MUSEUM + '/media/gone.jpg'], 'withdrawn:410')
        self.assertEqual(reasons[MUSEUM + '/media/secret.jpg'], 'unauthorized:403')
        view = closure_view(db, 'exhibit-13')
        gone = next(i for i in view['items'] if i['id'] == MUSEUM + '/media/gone.jpg')
        self.assertEqual(gone['status'], 'gap')
        self.assertEqual(gone['evidence']['status'], 410)
        ok = next(i for i in view['items'] if i['id'] == MUSEUM + '/media/ok.jpg')
        self.assertEqual(ok['status'], 'captured')
        self.assertTrue(ok['digest'])
        self.assertFalse(view['business']['publishable'])
        self.assertFalse(view['business']['sealed'])
        self.assertEqual(len(view['business']['reasons']), 2)
        self.assertEqual(len(view['closures']), 1)
        self.assertFalse(view['closures'][0]['complete'])
        db.close()

    def test_refresh_same_version_continues(self):
        p = plan()
        p['session'][MUSEUM + '/media/3.jpg'] = {'status': 401, 'headers': {}, 'body_text': 'expired'}
        good = {'status': 200, 'headers': {'Content-Type': 'image/jpeg'},
                'body_base64': '/9j/4EVYSElCSVQtMTItSU1HLTP/2Q=='}
        def refresher(store, label):
            store.refresh(label, 'tok-exhibit-2')
            return None
        db = connect(':memory:')
        tokens = TokenStore(); tokens.register('museum', 'tok-exhibit-1', [MUSEUM])
        client = ScriptedClient(p['session'])
        def refresh_and_heal(store, label):
            refresher(store, label)
            client.session[MUSEUM + '/media/3.jpg'] = good
        summary = Walker(client, tokens, db, 'exhibit-12', refresher=refresh_and_heal).walk(p['manifest_url'])
        self.assertTrue(summary['complete'])
        media3 = [r for r in client.requests if r['url'] == MUSEUM + '/media/3.jpg']
        self.assertEqual([r['headers'].get('Authorization') for r in media3],
                         ['Bearer tok-exhibit-1', 'Bearer tok-exhibit-2'])
        manifests = [r for r in client.requests if r['url'] == MUSEUM + '/iiif/manifests/12']
        self.assertEqual(len(manifests), 2)
        view = closure_view(db, 'exhibit-12')
        item = next(i for i in view['items'] if i['id'] == MUSEUM + '/media/3.jpg')
        self.assertEqual(item['evidence']['chain'][0]['status'], 401)
        self.assertEqual(item['status'], 'captured')
        db.close()

    def test_refresh_version_drift_aborts(self):
        p = plan()
        p['session'][MUSEUM + '/media/3.jpg'] = {'status': 401, 'headers': {}, 'body_text': 'expired'}
        drifted = json.loads(json.dumps(p['session'][MUSEUM + '/iiif/manifests/12']['body']))
        drifted['items'][1]['items'][0]['items'][0]['body']['value'] = '旁注：第四版'
        drifted_manifest = {'status': 200, 'headers': {'ETag': '"exhibit-12-v4"', 'Content-Type': 'application/ld+json'}, 'body': drifted}
        db = connect(':memory:')
        tokens = TokenStore(); tokens.register('museum', 'tok-exhibit-1', [MUSEUM])
        client = ScriptedClient(p['session'])
        def refresher(store, label):
            store.refresh(label, 'tok-exhibit-2')
            client.session[MUSEUM + '/iiif/manifests/12'] = drifted_manifest
            client.session[MUSEUM + '/media/3.jpg'] = {'status': 200, 'headers': {}, 'body_base64': '/9j/4EVYSElCSVQtMTItSU1HLTP/2Q=='}
        with self.assertRaises(VersionDrift):
            Walker(client, tokens, db, 'exhibit-12', refresher=refresher).walk(p['manifest_url'])
        view = closure_view(db, 'exhibit-12')
        self.assertEqual({i['manifest_version'] for i in view['items']}, {'etag:exhibit-12-v3'})
        self.assertEqual(view['closures'], [])
        summary = Walker(client, tokens, db, 'exhibit-12', refresher=refresher).walk(p['manifest_url'])
        self.assertEqual(summary['manifest_version'], 'etag:exhibit-12-v4')
        view = closure_view(db, 'exhibit-12')
        self.assertEqual(len(view['closures']), 1)
        self.assertEqual(view['closures'][0]['manifest_version'], 'etag:exhibit-12-v4')
        self.assertTrue(view['verify']['version_mixing'])
        self.assertEqual(view['verify']['duplicates'], [])
        v3 = [i for i in view['items'] if i['manifest_version'] == 'etag:exhibit-12-v3']
        self.assertTrue(v3)
        db.close()

    def test_retry_has_no_duplicates_or_mixing(self):
        p = plan()
        db = connect(':memory:')
        run_walk(p, db)
        first = closure_view(db, 'exhibit-12')
        run_walk(p, db)
        second = closure_view(db, 'exhibit-12')
        self.assertEqual(len(first['items']), len(second['items']))
        self.assertEqual(db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0], 6)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM closures').fetchone()[0], 1)
        self.assertEqual(second['verify'], {'duplicates': [], 'version_mixing': []})
        self.assertEqual([i['digest'] for i in first['items']], [i['digest'] for i in second['items']])
        db.close()

    def test_replay_matches_journal(self):
        p = plan()
        db = connect(':memory:')
        run_walk(p, db)
        original = closure_view(db, 'exhibit-12')
        client = replay_client(db, 'exhibit-12')
        db2 = connect(':memory:')
        tokens = TokenStore(); tokens.register('museum', 'tok-exhibit-1', [MUSEUM])
        summary = Walker(client, tokens, db2, 'exhibit-12').walk(p['manifest_url'])
        self.assertTrue(summary['complete'])
        replayed = closure_view(db2, 'exhibit-12')
        key = lambda items: [(i['kind'], i['id'], i['manifest_version'], i['status'], i['digest'], i['final_url'], i['auth_scope']) for i in items]
        self.assertEqual(key(original['items']), key(replayed['items']))
        self.assertEqual(original['closures'], replayed['closures'])
        db.close(); db2.close()

    def test_same_version_content_conflict(self):
        p = plan()
        db = connect(':memory:')
        run_walk(p, db)
        before = db.execute('SELECT COUNT(*) FROM items').fetchone()[0]
        p['session'][MUSEUM + '/media/3.jpg'] = {'status': 200, 'headers': {'Content-Type': 'image/jpeg'}, 'body_text': 'tampered'}
        with self.assertRaises(ValueError):
            run_walk(p, db)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM items').fetchone()[0], before)
        view = closure_view(db, 'exhibit-12')
        item = next(i for i in view['items'] if i['id'] == MUSEUM + '/media/3.jpg')
        self.assertNotEqual(item['digest'], __import__('hashlib').sha256(b'tampered').hexdigest())
        db.close()

    def test_walk_plan_files_via_cli(self):
        p, _ = load(DATA / 'walk-case.json')
        out = walk_command(p, ':memory:')
        self.assertTrue(out['walk']['complete'])
        self.assertEqual(out['verify'], {'duplicates': [], 'version_mixing': []})
        g, _ = load(DATA / 'walk-gap-case.json')
        out = walk_command(g, ':memory:')
        self.assertFalse(out['walk']['complete'])
        self.assertFalse(out['business']['sealed'])
        self.assertEqual(len(out['business']['reasons']), 2)
