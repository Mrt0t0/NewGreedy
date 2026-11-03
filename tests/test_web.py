import json
import unittest

from tests import reset_data

import config_schema as cs
import datastore

try:
    from fastapi.testclient import TestClient
    import newgreedy_web as web
except (ImportError, RuntimeError):      # fastapi.testclient needs httpx
    TestClient = None

FULL = "0123456789abcdef0123456789abcdef01234567"
LOCAL = "http://127.0.0.1:8080"


@unittest.skipIf(TestClient is None, "fastapi test client (httpx) not installed")
class WebTestCase(unittest.TestCase):
    settings = {}

    def setUp(self):
        reset_data(**self.settings)
        web.set_config(cs.load(datastore.CONFIG_FILE))
        web._rate_buckets.clear()
        web._invalidate_cache()
        self.client = TestClient(web.app, base_url=LOCAL)

    def post(self, path, body, **headers):
        return self.client.post(path, json=body, headers={"Origin": LOCAL, **headers})


class HostAndOrigin(WebTestCase):
    def test_dns_rebinding_refused_without_token(self):
        r = self.client.get("/api/config", headers={"Host": "evil.example:8080"})
        self.assertEqual(r.status_code, 421)
        r = self.client.post("/api/config/set", headers={"Host": "evil.example:8080",
                                                         "Origin": "http://evil.example:8080"},
                             json={"section": "notifications", "key": "webhook_url",
                                   "value": "https://attacker.example/hook"})
        self.assertEqual(r.status_code, 421)
        self.assertEqual(cs.get(cs.load(datastore.CONFIG_FILE), "notifications", "webhook_url"), "")

    def test_loopback_names_accepted(self):
        for host in ("127.0.0.1:8080", "localhost:8080", "[::1]:8080"):
            self.assertEqual(self.client.get("/api/config", headers={"Host": host}).status_code, 200, host)

    def test_health_stays_public(self):
        r = self.client.get("/api/health", headers={"Host": "evil.example"})
        self.assertNotEqual(r.status_code, 421)

    def test_cross_site_post_refused(self):
        r = self.client.post("/api/config/reload", json={}, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)


class AllowedOrigin(WebTestCase):
    settings = {"web__web_allowed_origins": "https://ng.example.org"}

    def test_configured_reverse_proxy_name_accepted(self):
        r = self.client.get("/api/config", headers={"Host": "ng.example.org"})
        self.assertEqual(r.status_code, 200)


class WithToken(WebTestCase):
    settings = {"web__web_secret_token": "s3cret-token-123"}

    def test_token_required_and_any_host_then_allowed(self):
        self.assertEqual(self.client.get("/api/config").status_code, 401)
        r = self.client.get("/api/config", headers={"X-Auth-Token": "s3cret-token-123",
                                                    "Host": "nas.lan:8080"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["web"]["web_secret_token"], "***")


class Secrets(WebTestCase):
    settings = {"notifications__webhook_url": "https://discord.com/api/webhooks/1/TOKEN",
                "upstream__proxy_url": "http://user:pa55@gluetun:8888"}

    def test_urls_masked(self):
        c = self.client.get("/api/config").json()
        self.assertEqual(c["notifications"]["webhook_url"], "https://discord.com/***")
        self.assertEqual(c["upstream"]["proxy_url"], "http://user:***@gluetun:8888")
        self.assertNotIn("TOKEN", json.dumps(c))
        self.assertNotIn("pa55", json.dumps(c))

    def test_masked_value_sent_back_changes_nothing(self):
        r = self.post("/api/config/set", {"section": "notifications", "key": "webhook_url",
                                          "value": "https://discord.com/***"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(cs.get(cs.load(datastore.CONFIG_FILE), "notifications", "webhook_url"),
                         "https://discord.com/api/webhooks/1/TOKEN")
        r = self.post("/api/config/set", {"section": "notifications", "key": "webhook_url",
                                          "value": "https://other.example/***"})
        self.assertEqual(r.status_code, 400)


class Control(WebTestCase):
    def setUp(self):
        super().setUp()
        datastore.CONTROL_FILE.write_text(json.dumps({
            "paused": [FULL[:8]], "overrides": {FULL[:8]: {"target_ratio": 2.0}},
            "tags": {FULL[:8]: ["old"]}}))
        datastore.STATS_FILE.write_text(json.dumps({FULL: {"cumul_rep_ul": 10, "cumul_rep_dl": 5}}))

    def test_legacy_entries_seen_under_full_hash(self):
        e = self.client.get("/api/stats").json()[FULL]
        self.assertTrue(e["paused"])
        self.assertEqual(e["tags"], ["old"])
        self.assertEqual(e["override"], {"target_ratio": 2.0})

    def test_writes_replace_the_legacy_alias(self):
        self.assertEqual(self.post("/api/control/pause", {"hash": FULL, "paused": False}).status_code, 200)
        self.post("/api/control/override", {"hash": FULL, "cumul_rep_ul": 1000})
        self.post("/api/control/tags", {"hash": FULL, "tags": "new"})
        ctl = json.loads(datastore.CONTROL_FILE.read_text())
        self.assertEqual(ctl["paused"], [])
        self.assertEqual(ctl["overrides"], {FULL: {"target_ratio": 2.0, "cumul_rep_ul": 1000.0}})
        self.assertEqual(ctl["tags"], {FULL: ["new"]})

    def test_removal_queues_the_purge(self):
        self.assertEqual(self.client.delete(f"/api/stats/{FULL}", headers={"Origin": LOCAL}).status_code, 200)
        self.assertEqual(datastore.take_purges(), [FULL])
        self.assertIn(FULL, datastore.trash_list())


class TrashRestore(WebTestCase):
    def test_torrent_that_came_back_is_not_overwritten(self):
        datastore.STATS_FILE.write_text(json.dumps({FULL: {"cumul_rep_ul": 10, "cumul_rep_dl": 5}}))
        datastore.trash_add({FULL: {"cumul_rep_ul": 9e9, "cumul_rep_dl": 1e9}}, "manual")
        r = self.post(f"/api/trash/{FULL}/restore", {})
        self.assertEqual(r.status_code, 409)
        self.assertIn(FULL, datastore.trash_list())
        self.assertFalse(datastore.IMPORT_FILE.exists())


class InvalidToken(WebTestCase):
    settings = {"web__web_secret_token": "short"}

    def test_ui_locked_instead_of_open(self):
        self.assertEqual(self.client.get("/api/config").status_code, 401)


class Pages(WebTestCase):
    def test_version_placeholder_filled(self):
        html = self.client.get("/torrents").text
        self.assertNotIn("__NG_VERSION__", html)
        self.assertIn("app.js?v=", html)
        self.assertNotIn("cdn.jsdelivr.net", self.client.get("/charts").headers["content-security-policy"])


if __name__ == "__main__":
    unittest.main()
