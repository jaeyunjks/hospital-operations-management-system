"""Single-process, proposal-only Plan -> Act -> Observe -> Adapt worker.

The existing Flask entrypoint runs one process. The shared lock serialises its
human order writes and the agent's check/create pair. Multiple backend replicas
would require database-side uniqueness/locking before enabling this worker.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging
import math
import os
import re
import threading
import time

from services.reorder_recommendation import advisory, candidates_for, OPEN_STATUSES

ORDER_LOCK = threading.RLock()
logger = logging.getLogger("student3.agent")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class AgentConfig:
    enabled: bool = True
    interval: int = 300
    max_proposals: int = 3
    budget: Decimal = Decimal("500.00")

    @classmethod
    def from_env(cls):
        def positive_int(name, default):
            try:
                value = int(os.environ.get(name, default))
                if value > 0:
                    return value
            except ValueError:
                pass
            logger.warning("Agent configuration | %s invalid; using %s", name, default)
            return default

        try:
            budget = Decimal(os.environ.get("AGENT_BUDGET_CAP", "500.00"))
            if not budget.is_finite() or budget <= 0:
                raise ValueError
        except (ValueError, ArithmeticError):
            budget = Decimal("500.00")
            logger.warning("Agent configuration | invalid budget; using $500.00")
        return cls(os.environ.get("AGENT_ENABLED", "true").lower() in {"true", "1", "yes"},
                   positive_int("AGENT_INTERVAL_SECONDS", 300),
                   positive_int("AGENT_MAX_PROPOSALS", 3), budget)


def decision_record(kind, reason, quantity=None):
    """Persist feedback in the existing decision_reason field, without a migration."""
    quantity_note = f"quantity={quantity} | " if quantity is not None else ""
    return f"{kind} {timestamp()} | {quantity_note}{reason.strip()}"


def recent_feedback(orders):
    rows = []
    for order in orders:
        reason = str(order.get("decision_reason") or "").strip()
        if not order.get("ai_generated") or not reason:
            continue
        edited = reason.startswith("EDITED ")
        if order.get("status") != "rejected" and not edited:
            continue
        recorded = re.match(r"(?:EDITED|REJECTED) (\S+) \|", reason)
        quantity = re.search(r"\| quantity=(\d+) \|", reason) if edited else None
        # Only an explicit integer unit limit can revise a rejected quantity.
        # Other reasons suppress the medicine rather than guessing what was meant.
        limit = re.search(r"\b(?:maximum|at most)\s+(\d+)\s+units\b", reason, re.IGNORECASE)
        rows.append({"po_id": order["po_id"], "medicine_id": order["medicine_id"],
                     "decision": "edited" if edited else "rejected",
                     "decision_reason": reason[:1000],
                     "quantity_ordered": order["quantity_ordered"],
                     "edited_quantity": int(quantity[1]) if quantity else None,
                     "quantity_limit": int(limit[1]) if limit else None,
                     "decided_at": recorded[1] if recorded else order["created_at"]})
    return sorted(rows, key=lambda row: (row["decided_at"], row["po_id"]), reverse=True)[:10]


class ScheduledAgent:
    def __init__(self, database_request, config=None):
        self.database_request = database_request
        self.config = config or AgentConfig.from_env()
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._cycle_lock = threading.Lock()
        self._thread = None
        self.feedback = []
        self._state = {"cycle": 0, "running": False, "last_cycle_time": None,
                       "proposals_created": 0, "observe_rejected": 0,
                       "last_error": None, "source": None}

    def status(self):
        with self._state_lock:
            return {**self._state, "enabled": self.config.enabled,
                    "interval_seconds": self.config.interval,
                    "max_proposals": self.config.max_proposals,
                    "budget_cap": str(self.config.budget),
                    "thread_alive": bool(self._thread and self._thread.is_alive())}

    def _update(self, **values):
        with self._state_lock:
            self._state.update(values)

    def log(self, stage, message):
        logger.info("%s | Cycle %03d | %-7s | %s", timestamp(), self._state["cycle"], stage, message)

    def start(self):
        with self._state_lock:
            if not self.config.enabled or (self._thread and self._thread.is_alive()):
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="pharmacy-agent", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            started = time.monotonic()
            self.run_cycle()
            # Never overlap cycles; overruns wait a full interval to avoid a busy loop.
            elapsed = time.monotonic() - started
            delay = self.config.interval - elapsed if elapsed < self.config.interval else self.config.interval
            self._stop.wait(delay)

    def run_cycle(self):
        if not self._cycle_lock.acquire(blocking=False):
            return
        created, rejected, source, error = 0, 0, None, None
        self._update(cycle=self._state["cycle"] + 1, running=True)
        try:
            self.log("PLAN", "Gathering stock, 30-day usage, supplier lead times, open orders and near-expiry batches")
            cache = {}

            def fetch(path):
                if path not in cache:
                    cache[path] = self.database_request(path)
                return cache[path]

            candidates = candidates_for(fetch)
            # Refresh immediately before Act too: decisions can arrive during the wait.
            self.feedback = recent_feedback(fetch("/purchase_orders"))
            self.log("PLAN", f"{len(candidates)} candidates found; {len(self.feedback)} past decisions loaded")
            latest = {}
            for feedback in self.feedback:
                latest.setdefault(feedback["medicine_id"], feedback)
            selected, notes = [], {}
            for candidate in candidates:
                candidate = dict(candidate)
                feedback = latest.get(candidate["medicine_id"])
                reduced_limit = (feedback.get("quantity_limit") if feedback and feedback["decision"] == "rejected" else None)
                can_reduce = bool(reduced_limit and reduced_limit < feedback["quantity_ordered"])
                if feedback and feedback["decision"] == "rejected" and not can_reduce:
                    self.log("PLAN", f"Skip {candidate['medicine_name']}: past rejection #{feedback['po_id']} — {feedback['decision_reason']}")
                    continue
                if can_reduce:
                    original = candidate["suggested_quantity"]
                    candidate["suggested_quantity"] = min(original, reduced_limit)
                    notes[candidate["medicine_id"]] = f"Revised after rejection #{feedback['po_id']}: maximum {reduced_limit} units. {feedback['decision_reason']}"
                    self.log("PLAN", f"{candidate['medicine_name']}: rejection applied, quantity {original} -> {candidate['suggested_quantity']}; {feedback['decision_reason']}")
                if feedback and feedback["edited_quantity"]:
                    original = candidate["suggested_quantity"]
                    candidate["suggested_quantity"] = min(original, feedback["edited_quantity"])
                    notes[candidate["medicine_id"]] = f"Human edit #{feedback['po_id']} limits quantity to {feedback['edited_quantity']}."
                    self.log("PLAN", f"{candidate['medicine_name']}: edit applied, quantity {original} -> {candidate['suggested_quantity']}")
                selected.append(candidate)
            self.log("ACT", f"Drafting {len(selected)} candidates using existing reorder service; {len(self.feedback)} past decisions in prompt context")
            payload, _, result = advisory(fetch, candidates=selected, feedback=self.feedback)
            source = payload["source"]
            if source == "fallback":
                self.log("ACT", f"Rule-based path: {payload.get('fallback_reason', 'AI unavailable')}")
            else:
                self.log("ACT", "AI-reviewed reasoning with backend-calculated quantities and per-item fallback")
            total = Decimal("0")
            self.log("OBSERVE", f"Checking every draft; cap ${self.config.budget:.2f}, maximum {self.config.max_proposals} proposals")
            for draft in payload["items"]:
                if self._stop.is_set():
                    break
                with ORDER_LOCK:
                    # Re-read after the model call; human actions may have changed orders.
                    orders = self.database_request("/purchase_orders")
                    duplicate = any(o["medicine_id"] == draft["medicine_id"] and o["status"] in OPEN_STATUSES for o in orders)
                    feedback_now = recent_feedback(orders)
                    latest_now = next((f for f in feedback_now if f["medicine_id"] == draft["medicine_id"]), None)
                    stale_feedback = bool(latest_now and latest_now != latest.get(draft["medicine_id"]))
                    quantity = draft["suggested_quantity"]
                    rate = draft["daily_usage_rate"]
                    cost = (Decimal(str(draft["unit_price"])) * quantity).quantize(Decimal("0.01"))
                    # Incoming expiry is unknown. 90 days is a planning assumption,
                    # never a claimed shelf life; include stock remaining at arrival.
                    at_arrival = max(0, draft["available_quantity"] - draft["near_expiry_quantity"] - rate * draft["lead_time_days"])
                    expiry_ok = rate > 0 and at_arrival + quantity <= rate * 90
                    normal_limit = math.ceil(rate * 60)
                    checks = [
                        ("Open/pending order", not duplicate, "none" if not duplicate else "already exists"),
                        ("Cycle budget", total + cost <= self.config.budget, f"${total + cost:.2f} / ${self.config.budget:.2f}"),
                        ("Expiry risk", expiry_ok, f"{at_arrival + quantity:.1f} units / {rate * 90:.1f} usable at {rate:.2f} units/day; 90-day assumption, incoming expiry unknown"),
                        ("Normal usage", rate > 0 and 0 < quantity <= normal_limit, f"{quantity} units / {normal_limit} maximum (60-day usage)"),
                        ("Human feedback current", not stale_feedback, "changed during Act" if stale_feedback else "unchanged"),
                    ]
                    self.log("OBSERVE", f"{draft['medicine_name']} — quantity {quantity}; {'AI' if draft['ai_reviewed'] else 'rule-based'} reasoning: {draft['reasoning']}")
                    for label, passed, detail in checks:
                        self.log("OBSERVE", f"  {label}: {'PASS' if passed else 'FAIL'} — {detail}")
                    reasons = [label for label, passed, _ in checks if not passed]
                    if reasons:
                        rejected += 1
                        self.log("OBSERVE", f"DROP {draft['medicine_name']}: {', '.join(reasons)}")
                        continue
                    if created >= self.config.max_proposals:
                        self.log("OBSERVE", f"DEFER {draft['medicine_name']}: per-cycle proposal limit reached")
                        continue
                    reasoning = draft["reasoning"] + " " + notes.get(draft["medicine_id"], "")
                    reasoning += " Source: " + ("AI-reviewed" if draft["ai_reviewed"] else "rule-based fallback") + "."
                    order = self.database_request("/purchase_orders", "POST", {
                        "medicine_id": draft["medicine_id"], "supplier_id": draft["supplier_id"],
                        "quantity_ordered": quantity, "quantity_received": 0,
                        "unit_price": draft["unit_price"], "status": "pending_approval",
                        "created_by": "agent", "approved_by": None, "ai_generated": 1,
                        "ai_reasoning": reasoning.strip(), "decision_reason": None,
                        "created_at": timestamp(),
                        "expected_at": (date.today() + timedelta(days=draft["lead_time_days"])).isoformat(),
                    })
                    total += cost
                    created += 1
                    self.log("OBSERVE", f"SAVE #{order['po_id']} {draft['medicine_name']} — pending_approval; cycle total ${total:.2f}")
        except Exception as exc:
            error = str(exc)
            self.log("ERROR", f"Cycle failed: {exc}. Thread will continue next interval; no automatic write retry.")
        finally:
            try:
                self.feedback = recent_feedback(self.database_request("/purchase_orders"))
                rejected_count = sum(f["decision"] == "rejected" for f in self.feedback)
                self.log("ADAPT", f"{rejected_count} past rejections and {len(self.feedback) - rejected_count} edits considered for next Act prompt")
                for feedback in self.feedback:
                    self.log("ADAPT", f"Learned from #{feedback['po_id']}: {feedback['decision_reason']}")
            except Exception as exc:
                error = error or str(exc)
                self.log("ADAPT", f"Feedback unavailable: {exc}; will refresh before next Act")
            self.log("ADAPT", f"Cycle complete: {created} proposals created, {rejected} Observe rejections; next interval {self.config.interval}s")
            self._update(running=False, last_cycle_time=timestamp(), proposals_created=created,
                         observe_rejected=rejected, last_error=error, source=source)
            self._cycle_lock.release()
