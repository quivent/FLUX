#!/usr/bin/env python3
import os
import tempfile
import unittest

import protocol_stream as ps


class ProtocolBranch(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(ps.normalize_branch(" Silk Road "), "silk-road")
        with self.assertRaises(ValueError):
            ps.normalize_branch("fashion")
        with self.assertRaises(ValueError):
            ps.normalize_branch("")

    def test_relpath(self):
        self.assertEqual(
            ps.branch_relpath("silk", "stream-1", 3),
            "collections/silk/protocol-silk-stream-1-003.png",
        )

    def test_belarro_prompt_leads_with_variety(self):
        import belarro_direction as d

        cfg = dict(d.DEFAULTS)
        cfg["_varieties"] = d.VARIETIES
        cfg["_shots"] = d.SHOTS
        p0 = d.prompt_for(0, cfg)
        self.assertTrue(p0.startswith("Red Rambo"))
        self.assertIn("violet", p0)
        self.assertIn("no people", p0)
        self.assertNotEqual(d.prompt_for(0, cfg), d.prompt_for(1, cfg))

    def test_running_cap_ignores_own_state(self):
        root = tempfile.mkdtemp()
        fluxd = os.path.join(root, ".fluxd")
        os.makedirs(fluxd)
        for slug in ("silk", "noir", "ivory"):
            path = ps.branch_state_path(root, slug)
            ps.save_state({"branch": slug, "status": "running"}, path)
        mine = ps.branch_state_path(root, "silk")
        live = ps.running_branch_slugs(root, ignore_state=mine)
        self.assertEqual(live, {"noir", "ivory"})
        self.assertLess(len(live), ps.MAX_PROTOCOL_BRANCHES)


class EquineAnatomyPrompt(unittest.TestCase):
    def test_structure_prompt_hardens_for_horses(self):
        import moj_evaluator as moj

        horse = moj.system_prompt_for("structure", prompt="One or a few horses")
        dress = moj.system_prompt_for("structure", prompt="editorial silk gown")
        self.assertIn("exactly four", horse)
        self.assertNotIn("exactly four", dress)


class CollectionJuryRouting(unittest.TestCase):
    def test_silken_horses_job_stays_in_its_collection(self):
        import jury_evaluator as je

        fashion = "/home/ubuntu/models/flux-output"
        job = {
            "filename": "collections/silken-horses/protocol-silken-horses-stream-1-001.png",
            "output": fashion + "/collections/silken-horses/protocol-silken-horses-stream-1-001.png",
        }
        got = je.collection_dir_for_job(job, fashion)
        self.assertEqual(got, fashion + "/collections/silken-horses")
        dress = {"filename": "protocol-fashion-001.png", "output": fashion + "/protocol-fashion-001.png"}
        self.assertEqual(je.collection_dir_for_job(dress, fashion), fashion)


if __name__ == "__main__":
    unittest.main()
