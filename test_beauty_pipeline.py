import unittest

import beauty_pipeline as pipeline


class BeautyPipelineTest(unittest.TestCase):
    def test_parse_object_accepts_fenced_json(self):
        value = pipeline.parse_object('```json\n{"critique":"flat", "next_prompt":"hard side light"}\n```')
        self.assertEqual(value["next_prompt"], "hard side light")

    def test_prompt_is_bounded_to_clip_sized_brief(self):
        prompt = pipeline.trim_prompt(" ".join("word%d" % n for n in range(100)))
        self.assertEqual(len(prompt.split()), 72)

    def test_local_direction_changes_without_growing_prompt(self):
        first = pipeline.local_prompt("portrait", 1)
        second = pipeline.local_prompt("portrait", 2)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("portrait"))
        self.assertLessEqual(len(first.split()), 72)


if __name__ == "__main__":
    unittest.main()
