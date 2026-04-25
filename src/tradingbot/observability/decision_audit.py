"""
Decision Audit Logger — Phase 4

Logs every signal evaluation and lockdown event as a structured JSON line
to logs/decisions/audit.jsonl. Provides session summary aggregation.

Used by:
  - apps/trader/main.py   (wired into the main trading loop)
  - tests/test_audit_logger.py

JSONL format (one JSON object per line):
  {
    "setup_id": "uuid4",
    "timestamp": "ISO-8601",
    "event_type": "EVALUATION" | "LOCKDOWN",
    "symbol": "XAUUSD",
    "timeframe": "M5",
    "gate_results": {"gate_name": true/false, ...},
    "first_failed_gate": "GATE_NAME" | null,
    "action": "ENTER" | "NO_TRADE",
    "lot_size": float | null,
    "entry_price": float | null,
    "sl_price": float | null,
    "tp_price": float | null,
    "rr_ratio": float | null,
    "risk_amount": float | null,
    "policy_state": {...},
    "lockdown_reason": str | null
  }
"""

import json
import logging
import os
import uuid
from datetime import datetime, date, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default log path (relative to CWD, i.e. TradingBOt/)
DEFAULT_AUDIT_PATH = "logs/decisions/audit.jsonl"


class AuditLogger:
    """
    Thread-safe structured audit logger for every signal evaluation.

    Usage:
        audit = AuditLogger()
        audit.log_evaluation(signal_result, execution_result, policy_state)
        audit.log_lockdown("MAX_DRAWDOWN", policy_state)
        summary = audit.get_session_summary()
    """

    def __init__(
        self,
        log_path: str = DEFAULT_AUDIT_PATH,
        symbol: str = "XAUUSD",
        timeframe: str = "M5",
    ) -> None:
        self.log_path = Path(log_path)
        self.symbol = symbol
        self.timeframe = timeframe
        self._lock = Lock()

        # Ensure directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"AuditLogger initialised → {self.log_path}")

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def log_evaluation(
        self,
        signal_result: Any,
        execution_result: Any,
        policy_state: Dict[str, Any],
    ) -> str:
        """
        Log a complete signal evaluation cycle.

        Args:
            signal_result:   Output from SignalEngine.evaluate() — must have
                             .action, .gates (dict), .direction,
                             .entry_price, .sl_price, .tp_price fields
                             OR a plain dict with the same keys.
            execution_result: ExecutionResult from OrderExecutor.execute_signal()
                              OR a plain dict. May be None for NO_TRADE cycles.
            policy_state:    Snapshot of ChallengePolicy state fields as dict.

        Returns:
            setup_id (UUID string) for cross-referencing.
        """
        setup_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()

        # ── Normalise signal_result (supports dataclass or dict) ──────────────
        if isinstance(signal_result, dict):
            action = signal_result.get("action", "NO_TRADE")
            gates_raw = signal_result.get("gates", {})
            entry_price = signal_result.get("entry_price")
            sl_price = signal_result.get("sl_price")
            tp_price = signal_result.get("tp_price")
        else:
            action = getattr(signal_result, "action", "NO_TRADE")
            gates_raw = getattr(signal_result, "gates", {})
            entry_price = getattr(signal_result, "entry_price", None)
            sl_price = getattr(signal_result, "sl_price", None)
            tp_price = getattr(signal_result, "tp_price", None)

        # ── Build gate_results: gate_name → bool ─────────────────────────────
        gate_results: Dict[str, bool] = {}
        first_failed_gate: Optional[str] = None

        for gate_name, gate_data in gates_raw.items():
            if isinstance(gate_data, dict):
                passed = bool(gate_data.get("passed", False))
            else:
                passed = bool(gate_data)
            gate_results[gate_name] = passed
            if not passed and first_failed_gate is None:
                first_failed_gate = gate_name

        # ── Normalise execution_result ────────────────────────────────────────
        exec_dict: Dict[str, Any] = {}
        if execution_result is not None:
            if isinstance(execution_result, dict):
                exec_dict = execution_result
            elif hasattr(execution_result, "to_dict"):
                exec_dict = execution_result.to_dict()
            else:
                exec_dict = {
                    "success":          getattr(execution_result, "success", False),
                    "lot_size":         getattr(execution_result, "lot_size", None),
                    "entry_price":      getattr(execution_result, "entry_price", None),
                    "sl_price":         getattr(execution_result, "sl_price", None),
                    "tp_price":         getattr(execution_result, "tp_price", None),
                    "rr_ratio":         getattr(execution_result, "rr_ratio", None),
                    "risk_amount":      getattr(execution_result, "risk_amount", None),
                    "rejection_reason": getattr(execution_result, "rejection_reason", None),
                }

        is_enter = action == "ENTER" and exec_dict.get("success", False)

        record = {
            "setup_id":          setup_id,
            "timestamp":         ts,
            "event_type":        "EVALUATION",
            "symbol":            self.symbol,
            "timeframe":         self.timeframe,
            "gate_results":      gate_results,
            "first_failed_gate": first_failed_gate,
            "action":            "ENTER" if is_enter else "NO_TRADE",
            # Execution details — only populated on ENTER
            "lot_size":          exec_dict.get("lot_size") if is_enter else None,
            "entry_price":       exec_dict.get("entry_price") or entry_price if is_enter else None,
            "sl_price":          exec_dict.get("sl_price") or sl_price if is_enter else None,
            "tp_price":          exec_dict.get("tp_price") or tp_price if is_enter else None,
            "rr_ratio":          exec_dict.get("rr_ratio") if is_enter else None,
            "risk_amount":       exec_dict.get("risk_amount") if is_enter else None,
            "rejection_reason":  exec_dict.get("rejection_reason"),
            "policy_state":      self._sanitise(policy_state),
            "lockdown_reason":   None,
        }

        self._write(record)
        logger.info(
            f"[AUDIT] EVALUATION logged | setup_id={setup_id} | "
            f"action={record['action']} | first_failed={first_failed_gate}"
        )
        return setup_id

    def log_lockdown(
        self,
        reason: str,
        policy_state: Dict[str, Any],
    ) -> str:
        """
        Log a lockdown event (account blocked from trading).

        Args:
            reason:       Human-readable lockdown reason code, e.g.
                          "MAX_DRAWDOWN", "DAILY_LOSS_LIMIT".
            policy_state: Snapshot dict of ChallengePolicy runtime fields.

        Returns:
            setup_id (UUID string).
        """
        setup_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()

        record = {
            "setup_id":          setup_id,
            "timestamp":         ts,
            "event_type":        "LOCKDOWN",
            "symbol":            self.symbol,
            "timeframe":         self.timeframe,
            "gate_results":      {},
            "first_failed_gate": None,
            "action":            "NO_TRADE",
            "lot_size":          None,
            "entry_price":       None,
            "sl_price":          None,
            "tp_price":          None,
            "rr_ratio":          None,
            "risk_amount":       None,
            "rejection_reason":  reason,
            "policy_state":      self._sanitise(policy_state),
            "lockdown_reason":   reason,
        }

        self._write(record)
        logger.warning(f"[AUDIT] LOCKDOWN logged | reason={reason}")
        return setup_id

    def get_session_summary(self, session_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Read audit.jsonl and return aggregated session statistics.

        Args:
            session_date: Date to filter by (default: today UTC).

        Returns:
            {
                "date": "YYYY-MM-DD",
                "trades_taken": int,
                "trades_rejected": int,
                "rejection_reasons_count": {"REASON": int, ...},
                "total_pnl": float,       # sum of risk_amount for winners (approx)
                "win_rate": float,        # fraction of ENTER trades (0.0–1.0)
                "avg_rr": float,          # average rr_ratio of ENTER trades
                "lockdowns": int,
            }
        """
        if session_date is None:
            session_date = datetime.now(timezone.utc).date()

        date_prefix = session_date.isoformat()  # "YYYY-MM-DD"

        trades_taken = 0
        trades_rejected = 0
        rejection_reasons: Dict[str, int] = {}
        rr_values: List[float] = []
        total_pnl = 0.0
        lockdowns = 0

        records = self._read_all()
        for rec in records:
            ts_str = rec.get("timestamp", "")
            if not ts_str.startswith(date_prefix):
                continue

            event_type = rec.get("event_type", "EVALUATION")

            if event_type == "LOCKDOWN":
                lockdowns += 1
                continue

            action = rec.get("action", "NO_TRADE")
            if action == "ENTER":
                trades_taken += 1
                rr = rec.get("rr_ratio")
                if rr is not None:
                    rr_values.append(float(rr))
                risk = rec.get("risk_amount")
                if risk is not None:
                    total_pnl += float(risk)
            else:
                trades_rejected += 1
                reason = rec.get("rejection_reason") or rec.get("first_failed_gate") or "UNKNOWN"
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

        total_evaluated = trades_taken + trades_rejected
        win_rate = (trades_taken / total_evaluated) if total_evaluated > 0 else 0.0
        avg_rr = (sum(rr_values) / len(rr_values)) if rr_values else 0.0

        return {
            "date":                    date_prefix,
            "trades_taken":            trades_taken,
            "trades_rejected":         trades_rejected,
            "rejection_reasons_count": rejection_reasons,
            "total_pnl":               round(total_pnl, 2),
            "win_rate":                round(win_rate, 4),
            "avg_rr":                  round(avg_rr, 4),
            "lockdowns":               lockdowns,
        }

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    def _write(self, record: Dict[str, Any]) -> None:
        """Append a single JSON record as one line (thread-safe)."""
        line = json.dumps(record, default=str) + "\n"
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line)

    def _read_all(self) -> List[Dict[str, Any]]:
        """Read all records from the JSONL file. Returns [] if file missing."""
        records: List[Dict[str, Any]] = []
        if not self.log_path.exists():
            return records
        with self._lock:
            try:
                with open(self.log_path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                records.append(json.loads(line))
                            except json.JSONDecodeError:
                                logger.warning(f"Skipping malformed audit line: {line[:80]}")
            except OSError as e:
                logger.error(f"Could not read audit log: {e}")
        return records

    @staticmethod
    def _sanitise(obj: Any) -> Any:
        """Recursively make a value JSON-serialisable."""
        if isinstance(obj, dict):
            return {k: AuditLogger._sanitise(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [AuditLogger._sanitise(v) for v in obj]
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)


# ---------------------------------------------------------------------------
# Backwards-compat shim: keep the old OBObservationLogger importable so
# main.py line 21 ("from ... import OBObservationLogger") still works.
# ---------------------------------------------------------------------------
class OBObservationLogger:
    """Legacy shim — kept so existing imports don't break."""

    def __init__(self, path: str = "research/ob_observations.json") -> None:
        self.path = Path(path)
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def log(self, payload: dict) -> None:
        payload = dict(payload)
        payload["logged_at"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                data = []
            data.append(payload)
            self.path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")