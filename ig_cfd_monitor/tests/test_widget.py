"""HTTP isolation boundaries for the external reference-chart wrapper."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def directives(policy):
    return {parts[0]: parts[1:] for item in policy.split(';') if (parts := item.strip().split())}


class WidgetIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root/'web/vendor').mkdir(parents=True)
        (cls.root/'web/index.html').write_text('<html>synthetic-private-monitor</html>')
        (cls.root/'web/tv-reference.html').write_text('<html><script src="/vendor/tv-reference.js"></script>synthetic-widget</html>')
        (cls.root/'web/vendor/tv-reference.js').write_text('window.syntheticPublicReferenceBundle=true;')
        (cls.root/'web/live.js').write_text('window.syntheticPrivateMonitor=true;')
        cls.patches = [patch.object(server, 'ROOT', cls.root),
                       patch.object(server, 'ACCESS_FILE', cls.root/'state/access.json'),
                       patch.object(server, 'MONITOR')]
        for item in cls.patches:item.start()
        server.MONITOR.project.return_value = {'status': 'connected', 'markets': [], 'history': {},
                                               'private_test_value': 'synthetic-account-secret'}
        server.set_viewer_password('synthetic-widget-password')
        cls.cookie = server.COOKIE+'='+server.session_token(server.access_config())
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown();cls.httpd.server_close();cls.thread.join()
        for item in reversed(cls.patches):item.stop()
        cls.temp.cleanup()

    def request(self, path, method='GET', auth=False, **headers):
        sent = {'Host': 'monitor.example', **headers}
        if auth:sent['Cookie'] = self.cookie
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        connection.request(method, path, headers=sent)
        reply = connection.getresponse()
        status, received, body = reply.status, dict(reply.getheaders()), reply.read()
        connection.close()
        return status, received, body

    def test_main_document_scripts_and_connections_stay_self_only(self):
        status, headers, _ = self.request('/', auth=True)
        self.assertEqual(status, 200)
        policy = directives(headers['Content-Security-Policy'])
        self.assertEqual(policy['script-src'], ["'self'"])
        self.assertEqual(policy['connect-src'], ["'self'"])
        self.assertEqual(set(policy['frame-src']), {"'self'", 'https://s.tradingview.com',
                                                    'https://www.tradingview.com', 'https://www.tradingview-widget.com'})
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')

    def test_wrapper_requires_cookie_and_is_sandboxed_even_when_opened_directly(self):
        status, _, body = self.request('/tv-reference.html')
        self.assertEqual(status, 200)
        self.assertIn(b'type="password"', body)
        self.assertNotIn(b'synthetic-widget', body)
        for headers in ({}, {'Sec-Fetch-Dest': 'iframe'}, {'Sec-Fetch-Dest': 'document'}):
            status, received, body = self.request('/tv-reference.html', auth=True, **headers)
            self.assertEqual(status, 200)
            self.assertIn(b'synthetic-widget', body)
            policy = directives(received['Content-Security-Policy'])
            self.assertEqual(policy['sandbox'], ['allow-scripts'])
            self.assertNotIn('allow-same-origin', received['Content-Security-Policy'])
            self.assertEqual(policy['connect-src'], ["'none'"])
            self.assertEqual(set(policy['script-src']), {"'self'", 'https://s3.tradingview.com'})
            self.assertEqual(set(policy['frame-src']), {'https://s.tradingview.com', 'https://www.tradingview.com', 'https://www.tradingview-widget.com'})
            self.assertNotIn('frame-ancestors', policy)
            self.assertEqual(received['Referrer-Policy'], 'no-referrer')

    def test_only_exact_public_bundle_is_available_without_cookie(self):
        for method in ('GET', 'HEAD'):
            status, headers, body = self.request('/vendor/tv-reference.js', method)
            self.assertEqual(status, 200)
            self.assertTrue(headers['Content-Type'].startswith('text/javascript'))
            self.assertNotIn('Set-Cookie', headers)
            self.assertNotIn(b'synthetic-account-secret', body)
            if method == 'GET':self.assertEqual(body, b'window.syntheticPublicReferenceBundle=true;')
            else:self.assertEqual(body, b'')
        for path in ('/live.js', '/charts.js', '/style.css', '/vendor/lightweight-charts.standalone.production.js', '/vendor/tv-reference.js/extra', '/vendor/tv-reference.js.map'):
            status, _, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(b'type="password"', body)
            self.assertNotIn(b'syntheticPublicReferenceBundle', body)
        for path in ('/api/state', '/api/events', '/api/ledger.csv', '/api/chart?epic=X&resolution=DAY', '/api/chart/drawings?epic=X'):
            status, _, body = self.request(path)
            self.assertEqual(status, 401)
            self.assertEqual(json.loads(body)['error'], 'authentication_required')

    def test_null_origin_has_no_cors_access_to_private_apis(self):
        for auth in (False, True):
            status, headers, body = self.request('/api/state', auth=auth, Origin='null')
            self.assertEqual(status, 200 if auth else 401)
            self.assertFalse(any(name.lower().startswith('access-control-') for name in headers))
            if not auth:self.assertNotIn(b'synthetic-account-secret', body)
        status, headers, _ = self.request('/api/state', 'OPTIONS', Origin='null', **{'Access-Control-Request-Method': 'GET'})
        self.assertEqual(status, 405)
        self.assertFalse(any(name.lower().startswith('access-control-') for name in headers))
        self.assertEqual(self.request('/api/chart/drawings?epic=X', 'PUT', auth=True, Origin='null')[0], 403)


if __name__ == '__main__':
    unittest.main()
