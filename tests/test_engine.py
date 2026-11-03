import json
import logging
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, quote_from_bytes

from tests import reset_data

import datastore

try:
    from mitmproxy.test import tflow, tutils
    import newgreedy_addon as addon
    # the instance mitmproxy would load: stopped, so it leaves the data folder to the tests
    getattr(addon.addons[0], "_stop_evt", None) and addon.addons[0]._stop_evt.set()
    logging.getLogger("NewGreedy").propagate = False      # log file only, no console noise
except ImportError:
    addon = None

IH = bytes(range(20))
FULL = IH.hex()
OTHER = FULL[:8] + "f" * 32
RESPONSE = b"d8:intervali1800e8:completei5e10:incompletei3e5:peers0:e"


REAL_PEER_ID = "-qB4250-abcdefghijkl"
SIZE = 10_000_000_000
# figures that do not depend on chance or on the hour of the day
STEADY = dict(anti_detection__desync_announces="false", anti_detection__startup_burst="false",
              advanced__interval_jitter_pct="0", spoofing__upload_noise_pct="0",
              spoofing__stagnation_probability="0", bandwidth__circadian_smoothing="false",
              bandwidth__global_max_speed_mbps="0", spoofing__target_ratio="50",
              spoofing__max_ratio_per_torrent="100", spoofing__max_global_ratio_per_tracker="100")


def announce(engine, ih=IH, event="", downloaded=50_000_000, left=0, host="tracker.example"):
    q = (f"info_hash={quote_from_bytes(ih, safe='')}&peer_id={REAL_PEER_ID}&port=51413"
         f"&uploaded=0&downloaded={downloaded}&left={left}" + (f"&event={event}" if event else ""))
    f = tflow.tflow(req=tutils.treq(host=host, port=80, path=("/announce?" + q).encode()),
                    resp=tutils.tresp(content=RESPONSE))
    engine.request(f)
    engine.response(f)
    return f


def sent(flow):
    """The query that left for the tracker, decoded."""
    raw = flow.request.data.path.split(b"?", 1)[1].decode("latin-1")
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}


class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now


@unittest.skipIf(addon is None, "mitmproxy not installed")
class EngineTestCase(unittest.TestCase):
    def start(self):
        engine = addon.NewGreedyAddon()
        engine._stop_evt.set()      # background loop off: the tests drive it
        self.addCleanup(engine.done)
        return engine

    def setUp(self):
        reset_data()


class Keys(EngineTestCase):
    def test_new_torrent_keyed_by_full_hash(self):
        engine = self.start()
        f = announce(engine)
        self.assertEqual(list(engine._stats), [FULL])
        self.assertIn(b"uploaded=", f.request.data.path)
        engine._save_stats(force=True)
        saved = json.loads(datastore.STATS_FILE.read_text())
        self.assertIn(FULL, saved)

    def test_legacy_entry_migrated_with_its_control(self):
        datastore.STATS_FILE.write_text(json.dumps({
            "_schema_version": 7,
            FULL[:8]: {"cumul_rep_ul": 123e6, "cumul_rep_dl": 50e6, "ann_count": 12}}))
        datastore.CONTROL_FILE.write_text(json.dumps({"paused": [FULL[:8]], "tags": {FULL[:8]: ["t"]}}))
        engine = self.start()
        announce(engine)
        self.assertEqual(list(engine._stats), [FULL])
        st = engine._stats[FULL]
        self.assertGreaterEqual(st._cumul_rep_ul, 123e6)
        self.assertTrue(st._paused)
        self.assertEqual(st._tags, ["t"])

    def test_legacy_entry_of_another_torrent_not_taken(self):
        datastore.STATS_FILE.write_text(json.dumps({
            "_schema_version": 7, FULL[:8]: {"cumul_rep_ul": 1.0, "cumul_rep_dl": 1.0}}))
        datastore.REGISTRY_FILE.write_text(json.dumps({FULL[:8]: {"info_hash": OTHER}}))
        engine = self.start()
        announce(engine)
        self.assertEqual(sorted(engine._stats), sorted([FULL[:8], FULL]))

    def test_torrents_sharing_a_prefix_stay_apart(self):
        engine = self.start()
        announce(engine, IH)
        announce(engine, bytes.fromhex(OTHER), downloaded=10)
        self.assertEqual(len(engine._stats), 2)


class Persistence(EngineTestCase):
    def test_web_removal_not_undone_by_engine_save(self):
        engine = self.start()
        announce(engine)
        engine._save_stats(force=True)
        # what the web UI does: remove under the stats lock, queue the purge first
        with datastore.file_lock(datastore.STATS_FILE):
            data = json.loads(datastore.STATS_FILE.read_text())
            data.pop(FULL)
            datastore.queue_purge([FULL])
            datastore.atomic_write_json(datastore.STATS_FILE, data)
        engine._save_stats(force=True)      # before the background loop applied the purge
        self.assertNotIn(FULL, json.loads(datastore.STATS_FILE.read_text()))
        self.assertNotIn(FULL, engine._stats)

    def test_stats_reload(self):
        engine = self.start()
        announce(engine)
        engine._save_stats(force=True)
        again = self.start()
        self.assertEqual(again._stats[FULL]._ann_count, 1)


class Upload(EngineTestCase):
    def setUp(self):
        reset_data(**STEADY)
        self.clock = Clock()
        for patcher in (mock.patch("time.time", self.clock),
                        # the occasional burst or lull of the noise model: off here
                        mock.patch("engine_model._pareto_noise", lambda inc, pct: inc)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def rounds(self, engine, hosts, n=6, downloaded=50_000_000_000, left=1_000_000_000):
        flows = []
        for _ in range(n):
            for host in hosts:
                flows.append(announce(engine, downloaded=downloaded, left=left, host=host))
                self.clock.now += 1
            self.clock.now += 1800 - len(hosts)
        return flows

    def test_first_announce_reports_no_upload(self):
        f = announce(self.start(), event="started", downloaded=0, left=SIZE)
        self.assertEqual(sent(f)["uploaded"], "0")

    def test_several_trackers_share_one_upload_clock(self):
        one = self.start()
        self.rounds(one, ["a.example"])
        reset_data(**STEADY)
        three = self.start()
        self.rounds(three, ["a.example", "b.example", "c.example"])
        ul1, ul3 = one._stats[FULL]._cumul_rep_ul, three._stats[FULL]._cumul_rep_ul
        self.assertGreater(ul1, 0)
        self.assertLess(abs(ul3 - ul1) / ul1, 0.01, (ul1, ul3))

    def test_upload_never_beats_the_configured_speed(self):
        engine = self.start()
        self.rounds(engine, ["a.example", "b.example"])
        speed = engine._stats[FULL]._cumul_rep_ul / (5 * 1800)
        self.assertLessEqual(speed, engine._stats_params[2] * 1.001)

    def test_global_ratio_cap_is_honoured(self):
        reset_data(**dict(STEADY, spoofing__max_global_ratio_per_tracker="0.3"))
        engine = self.start()
        flows = self.rounds(engine, ["a.example"])
        for f in flows:
            self.assertLessEqual(int(sent(f)["uploaded"]), 0.3 * 50_000_000_000 + 1)
        self.assertGreater(int(sent(flows[-1])["uploaded"]), 0)
        tc = engine._tracker_cumul["a.example"]
        self.assertAlmostEqual(tc["ul"], engine._stats[FULL]._cumul_rep_ul, delta=1)

    def test_shared_uplink_budget_spans_the_announce_interval(self):
        reset_data(**dict(STEADY, bandwidth__global_max_speed_mbps="2"))
        engine = self.start()
        self.rounds(engine, ["a.example"], n=2)
        # one 1800 s interval at 2 MB/s, not one minute's worth
        self.assertAlmostEqual(engine._stats[FULL]._cumul_rep_ul, 2e6 * 1800, delta=2e6 * 1800 * 0.02)

    def test_tracker_in_backoff_still_gets_a_rewritten_announce(self):
        engine = self.start()
        announce(engine)
        engine._tracker_errors["tracker.example"] = (5, time.time())
        self.clock.now += 60
        self.assertTrue(engine._tracker_in_backoff("tracker.example"))
        f = announce(engine)
        self.assertNotEqual(sent(f)["peer_id"], REAL_PEER_ID)


class Progress(EngineTestCase):
    def setUp(self):
        reset_data(bandwidth__max_download_speed_mbps="1")
        datastore.REGISTRY_FILE.write_text(json.dumps({FULL: {"info_hash": FULL, "size_bytes": SIZE}}))
        self.clock = Clock()
        patcher = mock.patch("time.time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def figures(self, f):
        q = sent(f)
        return int(q["downloaded"]), int(q["left"])

    def test_resumed_torrent_reported_where_the_client_is_then_complete(self):
        engine = self.start()
        f = announce(engine, event="started", downloaded=0, left=3_000_000_000)
        self.assertEqual(self.figures(f), (7_000_000_000, 3_000_000_000))
        self.clock.now += 1800
        f = announce(engine, event="completed", downloaded=3_000_000_000, left=0)
        self.assertEqual(self.figures(f), (SIZE, 0))

    def test_progress_the_client_did_not_download_is_still_paced(self):
        engine = self.start()
        announce(engine, downloaded=3_000_000_000, left=7_000_000_000)
        self.clock.now += 1800
        dl, left = self.figures(announce(engine, downloaded=0, left=0))
        self.assertGreater(dl, 3_000_000_000)
        self.assertLessEqual(dl, 3_000_000_000 + 1e6 * 1800)
        self.assertEqual(dl + left, SIZE)
        self.assertTrue(engine._stats[FULL]._dl_capped)


class Trash(EngineTestCase):
    def test_restore_of_a_torrent_that_came_back_keeps_the_trashed_entry(self):
        engine = self.start()
        announce(engine)
        old = {"cumul_rep_ul": 9e9, "cumul_rep_dl": 1e9, "ann_count": 40}
        datastore.queue_stats_import({FULL: old}, "merge")
        engine._apply_import()
        self.assertNotEqual(engine._stats[FULL]._cumul_rep_ul, 9e9)
        self.assertEqual(datastore.trash_list()[FULL]["data"]["cumul_rep_ul"], 9e9)


class FailClosed(EngineTestCase):
    def test_reports_the_failure_and_blocks_announces(self):
        # the case that matters: files of two versions, the engine modules never imported
        with mock.patch.dict(addon.__dict__):
            for name in ("datastore", "notify", "cs"):
                addon.__dict__.pop(name, None)
            engine = addon.FailClosedAddon("files of two versions")
        status = datastore.read_status()
        self.assertEqual(status["state"], "failed")
        self.assertIn("two versions", status["error"])
        f = tflow.tflow(req=tutils.treq(host="tracker.example", port=80, path=b"/announce?info_hash=x"))
        engine.request(f)
        self.assertIn(b"failure reason", f.response.content)


if __name__ == "__main__":
    unittest.main()
