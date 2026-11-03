import re
import unittest

from tests import DATA, ROOT, reset_data

import config_merge
import config_schema as cs


class ParseValue(unittest.TestCase):
    port = cs.RULES[("proxy", "listen_port")]
    ratio = cs.RULES[("spoofing", "target_ratio")]

    def test_non_finite_numbers_fall_back_to_default(self):
        for raw in ("inf", "-inf", "1e400", "nan"):
            self.assertEqual(cs.parse_value(self.port, raw), (3456, "must be a number"), raw)
            self.assertEqual(cs.parse_value(self.ratio, raw)[0], 1.5, raw)

    def test_get_never_raises(self):
        c = cs.new_parser()
        c.read_string("[proxy]\nlisten_port = 1e999\n")
        self.assertEqual(cs.get(c, "proxy", "listen_port"), 3456)

    def test_out_of_range_is_clamped_unless_strict(self):
        self.assertEqual(cs.parse_value(self.port, "70000")[0], 65535)
        self.assertEqual(cs.parse_value(self.port, "70000", strict=True)[0], 3456)

    def test_comma_decimal_and_normalize(self):
        self.assertEqual(cs.parse_value(self.ratio, "1,6"), (1.6, None))
        self.assertEqual(cs.normalize("spoofing", "target_ratio", "1,6"), "1.6")
        self.assertEqual(cs.normalize("ssl", "ssl_verify_trackers", "TRUE"), "true")

    def test_validate_value(self):
        self.assertIsNone(cs.validate_value("advanced", "inject_hours", "8-22"))
        self.assertIn("between", cs.validate_value("advanced", "inject_hours", "8-25"))


class SetValue(unittest.TestCase):
    def setUp(self):
        reset_data()
        self.path = DATA / "config.ini"

    def test_keeps_comments_and_layout(self):
        before = self.path.read_text(encoding="utf-8")
        cs.set_value(self.path, "spoofing", "target_ratio", "2.0")
        after = self.path.read_text(encoding="utf-8")
        self.assertEqual(before.count(";"), after.count(";"))
        self.assertEqual(cs.get(cs.load(self.path), "spoofing", "target_ratio"), 2.0)

    def test_adds_missing_key_and_section(self):
        cs.set_value(self.path, "tracker:example.org", "target_ratio", "1.1")
        c = cs.load(self.path)
        self.assertEqual(c.get("tracker:example.org", "target_ratio"), "1.1")


class Merge(unittest.TestCase):
    shipped = ROOT.joinpath("config.ini").read_text(encoding="utf-8")

    def test_user_values_kept_and_new_keys_added(self):
        current = re.sub(r"(?m)^target_ratio\s*=.*$", "target_ratio = 2.2", self.shipped)
        current = "\n".join(ln for ln in current.splitlines() if not ln.startswith("dry_run"))
        current += "\n[spoofing]\nmy_custom_key = 1\n"
        merged, report = config_merge.merge(self.shipped, current)
        parser = cs.new_parser()
        parser.read_string(merged)
        self.assertEqual(parser.get("spoofing", "target_ratio"), "2.2")
        self.assertTrue(parser.has_option("simulation", "dry_run"))
        self.assertEqual(parser.get("spoofing", "my_custom_key"), "1")
        self.assertIn(("simulation", "dry_run", "false"), report["added"])

    def test_retired_keys_dropped(self):
        merged, report = config_merge.merge(self.shipped, self.shipped + "\n[proxy]\ntracker_timeout = 5\n")
        self.assertNotIn("tracker_timeout", merged)
        self.assertEqual(len(report["removed"]), 1)

    def test_needs_merge(self):
        self.assertFalse(config_merge.needs_merge(self.shipped, self.shipped))
        self.assertTrue(config_merge.needs_merge(self.shipped, "[proxy]\nlisten_port = 1\n"))


class ShippedConfig(unittest.TestCase):
    def test_shipped_config_is_clean(self):
        problems = [i for i in cs.check(cs.load(ROOT / "config.ini")) if i[0] != "info"]
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
