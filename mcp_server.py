"""Stdio MCP server exposing DeepSeek V4.1 Flash (OpenCode Go gateway) as a `deepseek` tool.

Standard library only. The API key is read from the OPENCODE_GO_API_KEY environment variable.
Set it once in PowerShell, then restart the Claude app:
    [Environment]::SetEnvironmentVariable('OPENCODE_GO_API_KEY', '<key>', 'User')
Register with Claude Code (Python 3 required, no pip packages):
    claude mcp add -s user deepseek-worker -- python -I "<path>/mcp_server.py"
Toggle it by connecting/disconnecting the server.
"""

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

URL = "https://opencode.ai/inference/go/openai/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"
SESSION = str(uuid.uuid4())

INSTRUCTIONS = (
    "Use `deepseek` unprompted for long mechanical output (many similar files, tests, sample data, "
    "boilerplate, conversions). Not for small edits or code needing codebase search. "
    "It sees only `prompt` + `context_files`: pass existing code as context_files, never pasted. "
    "Use out_file, then Read and fix with Edit; never retype its output (in files or messages). "
    "Its output is an unverified draft: check it and run tests. One batched call beats many small ones."
)

TOOL = {
    "name": "deepseek",
    "description": "Delegate bulk text/code generation to DeepSeek V4.1 Flash (cheap, error-prone draft).",
    "inputSchema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Full task and exact output format."},
            "context_files": {"type": "array", "items": {"type": "string"}, "description": "Files appended to the prompt."},
            "out_file": {"type": "string", "description": "Write reply here; returns only a summary."},
            "force": {"type": "boolean", "description": "Allow overwriting out_file."},
            "max_tokens": {"type": "integer", "default": 64000},
        },
        "required": ["prompt"],
    },
}


def strip_fence(text):
    """Remove a single markdown code fence wrapping the whole reply, if present."""
    lines = text.strip().splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        lines = lines[1:-1]
    return "\n".join(lines) + "\n"


def build_prompt(args):
    """Combine the prompt with the contents of any context files."""
    parts = [args["prompt"]]
    for path in args.get("context_files") or []:
        with open(path, encoding="utf-8") as f:
            parts.append(f"\n--- {path} ---\n{f.read()}")
    return "\n".join(parts)


def call_deepseek(args):
    """Run one completion and return (text, is_error)."""
    key = os.environ.get("OPENCODE_GO_API_KEY")
    if not key:
        return ("ERROR missing OPENCODE_GO_API_KEY. Do not handle the key yourself; tell the user to run "
                "[Environment]::SetEnvironmentVariable('OPENCODE_GO_API_KEY', '<key>', 'User') in PowerShell "
                "and restart the Claude app."), True
    out_file = args.get("out_file")
    if out_file and os.path.exists(out_file) and not args.get("force"):
        return (f"ERROR {out_file} exists. Pass force=true only to fully replace it; to change part of it, "
                "write the new piece to a temp file and splice it in with Edit."), True
    try:
        prompt = build_prompt(args)
    except OSError as e:
        return f"ERROR reading context file: {e}", True

    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": args.get("max_tokens", 64000),
        "temperature": 0.3,
        "thinking": {"type": "disabled"},
    }).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "x-opencode-session": SESSION,
        "User-Agent": "claude-deepseek-worker-mcp/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        hint = {
            401: " Bad API key: tell the user.",
            402: " Out of OpenCode funds: stop delegating and tell the user.",
        }.get(e.code, "")
        detail = e.read().decode("utf-8", "replace")
        if "region" in detail.lower():
            hint = " Tell the user to enable the Global region in OpenCode Settings > Privacy."
        return f"ERROR {e.code} {detail}{hint}", True
    except (urllib.error.URLError, TimeoutError) as e:
        return f"ERROR {e}", True

    choice = data["choices"][0]
    text = choice["message"]["content"] or ""
    warning = (f"\nWARNING truncated at max_tokens {args.get('max_tokens', 64000)}: raise it or split the task; "
               "do not use this output as is.") if choice.get("finish_reason") == "length" else ""
    if not out_file:
        return text + warning, False

    content = strip_fence(text)
    full = os.path.abspath(out_file)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    return f"WROTE {full} ({content.count(chr(10))} lines){warning}", False


def handle(msg):
    """Answer one JSON-RPC request; return None for notifications."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "deepseek-worker", "version": "1.2.0"},
            "instructions": INSTRUCTIONS,
        }
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        params = msg.get("params", {})
        if params.get("name") != "deepseek":
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}}
        text, is_error = call_deepseek(params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    """Serve newline-delimited JSON-RPC over stdin/stdout."""
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        reply = handle(json.loads(line))
        if reply is not None:
            sys.stdout.buffer.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
