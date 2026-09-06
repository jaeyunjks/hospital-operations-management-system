"""Agent guardrails, feedback, fallback and lifecycle tests; no live services."""
import copy
from decimal import Decimal
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.ai_client import AIResult
from services import scheduled_agent as module
from services import reorder_recommendation as reorder


def candidate(medicine_id=1, **changes):
    row = dict(medicine_id=medicine_id, medicine_name=f'Medicine {medicine_id}',
               supplier_id=1, supplier_name='Supplier', available_quantity=0,
               daily_usage_rate=2.0, lead_time_days=7, open_order_quantity=0,
               near_expiry_quantity=0, suggested_quantity=20, unit_price=2.0)
    return {**row, **changes}


class Database:
    def __init__(self, orders=()):
        self.orders = copy.deepcopy(list(orders))
        self.writes = []

    def __call__(self, path, method='GET', payload=None):
        if path == '/purchase_orders' and method == 'GET':
            return copy.deepcopy(self.orders)
        if path == '/purchase_orders' and method == 'POST':
            self.writes.append((path, method, copy.deepcopy(payload)))
            row = {**payload, 'po_id': max([o['po_id'] for o in self.orders] or [0]) + 1}
            self.orders.append(row)
            return copy.deepcopy(row)
        if path.startswith('/purchase_orders/'):
            row = next(o for o in self.orders if o['po_id'] == int(path.rsplit('/', 1)[1]))
            if method == 'PUT':
                # Deliberately mirror the database API's restricted update fields.
                for key in {'status', 'decision_reason', 'approved_by', 'quantity_received', 'expected_at'} & payload.keys():
                    row[key] = payload[key]
            return copy.deepcopy(row)
        if path == '/medicines/1':
            return {'status': 'active'}
        if path == '/suppliers/1':
            return {'status': 'active'}
        raise AssertionError(f'Unexpected database operation: {method} {path}')


def order(po_id=1, **changes):
    return dict(po_id=po_id, medicine_id=1, supplier_id=1, quantity_ordered=20,
                quantity_received=0, unit_price=2.0, ai_generated=1,
                status='pending_approval', decision_reason=None,
                created_at='2026-01-01T00:00:00+00:00', **changes)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.prompt = patch.object(reorder, 'run_prompt', return_value=AIResult(False, error='Ollama is unreachable'))
        self.model = self.prompt.start()
        self.addCleanup(self.prompt.stop)

    def run_cycle(self, rows, db=None, **config):
        db = db or Database()
        worker = module.ScheduledAgent(db, module.AgentConfig(**config))
        with patch.object(module, 'candidates_for', return_value=rows), self.assertLogs('student3.agent') as logs:
            worker.run_cycle()
        return worker, db, '\n'.join(logs.output)

    def test_fallback_creates_only_pending_proposals_and_logs_all_checks(self):
        worker, db, logs = self.run_cycle([candidate()])
        self.assertEqual(worker.status()['proposals_created'], 1)
        payload = db.writes[0][2]
        self.assertEqual((payload['status'], payload['created_by'], payload['ai_generated']), ('pending_approval', 'agent', 1))
        self.assertIsNone(payload['approved_by'])
        self.assertIn('rule-based fallback', payload['ai_reasoning'])
        for stage in ('PLAN', 'ACT', 'OBSERVE', 'ADAPT'):
            self.assertIn(stage, logs)
        for check in ('Open/pending order', 'Cycle budget', 'Expiry risk', 'Normal usage'):
            self.assertIn(check + ': PASS', logs)
        self.assertIn('Rule-based path: Ollama is unreachable', logs)
        self.assertEqual(self.model.call_args.kwargs['version'], 'v2')

    def test_duplicate_budget_expiry_and_excessive_quantity_are_dropped(self):
        cases = [
            (candidate(), Database([order()]), {}, 'Open/pending order'),
            (candidate(unit_price=30), Database(), {}, 'Cycle budget'),
            (candidate(available_quantity=200), Database(), {}, 'Expiry risk'),
            (candidate(suggested_quantity=121), Database(), {}, 'Normal usage'),
            (candidate(daily_usage_rate=0), Database(), {}, 'Expiry risk'),
        ]
        for row, db, config, failed in cases:
            with self.subTest(check=failed):
                worker, db, logs = self.run_cycle([row], db, **config)
                self.assertFalse(db.writes)
                self.assertEqual(worker.status()['observe_rejected'], 1)
                self.assertIn(failed + ': FAIL', logs)
                # Even a duplicate still gets every requested check logged.
                for check in ('Open/pending order', 'Cycle budget', 'Expiry risk', 'Normal usage'):
                    self.assertIn(check + ':', logs)

    def test_budget_is_cumulative_and_limit_is_per_cycle(self):
        worker, db, _ = self.run_cycle([candidate(i, unit_price=10) for i in range(1, 5)])
        self.assertEqual(len(db.writes), 2)
        self.assertEqual(worker.status()['observe_rejected'], 2)
        _, db, _ = self.run_cycle([candidate(i) for i in range(1, 5)], max_proposals=1)
        self.assertEqual(len(db.writes), 1)

    def test_second_cycle_and_restart_do_not_duplicate(self):
        worker, db, _ = self.run_cycle([candidate()])
        with patch.object(module, 'candidates_for', return_value=[candidate()]):
            worker.run_cycle()
            module.ScheduledAgent(db).run_cycle()
        self.assertEqual(len(db.writes), 1)

    def test_feedback_changes_next_candidate_even_without_ollama(self):
        worker, db, _ = self.run_cycle([candidate()])
        db.orders[0].update(status='rejected', decision_reason=module.decision_record('REJECTED', 'Do not reorder this medicine; demand review needed'))
        with patch.object(module, 'candidates_for', return_value=[candidate(), candidate(2)]):
            worker.run_cycle()
        self.assertEqual([w[2]['medicine_id'] for w in db.writes], [1, 2])
        self.assertIn('demand review needed', self.model.call_args.kwargs['values']['feedback_json'])
        self.assertNotIn('Medicine 1', self.model.call_args.kwargs['values']['medicines_json'])

    def test_human_edit_caps_future_quantity_and_keeps_reason(self):
        past = order()
        past.update(status='cancelled', decision_reason=module.decision_record('EDITED', 'Smaller pack, reduce waste', 7))
        _, db, _ = self.run_cycle([candidate()], Database([past]))
        self.assertEqual(db.writes[0][2]['quantity_ordered'], 7)
        self.assertIn('Human edit #1', db.writes[0][2]['ai_reasoning'])

    def test_explicit_rejection_limit_revises_quantity_without_ai(self):
        past = order()
        past.update(status='rejected', decision_reason=module.decision_record('REJECTED', 'Use at most 7 units to reduce waste'))
        _, db, logs = self.run_cycle([candidate()], Database([past]))
        self.assertEqual(db.writes[0][2]['quantity_ordered'], 7)
        self.assertIn('Revised after rejection #1', db.writes[0][2]['ai_reasoning'])
        self.assertIn('quantity 20 -> 7', logs)
        self.assertIn('at most 7 units', self.model.call_args.kwargs['values']['feedback_json'])

    def test_rejection_does_not_repeat_same_quantity_or_raise_it(self):
        for limit in (0, 20, 40):
            past = order()
            past.update(status='rejected', decision_reason=module.decision_record('REJECTED', f'maximum {limit} units'))
            _, db, _ = self.run_cycle([candidate()], Database([past]))
            self.assertFalse(db.writes)

    def test_feedback_is_latest_ten_by_decision_time(self):
        rows = []
        for i in range(1, 13):
            row = order(i)
            row.update(status='rejected', decision_reason=f'REJECTED 2026-01-{i:02}T00:00:00+00:00 | reason {i}')
            rows.append(row)
        self.assertEqual([r['po_id'] for r in module.recent_feedback(rows)], list(range(12, 2, -1)))

    def test_order_or_rejection_arriving_during_act_blocks_write(self):
        for status in ('pending_approval', 'rejected'):
            db = Database()
            def model(*args, **kwargs):
                row = order()
                row.update(status=status, decision_reason=module.decision_record('REJECTED', 'Stop reorder'))
                db.orders.append(row)
                return AIResult(False, error='Ollama is unreachable')
            self.model.side_effect = model
            _, db, _ = self.run_cycle([candidate()], db)
            self.assertFalse(db.writes)

    def test_thread_survives_failed_cycle_and_start_is_idempotent(self):
        done = threading.Event()
        calls = []
        def fail_then_recover(fetch):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('temporary database outage')
            done.set()
            return []
        worker = module.ScheduledAgent(Database(), module.AgentConfig(interval=0.01))
        with patch.object(module, 'candidates_for', side_effect=fail_then_recover):
            worker.start()
            thread = worker._thread
            worker.start()
            self.assertIs(worker._thread, thread)
            self.assertTrue(done.wait(2), 'Worker did not recover')
            worker.stop()
            thread.join(2)
        self.assertGreaterEqual(worker.status()['cycle'], 2)
        self.assertFalse(thread.is_alive())

    def test_disabled_does_not_start_and_invalid_configuration_defaults(self):
        worker = module.ScheduledAgent(Database(), module.AgentConfig(enabled=False))
        worker.start()
        self.assertIsNone(worker._thread)
        with patch.dict(os.environ, {'AGENT_INTERVAL_SECONDS': '0', 'AGENT_MAX_PROPOSALS': '-1', 'AGENT_BUDGET_CAP': 'NaN'}, clear=True):
            config = module.AgentConfig.from_env()
        self.assertEqual((config.interval, config.max_proposals, config.budget), (300, 3, Decimal('500.00')))

    def test_per_item_fallback_is_preserved(self):
        self.model.return_value = AIResult(True, data={'items': [
            {'priority': 'high', 'reasoning': 'Actual usage is 2.00 units/day, maintain lead-time cover.', 'adjustment_flag': False},
            {'priority': 'invalid', 'reasoning': 'bad'},
        ]})
        payload, _, _ = reorder.advisory(None, candidates=[candidate(), candidate(2)], feedback=[])
        self.assertEqual([row['ai_reviewed'] for row in payload['items']], [True, False])
        self.assertEqual([row['suggested_quantity'] for row in payload['items']], [20, 20])


class OrderApiTests(unittest.TestCase):
    def test_edit_retains_original_and_requires_human_approval(self):
        import app
        db = Database([order()])
        with patch.object(app, 'database_request', db):
            client = app.app.test_client()
            denied = client.put('/api/purchase-orders/1', json={'quantity_ordered': 7, 'decision_reason': 'Reduce waste'})
            self.assertEqual(denied.status_code, 403)
            response = client.put('/api/purchase-orders/1', json={'quantity_ordered': 7, 'decision_reason': 'Reduce waste'}, headers={'X-HOMS-Role': app.MANAGER_ROLE})
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(db.orders[0]['status'], 'cancelled')
        self.assertIn('EDITED', db.orders[0]['decision_reason'])
        self.assertEqual(response.json['quantity_ordered'], 7)
        self.assertEqual(response.json['status'], 'pending_approval')
        self.assertEqual(response.json['ai_generated'], 1)
        self.assertIsNone(response.json['approved_by'])

    def test_reject_reason_persisted_and_status_endpoint_is_read_only(self):
        import app
        db = Database([order()])
        with patch.object(app, 'database_request', db):
            client = app.app.test_client()
            response = client.post('/api/purchase-orders/1/reject', json={'decision_reason': 'Demand has fallen'}, headers={'X-HOMS-Role': app.MANAGER_ROLE})
            self.assertEqual(response.status_code, 200)
            self.assertIn('Demand has fallen', module.recent_feedback(db.orders)[0]['decision_reason'])
            self.assertEqual(client.get('/api/agent/status').status_code, 200)
            self.assertEqual(client.post('/api/agent/status').status_code, 405)


if __name__ == '__main__':
    unittest.main()
