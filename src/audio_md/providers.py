"""Summarization providers, called as headless agent CLIs — no API key in code.

Each provider authenticates through its own existing login:
  - claude-cli  -> `claude -p ... --model <model>`
  - opencode    -> `opencode run -m <provider/model>`
  - codex-cli   -> `codex exec ... -m <model>`

The instruction (prompt) is passed as an argument; the transcript is fed in as
the document content (stdin for claude/codex, an attached file for opencode).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_OUTPUT_ONLY = (
    "\n\nIMPORTANT: respond ONLY with the final markdown document. "
    "Do NOT use any tools, do NOT write files, do NOT include any preamble "
    "or commentary — just the markdown."
)

# Claude tools to disable so `claude -p` only prints text (does not try to Write).
_CLAUDE_NO_TOOLS = "Write Edit Read Bash Glob Grep Task WebFetch WebSearch NotebookEdit TodoWrite"


def _run(cmd: list[str], stdin: str, *, want_stdout: bool) -> str:
    """Run ``cmd`` capturing stdout/stderr. Return stdout if asked."""
    proc = subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"{cmd[0]} exited with {proc.returncode}: {detail}")
    return proc.stdout or ""


def _require(binary: str) -> None:
    if shutil.which(binary) is None:
        raise RuntimeError(
            f"'{binary}' CLI not found on PATH. Install it and complete its login first."
        )


def _call_claude(instruction: str, content: str, model: str) -> str:
    """`claude -p` with tools disabled so it only prints the markdown (result on stdout)."""
    _require("claude")
    cmd = [
        "claude", "-p", instruction + _OUTPUT_ONLY, "--model", model,
        "--output-format", "text", "--disallowed-tools", _CLAUDE_NO_TOOLS,
    ]
    return _run(cmd, content, want_stdout=True).strip()


def _call_opencode(instruction: str, content: str, model: str) -> str:
    """`opencode run -m <provider/model>`; content attached via -f, result on stdout."""
    _require("opencode")
    fd, in_file = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        cmd = [
            "opencode", "run", "-m", model, "--format", "default",
            "-f", in_file, instruction + _OUTPUT_ONLY,
        ]
        return _run(cmd, "", want_stdout=True).strip()
    finally:
        try:
            os.unlink(in_file)
        except OSError:
            pass


def _call_codex(instruction: str, content: str, model: str) -> str:
    """`codex exec` with a read-only sandbox so it only generates text."""
    _require("codex")
    fd, out_file = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    try:
        cmd = [
            "codex", "exec", "--skip-git-repo-check", "--ephemeral",
            "--sandbox", "read-only", "--color", "never",
            "-m", model, "-o", out_file,
            instruction + _OUTPUT_ONLY,
        ]
        _run(cmd, content, want_stdout=False)
        return Path(out_file).read_text(encoding="utf-8").strip()
    finally:
        try:
            os.unlink(out_file)
        except OSError:
            pass


PROVIDERS = {
    "claude-cli": _call_claude,
    "opencode": _call_opencode,
    "codex-cli": _call_codex,
}

# CLI binary each provider shells out to (for a friendly pre-flight check).
BINARIES = {
    "claude-cli": "claude",
    "opencode": "opencode",
    "codex-cli": "codex",
}


def call_llm(instruction: str, content: str, model: str, provider: str) -> str:
    fn = PROVIDERS.get(provider)
    if fn is None:
        raise RuntimeError(
            f"unknown provider {provider!r}; options: {', '.join(PROVIDERS)}."
        )
    return fn(instruction, content, model)
