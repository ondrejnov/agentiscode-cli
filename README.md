# agentiscode

`agentiscode` is a standalone command-line wrapper for OpenCode, Claude Code, and `claude-p`. It normalizes their
streamed events, writes workflow-friendly output files, and can optionally report progress to an Agentis task.

The package is independent from Agentis Adapter. A workflow can use `agentiscode` as one optional step, just like any
other executable; installing the adapter does not install this CLI.

## Installation

`agentiscode` requires Python 3.13. The recommended installation uses
[`pipx`](https://pipx.pypa.io/), which keeps the package in an isolated environment while making the command available
outside the project's Poetry environment.

From the repository root, install it for the current user:

```bash
pipx install --python python3.13 .
pipx ensurepath
agentiscode --help
```

Open a new shell after `pipx ensurepath`. If the command is still not found, add pipx's default binary directory to
`PATH` explicitly:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Add that export to the shell profile used by the processes that need `agentiscode` (for example `~/.profile`). Services
and other non-interactive processes may not load a user shell profile; configure their `PATH` explicitly instead.

To expose the command system-wide through `/usr/local/bin`, install it with a shared pipx location:

```bash
sudo env PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin \
  pipx install --python "$(command -v python3.13)" .
/usr/local/bin/agentiscode --help
```

The selected runtime executable must also be available in the target process's `PATH`: `opencode`, `claude`, or
`claude-p`.

For development, keep the Poetry-managed environment instead:

```bash
poetry install
poetry run agentiscode --help
```

## Usage

```bash
agentiscode --adapter opencode --model openai/gpt-5 "fix the failing test"
agentiscode --adapter claude --model claude-sonnet-4-5 "review the API"
printf '%s' "continue the task" | agentiscode --adapter claude-p --resume SESSION_ID --json
```

Without `--json`, assistant text is written to stdout and activity is written to stderr. With `--json`, stdout is a
stream of flattened JSON Lines objects such as `{"type":"text","text":"..."}`.

Event types: `session`, `text`, `reasoning`, `tool` (`status` `running` → `completed`/`error`), `step` (per-turn
usage), `api_retry` (Claude retrying an API call), `stderr`, `error`, and exactly one final `result`. For Claude the
`result` also carries the final answer (`result`), `structured_output` (with `--json-schema`), `subtype`, `num_turns`,
`duration_ms` and, on failure, `api_error_status` / `terminal_reason` / `errors`. A `result` with `"synthetic": true`
was computed by the wrapper because the runtime did not emit one (crash, timeout); it is `is_error: true` whenever an
`error` event preceded it.

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
| `--die-with-parent` | Linux: terminate (as on SIGTERM) when the parent process dies, even by SIGKILL. Default from `$AGENTISCODE_DIE_WITH_PARENT`; ultracode sets it for its agents. |
| `--permission-mode MODE` | Claude only: pass `--permission-mode MODE` instead of the default `--dangerously-skip-permissions`. |
| `--disallowed-tools LIST` | Claude only: comma-separated tools disallowed in addition to `AskUserQuestion` (merged into one `--disallowedTools`). |
| `--extra-args-json JSON` | JSON array of strings appended verbatim to the runtime CLI arguments, e.g. `'["--json-schema","{...}"]'`. |
| `--task-id TASK_ID` | Attach telemetry to an Agentis task and create a run unless `--run-id` is supplied. |
| `--project-id PROJECT_ID` | Append Agentis project context to a new prompt; defaults to `AGENTIS_PROJECT_ID`. |
| `--run-id RUN_ID` | Report into an existing Agentis run; requires `--task-id`. |
| `--task-status STATUS_ID` | Status used by the optional final Agentis comment. |
| `--last-message-to-comment` | Send the final assistant response as a primary Agentis comment. |
| `--primary-session BOOL` | Mark the first session as primary; defaults to `true`. |
| `--session-title TITLE` | Title of the agent's session shown in Agentis (e.g. the name of a workflow subagent). |
| `--parent-session-id SESSION_ID` | Session the agent's session belongs under in Agentis (e.g. the orchestrator that started it). |
| `--agentis-api URL` | Agentis JSON-RPC endpoint; defaults to `AGENTIS_ENDPOINT`. |
| `--agentis-token TOKEN` | User token; defaults to `AGENTIS_API_TOKEN`, then `AGENTIS_TOKEN`. |
| `--agentis-service-token TOKEN` | Service callback token; defaults to `AGENTIS_SERVICE_TOKEN`. |
| `--final-output PATH` | Write the final contiguous assistant text block to a file. |
| `--session-output PATH` | Write the coding-agent session ID as soon as it is known. |
| `--output-schema PATH` | JSON Schema the final answer must satisfy (see [Structured Output](#structured-output)). |
| `--schema-retries N` | How many times to ask the agent to fix an answer that fails `--output-schema`; defaults to `2`. |
| `PROMPT ...` | Prompt words; when omitted, the prompt is read from stdin. |

For a new session, `--task-id` and `--project-id` are appended to the prompt as `agentis_task_id` and
`agentis_project_id` XML tags. They are not appended when `--resume` is used.

## Termination

On SIGTERM/SIGINT the agent gets SIGTERM and 2 s to clean up, then SIGKILL. After that every remaining descendant gets SIGTERM, then SIGKILL after another 2 s. Descendants are found under agentiscode because it is a child subreaper on Linux. This covers processes that the agent's Bash tool started in their own sessions (for example `ultracode run` and its subagents), which a `killpg` of the agent's group does not reach. The whole shutdown fits into the 5 s grace period that `agentis-workflow` gives a step. A second signal kills the agent immediately.

## Structured Output

```bash
agentiscode --adapter claude --model claude-sonnet-5-5 \
  --output-schema .agentis/schemas/selection.schema.json \
  --final-output outputs/selection.json < prompt.md
```

`--output-schema` makes the final answer a JSON document validated against the schema:

- Claude and `claude-p` receive the schema natively (`--json-schema`); the answer is taken from `structured_output`.
  The top-level `$schema` key is not forwarded (Claude Code's validator only knows its default draft); local
  validation still honours it.
- OpenCode has no native support, so the schema is appended to the prompt and the JSON is parsed from the final text
  (Markdown code fences and surrounding prose are tolerated).
- The answer is always validated locally as well. When it does not match, the same session is resumed (`--resume`)
  with the list of validation errors and the agent is asked to answer again, up to `--schema-retries` times.
- `--final-output` then contains the validated, pretty-printed JSON. If the answer still does not match, nothing is
  written and the command exits with code `3`; runtime failures keep exit code `1`.

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
