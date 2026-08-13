import unittest

from stallion_motion_rubric import (
    ADVERSARIAL_FIXTURES,
    classify_projection_step,
    evaluate_pair,
    serpentine_coordinate,
    serpentine_index,
    topology_neighbors,
    validate_adversarial_fixtures,
)


class StallionMotionRubricTest(unittest.TestCase):
    def test_only_positive_controls_pass(self):
        result = validate_adversarial_fixtures()
        self.assertTrue(result["passed"], result)

    def test_inverse_motion_is_rejected(self):
        verdict = evaluate_pair(ADVERSARIAL_FIXTURES["static_horse_moving_background"])
        self.assertFalse(verdict.qualified)
        self.assertIn("inverse_motion", verdict.failures)
        self.assertIn("background_moves", verdict.failures)

    def test_symmetry_is_a_hard_failure(self):
        verdict = evaluate_pair(ADVERSARIAL_FIXTURES["symmetric_pose"])
        self.assertFalse(verdict.qualified)
        self.assertIn("symmetry", verdict.failures)

    def test_serpentine_mapping_round_trips(self):
        for index in range(64):
            row, col = serpentine_coordinate(index, 8)
            self.assertEqual(serpentine_index(row, col, 8), index)

    def test_topology_does_not_excuse_visual_failure(self):
        bad = evaluate_pair(ADVERSARIAL_FIXTURES["incoherent_horse"])
        self.assertEqual(
            classify_projection_step(1.0, bad),
            "projection_local_but_visually_discontinuous",
        )

    def test_topology_neighbors_cross_serpentine_turn(self):
        # Stored indices 7 and 8 are adjacent at the right edge of an 8-column
        # serpentine atlas.
        self.assertIn(8, topology_neighbors(7, 4, 8, ((1, 0),)))


if __name__ == "__main__":
    unittest.main()

