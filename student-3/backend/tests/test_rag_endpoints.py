"""Backend access to the shared RAG server: flag, validation, contract and failures."""
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from services import rag_client

CITATION = {"id": "S1", "source": "student-3/expiry-and-write-off.md", "title": "Batch Expiry and Write-Off",
            "section": "Writing off a batch", "score": 0.842, "snippet": "Only a Pharmacy Manager can write off a batch.",
            "embedding": [0.1, 0.2]}
ANSWERED = {"schema_version": "1.0", "status": "answered", "question": "Who can write off a batch?",
            "feature": "student-3", "answer": "Only a Pharmacy Manager can write off a batch [S1].",
            "confidence": "high", "citations": [CITATION], "reason": None,
            "retrieval": {"top_score": 0.842, "threshold": 0.62, "considered": 4, "relevant": 4},
            "models": {"embedding": "nomic-embed-text", "generation": "llama3.2:3b"}, "duration_ms": 2300}
REFUSED = {**ANSWERED, "status": "insufficient_context", "confidence": "none", "citations": [],
           "reason": "no_relevant_context", "answer": "The knowledge base does not contain enough relevant information.",
           "retrieval": {"top_score": 0.5, "threshold": 0.62, "considered": 4, "relevant": 0}}


class RAGTestCase(unittest.TestCase):
    enabled = True

    def setUp(self):
        self.client = app.app.test_client()
        self.calls = []
        for name, value in (("RAG_ENABLED", self.enabled), ("RAG_SERVER_URL", "http://127.0.0.1:8100")):
            patcher = patch.object(rag_client, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def respond(self, *responses):
        """Replace the HTTP call; each item is (status, body) or an exception."""
        queue = list(responses)

        def fake(method, path, payload=None, timeout=None):
            self.calls.append((method, path, payload))
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        patcher = patch.object(rag_client, "_request", fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, question="Who can write off a batch?"):
        return self.client.post("/api/rag/ask", json={"question": question})


class RAGDisabledTests(RAGTestCase):
    """CI runs with RAG disabled; nothing may contact the RAG server."""
    enabled = False

    def test_status_reports_disabled_without_contact(self):
        self.respond()
        body = self.client.get("/api/rag/status").get_json()
        self.assertEqual((body["enabled"], body["reachable"], body["feature"]), (False, False, "student-3"))
        self.assertEqual(self.calls, [])

    def test_ask_returns_503(self):
        self.respond()
        response = self.ask()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["outcome"], "disabled")
        self.assertEqual(self.calls, [])


class RAGValidationTests(RAGTestCase):
    def test_bad_requests_never_reach_rag(self):
        self.respond()
        for body in ([], {}, {"question": " "}, {"question": 7}, {"question": "x" * 501},
                     {"question": "ok", "feature": "student-4"}):
            with self.subTest(body=str(body)[:40]):
                self.assertEqual(self.client.post("/api/rag/ask", json=body).status_code, 400)
        self.assertEqual(self.calls, [])


class RAGAnswerTests(RAGTestCase):
    def test_answer_is_scoped_to_pharmacy_and_projected(self):
        self.respond((200, ANSWERED))
        response = self.ask("  Who can   write off a batch? ")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls, [("POST", "/query", {"question": "Who can write off a batch?", "feature": "student-3"})])
        body = response.get_json()
        self.assertEqual((body["outcome"], body["ok"], body["confidence"]), ("answered", True, "high"))
        self.assertEqual(body["citations"], [{k: CITATION[k] for k in ("id", "source", "title", "section", "score", "snippet")}])
        self.assertEqual(body["retrieval"]["threshold"], 0.62)

    def test_insufficient_context_is_a_valid_200(self):
        self.respond((200, REFUSED))
        body = self.ask("What is the capital of France?").get_json()
        self.assertEqual((body["outcome"], body["confidence"], body["citations"]), ("insufficient_context", "none", []))
        self.assertEqual(body["reason"], "no_relevant_context")

    def test_contract_violations_are_rejected(self):
        broken = [
            {**ANSWERED, "status": "maybe"},
            {**ANSWERED, "citations": []},
            {**REFUSED, "citations": [CITATION]},
            {**ANSWERED, "confidence": "none"},
            {**ANSWERED, "citations": [{**CITATION, "source": "student-4/beds.md"}]},
            {**ANSWERED, "citations": [{**CITATION, "score": "high"}]},
            {**ANSWERED, "answer": ""},
            [],
        ]
        for body in broken:
            with self.subTest(body=str(body)[:60]):
                self.respond((200, body))
                response = self.ask()
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.get_json()["error"], "Shared RAG server returned an invalid response")


class RAGFailureTests(RAGTestCase):
    def test_failure_mapping(self):
        cases = [
            ((503, {"error": {"code": "ollama_unavailable"}}), 502, "rag_error", "(ollama_unavailable)"),
            ((504, {"error": {"code": "ollama_timeout"}}), 504, "timeout", "(ollama_timeout)"),
            (urllib.error.URLError(ConnectionRefusedError()), 502, "unavailable", "unavailable"),
            (urllib.error.URLError(socket.timeout()), 504, "timeout", "timed out"),
            (TimeoutError(), 504, "timeout", "timed out"),
        ]
        for reply, http_status, outcome, message in cases:
            with self.subTest(outcome=outcome, reply=repr(reply)[:40]):
                self.respond(reply)
                response = self.ask()
                self.assertEqual(response.status_code, http_status)
                self.assertEqual(response.get_json()["outcome"], outcome)
                self.assertIn(message, response.get_json()["error"])

    def test_real_closed_port_returns_502(self):
        with patch.object(rag_client, "RAG_SERVER_URL", "http://127.0.0.1:9"):
            response = self.ask()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["outcome"], "unavailable")


class RAGStatusTests(RAGTestCase):
    def test_ready_when_healthy_with_pharmacy_documents(self):
        self.respond((200, {"status": "ok"}), (200, {"documents": [
            {"title": "Batch Expiry and Write-Off", "source": "student-3/expiry-and-write-off.md", "chunks": 3}]}))
        body = self.client.get("/api/rag/status").get_json()
        self.assertTrue(body["reachable"] and body["ready"])
        self.assertEqual(body["documents"], [{"title": "Batch Expiry and Write-Off",
                                              "source": "student-3/expiry-and-write-off.md"}])
        self.assertEqual(self.calls[1][1], "/sources?feature=student-3")

    def test_not_ready_when_degraded_or_unreachable(self):
        self.respond((503, {"status": "degraded"}), (200, {"documents": [{"title": "x", "source": "student-3/x.md"}]}))
        self.assertFalse(self.client.get("/api/rag/status").get_json()["ready"])
        self.respond(urllib.error.URLError(ConnectionRefusedError()))
        body = self.client.get("/api/rag/status").get_json()
        self.assertEqual((body["reachable"], body["error"]), (False, "Shared RAG server is unavailable"))


if __name__ == "__main__":
    unittest.main()
