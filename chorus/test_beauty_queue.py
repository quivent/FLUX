import json
import pathlib
import unittest


class BeautyQueueTest(unittest.TestCase):
    def test_manifest_is_explicit_and_nightly_scale(self):
        path = pathlib.Path(__file__).with_name("beauty-queue.json")
        queue = json.loads(path.read_text())
        jobs = queue["jobs"]

        self.assertEqual(len(jobs), 48)
        self.assertEqual(len({job["name"] for job in jobs}), 48)
        self.assertTrue(all(job.get("approved") is True for job in jobs))
        self.assertTrue(all(job.get("focus") and job.get("axis") for job in jobs))
        self.assertEqual(queue["defaults"]["generations"], 512)
        self.assertEqual(queue["defaults"]["width"], 512)
        self.assertEqual(queue["defaults"]["height"], 512)
        self.assertEqual(queue["defaults"]["batch"], 1)


if __name__ == "__main__":
    unittest.main()
