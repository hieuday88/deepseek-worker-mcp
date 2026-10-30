# deepseek-worker-mcp

A single-file MCP server that lets Claude Code hand bulk, mechanical generation to **DeepSeek V4.1 Flash** through the **OpenCode Go** gateway. Claude stays the orchestrator and reviewer; DeepSeek does the cheap typing.

- One file, Python standard library only, no pip packages.
- Writes output straight to disk (`out_file`), so Claude reads and fixes it instead of retyping it.
- Feeds existing files to the worker (`context_files`), so Claude never pastes code into the prompt.
- Runs several workers at once: pass independent tasks as `jobs` in one call. (Claude Code sends separate tool calls one after another, so parallelism has to happen inside a single call.) There is no concurrency cap: the Go gateway handled 24 realistic jobs and 128 tiny requests at once without a single rate-limit error.
- Retries rate limits (429), gateway errors (5xx) and connection failures twice, after 2 s and 6 s.
- Every result ends with token use and cost, e.g. `(in 1200 / out 9800 tok, $0.00420)`.
- Reasoning is disabled and the output cap is 64k tokens: the worker only does mechanical work, so this is the cheapest and fastest setting.

## Requirements

- Python 3
- An [OpenCode Go](https://opencode.ai) subscription and API key
- DeepSeek models enabled for your account (OpenCode Settings > Privacy > Global region, if required)

## Setup

1. Set the API key as a user environment variable.

   Windows (PowerShell):
   ```powershell
   [Environment]::SetEnvironmentVariable('OPENCODE_GO_API_KEY', '<key>', 'User')
   ```
   macOS / Linux (add to your shell profile):
   ```bash
   export OPENCODE_GO_API_KEY='<key>'
   ```

2. Register the server with Claude Code:
   ```bash
   claude mcp add -s user deepseek-worker -- python -I "/path/to/mcp_server.py"
   ```

3. Restart Claude Code (or the Claude desktop app) so it picks up the key and the server.

To turn it off, disconnect the server, or remove it with `claude mcp remove deepseek-worker -s user`.

## Tool

`deepseek`

| Argument | Description |
|---|---|
| `prompt` | Full task and exact output format. Required. |
| `context_files` | Paths of files appended to the prompt. |
| `out_file` | Write the reply here (one wrapping code fence stripped); returns only `WROTE <path> (<n> lines)`. |
| `force` | Allow overwriting an existing `out_file`. |
| `max_tokens` | Output cap, default 64000. |
| `jobs` | Instead of the fields above: a list of jobs, each with its own `prompt`, `context_files`, `out_file`, `force`, `max_tokens`. They run in parallel and the reply lists one result per job (`[1] ...`, `[2] ...`). |

The server also sends Claude short usage instructions: use it for long mechanical output, never retype its output, and always verify it, because the worker is a cheap model that makes real mistakes.

## How it calls OpenCode Go

`POST https://opencode.ai/inference/go/openai/v1/chat/completions` with model `deepseek-v4.1-flash`, a Bearer key, its own User-Agent, and a stable `x-opencode-session` header per server process. The Go gateway rejects requests without that header (`400 MissingSessionID`), which is why generic OpenAI-compatible MCP bridges do not work with it as-is.
