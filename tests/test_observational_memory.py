import sys
import tempfile
import unittest
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/context-budget"))
from memory import ingest, record_observation, render


class ObservationalMemoryTest(unittest.TestCase):
    def test_full_history_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            batch = [{"id": "m1", "text": "  user   asked  "}, {"id": "m2", "text": "answer"}]
            self.assertEqual(ingest(root, "s", batch), 2)
            self.assertEqual(ingest(root, "s", batch), 0)
            self.assertEqual(render(root, "s"), "answer\nuser asked")

    def test_sessions_are_isolated(self):
        with tempfile.TemporaryDirectory() as root:
            ingest(root, "one", [{"id": "x", "text": "private"}])
            self.assertEqual(render(root, "two"), "")

    def test_context_limit_and_observer_chunk(self):
        with tempfile.TemporaryDirectory() as root:
            chunks = []

            def observer(chunk):
                chunks.append([item["id"] for item in chunk])
                return "summary: " + " ".join(item["text"] for item in chunk)

            ingest(root, "s", [{"id": "1", "text": "alpha"}, {"id": "2", "text": "beta"}], observer=observer)
            self.assertEqual(chunks, [["1", "2"]])
            record_observation(root, "s", "z" * 40)
            self.assertLessEqual(len(render(root, "s", max_chars=12)), 12)
            self.assertEqual(render(root, "s", max_chars=0), "")

    def test_persisted_dedup_and_bounded_observations(self):
        with tempfile.TemporaryDirectory() as root:
            ingest(root, "s", [{"id": str(i), "text": "message-" + str(i)} for i in range(5)], max_observations=2)
            self.assertEqual(ingest(root, "s", [{"id": "4", "text": "message-4"}]), 0)
            self.assertEqual(render(root, "s"), "message-4\nmessage-3")

    def test_full_history_replay_over_8192_ids_stays_deduplicated(self):
        with tempfile.TemporaryDirectory() as root:
            messages = [{"id": str(i), "text": "message-" + str(i)} for i in range(8205)]
            self.assertEqual(ingest(root, "long", messages), 8205)
            self.assertEqual(ingest(root, "long", messages), 0)

    def test_state_permissions_and_exact_render_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            ingest(root, "private", [{"id": "1", "text": "abc"}, {"id": "2", "text": "de"}])
            self.assertEqual(render(root, "private", 6), "de\nabc")
            self.assertEqual(render(root, "private", 5), "de")
            self.assertLessEqual(len(render(root, "private", 4)), 4)
            if os.name == "posix":
                state_file = next(Path(root).glob("*.json"))
                self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)
                self.assertEqual(state_file.parent.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
