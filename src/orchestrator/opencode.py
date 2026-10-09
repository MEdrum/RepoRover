"""
OpenCode Client

Handles communication with the OpenCode runtime for task execution.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import httpx

from orchestrator.config import OrchestratorConfig

logger = logging.getLogger(__name__)


@dataclass
class OpenCodeTask:
    """Task to send to OpenCode."""
    repository: str
    project: str
    branch: str
    issue_number: int
    issue_title: str
    issue_body: str
    issue_labels: List[str]
    phase: str
    instructions: str
    context: Dict[str, Any] = field(default_factory=dict)
    run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass
class OpenCodeResult:
    """Result from OpenCode execution."""
    success: bool
    output: str = ""
    error: str = ""
    run_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class OpenCodeClient:
    """Client for invoking OpenCode tasks."""
    
    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.api_url = config.opencode.api_url.rstrip("/")
        self.timeout = config.opencode.timeout
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.api_url,
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=5)
            )
        return self._client
    
    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    def _parse_result(self, response: httpx.Response, run_id: str) -> OpenCodeResult:
        """Parse and validate a task response body.

        The contract is synchronous: the response body must contain an explicit
        boolean `success` flag (plus optional output/error/metadata). A missing or
        invalid `success` is treated as a malformed response, never as success.
"""
        try:
            data = response.json()
        except ValueError:
            return OpenCodeResult(
                success=False,
                error=f"Malformed OpenCode response (not JSON): {response.text}",
                run_id=run_id
            )
        if not isinstance(data, dict):
            return OpenCodeResult(
                success=False,
                error=f"Malformed OpenCode response: expected JSON object, got {type(data).__name__}",
                run_id=run_id
            )
        success = data.get("success")
        if not isinstance(success, bool):
            return OpenCodeResult(
                success=False,
                error="Malformed OpenCode response: missing or invalid boolean 'success' field",
                run_id=run_id
            )
        output = data.get("output", "")
        error = data.get("error", "")
        metadata = data.get("metadata", {})
        return OpenCodeResult(
            success=success,
            output=output if isinstance(output, str) else "",
            error=error if isinstance(error, str) else "",
            run_id=run_id,
            metadata=metadata if isinstance(metadata, dict) else {}
        )
    
    async def run_task(self, task: OpenCodeTask) -> OpenCodeResult:
        """Run a task via OpenCode.

        The task API is treated as synchronous: a 200/201/202 response
        carries the completed task result, which must include an explicit
        boolean `success`. Non-2xx statuses or malformed bodies are failures.
"""
        logger.info(f"Running OpenCode task {task.run_id} for {task.repository}#{task.issue_number} [{task.phase}]")
        
        try:
            client = await self._get_client()
            
            # Prepare task payload
            payload = {
                "run_id": task.run_id,
                "repository": task.repository,
                "project": task.project,
                "branch": task.branch,
                "issue_number": task.issue_number,
                "issue_title": task.issue_title,
                "issue_body": task.issue_body,
                "issue_labels": task.issue_labels,
                "phase": task.phase,
                "instructions": task.instructions,
                "context": task.context
            }
            
            # Call OpenCode API
            # Note: This assumes OpenCode has a /api/v1/tasks endpoint
            # The actual endpoint may need to be adjusted based on OpenCode's API
            response = await client.post(
                "/api/v1/tasks",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code in (200, 201, 202):
                return self._parse_result(response, task.run_id)
            else:
                error_text = response.text
                logger.error(f"OpenCode API error {response.status_code}: {error_text}")
                return OpenCodeResult(
                    success=False,
                    error=f"OpenCode API error {response.status_code}: {error_text}",
                    run_id=task.run_id
                )
                
        except httpx.TimeoutException:
            logger.error(f"OpenCode task {task.run_id} timed out after {self.timeout}s")
            return OpenCodeResult(
                success=False,
                error=f"Task timed out after {self.timeout}s",
                run_id=task.run_id
            )
        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to OpenCode at {self.api_url}: {e}")
            return OpenCodeResult(
                success=False,
                error=f"Cannot connect to OpenCode: {e}",
                run_id=task.run_id
            )
        except Exception as e:
            logger.error(f"OpenCode task {task.run_id} failed: {e}")
            return OpenCodeResult(
                success=False,
                error=str(e),
                run_id=task.run_id
            )
    
    async def get_task_status(self, run_id: str) -> Optional[OpenCodeResult]:
        """Get status of a running task."""
        try:
            client = await self._get_client()
            response = await client.get(f"/api/v1/tasks/{run_id}")
            
            if response.status_code in (200, 201, 202):
                return self._parse_result(response, run_id)
            elif response.status_code == 404:
                return None
            else:
                logger.error(f"OpenCode status error {response.status_code}: {response.text}")
                return None
        except Exception as e:
            logger.error(f"Failed to get task status: {e}")
            return None
    
    async def cancel_task(self, run_id: str) -> bool:
        """Cancel a running task."""
        try:
            client = await self._get_client()
            response = await client.post(f"/api/v1/tasks/{run_id}/cancel")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Failed to cancel task: {e}")
            return False
    
    async def health_check(self) -> bool:
        """Check if OpenCode is healthy."""
        try:
            client = await self._get_client()
            response = await client.get("/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False