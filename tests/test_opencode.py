"""Tests for the OpenCode client."""

import asyncio
from unittest import mock

from orchestrator.config import OrchestratorConfig
from orchestrator.opencode import OpenCodeClient, OpenCodeTask


def _task():
    return OpenCodeTask(
        repository="owner/repo", project="repo", branch="agent/1-fix-bug",
        issue_number=1, issue_title="Fix bug", issue_body="", issue_labels=["agent"],
        phase="planning", instructions="do it",
    )
    


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("No JSON body")
        return self._json


class FakeClient:
    def __init__(self, response):
        self.response = response

    async def post(self, *args, **kwargs):
        return self.response

    async def get(self, *args, **kwargs):
        return self.response


def _run(client, response):
    fake = FakeClient(response)
    with mock.patch.object(client, "_get_client", new_callable=mock.AsyncMock, return_value=fake):
        return asyncio.run(client.run_task(_task()))


def _status(client, response):
    fake = FakeClient(response)
    with mock.patch.object(client, "_get_client", new_callable=mock.AsyncMock, return_value=fake):
        return asyncio.run(client.get_task_status("abc"))


class TestRunTaskResponseContract:
    def test_accepts_200_with_valid_body(self):
        client = OpenCodeClient(OrchestratorConfig())
        result = _run(client, FakeResponse(200, {"success": True, "output": "plan", "metadata": {"k": "v"}}))
        assert result.success
        assert result.output == "plan"
        assert result.metadata == {"k": "v"}

    def test_accepts_201_and_202(self):
        client = OpenCodeClient(OrchestratorConfig())
        result = _run(client, FakeResponse(201, {"success": False, "error": "boom"}))
        assert result.success is False
        assert result.error == "boom"
        result = _run(client, FakeResponse(202, {"success": True, "output": "ok"}))
        assert result.success
        assert result.output == "ok"

    def test_missing_success_is_not_defaulted_to_true(self):
        client = OpenCodeClient(OrchestratorConfig())
        result = _run(client, FakeResponse(201, {"output": "plan"}))
        assert result.success is False
        assert "success" in result.error

    def test_non_boolean_success_is_malformed(self):
        client = OpenCodeClient(OrchestratorConfig())
        result = _run(client, FakeResponse(200, {"success": "yes"}))
        assert result.success is False
        assert "success" in result.error

    def test_non_json_body_is_malformed(self):
        client = OpenCodeClient(OrchestratorConfig())
        result = _run(client, FakeResponse(200, text="not json"))
        assert result.success is False
        assert "JSON" in result.error

    def test_non_dict_body_is_malformed(self):
        client = OpenCodeClient(OrchestratorConfig())
        result = _run(client, FakeResponse(200, [1, 2]))
        assert result.success is False
        assert "JSON object" in result.error

    def test_server_error_returns_failure(self):
        client = OpenCodeClient(OrchestratorConfig())
        result = _run(client, FakeResponse(500, text="boom"))
        assert result.success is False
        assert "500" in result.error


class TestGetTaskStatusResponseContract:
    def test_accepts_2xx_and_returns_none_on_404(self):
        client = OpenCodeClient(OrchestratorConfig())
        result = _status(client, FakeResponse(201, {"success": True, "output": "done"}))
        assert result is not None
        assert result.output == "done"
        assert _status(client, FakeResponse(404, text="nope")) is None

    def test_malformed_status_is_reported(self):
        client = OpenCodeClient(OrchestratorConfig())
        result = _status(client, FakeResponse(200, {"output": "x"}))
        assert result is not None
        assert result.success is False


class TestPendingStatus:
    def test_pending_statuses_are_marked_pending(self):
        client = OpenCodeClient(OrchestratorConfig())
        for status in ("queued", "running", "accepted", "pending"):
            result = _run(client, FakeResponse(201, {"success": True, "status": status, "run_id": "r1"}))
            assert result.pending
            assert result.success
            assert result.run_id == "r1"
