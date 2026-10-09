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
    
    async def run_task(self, task: OpenCodeTask) -> OpenCodeResult:
        """Run a task via OpenCode."""
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
            
            if response.status_code == 200:
                data = response.json()
                return OpenCodeResult(
                    success=data.get("success", True),
                    output=data.get("output", ""),
                    error=data.get("error", ""),
                    run_id=task.run_id,
                    metadata=data.get("metadata", {})
                )
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
            
            if response.status_code == 200:
                data = response.json()
                return OpenCodeResult(
                    success=data.get("success", True),
                    output=data.get("output", ""),
                    error=data.get("error", ""),
                    run_id=run_id,
                    metadata=data.get("metadata", {})
                )
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