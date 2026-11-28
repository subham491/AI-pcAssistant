import asyncio
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

REFUSAL_PATTERNS = re.compile(
    r"\b(i cannot|i can't|i am unable to|i'm unable to|blocked by policy|cannot assist with|won't be able to)\b",
    re.IGNORECASE,
)

def is_refusal(text: str) -> bool:
    """Return True if Claude's reply looks like a refusal or block."""
    return bool(REFUSAL_PATTERNS.search(text or ""))

async def run_wintermute(
    trigger: str,
    user_prompt: str,
    use_case: str = "MinimalPrivEscLinux",
    max_rounds: int = 5,
    max_duration: int = 45,
) -> str:
    """Run wintermute asynchronously and return a short summary for voice output."""
    args = [
        "wintermute",
        use_case,
        "--conn=ssh",
        "--conn.host=127.0.0.1",
        "--conn.username=lowpriv",
        "--conn.password=trustno1",
        f"--limits.max_rounds={max_rounds}",
        f"--limits.max_duration={max_duration}",
        f"--log.tag={trigger}",
    ]
    log.info("Triggering wintermute (%s) for prompt: %s", trigger, user_prompt[:120])

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        return "Wintermute is not installed or not on PATH."

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip().splitlines()
        last_err = err[-1] if err else f"exit code {proc.returncode}"
        log.warning("wintermute failed: %s", last_err)
        return f"Wintermute exited with an error: {last_err}"

    lines = [ln.strip() for ln in stdout.decode(errors="replace").splitlines() if ln.strip()]
    tail = lines[-1] if lines else "Run completed."
    return f"Wintermute finished ({trigger}). {tail}"