"""
Tests for AuditLogger (src/tradingbot/observability/decision_audit.py)

Covers:
  - log_evaluation() with ENTER and NO_TRADE outcomes
  - log_evaluation() with dict and dataclass-like inputs
  - log_lockdown()
  - get_session_summary() aggregation
  - JSONL file correctness (one record per line, valid JSON)
  - Thread safety (concurrent writes)
  - File auto-creation / directory creation
  - Missing/empty file handled gracefully
"""

import json
import os
import threading
import tempfile
import shutil
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.tradingbot.observability.decision_audit import AuditLogger


# ============================================================================
# HELPERS / FIXTURES
# ============================================================================

@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a clean temp directory for each test."""
    return tmp_path


@pytest.fixture
def audit(tmp_dir):
    """AuditLogger writing to a temporary file."""
    log_path = str(tmp_dir / "decisions" / "audit.jsonl")
    return AuditLogger(log_path=log_path, symbol="XAUUSD", timeframe="M5")


def _make_signal(action="ENTER", gates=None, entry=2700.0, sl=2695.0, tp=2712.5):
    """Return a dict-style signal result."""
    if gates is None:
        gates = {
            "step_1_htf_bias":      {"passed": True,  "reason": "BULLISH"},
            "step_2_session":       {"passed": True,  "reason": "NY_KZ"},
            "step_3_poi":           {"passed": True,  "reason": "POI_HIT"},
            "step_4_inducement":    {"passed": True,  "reason": "IDM_OK"},
            "step_5_choch":         {"passed": True,  "reason": "CHOCH_CONFIRMED"},
            "step_6_dealing_range": {"passed": True,  "reason": "DISCOUNT"},
            "step_7_entry_model":   {"passed": True,  "reason": "FVG_ENTRY"},
            "step_8_risk_reward":   {"passed": True,  "reason": "RR_2.5x"},
        }
    return {
        "action":      action,
        "direction":   "BULLISH",
        "entry_price": entry,
        "sl_price":    sl,
        "tp_price":    tp,
        "gates":       gates,
        "reason":      "ALL_GATES_PASSED",
        "confidence_score": 90,
    }


def _make_exec_result(success=True, lot=0.02, entry=2700.0, sl=2698.0,
                      tp=2705.0, rr=2.5, risk=25.0, reason=None):
    """Return a dict-style execution result."""
    return {
        "success":          success,
        "lot_size":         lot,
        "entry_price":      entry,
        "sl_price":         sl,
        "tp_price":         tp,
        "rr_ratio":         rr,
        "risk_amount":      risk,
        "rejection_reason": reason,
    }


def _make_policy_state(daily_pnl_pct=0.0, consecutive_losses=0,
                       trades_today=0, peak_balance=100000.0,
                       current_balance=100000.0):
    return {
        "daily_pnl_pct":        daily_pnl_pct,
        "consecutive_losses":   consecutive_losses,
        "trades_today":         trades_today,
        "peak_balance":         peak_balance,
        "current_balance":      current_balance,
        "max_drawdown_pct":     3.5,
        "daily_loss_limit_pct": 1.0,
        "max_consecutive_losses": 2,
        "max_trades_per_day":   2,
    }


def _read_jsonl(path: str):
    """Read all JSON records from a JSONL file."""
    records = []
    p = Path(path)
    if not p.exists():
        return records
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


# ============================================================================
# TEST SUITE 1: log_evaluation()
# ============================================================================

class TestLogEvaluation:

    def test_enter_trade_writes_jsonl_record(self, audit):
        """ENTER trade should produce one valid JSONL record."""
        sid = audit.log_evaluation(
            signal_result=_make_signal(),
            execution_result=_make_exec_result(),
            policy_state=_make_policy_state(),
        )
        records = _read_jsonl(audit.log_path)
        assert len(records) == 1
        r = records[0]
        assert r["setup_id"] == sid
        assert r["event_type"] == "EVALUATION"
        assert r["action"] == "ENTER"
        assert r["symbol"] == "XAUUSD"
        assert r["timeframe"] == "M5"
        print(f"✓ ENTER record written: {sid}")

    def test_enter_trade_populates_execution_fields(self, audit):
        """ENTER record must carry lot, entry, sl, tp, rr, risk."""
        audit.log_evaluation(
            signal_result=_make_signal(),
            execution_result=_make_exec_result(lot=0.05, rr=2.7, risk=62.5),
            policy_state=_make_policy_state(),
        )
        r = _read_jsonl(audit.log_path)[0]
        assert r["lot_size"] == 0.05
        assert r["rr_ratio"] == 2.7
        assert r["risk_amount"] == 62.5
        assert r["entry_price"] is not None
        assert r["sl_price"] is not None
        assert r["tp_price"] is not None
        print(f"✓ Execution fields present: lot={r['lot_size']} rr={r['rr_ratio']}")

    def test_no_trade_action_sets_none_exec_fields(self, audit):
        """NO_TRADE record must have null execution fields."""
        failed_gates = {
            "step_1_htf_bias": {"passed": False, "reason": "NO_BIAS"},
            "step_2_session":  {"passed": False, "reason": "OFF_KZ"},
        }
        audit.log_evaluation(
            signal_result=_make_signal(action="NO_TRADE", gates=failed_gates),
            execution_result=None,
            policy_state=_make_policy_state(),
        )
        r = _read_jsonl(audit.log_path)[0]
        assert r["action"] == "NO_TRADE"
        assert r["lot_size"] is None
        assert r["entry_price"] is None
        assert r["rr_ratio"] is None
        print("✓ NO_TRADE fields are None")

    def test_first_failed_gate_captured(self, audit):
        """first_failed_gate should be the FIRST gate that failed."""
        gates = {
            "step_1_htf_bias":   {"passed": True,  "reason": "ok"},
            "step_2_session":    {"passed": False, "reason": "OFF_KZ"},
            "step_3_poi":        {"passed": False, "reason": "NO_POI"},
        }
        audit.log_evaluation(
            signal_result=_make_signal(action="NO_TRADE", gates=gates),
            execution_result=None,
            policy_state=_make_policy_state(),
        )
        r = _read_jsonl(audit.log_path)[0]
        assert r["first_failed_gate"] == "step_2_session"
        print(f"✓ first_failed_gate={r['first_failed_gate']}")

    def test_all_gates_passed_first_failed_gate_is_none(self, audit):
        """When all gates pass, first_failed_gate must be None."""
        audit.log_evaluation(
            signal_result=_make_signal(),
            execution_result=_make_exec_result(),
            policy_state=_make_policy_state(),
        )
        r = _read_jsonl(audit.log_path)[0]
        assert r["first_failed_gate"] is None
        print("✓ first_failed_gate=None when all pass")

    def test_gate_results_dict_is_bool_map(self, audit):
        """gate_results must map gate name → True/False."""
        audit.log_evaluation(
            signal_result=_make_signal(),
            execution_result=_make_exec_result(),
            policy_state=_make_policy_state(),
        )
        r = _read_jsonl(audit.log_path)[0]
        assert isinstance(r["gate_results"], dict)
        assert all(isinstance(v, bool) for v in r["gate_results"].values())
        assert len(r["gate_results"]) == 8
        print(f"✓ gate_results: {r['gate_results']}")

    def test_policy_state_snapshot_stored(self, audit):
        """policy_state should be embedded verbatim in the record."""
        policy = _make_policy_state(
            consecutive_losses=1, daily_pnl_pct=-0.4, trades_today=1
        )
        audit.log_evaluation(
            signal_result=_make_signal(),
            execution_result=_make_exec_result(),
            policy_state=policy,
        )
        r = _read_jsonl(audit.log_path)[0]
        assert r["policy_state"]["consecutive_losses"] == 1
        assert r["policy_state"]["daily_pnl_pct"] == -0.4
        assert r["policy_state"]["trades_today"] == 1
        print("✓ policy_state snapshot correct")

    def test_returns_unique_setup_id(self, audit):
        """Each call should return a distinct UUID string."""
        ids = {
            audit.log_evaluation(_make_signal(), _make_exec_result(), _make_policy_state())
            for _ in range(5)
        }
        assert len(ids) == 5
        print(f"✓ 5 unique setup_ids generated")

    def test_exec_result_as_dataclass_mock(self, audit):
        """Works with a dataclass-like object (has to_dict method)."""
        mock_exec = MagicMock()
        mock_exec.to_dict.return_value = {
            "success": True,
            "lot_size": 0.03,
            "entry_price": 2700.0,
            "sl_price": 2698.0,
            "tp_price": 2705.0,
            "rr_ratio": 2.5,
            "risk_amount": 37.5,
            "rejection_reason": None,
        }
        sid = audit.log_evaluation(
            signal_result=_make_signal(),
            execution_result=mock_exec,
            policy_state=_make_policy_state(),
        )
        records = _read_jsonl(audit.log_path)
        assert len(records) == 1
        assert records[0]["lot_size"] == 0.03
        print(f"✓ Dataclass-style exec result accepted: sid={sid}")

    def test_signal_result_as_object_attributes(self, audit):
        """Works when signal_result is an object with attributes (not dict)."""
        mock_signal = MagicMock()
        mock_signal.action = "ENTER"
        mock_signal.gates = {
            "step_1_htf_bias": {"passed": True, "reason": "ok"},
        }
        mock_signal.entry_price = 2700.0
        mock_signal.sl_price = 2695.0
        mock_signal.tp_price = 2712.5

        audit.log_evaluation(
            signal_result=mock_signal,
            execution_result=_make_exec_result(),
            policy_state=_make_policy_state(),
        )
        r = _read_jsonl(audit.log_path)[0]
        assert r["event_type"] == "EVALUATION"
        print("✓ Object-attribute signal_result accepted")

    def test_failed_execution_still_records_no_trade(self, audit):
        """If execution_result.success=False, action should be NO_TRADE."""
        audit.log_evaluation(
            signal_result=_make_signal(action="ENTER"),
            execution_result=_make_exec_result(
                success=False, reason="MAX_CONSECUTIVE_LOSSES reached"
            ),
            policy_state=_make_policy_state(consecutive_losses=2),
        )
        r = _read_jsonl(audit.log_path)[0]
        assert r["action"] == "NO_TRADE"
        assert r["rejection_reason"] == "MAX_CONSECUTIVE_LOSSES reached"
        print(f"✓ Failed execution logged as NO_TRADE: {r['rejection_reason']}")


# ============================================================================
# TEST SUITE 2: log_lockdown()
# ============================================================================

class TestLogLockdown:

    def test_lockdown_writes_jsonl_record(self, audit):
        """log_lockdown() should write one LOCKDOWN event."""
        sid = audit.log_lockdown(
            reason="MAX_DRAWDOWN",
            policy_state=_make_policy_state(current_balance=96400.0),
        )
        records = _read_jsonl(audit.log_path)
        assert len(records) == 1
        r = records[0]
        assert r["setup_id"] == sid
        assert r["event_type"] == "LOCKDOWN"
        assert r["lockdown_reason"] == "MAX_DRAWDOWN"
        assert r["action"] == "NO_TRADE"
        print(f"✓ LOCKDOWN record written: {sid}")

    def test_lockdown_exec_fields_are_null(self, audit):
        """Lockdown records must have null execution fields."""
        audit.log_lockdown("DAILY_LOSS_LIMIT", _make_policy_state())
        r = _read_jsonl(audit.log_path)[0]
        for field in ("lot_size", "entry_price", "sl_price", "tp_price", "rr_ratio"):
            assert r[field] is None, f"{field} should be None in lockdown record"
        print("✓ All execution fields null in LOCKDOWN record")

    def test_lockdown_gate_results_empty(self, audit):
        """Lockdown records have empty gate_results."""
        audit.log_lockdown("CONSECUTIVE_LOSSES", _make_policy_state())
        r = _read_jsonl(audit.log_path)[0]
        assert r["gate_results"] == {}
        print("✓ gate_results empty in LOCKDOWN")

    def test_lockdown_policy_state_stored(self, audit):
        """Policy state snapshot should be present in lockdown record."""
        policy = _make_policy_state(consecutive_losses=2, daily_pnl_pct=-1.2)
        audit.log_lockdown("DAILY_LOSS_LIMIT", policy)
        r = _read_jsonl(audit.log_path)[0]
        assert r["policy_state"]["consecutive_losses"] == 2
        assert r["policy_state"]["daily_pnl_pct"] == -1.2
        print("✓ policy_state in lockdown record")

    def test_multiple_lockdowns_all_written(self, audit):
        """Multiple lockdown calls each produce a separate record."""
        audit.log_lockdown("MAX_DRAWDOWN",    _make_policy_state())
        audit.log_lockdown("DAILY_LOSS_LIMIT", _make_policy_state())
        records = _read_jsonl(audit.log_path)
        assert len(records) == 2
        assert all(r["event_type"] == "LOCKDOWN" for r in records)
        print("✓ Both lockdown records present")


# ============================================================================
# TEST SUITE 3: get_session_summary()
# ============================================================================

class TestGetSessionSummary:

    def test_empty_file_returns_zeroed_summary(self, audit):
        """No records → all counts are 0."""
        summary = audit.get_session_summary()
        assert summary["trades_taken"] == 0
        assert summary["trades_rejected"] == 0
        assert summary["total_pnl"] == 0.0
        assert summary["win_rate"] == 0.0
        assert summary["avg_rr"] == 0.0
        assert summary["lockdowns"] == 0
        print("✓ Empty file → zeroed summary")

    def test_missing_file_returns_zeroed_summary(self, tmp_dir):
        """Non-existent file should not crash — returns zeros."""
        audit = AuditLogger(
            log_path=str(tmp_dir / "nonexistent" / "audit.jsonl")
        )
        summary = audit.get_session_summary()
        assert summary["trades_taken"] == 0
        print("✓ Missing file handled gracefully")

    def test_summary_counts_enter_and_no_trade(self, audit):
        """Correct trades_taken and trades_rejected counts."""
        audit.log_evaluation(_make_signal(), _make_exec_result(), _make_policy_state())
        audit.log_evaluation(_make_signal(), _make_exec_result(), _make_policy_state())
        # NO_TRADE
        failed_gates = {"step_1_htf_bias": {"passed": False, "reason": "NO_BIAS"}}
        audit.log_evaluation(
            _make_signal(action="NO_TRADE", gates=failed_gates),
            None,
            _make_policy_state(),
        )

        summary = audit.get_session_summary()
        assert summary["trades_taken"] == 2
        assert summary["trades_rejected"] == 1
        print(f"✓ taken={summary['trades_taken']} rejected={summary['trades_rejected']}")

    def test_summary_win_rate_calculation(self, audit):
        """win_rate = trades_taken / (taken + rejected)."""
        audit.log_evaluation(_make_signal(), _make_exec_result(), _make_policy_state())
        failed_gates = {"step_1_htf_bias": {"passed": False, "reason": "x"}}
        audit.log_evaluation(
            _make_signal(action="NO_TRADE", gates=failed_gates), None, _make_policy_state()
        )
        audit.log_evaluation(
            _make_signal(action="NO_TRADE", gates=failed_gates), None, _make_policy_state()
        )

        summary = audit.get_session_summary()
        # 1 taken, 2 rejected → win_rate = 1/3
        assert summary["win_rate"] == pytest.approx(1 / 3, rel=0.01)
        print(f"✓ win_rate={summary['win_rate']:.4f}")

    def test_summary_avg_rr_calculation(self, audit):
        """avg_rr is the average of rr_ratio across ENTER trades."""
        audit.log_evaluation(
            _make_signal(), _make_exec_result(rr=2.5), _make_policy_state()
        )
        audit.log_evaluation(
            _make_signal(), _make_exec_result(rr=3.0), _make_policy_state()
        )

        summary = audit.get_session_summary()
        assert summary["avg_rr"] == pytest.approx(2.75, rel=0.01)
        print(f"✓ avg_rr={summary['avg_rr']}")

    def test_summary_rejection_reasons_count(self, audit):
        """rejection_reasons_count tracks each distinct reason."""
        failed = {"step_2_session": {"passed": False, "reason": "OFF_KZ"}}
        # Rejection via exec_result reason
        audit.log_evaluation(
            _make_signal(action="ENTER"),
            _make_exec_result(success=False, reason="MAX_CONSECUTIVE_LOSSES reached"),
            _make_policy_state(),
        )
        audit.log_evaluation(
            _make_signal(action="ENTER"),
            _make_exec_result(success=False, reason="MAX_CONSECUTIVE_LOSSES reached"),
            _make_policy_state(),
        )
        audit.log_evaluation(
            _make_signal(action="NO_TRADE", gates=failed), None, _make_policy_state()
        )

        summary = audit.get_session_summary()
        counts = summary["rejection_reasons_count"]
        assert counts.get("MAX_CONSECUTIVE_LOSSES reached", 0) == 2
        print(f"✓ rejection_reasons_count={counts}")

    def test_summary_date_filter_excludes_other_days(self, audit, tmp_dir):
        """Records from different dates must not be counted for today."""
        # Write a record with yesterday's timestamp directly into the file
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        old_record = {
            "setup_id": "old-id",
            "timestamp": yesterday,
            "event_type": "EVALUATION",
            "action": "ENTER",
            "rr_ratio": 3.0,
            "risk_amount": 50.0,
            "rejection_reason": None,
            "first_failed_gate": None,
        }
        with open(audit.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(old_record) + "\n")

        # Today's record
        audit.log_evaluation(_make_signal(), _make_exec_result(), _make_policy_state())

        summary = audit.get_session_summary()  # defaults to today
        assert summary["trades_taken"] == 1  # only today's record
        print(f"✓ Date filter works: only today's records counted")

    def test_summary_lockdowns_counted(self, audit):
        """Lockdown events are counted separately, not as trades."""
        audit.log_evaluation(_make_signal(), _make_exec_result(), _make_policy_state())
        audit.log_lockdown("MAX_DRAWDOWN", _make_policy_state())
        audit.log_lockdown("DAILY_LOSS_LIMIT", _make_policy_state())

        summary = audit.get_session_summary()
        assert summary["trades_taken"] == 1
        assert summary["lockdowns"] == 2
        assert summary["trades_rejected"] == 0
        print(f"✓ lockdowns={summary['lockdowns']} not mixed with trades")

    def test_summary_date_key_present(self, audit):
        """Summary dict includes a 'date' key in YYYY-MM-DD format."""
        summary = audit.get_session_summary()
        today_str = datetime.now(timezone.utc).date().isoformat()
        assert summary["date"] == today_str
        print(f"✓ summary['date']={summary['date']}")

    def test_summary_specific_date(self, audit):
        """Can request summary for a specific date."""
        specific_date = date(2025, 1, 15)
        summary = audit.get_session_summary(session_date=specific_date)
        assert summary["date"] == "2025-01-15"
        assert summary["trades_taken"] == 0
        print(f"✓ Specific date summary: {summary['date']}")


# ============================================================================
# TEST SUITE 4: File / JSONL Correctness
# ============================================================================

class TestFileCorrectness:

    def test_each_write_appends_one_line(self, audit):
        """Multiple calls must each append exactly one line."""
        for i in range(5):
            audit.log_evaluation(_make_signal(), _make_exec_result(), _make_policy_state())

        lines = Path(audit.log_path).read_text(encoding="utf-8").splitlines()
        non_empty = [l for l in lines if l.strip()]
        assert len(non_empty) == 5
        print(f"✓ {len(non_empty)} lines written, one per call")

    def test_every_line_is_valid_json(self, audit):
        """Every written line must parse as valid JSON."""
        audit.log_evaluation(_make_signal(), _make_exec_result(), _make_policy_state())
        audit.log_lockdown("MAX_DRAWDOWN", _make_policy_state())

        for line in Path(audit.log_path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                obj = json.loads(line)   # raises if invalid
                assert isinstance(obj, dict)
        print("✓ All lines are valid JSON objects")

    def test_directory_auto_created(self, tmp_dir):
        """AuditLogger must create parent directories automatically."""
        deep_path = str(tmp_dir / "a" / "b" / "c" / "audit.jsonl")
        al = AuditLogger(log_path=deep_path)
        assert Path(deep_path).parent.exists()
        print(f"✓ Directories auto-created: {Path(deep_path).parent}")

    def test_records_survive_across_instances(self, tmp_dir):
        """Records written by one instance are readable by a new instance."""
        path = str(tmp_dir / "audit.jsonl")

        al1 = AuditLogger(log_path=path)
        al1.log_evaluation(_make_signal(), _make_exec_result(), _make_policy_state())
        al1.log_lockdown("MAX_DRAWDOWN", _make_policy_state())

        al2 = AuditLogger(log_path=path)
        summary = al2.get_session_summary()
        assert summary["trades_taken"] == 1
        assert summary["lockdowns"] == 1
        print("✓ Records persist and are readable by a new instance")

    def test_schema_required_fields_present(self, audit):
        """Every record must have all required schema fields."""
        required = {
            "setup_id", "timestamp", "event_type", "symbol", "timeframe",
            "gate_results", "first_failed_gate", "action",
            "lot_size", "entry_price", "sl_price", "tp_price",
            "rr_ratio", "risk_amount", "rejection_reason",
            "policy_state", "lockdown_reason",
        }
        audit.log_evaluation(_make_signal(), _make_exec_result(), _make_policy_state())
        audit.log_lockdown("MAX_DRAWDOWN", _make_policy_state())

        records = _read_jsonl(audit.log_path)
        for r in records:
            missing = required - set(r.keys())
            assert not missing, f"Record missing fields: {missing}"
        print(f"✓ All {len(required)} required fields present in each record")


# ============================================================================
# TEST SUITE 5: Thread Safety
# ============================================================================

class TestThreadSafety:

    def test_concurrent_writes_produce_correct_count(self, audit):
        """50 concurrent write threads → 50 distinct records."""
        N = 50
        errors = []

        def write():
            try:
                audit.log_evaluation(
                    _make_signal(), _make_exec_result(), _make_policy_state()
                )
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=write) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        records = _read_jsonl(audit.log_path)
        assert len(records) == N
        print(f"✓ {N} concurrent writes → {len(records)} records, no corruption")

    def test_concurrent_writes_all_valid_json(self, audit):
        """Concurrent writes must not corrupt JSON lines."""
        N = 30

        def write():
            audit.log_lockdown("STRESS_TEST", _make_policy_state())

        threads = [threading.Thread(target=write) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = [l for l in Path(audit.log_path)
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == N
        for line in lines:
            obj = json.loads(line)
            assert obj["event_type"] == "LOCKDOWN"
        print(f"✓ {N} concurrent lockdown writes — all valid JSON")


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v", "-s"])
