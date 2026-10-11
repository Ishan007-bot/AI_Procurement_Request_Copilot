"""UI smoke tests (Streamlit AppTest) and the human decision log. No API key needed."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

from src.config import ROOT
from src.decision_log import HUMAN_ACTIONS, append_decision, read_decisions

NO_KEY = {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "", "GROQ_API_KEY": ""}


class AppSmokeTests(unittest.TestCase):
    def run_app(self, request_id: str | None = None, click: bool = False) -> AppTest:
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
        with mock.patch.dict(os.environ, NO_KEY):
            at.run()
            if request_id:
                at.selectbox[0].set_value(request_id).run()
            if click:
                next(b for b in at.button if b.label == "Run analysis").click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def test_loads(self):
        at = self.run_app()
        self.assertTrue(any(b.label == "Run analysis" for b in at.button))

    def test_rules_only_run_renders_every_tab(self):
        for request_id in ("REQ-1005", "REQ-1006", "REQ-1009"):
            at = self.run_app(request_id, click=True)
            self.assertEqual(len(at.tabs), 5, request_id)
            html = " ".join(m.value for m in at.markdown)
            self.assertIn("Human review required", html, request_id)
            self.assertIn("Rules-only result", html, request_id)

    def test_untrusted_text_is_escaped(self):
        at = self.run_app("REQ-1006")
        html = " ".join(m.value for m in at.markdown)
        self.assertIn("contains embedded instructions", html)
        self.assertNotIn("<script", html.lower())


class DecisionLogTests(unittest.TestCase):
    def test_append_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            append_decision({"request_id": "REQ-1", "reviewer": "A. Reviewer", "action": HUMAN_ACTIONS[0]}, path)
            append_decision({"request_id": "REQ-2", "reviewer": "B", "action": HUMAN_ACTIONS[1]}, path)
            rows = read_decisions("REQ-1", path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["decided_by"], "human")
            self.assertEqual(len(read_decisions(None, path)), 2)

    def test_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.jsonl"
            with self.assertRaises(ValueError):
                append_decision({"request_id": "R", "reviewer": "x", "action": "Auto-approve"}, path)
            with self.assertRaises(ValueError):
                append_decision({"request_id": "R", "reviewer": " ", "action": HUMAN_ACTIONS[0]}, path)
            self.assertEqual(read_decisions(None, path), [])


if __name__ == "__main__":
    unittest.main()
