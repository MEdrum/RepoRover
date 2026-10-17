"""
Prompt builders for the OpenCode task phases.

Every phase prompt ends with the agent-driven contract: the agent works inside
the session, then signals completion by calling the RepoRover MCP tools
`report_done(repository, issue_id, result)` or, if user input is needed,
`report_stuck(repository, issue_id, block_description)`.
"""

import re

from git_provider import Issue
from orchestrator.state import WorkflowState


def _step_footer(workflow: WorkflowState) -> str:
    """Append the agent-driven report_done/report_stuck footer to a step prompt."""
    return f"""When you have finished this step, call the tool `report_done` with:
- repository: {workflow.repository}
- issue_id: {workflow.issue_number}
- result: <the result/outcome of this step>

If you need user input or a design decision to continue, call `report_stuck` with:
- repository: {workflow.repository}
- issue_id: {workflow.issue_number}
- block_description: <what you need from the user>

Do not proceed to the next step yourself; RepoRover advances the workflow when you report done."""


def build_planning_prompt(issue: Issue, workflow: WorkflowState) -> str:
    """Build the planning prompt for OpenCode."""
    return f"""You are an autonomous coding agent. Analyze this issue and create a detailed implementation plan.

## Issue
**Title:** {issue.title}
**Number:** #{workflow.issue_number}
**Repository:** {workflow.repository}
**Branch:** {workflow.branch}
**Base:** {workflow.base_branch}

## Issue Description
{issue.body}

## Labels
{", ".join(issue.labels)}

## Your Task
Create a detailed implementation plan following this format:

## Agent plan

### Approach
[High-level approach to solving this issue]

### Changes
- [Specific file/component changes]
- [More changes...]

### Verification
- [How to verify the implementation works]
- [Test strategy: automated tests, manual checks, etc.]

### Potential risks
- [Risk 1]
- [Risk 2]

### Branch
`{workflow.branch}`
Base: `{workflow.base_branch}`

Do not implement anything yet. Only create the plan.

{_step_footer(workflow)}"""


def build_verification_design_prompt(issue: Issue, workflow: WorkflowState) -> str:
    """Build the verification design prompt (testability step)."""
    return f"""Based on the issue and your plan, design a verification strategy.

## Issue
**Title:** {issue.title}
**Number:** #{workflow.issue_number}

## Your Plan
{workflow.plan}

## Your Task
Create a verification strategy following this format:

## Verification plan

### Automated tests
[List specific automated tests to write/run, or state "No automated tests meaningfully cover this change"]

### Manual / visual verification
[If automated tests cannot meaningfully cover the requirement, provide a detailed manual
verification plan with specific steps]

### Acceptance criteria
[Specific criteria that must be met for the issue to be considered complete]

Be specific and actionable. Do not create fake tests just to satisfy a test-first rule.

Your report_done `result` must end with exactly one of these lines:
AUTOMATED_TESTS: yes
AUTOMATED_TESTS: no

{_step_footer(workflow)}"""


def build_write_tests_prompt(issue: Issue, workflow: WorkflowState) -> str:
    """Build the write-tests prompt."""
    return f"""Write tests for the issue based on the verification plan.

## Issue
**Title:** {issue.title}
**Number:** #{workflow.issue_number}

## Verification Plan
{workflow.verification_plan}

## Your Plan
{workflow.plan}

## Your Task
Write tests that:
1. Represent the requirements and acceptance criteria
2. Cover edge cases
3. Will fail before implementation and pass after
4. Are meaningful (not fake tests)

Focus on the automated test portion of the verification plan. Do not implement the feature yet.

{_step_footer(workflow)}"""


def build_implementation_prompt(issue: Issue, workflow: WorkflowState) -> str:
    """Build the implementation prompt."""
    return f"""Implement the feature/fix based on your plan and tests.

## Issue
**Title:** {issue.title}
**Number:** #{workflow.issue_number}

## Your Plan
{workflow.plan}

## Verification Plan
{workflow.verification_plan}

## Your Task
Implement the changes:
1. Follow your plan
2. Make tests pass
3. Commit regularly with conventional commit messages
4. Push to branch `{workflow.branch}`
5. Run the tests and checks and make them pass before reporting done

Do not modify the tests you wrote. Make the implementation satisfy them.

{_step_footer(workflow)}"""


def build_verification_prompt(issue: Issue, workflow: WorkflowState) -> str:
    """Build the verification prompt."""
    return f"""Run verification for the implemented changes.

## Issue
**Title:** {issue.title}
**Number:** #{workflow.issue_number}

## Your Plan
{workflow.plan}

## Verification Plan
{workflow.verification_plan}

## Your Task
Run verification:
1. Run all relevant automated tests
2. Run linting/type checks
3. Run build checks
4. Perform manual/visual verification if required
5. Report results

If verification fails, fix the failures and re-run until they pass before
reporting done. Only report stuck if you are genuinely blocked.

{_step_footer(workflow)}"""


def slugify(text: str) -> str:
    """Convert text to a slug suitable for a branch name."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:50]


def verification_decides_tests(result: str) -> str:
    """Return \"yes\" or \"no\" for the testability decision in a report result."""
    match = re.search(r"automated_tests:\s*(yes|no)", result, re.IGNORECASE)
    return match.group(1).lower() if match else "no"
