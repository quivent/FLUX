import os
import unittest
from unittest.mock import patch

import pipeline_paths as pp


class BeautyTopologyTests(unittest.TestCase):
    def load(self, profile, **env):
        clean = (
            "ARCANE_KONTEXT",
            "ARCANE_GOVERNOR_REMOTE",
            "ARCANE_WITNESS_REMOTE",
            "ARCANE_PIXTRAL_REMOTE",
            "MOJ_VISUAL_WITNESS_URL",
        )
        with patch.dict(os.environ, {}, clear=False):
            for name in clean:
                os.environ.pop(name, None)
            os.environ["ARCANE_PROFILE"] = profile
            os.environ.update(env)
            return pp.load_continuum()

    def test_compact_keeps_witness_and_pixtral_local(self):
        cfg = self.load("h100")
        self.assertFalse(cfg["tenants"]["witness"]["remote"])
        self.assertFalse(cfg["tenants"]["pixtral"]["remote"])
        self.assertEqual(cfg["vram"]["allocated_gib"], 76.8)
        self.assertTrue(cfg["vram"]["fits"])

    def test_remote_witness_frees_studio_vram_but_not_pixtral(self):
        cfg = self.load("h100-remote-witness")
        self.assertTrue(cfg["tenants"]["witness"]["remote"])
        self.assertFalse(cfg["tenants"]["pixtral"]["remote"])
        self.assertEqual(cfg["vram"]["allocated_gib"], 46.8)
        self.assertEqual(cfg["tenants"]["pixtral"]["gpu_span"], cfg["tenants"]["flux"]["gpu_span"])

    def test_distributed_atelier_reserves_kontext_locally(self):
        cfg = self.load("h100-distributed-atelier")
        self.assertTrue(cfg["toggles"]["kontext"])
        self.assertTrue(cfg["toggles"]["witness_remote"])
        self.assertTrue(cfg["toggles"]["governor_remote"])
        self.assertEqual(cfg["vram"]["allocated_gib"], 55.8)
        self.assertTrue(cfg["vram"]["fits"])

    def test_remote_witness_url_is_independently_overridable(self):
        cfg = self.load(
            "h100-distributed-atelier",
            MOJ_VISUAL_WITNESS_URL="https://qwen.example.test/v1/",
        )
        self.assertEqual(cfg["endpoints"]["witness"], "https://qwen.example.test/v1")
        self.assertEqual(cfg["endpoints"]["pixtral"], "http://127.0.0.1:8002/v1")

    def test_pixtral_remote_override_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Pixtral"):
            self.load("h100-remote-witness", ARCANE_PIXTRAL_REMOTE="1")


if __name__ == "__main__":
    unittest.main()
