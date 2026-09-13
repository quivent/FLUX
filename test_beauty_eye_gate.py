import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jury_evaluator
import moj_evaluator
import pipeline_paths
import beauty_eye_gate


class BeautyEyeGateTests(unittest.TestCase):
    def test_beauty_profile_exposes_ralpheye_contract(self):
        with patch.dict(os.environ, {"ARCANE_PROFILE": "h100-distributed-atelier"}):
            cfg = pipeline_paths.load_continuum()
        self.assertEqual(cfg["suite"], "beauty")
        self.assertEqual(cfg["eye_gate"]["protocol"], "EGRL")
        self.assertTrue(cfg["eye_gate"]["required"])
        self.assertEqual(cfg["eye_gate"]["builder_observation"], "qwen")
        self.assertEqual(cfg["eye_gate"]["independent_critic"], "pixtral")
        self.assertTrue(cfg["eye_gate"]["operator_is_final"])

    def test_pending_candidate_is_not_auto_promoted(self):
        receipt = {
            "ts": 1,
            "job_id": "beauty-1",
            "seed": 7,
            "prompt": "test",
            "tier": "masterpiece",
            "percentile_rank": 99.0,
            "curved_score": 96.0,
            "raw_composite": 95.0,
            "jury_scores": {},
            "judges": [],
            "mode": "parallel",
            "image_path": "",
            "operator_gate": {"required": True, "status": "pending"},
            "is_masterpiece": False,
            "is_spectacle": False,
        }
        with tempfile.TemporaryDirectory() as out:
            jury_evaluator.persist_receipt(receipt, out)
            queued = [json.loads(line) for line in Path(
                out, "eye-gate-candidates.jsonl"
            ).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(queued[0]["job_id"], "beauty-1")
            self.assertFalse(os.path.exists(os.path.join(out, "masterpiece_vault.jsonl")))
            self.assertFalse(os.path.exists(os.path.join(out, "spectacle_genome.jsonl")))

    def test_api_keys_use_bearer_auth(self):
        self.assertEqual(
            moj_evaluator._headers({"api_key": "secret"})["Authorization"],
            "Bearer secret",
        )

    def test_operator_crown_is_verbatim_and_promotes(self):
        with tempfile.TemporaryDirectory() as out_raw:
            out = os.path.abspath(out_raw)
            candidate = {
                "job_id": "beauty-2", "tier": "spectacle",
                "machine_recommendation": "spectacle", "percentile_rank": 95,
                "curved_score": 91, "image_path": "/renders/beauty-2.png",
                "judges": [{"role": "structure"}, {"role": "aesthetic"}],
                "operator_gate": {"required": True, "status": "pending"},
            }
            with open(os.path.join(out, "eye-gate-candidates.jsonl"), "w", encoding="utf-8") as handle:
                handle.write(json.dumps(candidate) + "\n")
            row = beauty_eye_gate.record_verdict(
                Path(out), "beauty-2", "crown", "SICK!!!", 2.0, 3
            )
            self.assertEqual(row["operator_words"], "SICK!!!")
            self.assertTrue(row["observed"])
            self.assertEqual(beauty_eye_gate.pending_slate(Path(out)), [])
            promoted = json.loads(Path(out, "masterpiece_vault.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(promoted["operator_gate"]["status"], "crowned")


if __name__ == "__main__":
    unittest.main()
