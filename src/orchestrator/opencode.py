"""
OpenCode client and session management.

Talks to the OpenCode HTTP server's real session API:

- POST   /session                        -> create a session, returns {"id"}
- POST   /session/{id}/prompt_async      -> submit a prompt asynchronously (204)
- GET    /session/status                 -> {sessionID: {type: idle|busy|retry, ...}}
- GET    /session/{id}/message           -> [{info, parts}] (assistant text in parts)
- POST   /session/{id}/abort             -> cancel a running turn
- GET    /global/health                  -> health check

Auth is HTTP Basic (user "opencode", password from OPENCODE_SERVER_PASSWORD).
"""

import base64
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from orchestrator.config import OrchestratorConfig
from orchestrator.state import WorkflowState

logger = logging.getLogger(__name__)


class OpenCodeError(Exception):
    """Base error for OpenCode API failures."""


@dataclass
class OpenCodeTask:
    """A prompt submitted to OpenCode for a workflow phase."""
    repository: str
    project: str
    branch: str
    issue_number: int
    issue_title: str
    issue_body: str
    issue_labels: list[str]
    phase: str
    instructions: str
    context: dict[str, Any] = field(default_factory=dict)
    run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass
class OpenCodeResult:
    """Result of an OpenCode interaction."""
    success: bool
    output: str = ""
    error: str = ""
    run_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    pending: bool = False


class OpenCodeClient:
    """HTTP client for the OpenCode session API."""

    def __init__(self, config: OrchestratorConfig):
        self.api_url = config.opencode.api_url.rstrip("/")
        self.timeout = config.opencode.timeout
        password = config.opencode.password
        credentials = base64.b64encode(f"opencode:{password}".encode()).decode()
        self._auth_header = {"Authorization": f"Basic {credentials}"}
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.api_url,
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=5),
            )
        return self._client

    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        client = await self._get_client()
        kwargs.setdefault("headers", {}).update(self._auth_header)
        return await client.request(method, path, **kwargs)

    async def create_session(self, title: str) -> str:
        """Create a session and return its id."""
        response = await self._request("POST", "/session", json={"title": title})
        if response.status_code >= 400:
            raise OpenCodeError(f"create_session failed: HTTP {response.status_code}: {response.text}")
        data = response.json()
        session_id = data.get("id")
        if not session_id:
            raise OpenCodeError("create_session response missing 'id'")
        return session_id

    async def session_status(self, session_id: str) -> str:
        """Return the session status type: idle, busy, or retry."""
        response = await self._request("GET", "/session/status")
        if response.status_code >= 400:
            raise OpenCodeError(f"session_status failed: HTTP {response.status_code}: {response.text}")
        data = response.json()
        status = (data or {}).get(session_id, {}).get("type")
        if status not in ("idle", "busy", "retry"):
            logger.warning(f"Unknown session status for {session_id}: {status!r}")
            return "idle"
        return status

    async def submit_prompt_async(self, session_id: str, text: str) -> None:
        """Submit a prompt to a session without waiting for the response."""
        response = await self._request(
            "POST",
            f"/session/{session_id}/prompt_async",
            json={"parts": [{"type": "text", "text": text}]},
        )
        if response.status_code >= 400:
            raise OpenCodeError(f"prompt_async failed: HTTP {response.status_code}: {response.text}")

    async def get_messages(self, session_id: str) -> list[dict]:
        """Return the message list for a session."""
        response = await self._request("GET", f"/session/{session_id}/message")
        if response.status_code >= 400:
            raise OpenCodeError(f"get_messages failed: HTTP {response.status_code}: {response.text}")
        data = response.json()
        if not isinstance(data, list):
            raise OpenCodeError("get_messages response is not a list")
        return data

    async def abort(self, session_id: str) -> bool:
        """Cancel the running turn of a session."""
        try:
            response = await self._request("POST", f"/session/{session_id}/abort")
            return response.status_code < 400
        except Exception as e:  # noqa: BLE001 - convert client failures to results / best-effort
            logger.error(f"Failed to abort session {session_id}: {e}")
            return False

    async def health_check(self) -> bool:
        """Check whether the OpenCode server is healthy."""
        try:
            response = await self._request("GET", "/global/health", timeout=5)
            return response.status_code == 200
        except Exception:  # noqa: BLE001 - convert client failures to results / best-effort
            return False

    @staticmethod
    def latest_assistant_message(messages: list[dict]) -> dict | None:
        """Return the last assistant message from a message list, if any."""
        for message in reversed(messages):
            if message.get("info", {}).get("role") == "assistant":
                return message
        return None

    @staticmethod
    def assistant_text(message: dict | None) -> str:
        """Extract concatenated text-part content from an assistant message."""
        if not message:
            return ""
        parts = message.get("parts", []) or []
        return "".join(part.get("text", "") for part in parts if part.get("type") == "text")


class OpenCodeSessionManager:
    """Manages one OpenCode session per repository.

    Sessions are reused across issues and phases within a repository. A prompt
    is submitted only when the session is idle and no newer assistant message
    is available. After a restart, a workflow that already has a session id is
    resumed by polling only (never auto-resubmitted).
    """

    def __init__(self, client: OpenCodeClient, state):
        self.client = client
        self.state = state
        self._repo_session: dict[str, str] = {}
        self._consumed: dict[str, str | None] = {}
        self._inflight: set[str] = set()
        self._attempted: set[str] = set()

    def _workflow_key(self, workflow: WorkflowState) -> str:
        return self.state.get_workflow_key(workflow.repository, workflow.issue_number)

    async def ensure_session(self, workflow: WorkflowState) -> str:
        """Resolve the session for a repository, creating one if needed."""
        repo = workflow.repository
        session_id = self._repo_session.get(repo)
        if session_id is None:
            # Adopt an existing session persisted on another workflow of this repo.
            for other in self.state.list_workflows():
                if other.repository == repo and other.opencode_session_id:
                    session_id = other.opencode_session_id
                    break
        if session_id is None:
            session_id = await self.client.create_session(f"RepoRover: {repo}")
        self._repo_session[repo] = session_id
        workflow.opencode_session_id = session_id
        return session_id

    async def run_or_poll(self, workflow: WorkflowState, prompt: str) -> OpenCodeResult:
        """Submit or poll a prompt against the repository's session."""
        key = self._workflow_key(workflow)
        resumed = key not in self._attempted and bool(workflow.opencode_session_id)
        session_id = await self.ensure_session(workflow)
        self._attempted.add(key)

        try:
            status = await self.client.session_status(session_id)
        except Exception as e:  # noqa: BLE001 - convert client failures to results / best-effort
            return OpenCodeResult(success=False, error=f"OpenCode status check failed: {e}", run_id=session_id)

        if status in ("busy", "retry"):
            return OpenCodeResult(success=True, pending=True, run_id=session_id)

        # Idle: look for a new assistant message beyond the consumed boundary.
        try:
            messages = await self.client.get_messages(session_id)
        except Exception as e:  # noqa: BLE001 - convert client failures to results / best-effort
            return OpenCodeResult(success=False, error=f"OpenCode message check failed: {e}", run_id=session_id)

        latest = OpenCodeClient.latest_assistant_message(messages)
        latest_id = latest.get("info", {}).get("id") if latest else None
        if latest_id and latest_id != self._consumed.get(session_id):
            self._consumed[session_id] = latest_id
            self._inflight.discard(session_id)
            return OpenCodeResult(
                success=True,
                output=OpenCodeClient.assistant_text(latest),
                run_id=session_id,
            )

        # No new message. Never auto-resubmit a resumed session after restart.
        if resumed and session_id not in self._inflight:
            return OpenCodeResult(success=True, pending=True, run_id=session_id)

        # Avoid resubmitting while we are already waiting on a response.
        if session_id in self._inflight:
            return OpenCodeResult(success=True, pending=True, run_id=session_id)

        try:
            await self.client.submit_prompt_async(session_id, prompt)
        except Exception as e:  # noqa: BLE001 - convert client failures to results / best-effort
            return OpenCodeResult(success=False, error=f"OpenCode submit failed: {e}", run_id=session_id)
        self._inflight.add(session_id)
        return OpenCodeResult(success=True, pending=True, run_id=session_id)
