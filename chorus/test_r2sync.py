import json
import pathlib
import tempfile
import unittest

from chorus import r2sync


class FakeS3:
    def __init__(self, keys=()):
        self.keys = set(keys)
        self.uploads = []

    def list_objects_v2(self, Bucket, Prefix, MaxKeys, ContinuationToken=None):
        keys = sorted(key for key in self.keys if key.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + MaxKeys]
        next_at = start + len(page)
        return {
            "Contents": [{"Key": key} for key in page],
            "IsTruncated": next_at < len(keys),
            "NextContinuationToken": str(next_at) if next_at < len(keys) else None,
        }

    def upload_file(self, source, bucket, key, ExtraArgs=None):
        self.keys.add(key)
        self.uploads.append((pathlib.Path(source).name, bucket, key, ExtraArgs))


class R2StreamTest(unittest.TestCase):
    def test_remote_inventory_is_authority_over_local_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            out = pathlib.Path(directory)
            sheets = out / "_sheets"
            sheets.mkdir()
            (out / "frame.png").write_bytes(b"frame")
            (sheets / "r2-ledger.json").write_text(json.dumps(["frame.png"]))
            protocol = out / "protocol"
            protocol.mkdir()
            (protocol / "PROTOCOL.md").write_text("law")
            s3 = FakeS3()

            remote = r2sync.remote_frame_names(s3, "bucket", "chorus")
            receipt, remote, _ = r2sync.sweep(
                s3, "bucket", out, remote, sheets / "r2-ledger.json",
                lambda _message: None, settle_seconds=0, remote_verified=True,
                protocol_dir=protocol)

            self.assertIn("frame.png", remote)
            self.assertIn("chorus/frames/frame.png", {row[2] for row in s3.uploads})
            self.assertEqual(receipt["frames"]["missing_settled"], 0)

    def test_remote_listing_follows_continuation(self):
        keys = {f"chorus/frames/{n:04}.png" for n in range(1005)}
        s3 = FakeS3(keys)
        self.assertEqual(len(r2sync.remote_frame_names(s3, "bucket")), 1005)

    def test_atlas_cells_and_manifest_are_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            out, protocol = root / "out", root / "chorus"
            sphere = out / "atlas" / "bell.sphere"
            sphere.mkdir(parents=True)
            protocol.mkdir()
            (sphere / "cell_00000.png").write_bytes(b"cell")
            (sphere / "manifest.json").write_text("{}")
            s3 = FakeS3()

            receipt, remote, _ = r2sync.sweep(
                s3, "bucket", out, set(), out / "_sheets/r2-ledger.json",
                lambda _message: None, settle_seconds=0, remote_verified=True,
                protocol_dir=protocol)

            keys = {row[2] for row in s3.uploads}
            self.assertIn("atlas/bell.sphere/cell_00000.png", remote)
            self.assertIn("chorus/frames/atlas/bell.sphere/cell_00000.png", keys)
            self.assertIn("chorus/state/atlas/bell.sphere/manifest.json", keys)
            self.assertEqual(receipt["frames"]["missing_settled"], 0)

    def test_complete_artistic_state_is_streamed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            out, protocol = root / "out", root / "chorus"
            (out / "_sheets").mkdir(parents=True)
            protocol.mkdir()
            for name in r2sync.LIVE_STATE_FILES:
                (out / name).write_text("{}" if name.endswith(".json") else "{}\n")
            (out / "_sheets" / "manifest.json").write_text("{}")
            (out / "_sheets" / "contact.jpg").write_bytes(b"jpg")
            (protocol / "PROTOCOL.md").write_text("law")
            (protocol / "language.py").write_text("VOICE = 1")

            sources = r2sync.state_sources(out, protocol)

            for name in r2sync.LIVE_STATE_FILES:
                self.assertIn(f"state/{name}", sources)
            self.assertIn("state/sheets/manifest.json", sources)
            self.assertIn("state/sheets/contact.jpg", sources)
            self.assertIn("state/protocol/PROTOCOL.md", sources)
            self.assertIn("state/protocol/language.py", sources)

    def test_receipt_is_healthy_only_with_remote_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            out = pathlib.Path(directory)
            (out / "_sheets").mkdir()
            protocol = out / "protocol"
            protocol.mkdir()
            s3 = FakeS3()
            receipt, _, _ = r2sync.sweep(
                s3, "bucket", out, set(), out / "_sheets/r2-ledger.json",
                lambda _message: None, settle_seconds=0, remote_verified=False,
                protocol_dir=protocol)
            self.assertEqual(receipt["status"], "degraded")
            self.assertFalse(receipt["remote_verified"])

    def test_remote_proof_reuploads_state_and_preserves_prior_success(self):
        with tempfile.TemporaryDirectory() as directory:
            out = pathlib.Path(directory)
            (out / "_sheets").mkdir()
            (out / "picks.json").write_text("{}")
            protocol = out / "protocol"
            protocol.mkdir()
            s3 = FakeS3()
            prior = {"chorus/state/picks.json": r2sync.sha256_file(out / "picks.json")}

            receipt, _, _ = r2sync.sweep(
                s3, "bucket", out, set(), out / "_sheets/r2-ledger.json",
                lambda _message: None, settle_seconds=0, remote_verified=False,
                state_hashes=prior, protocol_dir=protocol, force_verify=False,
                last_success_at=123.0)
            self.assertNotIn("chorus/state/picks.json", {row[2] for row in s3.uploads})
            self.assertEqual(receipt["last_success_at"], 123.0)

            s3.uploads.clear()
            receipt, _, _ = r2sync.sweep(
                s3, "bucket", out, set(), out / "_sheets/r2-ledger.json",
                lambda _message: None, settle_seconds=0, remote_verified=True,
                state_hashes=prior, protocol_dir=protocol, force_verify=True,
                last_success_at=123.0)
            self.assertIn("chorus/state/picks.json", {row[2] for row in s3.uploads})
            self.assertEqual(receipt["status"], "healthy")
            self.assertGreater(receipt["last_success_at"], 123.0)


if __name__ == "__main__":
    unittest.main()
