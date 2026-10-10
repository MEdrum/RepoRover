# Agent Orchestrator

## Index

- [Description](#description)
- [Quick start](#quick-start)
- [Current workflow](#current-workflow)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [State and operations](#state-and-operations)
- [Testing](#testing)
- [Security notes](#security-notes)
- [Contributing](#contributing)
- [License](#license)
- [Roadmap](#roadmap)

## Description

The Agent Orchestrator is a Python service intended to discover labeled issues
in GitHub or GitLab, run an OpenCode task through a phase-based workflow, and
open a pull request or merge request for human review.

> **Status:** This repository is an early implementation. It compiles, but it
> is not currently production-ready. See [`todo.md`](todo.md) for the review.

## Quick start

### Prerequisites

- Docker Engine with Docker Compose
- Access to the external `agent-internal` and `agent-llm` Docker networks
- A GitHub or GitLab token with the permissions described in
[`config.yaml.example`](config.yaml.example)

### Configure and run

1. Copy the example configuration and edit it for the repositories and labels
 that should be monitored:

 ```bash
 cp config.yaml.example config.yaml
 $EDITOR config.yaml
 ```

2. Export the provider token used by the selected provider. Keep the value out
 of source control:

 ```bash
 export GITHUB_TOKEN='your-token'
 # or: export GITLAB_TOKEN='your-token'
 ```

3. Review the host paths and network names in
 [`docker-compose.yml`](docker-compose.yml), then start the service:

 ```bash
 docker compose up -d --build
 docker compose logs -f agent-orchestrator
 ```

The compose configuration mounts `config.yaml` read-only and persists workflow
state under the configured `/data` host directory. Stop the service with
`docker compose down`.

## Current workflow

The intended flow is:

```text
eligible issue
    -> NEW
    -> PLANNING
    -> VERIFICATION_DESIGN
    -> WRITE_TESTS (when automated tests are identified)
    -> IMPLEMENTING
    -> VERIFY
    -> AWAITING_REVIEW
    -> COMPLETED (PR/MR created)
```

`STUCK` and `FAILED` are also represented in the state model. On startup the
orchestrator reconciles provider-side resources (labels, branches, open PRs/MRs)
against the persisted state. OpenCode interactions are asynchronous: each
repository gets a persistent session, prompts are submitted without waiting,
and the workflow polls the session status/messages on subsequent passes instead
of re-submitting or marking failed. The current implementation does not yet
provide CI polling or merge tracking.

## Architecture

- [`src/orchestrator/main.py`](src/orchestrator/main.py): service lifecycle and
  polling loops.
- [`src/orchestrator/discovery.py`](src/orchestrator/discovery.py): finds
  issues with the configured eligible label.
- [`src/orchestrator/workflow.py`](src/orchestrator/workflow.py): invokes the
  phase-specific OpenCode prompts and provider operations.
- [`src/orchestrator/state.py`](src/orchestrator/state.py): JSON workflow state
  and JSONL history.
- [`src/orchestrator/opencode.py`](src/orchestrator/opencode.py): HTTP client
  and session manager for the OpenCode server's session API.
- [`src/git_provider/`](src/git_provider): common provider models plus GitHub
  and GitLab adapters.

## Configuration

Copy [`config.yaml.example`](config.yaml.example) to `config.yaml` and provide
credentials out of source control. The supported top-level shape is:

```yaml
provider: github # github, gitlab, or both (routes repos to the provider that owns them)

github:
  token: ""
  base_url: https://api.github.com

gitlab:
  token: ""
  url: https://gitlab.com

labels:
  eligible: agent
  in_progress: doing
  stuck: stuck
  awaiting_review: awaiting review

repositories:
  - owner/repository

polling:
  interval_seconds: 60
  max_issues_per_poll: 50

retry:
  max_attempts: 3
  base_delay_seconds: 2
  max_delay_seconds:  ​60
  exponential_base:​ 2

opencode:
  api_url: http://opencode:8080
  timeout: 300
  max_concurrent: 1  # reserved; concurrency not yet enabled

state:
  state_dir: /data/state
  max_history: 1000

log_level: INFO
```

`GITHUB_TOKEN`, `GITLAB_TOKEN`, and `OPENCODE_SERVER_PASSWORD` are used as
fallbacks when the corresponding YAML value is empty. The OpenCode server uses
HTTP Basic auth with user `opencode`; provide the password via the
`OPENCODE_SERVER_PASSWORD` environment variable rather than `config.yaml`. Do
not commit `config.yaml`, `.env` files, or tokens.

At present, `repositories` must contain explicit `owner/repository` entries.
An empty list does not enable repository discovery.

## Run with Docker Compose

The compose file expects the external `agent-internal` and `agent-llm` Docker
networks and mounts:

- the host state directory at `/data`;
- a host `config.yaml` at `/app/config.yaml`.

Start the service:

```bash
docker compose up -d --build
docker logs -f agent-orchestrator
```

The container runs as a non-root user with dropped capabilities, resource
limits, and a read-only config mount. Adjust the host paths and network names
in [`docker-compose.yml`](docker-compose.yml) for another deployment.

## State and operations

The intended state files are:

- `/data/state/workflows.json`
- `/data/state/history.jsonl`

The state model records the repository, issue, phase, branch, plans, timestamps,
errors, and metadata. On startup the orchestrator reconciles labels, branches,
and open PRs/MRs against the provider before resuming, and logs any missing
resources it finds.

## Testing

The project has an isolated, mocked unit-test suite (no live credentials or
network access required):

```bash
.venv/bin/python -m pytest tests/ -q
```

A syntax check that does not contact providers is:

```bash
python3 -m compileall -q src
```

## Security notes

- Keep provider tokens outside version control and prefer a secret store.
- Treat issue bodies and OpenCode output as untrusted input.
- Use least-privilege provider tokens and limit the repositories monitored.
- Review generated PRs/MRs and verify CI before merging.
- Do not expose the OpenCode endpoint or state directory publicly.

## Contributing

Contributions are welcome. Before opening a pull request:

1. Create a focused branch from the current default branch.
2. Keep changes scoped and update the README or configuration example when
   behavior or setup changes.
3. Run the checks that CI enforces:

   ```bash
   python -m compileall -q src
   ruff check src tests
   ruff format --check src tests
   mypy src
   pytest -q tests/
   ```

4. Explain the motivation, implementation, and verification in the pull
   request description. Do not include credentials, private configuration, or
   generated state files.

Please report security issues privately rather than opening a public issue with
exploitable details.

## License

This project is licensed under the [MIT License](LICENSE).

## Roadmap
The most important next steps are to add atomic state persistence and bounded
retries, make provider operations idempotent,and add mocked adapter tests. Longer-term
possibilities include webhooks, CI-aware verification, per-repository policy,
concurrency controls,and an operator API. The complete review is in
[`todo.md`](todo.md.)

