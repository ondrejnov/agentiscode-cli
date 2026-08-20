# agentiscode

`agentiscode` is a standalone command-line wrapper for OpenCode, Claude Code, and `claude-p`. It normalizes their
streamed events, writes workflow-friendly output files, and can optionally report progress to an Agentis task.

The package is independent from Agentis Adapter. A workflow can use `agentiscode` as one optional step, just like any
other executable; installing the adapter does not install this CLI.

## Installation

```bash
poetry install
poetry run agentiscode --help
```

The selected runtime executable must also be available in `PATH`: `opencode`, `claude`, or `claude-p`.

## Usage

```bash
poetry run agentiscode --adapter opencode --model openai/gpt-5 "fix the failing test"
poetry run agentiscode --adapter claude --model claude-sonnet-4-5 "review the API"
printf '%s' "continue the task" | poetry run agentiscode --adapter claude-p --resume SESSION_ID --json
```

Without `--json`, assistant text is written to stdout and activity is written to stderr. With `--json`, stdout is a
stream of flattened JSON Lines objects such as `{"type":"text","text":"..."}`.

## Options

| Option | Purpose |
| --- | --- |
| `-a`, `--adapter NAME` | Required runtime: `opencode`, `claude`, or `claude-p`; aliases include `oc`, `cloud`, `cc`, and `cp`. |
| `-m`, `--model MODEL` | Model passed to the selected runtime. |
| `-e`, `--effort VALUE` | Claude `--effort` or OpenCode `--variant`. |
| `--agent NAME` | Named agent or CLI mode. |
| `--cwd PATH` | Working directory; defaults to the current directory. |
| `--resume SESSION_ID` | Resume an existing coding-agent session. |
| `--timeout SECONDS` | Whole-run timeout; `0` means no timeout. |
| `--json` | Emit normalized flattened JSON Lines on stdout. |
| `--task-id TASK_ID` | Attach telemetry to an Agentis task and create a run unless `--run-id` is supplied. |
| `--project-id PROJECT_ID` | Append Agentis project context to a new prompt; defaults to `AGENTIS_PROJECT_ID`. |
| `--run-id RUN_ID` | Report into an existing Agentis run; requires `--task-id`. |
| `--task-status STATUS_ID` | Status used by the optional final Agentis comment. |
| `--last-message-to-comment` | Send the final assistant response as a primary Agentis comment. |
| `--primary-session BOOL` | Mark the first session as primary; defaults to `true`. |
| `--agentis-api URL` | Agentis JSON-RPC endpoint; defaults to `AGENTIS_ENDPOINT`. |
| `--agentis-token TOKEN` | User token; defaults to `AGENTIS_API_TOKEN`, then `AGENTIS_TOKEN`. |
| `--agentis-service-token TOKEN` | Service callback token; defaults to `AGENTIS_SERVICE_TOKEN`. |
| `--final-output PATH` | Write the final contiguous assistant text block to a file. |
| `--session-output PATH` | Write the coding-agent session ID as soon as it is known. |
| `PROMPT ...` | Prompt words; when omitted, the prompt is read from stdin. |

For a new session, `--task-id` and `--project-id` are appended to the prompt as `agentis_task_id` and
`agentis_project_id` XML tags. They are not appended when `--resume` is used.

## Agentis Reporting

```bash
agentiscode --adapter opencode \
  --task-id "$AGENTIS_TASK_ID" \
  --run-id "$AGENTIS_RUN_ID" \
  --agentis-api "$AGENTIS_ENDPOINT" \
  --final-output .agentis/outputs/final-comment.md \
  --session-output .agentis/outputs/session-id \
  < "$AGENTIS_PROMPT_FILE"
```

Telemetry stores the session ID and activity stream in Agentis. When `--run-id` is supplied, `agentiscode` does not
finish that run; the workflow orchestrator remains responsible for applying outputs and completing the run.

## Runtime Image

`agent-container/` contains the optional workflow image with `agentiscode`, OpenCode, Claude Code, and related coding
tools. Its build script stages this project into the Docker context and installs the standalone package rather than the
Agentis Adapter:

```bash
./agent-container/build
```

The image is a deployment option for agent-oriented workflows, not a requirement of the Agentis Adapter or this Python
package.

## Development

```bash
poetry run pytest -q
poetry run ruff check .
poetry build
```
