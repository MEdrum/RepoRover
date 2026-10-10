# Codebase review TODO

This review is based on the current source tree and does not inspect the live
`config.yaml`, because it may contain credentials. Items are ordered roughly by
impact.

## Confirmed bugs and blockers

- [x] **Use one shared state manager for discovery and workflow processing
   (P0).** [`IssueOrchestrator`](src/orchestrator/main.py) creates one
  `OrchestratorState`, while [`IssueDiscovery`](src/orchestrator/discovery.py)
  creates another. Discovery writes newly queued workflows to its private
  instance, but [`WorkflowManager`](src/orchestrator/workflow.py) reads the
  main instance. As a result, discovered issues are not processed by the
  workflow loop, and every discovery poll can queue the same issue again.
- [x] **Fix the polling interval lookup (P0).** [`main.py`](src/orchestrator/main.py)
  reads `self.config.polling_interval`, but the attribute lives at
  `self.config.git_provider.polling_interval`. The discovery loop raises
  `AttributeError` after its first poll and never sleeps/repeats normally.
- [x] **`provider: both` runs separate provider adapters and routes
  repositories.** A shared `ProviderManager` runs GitHub and GitLab side by
  side and routes each repository to the provider that owns it (probed
  GitHub-first, cached per repository). Config validation requires both tokens
  when `both` is selected.
- [x] **Make workflow side effects idempotent (P1).** `_phase_new` persists the
  branch and phase before its initial comment completes, and
  `_phase_awaiting_review` creates the PR/MR before labels and the final
  comment. A crash or transient API failure can leave a branch/PR behind while
  the workflow retries the create operation and fails with a conflict or
  duplicate resource.
- [x] **Persist and bound failures/retries (P1).** `retry_count`, `last_error`,
  and the configured retry settings are not used to control phase retries.
  Most phase failures leave the workflow in the same phase and the loop retries
  every five seconds. Some error paths set state but `process_queue()` only
  saves when at least one workflow succeeds, so failure state can be lost on
  restart.
- [x] **Use the repository's actual default branch (P1).** `WorkflowState`
  defaults `base_branch` to `"main"` and `_phase_new` passes it to branch
  creation. Repositories whose default branch is `master`, `develop`, or
  another name will fail or create the branch from the wrong base. Fetch the
  repository metadata before creating the workflow/branch.
- [x] **Complete restart recovery (P1).** The README describes label/branch
  reconciliation, but startup only loads JSON state. There is no recovery pass
  that checks in-flight OpenCode runs, existing branches, existing PRs/MRs, or
  the provider labels before resuming.
- [x] **Close HTTP clients during shutdown (P2).** `IssueOrchestrator.stop()`
  does not close its OpenCode client, and `WorkflowManager` owns a second
  OpenCode client that is also never closed. Add lifecycle ownership and close
  both clients.
- [x] **Handle provider/API response contracts explicitly (P2).**
  `OpenCodeClient.run_task()` accepts only HTTP 200, although task creation
  commonly returns 201 or 202, and it defaults missing `success` to `True`.
  Validate the response schema and explicitly support the intended synchronous
  or asynchronous contract.
- [x] **Do not treat a missing OpenCode task as a completed failure forever
  (P2).** `get_task_status()` returns `None` for 404 and all exceptions, but
  `opencode_run_id` is never stored or polled by the workflow. Long-running or
  asynchronous tasks therefore cannot be recovered or observed.

## Correctness and reliability issues

- [x] Add configuration validation for provider names, repository syntax,
  positive intervals/timeouts, required tokens, label values, and state
  directory writability. Fail fast with actionable messages instead of
  allowing partially valid configuration.
- [x] Remove the misleading “empty repositories means all accessible
  repositories” behavior from documentation or implement repository discovery.
  `discover_and_queue()` currently returns immediately when the list is empty.
- [x] Avoid broad exception handling that converts malformed state, malformed
  API payloads, and programming errors into silent retries or generic
  `FAILED` states. Preserve exception categories and include structured context.
- [x] Make state writes atomic (`temporary file` + `os.replace`) and protect
  concurrent reads/writes. A process interruption during `workflows.json`
  writing can destroy all durable workflow state.
- [x] Bound or rotate `history.jsonl`. `StateConfig.max_history` is never used,
  and `OrchestratorState` loads the complete history into memory.
- [ ] Avoid duplicate API calls in the GitHub adapter, notably resolving the
  repository and labels repeatedly in `list_issues()`. Add pagination and
  explicit limits consistently for both providers.
- [ ] Normalize GitHub and GitLab state values (`open`, `closed`, `merged`,
  CI statuses, timestamps) at the adapter boundary and test the mappings.
- [ ] Make `list_repositories(owner=...)` exact. GitLab currently uses a search
  query and `owned=True, membership=True`, which can return unrelated projects.
- [ ] Verify merge-request API compatibility for the pinned `python-gitlab`
  version, especially `merge_method` and `mr.draft`; add mocked adapter tests
  rather than relying on live credentials.
- [ ] Replace the manual integration script in
  [`test_adapters.py`](test_adapters.py) with isolated unit tests using mocked
  provider/OpenCode clients. The current script reads live credentials and can
  mutate/read real repositories.
- [ ] Ensure comments, labels, and PR/MR creation are reconciled after partial
  failure. The current phase transitions can report failure after an external
  side effect has already succeeded.
- [ ] Sanitize and constrain issue content before embedding it into prompts,
  and define an explicit trust boundary for issue text/instructions returned
  from OpenCode.

## Unclean or incomplete implementation

- [ ] Remove unused imports and duplicated provider/client construction.
- [x] Use timezone-aware UTC timestamps instead of deprecated naive
  `datetime.utcnow()`.
- [ ] Replace magic values (`5` second workflow delay, `5000` GitHub limit,
  `/app/config.yaml`) with validated configuration or constants.
- [ ] Either implement memory and webhook configuration or remove it from the
  public configuration/documentation until it is wired into the runtime.
- [x] OpenCode is asynchronous and session-based. One session per repository,
  prompts are submitted via /prompt_async, completion is detected by polling
  /session/status and /session/{id}/message, and abort/health endpoints are
  wired into the client.
- [ ] Define whether a successful workflow means “PR/MR created” or “change
  merged.” The current code marks the workflow `COMPLETED` immediately after
  creating a review request and never observes review/merge state.
- [ ] Add structured metrics/health reporting for discovery failures, queue
  depth, phase duration, retries, provider rate limits, and OpenCode health.

## Potential future features

- [ ] Webhook-triggered discovery with signature verification and deduplication,
  alongside polling as a fallback.
- [ ] Per-repository configuration: provider, base branch, labels, concurrency,
  allowed paths, test commands, and OpenCode policy.
- [ ] Global and per-repository concurrency limits using
  `max_concurrent`, with fair scheduling and backpressure.
- [ ] Human approval gates before implementation, before opening a PR/MR, and
  before retrying destructive operations.
- [ ] CI-aware verification that waits for provider checks and feeds failed
  logs back into a bounded implementation retry.
- [ ] A small status API/CLI for workflow inspection, safe cancellation,
  retry/resume, and state repair.
- [ ] Stronger audit history with correlation IDs, redacted payloads, and
  retention controls.
- [ ] Secret management through Docker/host secret stores instead of YAML
  values or environment interpolation in compose files.
- [ ] Provider capability negotiation so unsupported operations are surfaced
  before a workflow starts.

## Validation performed

- `python -m compileall -q src test_adapters.py` passed.
- `pytest -q` could not run because `pytest` is not installed in the available
  environment.
