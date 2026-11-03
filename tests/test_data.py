import hashlib
import json
import re
import unittest

from tests import DATA, ROOT, reset_data

import _version
import datastore
from _version import bdecode, bd_find_int, bd_replace_int, bd_top_str, torrent_info, tracker_urls

FULL = "0123456789abcdef0123456789abcdef01234567"


def make_torrent(files=((b"a.bin", 1000),), announce=b"https://t.example/announce"):
    info = b"d5:filesl" + b"".join(
        b"d6:lengthi%de4:pathl%d:%see" % (size, len(name), name) for name, size in files
    ) + b"e4:name4:test12:piece lengthi16384e6:pieces0:e"
    return b"d8:announce%d:%s4:info%se" % (len(announce), announce, info), info


class Bencode(unittest.TestCase):
    def test_decode(self):
        self.assertEqual(bdecode(b"d1:ai1e1:bl2:xyee")[0], {b"a": 1, b"b": [b"xy"]})

    def test_depth_limit(self):
        with self.assertRaises(ValueError):
            bdecode(b"l" * 100 + b"e" * 100)

    def test_truncated_input_raises(self):
        for bad in (b"d1:a", b"5:ab", b"l1:a", b"x"):
            with self.assertRaises((ValueError, IndexError)):
                bdecode(bad)

    def test_find_and_replace_int(self):
        body = b"d8:intervali1800e12:min intervali900ee"
        self.assertEqual(bd_find_int(body, b"interval"), 1800)
        self.assertEqual(bd_find_int(body, b"min interval"), 900)
        patched = bd_replace_int(body, b"interval", 2000)
        self.assertEqual(bdecode(patched)[0][b"interval"], 2000)
        self.assertEqual(bdecode(patched)[0][b"min interval"], 900)

    def test_top_level_string(self):
        self.assertEqual(bd_top_str(b"d5:peers0:9:trackerid3:abce", b"trackerid"), b"abc")
        self.assertEqual(bd_top_str(b"d1:xd9:trackerid3:abcee", b"trackerid"), b"")

    def test_torrent_info_hash_and_total_size(self):
        raw, info = make_torrent(((b"a.bin", 1000), (b"b.bin", 2500)))
        ih, size = torrent_info(raw)
        self.assertEqual(ih, hashlib.sha1(info).hexdigest())
        self.assertEqual(size, 3500)
        self.assertEqual(tracker_urls(raw), ["https://t.example/announce"])


class Keys(unittest.TestCase):
    def test_alias_key(self):
        self.assertEqual(datastore.alias_key({FULL: 1}, FULL), FULL)
        self.assertEqual(datastore.alias_key({FULL[:8]: 1}, FULL), FULL[:8])
        self.assertEqual(datastore.alias_key({FULL[:8]}, FULL), FULL[:8])
        self.assertIsNone(datastore.alias_key({}, FULL))

    def test_registry_meta_refuses_a_colliding_legacy_entry(self):
        other = FULL[:8] + "f" * 32
        reg = {FULL[:8]: {"info_hash": other, "name": "other"}}
        self.assertIsNone(datastore.registry_meta(reg, FULL))
        self.assertEqual(datastore.registry_meta(reg, other)["name"], "other")
        self.assertEqual(datastore.registry_meta({FULL[:8]: {"name": "x"}}, FULL)["name"], "x")


class Files(unittest.TestCase):
    def setUp(self):
        reset_data()

    def test_update_json_and_purge_queue(self):
        datastore.queue_purge(["aa", "bb", "aa"])
        datastore.queue_purge(["cc"])
        self.assertEqual(datastore.take_purges(), ["aa", "bb", "cc"])
        self.assertEqual(datastore.take_purges(), [])

    def test_trash_roundtrip(self):
        datastore.trash_add({FULL: {"cumul_rep_ul": 1}}, "manual")
        self.assertIn(FULL, datastore.trash_list())
        self.assertEqual(datastore.trash_take(FULL)["data"], {"cumul_rep_ul": 1})
        self.assertEqual(datastore.trash_list(), {})

    def test_atomic_write_never_leaves_temp_files(self):
        for i in range(5):
            _version.atomic_write_json(DATA / "x.json", {"i": i})
        self.assertEqual(json.loads((DATA / "x.json").read_text())["i"], 4)
        self.assertEqual([p.name for p in DATA.iterdir() if p.name.startswith(".tmp_")], [])


class Release(unittest.TestCase):
    """The version is written in _version.py; the addon and config.ini must agree with it."""

    def test_versions_agree(self):
        addon = (ROOT / "newgreedy_addon.py").read_text(encoding="utf-8")
        self.assertIn(f'ADDON_VERSION = "{_version.VERSION}"', addon)
        cfg = (ROOT / "config.ini").read_text(encoding="utf-8")
        self.assertRegex(cfg, rf"(?m)^config_version\s*=\s*{re.escape(_version.VERSION)}$")

    def test_manifest_lists_every_module(self):
        listed = {ln.strip() for ln in (ROOT / "MANIFEST.txt").read_text().splitlines()
                  if ln.strip() and not ln.startswith("#")}
        for py in ROOT.glob("*.py"):
            self.assertIn(py.name, listed)
        for name in listed:
            self.assertTrue((ROOT / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
