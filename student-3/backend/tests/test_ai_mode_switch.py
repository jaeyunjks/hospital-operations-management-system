"""AI-Mode can be switched off (the cloud default): advisories fall back and Ollama is never contacted."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from services import ai_client


class AIModeDisabledTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(ai_client, "AI_ENABLED", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        no_network = patch.object(ai_client.urllib.request, "urlopen",
                                  side_effect=AssertionError("Ollama must not be contacted"))
        no_network.start()
        self.addCleanup(no_network.stop)

    def test_model_calls_return_a_disabled_result(self):
        result = ai_client.call_json("{}", {"ok": bool}, prompt_name="smoke")
        self.assertEqual((result.ok, result.outcome, result.error), (False, "disabled", "AI-Mode is disabled"))

    def test_health_reports_disabled(self):
        body = app.app.test_client().get("/api/ai/health").get_json()
        self.assertFalse(body["enabled"])
        self.assertFalse(body["reachable"])
        self.assertEqual(body["probe"]["error"], "AI-Mode is disabled")


if __name__ == "__main__":
    unittest.main()
