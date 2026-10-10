"""Tests for the OpenCode client and session manager."""

import asyncio
from unittest import mock

import pytest

from orchestrator.config import OrchestratorConfig
from orchestrator.opencode import (
    OpenCodeClient,
    OpenCodeError,
    OpenCodeSessionManager,
)
from orchestrator.state import OrchestratorState, WorkflowPhase, WorkflowState


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("No JSON body")
        return self._json


class FakeHTTPClient:
    """Fakes httpx.AsyncClient.request for client tests."""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    async def request(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        status, data = self.responses.get((method, path), (404, None))
        return FakeResponse(status, data, text="")


def _client(responses=None):
    config = OrchestratorConfig()
    config.opencode.password = "secret"
    client = OpenCodeClient(config)
    fake = FakeHTTPClient(responses or {})
    mock.patch.object(client, "_get_client", return_value=fake).start()
    return client, fake


def _workflow(number=1, repository="owner/repo", **overrides):
    defaults = {
        "issue_number": number,
        "repository": repository,
        "project": "repo",
        "phase": WorkflowPhase.PLANNING,
        "branch": "agent/1-fix-bug",
    }
    defaults.update(overrides)
    return WorkflowState(**defaults)


class TestOpenCodeClient:
    def test_create_session_returns_id(self):
        client, _ = _client({("POST", "/session"): (200, {"id": "ses_1"})})
        assert asyncio.run(client.create_session("title")) == "ses_1"

    def test_create_session_missing_id_raises(self):
        client, _ = _client({("POST", "/session"): (200, {})})
        with pytest.raises(OpenCodeError):
            asyncio.run(client.create_session("title"))

    def test_create_session_error_raises(self):
        client, _ = _client({("POST", "/session"): (500, None)})
        with pytest.raises(OpenCodeError):
            asyncio.run(client.create_session("title"))

    def test_session_status_values(self):
        client, _ = _client({("GET", "/session/status"): (200, {
            "ses_1": {"type": "idle"},
            "ses_2": {"type": "busy"},
            "ses_3": {"type": "retry", "attempt": 2},
        })})
        assert asyncio.run(client.session_status("ses_1")) == "idle"
        assert asyncio.run(client.session_status("ses_2")) == "busy"
        assert asyncio.run(client.session_status("ses_3")) == "retry"

    def test_submit_prompt_async_sends_parts(self):
        client, fake = _client({("POST", "/session/ses_1/prompt_async"): (204, None)})
        asyncio.run(client.submit_prompt_async("ses_1", "do it"))
        _, _, kwargs = fake.requests[0]
        assert kwargs["json"] == {"parts": [{"type": "text", "text": "do it"}]}

    def test_get_messages_returns_list(self):
        client, _ = _client({("GET", "/session/ses_1/message"): (200, [])})
        assert asyncio.run(client.get_messages("ses_1")) == []

    def test_get_messages_non_list_raises(self):
        client, _ = _client({("GET", "/session/ses_1/message"): (200, {})})
        with pytest.raises(OpenCodeError):
            asyncio.run(client.get_messages("ses_1"))

    def test_basic_auth_header_is_sent(self):
        client, fake = _client({("GET", "/session/status"): (200, {})})
        asyncio.run(client.session_status("ses_1"))
        headers = fake.requests[0][2]["headers"]
        assert headers["Authorization"].startswith("Basic ")

    def test_latest_assistant_message_and_text(self):
        messages = [
            {"info": {"role": "user"}, "parts": []},
            {"info": {"role": "assistant", "id": "m2"}, "parts": [
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "world"},
                {"type": "tool", "callID": "c1"},
            ]},
        ]
        latest = OpenCodeClient.latest_assistant_message(messages)
        assert latest["info"]["id"] == "m2"
        assert OpenCodeClient.assistant_text(latest) == "Hello world"
        assert OpenCodeClient.assistant_text(None) == ""


class TestOpenCodeSessionManager:
    def test_ensure_session_creates_and_persists(self, tmp_path):
        state = OrchestratorState(str(tmp_path))
        client = mock.Mock(spec=OpenCodeClient)
        client.create_session = mock.AsyncMock(return_value="ses_new")
        manager = OpenCodeSessionManager(client, state)
        workflow = _workflow()
        session = asyncio.run(manager.ensure_session(workflow))
        assert session == "ses_new"
        assert workflow.opencode_session_id == "ses_new"

    def test_ensure_session_adopts_existing(self, tmp_path):
        state = OrchestratorState(str(tmp_path))
        other = _workflow(number=2, opencode_session_id="ses_existing")
        state.set_workflow(other)
        client = mock.Mock(spec=OpenCodeClient)
        manager = OpenCodeSessionManager(client, state)
        workflow = _workflow(number=1)
        session = asyncio.run(manager.ensure_session(workflow))
        assert session == "ses_existing"
        client.create_session.assert_not_awaited()

    def test_run_or_poll_submits_then_polls_to_success(self, tmp_path):
        state = OrchestratorState(str(tmp_path))
        client = mock.Mock(spec=OpenCodeClient)
        client.create_session = mock.AsyncMock(return_value="ses_1")
        client.session_status = mock.AsyncMock(side_effect=["idle", "busy", "idle", "idle"])
        client.get_messages = mock.AsyncMock(side_effect=[
            [{"info": {"role": "user"}, "parts": []}],
            [{"info": {"role": "user"}, "parts": []}],
            [
                {"info": {"role": "user"}, "parts": []},
                {"info": {"role": "assistant", "id": "m_new"}, "parts": [{"type": "text", "text": "result"}]},
            ],
        ])
        client.submit_prompt_async = mock.AsyncMock()
        manager = OpenCodeSessionManager(client, state)
        workflow = _workflow()
        state.set_workflow(workflow)

        r1 = asyncio.run(manager.run_or_poll(workflow, "prompt"))
        assert r1.pending
        client.submit_prompt_async.assert_awaited_once_with("ses_1", "prompt")

        r2 = asyncio.run(manager.run_or_poll(workflow, "prompt"))
        assert r2.pending
        client.submit_prompt_async.assert_awaited_once()  # no resubmit while busy

        # r3 fetches the user-only list (idle but no response yet) -> keep waiting.
        r3 = asyncio.run(manager.run_or_poll(workflow, "prompt"))
        assert r3.pending
        client.submit_prompt_async.assert_awaited_once()  # no resubmit while inflight

        # The assistant response finally arrives.
        r4 = asyncio.run(manager.run_or_poll(workflow, "prompt"))
        assert r4.success
        assert r4.output == "result"

    def test_run_or_poll_resumed_never_submits(self, tmp_path):
        state = OrchestratorState(str(tmp_path))
        client = mock.Mock(spec=OpenCodeClient)
        client.session_status = mock.AsyncMock(return_value="idle")
        client.get_messages = mock.AsyncMock(return_value=[
            {"info": {"role": "assistant", "id": "m_old"}, "parts": [{"type": "text", "text": "old"}]},
        ])
        client.submit_prompt_async = mock.AsyncMock()
        manager = OpenCodeSessionManager(client, state)
        workflow = _workflow(opencode_session_id="ses_restored")
        state.set_workflow(workflow)

        result = asyncio.run(manager.run_or_poll(workflow, "prompt"))
        assert result.success
        assert result.output == "old"
        client.submit_prompt_async.assert_not_awaited()

    def test_session_is_shared_and_serial_within_repo(self, tmp_path):
        state = OrchestratorState(str(tmp_path))
        client = mock.Mock(spec=OpenCodeClient)
        client.create_session = mock.AsyncMock(return_value="ses_1")
        client.session_status = mock.AsyncMock(return_value="idle")
        client.get_messages = mock.AsyncMock(return_value=[
            {"info": {"role": "user"}, "parts": []},
        ])
        client.submit_prompt_async = mock.AsyncMock()
        manager = OpenCodeSessionManager(client, state)
        a = _workflow(number=1)
        b = _workflow(number=2)
        state.set_workflow(a)
        state.set_workflow(b)

        ra = asyncio.run(manager.run_or_poll(a, "prompt-a"))
        assert ra.pending
        assert a.opencode_session_id == "ses_1"
        # While a's prompt is in flight, b must not submit on the shared session.
        rb = asyncio.run(manager.run_or_poll(b, "prompt-b"))
        assert rb.pending
        assert b.opencode_session_id == "ses_1"
        client.submit_prompt_async.assert_awaited_once()