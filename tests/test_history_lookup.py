import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "plugins/context-budget"
sys.path.insert(0, str(ROOT))
from memory.history import archive_path, capture, capture_lookup_event, read_archive, stats_path


class HistoryLookupTest(unittest.TestCase):
    def test_exact_text_dedup_search_and_paged_read(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            transcript = directory / "session.jsonl"
            records = [
                {"type": "response_item", "ordinal": 1, "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "startup injection"}]}},
                {"type": "turn_context", "payload": {"turn_id": "one"}},
                {"type": "response_item", "ordinal": 3, "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Find the cobalt marker: " + "x" * 5000}]}},
                {"type": "response_item", "ordinal": 4, "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "secret"}]}},
                {"type": "response_item", "ordinal": 5, "payload": {"type": "custom_tool_call_output", "output": "tool secret"}},
            ]
            transcript.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
            old = os.environ.get("PLUGIN_DATA")
            os.environ["PLUGIN_DATA"] = str(directory / "state")
            try:
                event = {"session_id": "example", "transcript_path": str(transcript)}
                self.assertEqual(capture(event, "codex"), 1)
                self.assertEqual(capture(event, "codex"), 0)
                path = archive_path("codex", "example")
                saved = read_archive(path)
                self.assertEqual(len(saved), 1)
                self.assertEqual(len(saved[0]["text"]), len("Find the cobalt marker: ") + 5000)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                cmd = [sys.executable, str(ROOT / "memory/history.py"), "--host", "codex", "--session-id", "example"]
                found = subprocess.run(cmd + ["search", "--query", "cobalt"], capture_output=True, text=True, check=True)
                self.assertIn("codex:3", found.stdout)
                self.assertNotIn("secret", found.stdout)
                shown = subprocess.run(cmd + ["show", "--id", "codex:3", "--offset", "4000", "--limit-chars", "2000"], capture_output=True, text=True, check=True)
                self.assertEqual(len(shown.stdout.strip("\n")), len(saved[0]["text"]) - 4000)
                hook_cmd = cmd + ["--hook-metric", "search", "--query", "cobalt"]
                hook_found = subprocess.run(hook_cmd, capture_output=True, text=True, check=True)
                self.assertIn("CONTEXT_BUDGET_HISTORY_METRIC:", hook_found.stderr)
                self.assertTrue(capture_lookup_event({"tool_input": {"command": " ".join(hook_cmd)}, "tool_response": {"output": hook_found.stderr}}, "codex"))
                metrics = stats_path("codex", "example").read_text(encoding="utf-8")
                self.assertNotIn("cobalt", metrics)
                self.assertNotIn("secret", metrics)
                self.assertNotIn("codex:3", metrics)
                self.assertEqual(stats_path("codex", "example").stat().st_mode & 0o777, 0o600)
                summary = subprocess.run(cmd[:4] + ["report"], capture_output=True, text=True, check=True)
                report = json.loads(summary.stdout)
                self.assertEqual(report["sessions_archived"], 1)
                self.assertEqual(report["sessions_searched"], 1)
                self.assertEqual(report["sessions_shown"], 1)
                self.assertEqual(report["capture_runs"], 2)
                self.assertEqual(report["capture_new_messages"], 1)
                self.assertEqual(report["search_runs"], 2)
                self.assertEqual(report["searches_with_hits"], 2)
                self.assertEqual(report["shows_found"], 1)
                self.assertGreater(report["capture_archive_bytes_written"], 5000)
            finally:
                if old is None:
                    os.environ.pop("PLUGIN_DATA", None)
                else:
                    os.environ["PLUGIN_DATA"] = old


if __name__ == "__main__":
    unittest.main()
