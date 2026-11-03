import base64
import hashlib
import io
import unittest
import zipfile

from tests import reset_data   # imported first: points the data folder at a temp dir

import updater

setUpModule = reset_data


def zip_of(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, b"x")
    return zipfile.ZipFile(io.BytesIO(buf.getvalue()))


class SafeMembers(unittest.TestCase):
    def test_root_folder_stripped_and_data_files_skipped(self):
        zf = zip_of(["NG/newgreedy.py", "NG/static/app.js", "NG/config.ini", "NG/stats.json"])
        self.assertEqual(sorted(rel for _, rel in updater._safe_members(zf)),
                         ["newgreedy.py", "static/app.js"])

    def test_escapes_rejected(self):
        bad = ["NG/../evil.py", "NG/C:/evil.py", "NG/a\\b.py", "NG/x.py:stream"]
        zf = zip_of(["NG/ok.py"] + bad)
        self.assertEqual([rel for _, rel in updater._safe_members(zf)], ["ok.py"])
        # a lone leading "/" reads as the archive's root folder: the file stays in the program folder
        self.assertEqual(list(updater._safe_members(zip_of(["/abs.py"]))), [("/abs.py", "abs.py")])


class Checksums(unittest.TestCase):
    def test_expected_digest_formats(self):
        d = hashlib.sha256(b"a").hexdigest()
        self.assertEqual(updater._expected_digest(f"{d}  NewGreedy.zip\n", "NewGreedy.zip"), d)
        self.assertEqual(updater._expected_digest(f"{d} *dist/NewGreedy.zip", "NewGreedy.zip"), d)
        self.assertEqual(updater._expected_digest(d, "NewGreedy.zip"), d)
        with self.assertRaises(ValueError):
            updater._expected_digest("nope", "NewGreedy.zip")

    def test_assets_need_a_checksum(self):
        rel = {"assets": [{"name": "NG.zip", "browser_download_url": "u1"}]}
        with self.assertRaises(ValueError):
            updater._pick_assets(rel)
        rel["assets"] += [{"name": "NG.zip.sha256", "browser_download_url": "u2"},
                          {"name": "NG.zip.sig", "browser_download_url": "u3"}]
        self.assertEqual(updater._pick_assets(rel), ("NG.zip", "u1", "NG.zip.sha256", "u2", "u3"))

    def test_hosts(self):
        updater._check_host("https://github.com/x")
        for url in ("http://github.com/x", "https://evil.example/x"):
            with self.assertRaises(ValueError):
                updater._check_host(url)


try:
    import cryptography  # noqa: F401 - comes with mitmproxy
except ImportError:
    cryptography = None


@unittest.skipIf(cryptography is None, "cryptography not installed")
class Signatures(unittest.TestCase):
    def setUp(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self.key = Ed25519PrivateKey.generate()
        self.pub = base64.b64encode(self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()

    def test_valid_signature(self):
        sig = base64.b64encode(self.key.sign(b"archive")).decode()
        updater._verify_signature(b"archive", sig, self.pub)

    def test_tampered_archive_or_bad_signature(self):
        sig = base64.b64encode(self.key.sign(b"archive")).decode()
        for archive, s in ((b"archivE", sig), (b"archive", "AAAA"), (b"archive", "not base64!")):
            with self.assertRaises(ValueError):
                updater._verify_signature(archive, s, self.pub)


if __name__ == "__main__":
    unittest.main()
