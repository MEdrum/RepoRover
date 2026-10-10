"""Tests for the durable state store and model."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from orchestrator.state import OrchestratorState, WorkflowPhase, WorkflowState


def _workflow(number=1, **overrides):
    defaults = {
        "issue_number": number,
        "repository": "owner/repo",
        "project": "repo",
        "phase": WorkflowPhase.PLANNING,
        "branch": "agent/1-fix-bug",
    }
    defaults.update(overrides)
    return WorkflowState(**defaults)


class TestPersistence:
    def test_save_writes_versioned_file_and_reloads(self, tmp_path):
        state = OrchestratorState(str(tmp_path))
        state.set_workflow(_workflow())
        asyncio.run(state.save())

        raw = json.loads((tmp_path / "workflows.json").read_text())
        assert raw["schema_version"] == 1

        state2 = OrchestratorState(str(tmp_path))
        asyncio.run(state2.load())
        wf = state2.get_workflow("owner/repo", 1)
        assert wf is not None
        assert wf.phase == WorkflowPhase.PLANNING
        assert wf.branch == "agent/1-fix-bug"

    def test_timestamps_round_trip_as_datetimes(self, tmp_path):
        state = OrchestratorState(str(tmp_path))
        now = datetime.now(UTC)
        state.set_workflow(_workflow(started_at=now, updated_at=now, retry_at=now + timedelta(seconds=5)))
        asyncio.run(state.save())

        state2 = OrchestratorState(str(tmp_path))
        asyncio.run(state2.load())
        wf = state2.get_workflow("owner/repo", 1)
        assert wf.started_at == now
        assert wf.retry_at is not None
        assert wf.retry_at > now

    def test_legacy_flat_format_is_migrated(self, tmp_path):
        (tmp_path / "workflows.json").write_text(json.dumps({
            "owner/repo#1": {
                "issue_number": 1,
                "repository": "owner/repo",
                "project": "repo",
                "phase": "new",
                "branch": "",
                "base_branch": "main",
                "plan": "",
                "verification_plan": "",
                "opencode_run_id": "run-abc",
                "started_at": "",
                "updated_at": "",
                "stuck_reason": "",
                "retry_count": 0,
                "last_error": "",
                "retry_at": "",
                "metadata": {},
            }
        }))
        state = OrchestratorState(str(tmp_path))
        asyncio.run(state.load())
        wf = state.get_workflow("owner/repo", 1)
        assert wf is not None
        assert wf.opencode_session_id == "run-abc"
        assert not hasattr(wf, "opencode_run_id")


class TestHistory:
    def test_history_is_bounded_by_max_history(self, tmp_path):
        state = OrchestratorState(str(tmp_path), max_history=2)
        for i in range(1, 6):
            state.set_workflow(_workflow(number=i))
        asyncio.run(state.save())
        lines = (tmp_path / "history.jsonl").read_text().splitlines()
        assert len(lines) == 2

    def test_history_entries_are_loaded_bounded(self, tmp_path):
        state = OrchestratorState(str(tmp_path), max_history=3)
        for i in range(1, 6):
            state.set_workflow(_workflow(number=i))
        asyncio.run(state.save())
        state2 = OrchestratorState(str(tmp_path), max_history=3)
        asyncio.run(state2.load())
        assert len(state2.get_history(limit=100)) <= 3