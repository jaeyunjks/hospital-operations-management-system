"""Rendering checks for the dashboard's pharmacy assistant (shared RAG) panel."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import api_client

ANSWERED = {
    "ok": True, "outcome": "answered", "question": "Who can write off a batch?",
    "answer": "Only a Pharmacy Manager can write off a batch [S1].", "confidence": "high",
    "citations": [{"id": "S1", "source": "student-3/expiry-and-write-off.md", "title": "Batch Expiry and Write-Off",
                   "section": "Writing off a batch", "score": 0.842,
                   "snippet": "Only a Pharmacy Manager can write off a <batch>."}],
    "retrieval": {"top_score": 0.842, "threshold": 0.62, "considered": 4, "relevant": 4},
    "models": {"embedding": "nomic-embed-text", "generation": "llama3.2:3b"}, "duration_ms": 2323,
}
REFUSED = {
    "ok": True, "outcome": "insufficient_context", "question": "What is the capital of France?",
    "answer": "The knowledge base does not contain enough relevant information to answer this question.",
    "confidence": "none", "citations": [], "reason": "no_relevant_context",
    "retrieval": {"top_score": 0.4953, "threshold": 0.62, "considered": 4, "relevant": 0}, "duration_ms": 21,
}


class RAGPanelTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['demo_identity'] = {'role': 'Pharmacist', 'staff_id': 3, 'name': 'Demo pharmacist'}

    def test_dashboard_lazy_loads_rag_panel_without_calling_rag(self):
        summary = {"counts": {}, "low_stock": [], "expiring_soon": [], "recent_movements": [], "agent": None}
        with patch.object(api_client, 'dashboard_summary', return_value=summary), \
                patch.object(api_client, 'rag_status', side_effect=AssertionError('eager RAG call')), \
                patch.object(api_client, 'rag_ask', side_effect=AssertionError('eager RAG call')):
            html = self.client.get('/').get_data(as_text=True)
        self.assertIn('Ask the pharmacy assistant', html)
        self.assertIn('hx-get="/rag/status"', html)
        self.assertIn('data-rag-example="Who can write off an expired batch?"', html)

    def test_status_badge_variants(self):
        cases = [
            ({"enabled": True, "reachable": True, "ready": True, "server_url": "http://127.0.0.1:8100",
              "documents": [{"title": "A", "source": "student-3/a.md"}, {"title": "B", "source": "student-3/b.md"}]},
             'RAG connected · 2 pharmacy documents'),
            ({"enabled": False, "reachable": False, "ready": False, "server_url": "x", "documents": []}, 'RAG disabled'),
            ({"enabled": True, "reachable": False, "ready": False, "server_url": "x", "documents": []}, 'RAG server unreachable'),
            ({"enabled": True, "reachable": True, "ready": False, "server_url": "x", "documents": []}, 'RAG not ready'),
        ]
        for status, text in cases:
            with self.subTest(text=text), patch.object(api_client, 'rag_status', return_value=status):
                html = self.client.get('/rag/status').get_data(as_text=True)
                self.assertIn(text, html)
                self.assertNotIn(status['server_url'], html)
        with patch.object(api_client, 'rag_status', side_effect=api_client.BackendError('down')):
            self.assertIn('Backend unavailable', self.client.get('/rag/status').get_data(as_text=True))

    def test_answer_renders_citations_and_confidence(self):
        with patch.object(api_client, 'rag_ask', return_value=ANSWERED) as ask:
            html = self.client.post('/rag/ask', data={'question': '  Who can   write off a batch? '}).get_data(as_text=True)
        ask.assert_called_once_with('Who can write off a batch?')
        for text in ('High confidence', 'Only a Pharmacy Manager can write off a batch [S1].', '[S1]',
                     'Batch Expiry and Write-Off', 'Writing off a batch', 'Sources'):
            self.assertIn(text, html)
        for hidden in ('student-3/expiry-and-write-off.md', 'similarity', 'sections relevant', 'threshold',
                       'nomic-embed-text', 'llama3.2:3b', '2323 ms'):
            self.assertNotIn(hidden, html)
        self.assertIn('write off a &lt;batch&gt;.', html)

    def test_snippet_markdown_is_cleaned(self):
        answer = {**ANSWERED, 'answer': 'Only `pending_approval` orders [S1].',
                  'citations': [{**ANSWERED['citations'][0], 'snippet': 'Runs a cycle: - **Plan** — reads stock.'}]}
        with patch.object(api_client, 'rag_ask', return_value=answer):
            html = self.client.post('/rag/ask', data={'question': 'q'}).get_data(as_text=True)
        self.assertIn('Runs a cycle: Plan — reads stock.', html)
        self.assertNotIn('**', html)
        self.assertIn('Only pending_approval orders [S1].', html)
        self.assertNotIn('`', html)

    def test_confidence_badges(self):
        for confidence, badge in (('high', 'badge-success'), ('medium', 'badge-info'), ('low', 'badge-warning')):
            with self.subTest(confidence=confidence), \
                    patch.object(api_client, 'rag_ask', return_value={**ANSWERED, 'confidence': confidence}):
                html = self.client.post('/rag/ask', data={'question': 'q'}).get_data(as_text=True)
                self.assertIn(f'<span class="badge {badge}">{confidence.capitalize()} confidence</span>', html)

    def test_insufficient_context_is_its_own_state(self):
        with patch.object(api_client, 'rag_ask', return_value=REFUSED):
            html = self.client.post('/rag/ask', data={'question': 'What is the capital of France?'}).get_data(as_text=True)
        for text in ('Insufficient context', 'Not enough information in the pharmacy knowledge base',
                     'No part of the knowledge base was relevant enough to answer it.'):
            self.assertIn(text, html)
        for hidden in ('best match', 'threshold', '0.50', ' ms'):
            self.assertNotIn(hidden, html)
        self.assertNotIn('Sources', html)
        self.assertNotIn('confidence</span>', html)

    def test_other_refusal_reasons_are_explained(self):
        for reason, text in (('model_declined', 'did not contain the answer'), ('uncited_answer', 'withheld')):
            with self.subTest(reason=reason), \
                    patch.object(api_client, 'rag_ask', return_value={**REFUSED, 'reason': reason}):
                self.assertIn(text, self.client.post('/rag/ask', data={'question': 'q'}).get_data(as_text=True))

    def test_minimal_result_without_retrieval_still_renders(self):
        minimal = {k: v for k, v in REFUSED.items() if k not in ('retrieval', 'reason')}
        with patch.object(api_client, 'rag_ask', return_value=minimal):
            response = self.client.post('/rag/ask', data={'question': 'q'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Not enough information in the pharmacy knowledge base', response.get_data(as_text=True))

    def test_blank_question_is_not_sent(self):
        with patch.object(api_client, 'rag_ask', side_effect=AssertionError('sent')):
            html = self.client.post('/rag/ask', data={'question': '   '}).get_data(as_text=True)
        self.assertIn('Enter a question first', html)

    def test_backend_failure_renders_escaped_error(self):
        with patch.object(api_client, 'rag_ask', side_effect=api_client.BackendError('<RAG mode is disabled>', 503)):
            html = self.client.post('/rag/ask', data={'question': 'q'}).get_data(as_text=True)
        self.assertIn('The pharmacy assistant could not answer', html)
        self.assertIn('&lt;RAG mode is disabled&gt;', html)
        self.assertIn('No inventory data was changed', html)


if __name__ == '__main__':
    unittest.main()
