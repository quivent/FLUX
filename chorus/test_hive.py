import json
import os
import pathlib
import random
import tempfile
import unittest
from unittest import mock

from chorus import contact, hive, loop, sentinel
from chorus import language


class HiveEvidenceTest(unittest.TestCase):

    def test_disabled_services_are_not_supervision_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            run = pathlib.Path(directory) / "run"
            out = pathlib.Path(directory) / "out"
            run.mkdir(); out.mkdir()
            for name in ("piper", "nexus", "serve"):
                (run / f"{name}.pid").write_text(str(os.getpid()))
            args = type("Args", (), {
                "run_dir": str(run), "out_dir": str(out),
                "drift_stale": 1, "sentinel_stale": 1, "r2_stale": 1,
            })()
            with mock.patch.dict(os.environ, {
                "GEMMA": "0", "DRIFT": "0", "SENTINEL": "0",
                "CHORUS_SECOND_ENGINE": "",
            }, clear=False):
                self.assertIsNone(hive.audit(args))

    def test_enabled_optional_service_cannot_restart_the_gpu_stack(self):
        with tempfile.TemporaryDirectory() as directory:
            run = pathlib.Path(directory) / "run"
            out = pathlib.Path(directory) / "out"
            run.mkdir(); out.mkdir()
            for name in ("piper", "nexus", "serve"):
                (run / f"{name}.pid").write_text(str(os.getpid()))
            args = type("Args", (), {
                "run_dir": str(run), "out_dir": str(out),
                "drift_stale": 1, "sentinel_stale": 1, "r2_stale": 1,
            })()
            with mock.patch.dict(os.environ, {
                "GEMMA": "1", "DRIFT": "0", "SENTINEL": "0",
                "CHORUS_SECOND_ENGINE": "",
            }, clear=False):
                self.assertIsNone(hive.audit(args))

    def test_counter_voice_changes_world_and_subject(self):
        rng = random.Random(7)
        sequence = language.new_sequence(rng)
        counter = language.paired_sequence(rng, sequence)
        self.assertNotEqual(sequence["world"], counter["world"])
        self.assertNotEqual(sequence["subject"], counter["subject"])

    def test_new_sequence_can_avoid_last_world(self):
        for seed in range(20):
            sequence = language.new_sequence(random.Random(seed), avoid_world="creatures")
            self.assertNotEqual(sequence["world"], "creatures")

    def test_landing_can_hold_four_distinct_worlds(self):
        rng = random.Random(12)
        worlds = []
        for _ in range(4):
            sequence = language.new_sequence(rng, avoid_world=set(worlds))
            worlds.append(sequence["world"])
        self.assertEqual(len(set(worlds)), 4)

    def test_adjacent_latents_are_forced_apart(self):
        try:
            import torch
        except ImportError:
            self.skipTest("torch is only installed on generation nodes")
        previous = torch.randn(1, 32, 16)
        current = previous + torch.randn_like(previous) * 0.01
        original_norm = current.float().norm()
        moved, reported = loop.separate_latent(current, previous, -0.20)
        cosine = torch.nn.functional.cosine_similarity(
            moved.float().reshape(1, -1), previous.float().reshape(1, -1)
        ).item()
        self.assertAlmostEqual(reported, -0.20, places=5)
        self.assertLessEqual(cosine, -0.199)
        self.assertAlmostEqual(moved.float().norm().item(), original_norm.item(), places=3)
    def test_credit_joins_numbered_verdict_to_trial_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            out = pathlib.Path(directory)
            candidate = {"seat": "light", "kind": "mood", "phrase": "one red window",
                         "state": "trial", "appearances": 0, "keeps": 0}
            candidate["id"] = hive.candidate_id(candidate)
            rows = [
                {"file": "a.png", "arm": "anchor"},
                {"file": "b.png", "arm": "anchor"},
                {"file": "c.png", "arm": "trial", "challenger_id": candidate["id"]},
                {"file": "d.png", "arm": "trial", "challenger_id": candidate["id"]},
            ]
            (out / "trial-ledger.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
            verdict = {
                "judged": True,
                "window": hive.WINDOW,
                "sampled_frames": {"1": "a.png", "2": "b.png", "3": "c.png", "4": "d.png"},
                "verdict": {"keep": [1, 3], "cut": [2, 4], "arresting": []},
            }
            (out / "taste-log.jsonl").write_text(json.dumps(verdict) + "\n")

            state = hive.credit({"challengers": [candidate]}, out)
            self.assertEqual(state["baseline"], 0.5)
            self.assertEqual(state["challengers"][0]["appearances"], 2)
            self.assertEqual(state["challengers"][0]["keeps"], 1)
            self.assertEqual(state["challengers"][0]["change_wins"], 0)

    def test_keep_does_not_promote_without_movement_change(self):
        candidate = {"kind": "mood", "phrase": "one red window", "state": "trial",
                     "appearances": 20, "keeps": 20, "change_wins": 0}
        state = {"baseline": 0.75, "change_baseline": 0.0,
                 "challengers": [candidate]}
        hive.settle(state, lambda _message: None)
        self.assertEqual(candidate["state"], "trial")

    def test_operator_feedback_overrides_model_state(self):
        candidate = {"id": "abc", "kind": "mood", "phrase": "one red window",
                     "state": "trial"}
        state = {"challengers": [candidate]}
        feedback = [{"id": "human-1", "action": "promote", "challenger_id": "abc",
                     "instruction": "This preserves the voice."}]
        hive.apply_operator_feedback(state, feedback, lambda _message: None)
        self.assertEqual(candidate["state"], "promoted")
        self.assertEqual(state["applied_feedback"], ["human-1"])

    def test_soul_failure_is_attributed_to_one_axis(self):
        verdict = {"movement": {"progressing": False},
                   "verdict": "Traded stark graphic power for a soft digital sheen."}
        self.assertEqual(hive.movement_failure_axis(verdict), "surface")
        explicit = {"movement": {"progressing": False, "failure_axis": "light"}}
        self.assertEqual(hive.movement_failure_axis(explicit), "light")

    def test_change_requires_arresting_and_progressing(self):
        with tempfile.TemporaryDirectory() as directory:
            out = pathlib.Path(directory)
            candidate = {"seat": "light", "kind": "mood", "phrase": "one red window",
                         "state": "trial"}
            candidate["id"] = hive.candidate_id(candidate)
            ledgers, frames = [], {}
            for number in range(1, 5):
                name = f"{number}.png"
                ledgers.append({"file": name, "arm": "trial",
                                "challenger_id": candidate["id"]})
                frames[str(number)] = name
            (out / "trial-ledger.jsonl").write_text(
                "".join(json.dumps(x) + "\n" for x in ledgers))
            rows = [
                {"judged": True, "sampled_frames": frames,
                 "window": hive.WINDOW,
                 "verdict": {"keep": [1, 2, 3, 4], "cut": [],
                             "arresting": [{"frame": 1}],
                             "movement": {"progressing": False}}},
                {"judged": True, "sampled_frames": frames,
                 "window": hive.WINDOW,
                 "verdict": {"keep": [1, 2, 3, 4], "cut": [],
                             "arresting": [{"frame": 2}],
                             "movement": {"progressing": True}}},
            ]
            (out / "taste-log.jsonl").write_text(
                "".join(json.dumps(x) + "\n" for x in rows))
            state = hive.credit({"challengers": [candidate]}, out)
            self.assertEqual(state["challengers"][0]["change_wins"], 1)

    def test_trial_budget_changes_claimed_prompt_slot(self):
        candidate = {"seat": "light", "kind": "mood", "phrase": "one red window",
                     "state": "trial"}
        genome = {"mood": "blue", "detail": "still", "framing": "close"}
        result = loop.choose_hive_arm(random.Random(1), genome,
                                      {"eps": 0.5, "challengers": [candidate]})
        self.assertEqual(result["arm"], "trial")
        self.assertEqual(genome["mood"], "one red window")

    def test_one_style_holds_across_distinct_subjects(self):
        rng = random.Random(19)
        style = language.new_style(rng)
        sequences = [language.new_sequence(rng, avoid_world=[], style=style)
                     for _ in range(4)]
        self.assertTrue(all(s["medium"] == style["medium"] for s in sequences))
        self.assertTrue(all(s["mood"] == style["mood"] for s in sequences))

    def test_movement_resumes_after_daemon_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "drift-status.json"
            style = language.new_style(random.Random(3))
            path.write_text(json.dumps({"movement_style": style, "style_age": 7,
                                        "style_directive": "verdict-1"}))
            resumed, age, directive = loop.resume_movement(path)
            self.assertEqual(resumed, style)
            self.assertEqual(age, 7)
            self.assertEqual(directive, "verdict-1")

    def test_contact_sheet_sees_titled_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "drift-0001.png").touch()
            (root / "borrowed-sleep--g0024-00-s1.png").touch()
            self.assertEqual(len(contact.find_frames(root)), 2)

    def test_blanket_rejection_is_advisory(self):
        self.assertTrue(sentinel.blanket_rejection({"keep": [], "cut": list(range(1, 17))}, 16))
        self.assertTrue(sentinel.blanket_rejection({"keep": [1, 2, 3], "cut": list(range(4, 17))}, 16))
        self.assertFalse(sentinel.blanket_rejection({"keep": [1, 2, 3, 4], "cut": list(range(5, 17))}, 16))

    def test_keep_threshold_is_loosened_only_for_movement(self):
        self.assertIn("retain in the collection", sentinel.CALIBRATION)
        self.assertIn("Laws 6, 7, 8, and 10 grade the MOVEMENT", sentinel.CALIBRATION)
        self.assertIn("CUT only a frame that fails as an individual image", sentinel.CALIBRATION)
        self.assertIn("KEEP IS NOT APPROVAL", sentinel.CALIBRATION)
        self.assertIn("preserves the approved", sentinel.CALIBRATION)


if __name__ == "__main__":
    unittest.main()
