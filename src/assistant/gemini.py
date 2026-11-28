"""Gemini as a helper brain: one function to ask it a question, and a CLI for using it as a build buddy.

Gemini only ever returns text. It has no tools and cannot touch files or run commands; whoever calls it
(Claude, the watchdog, later the router) decides what to do with the answer.

    python -m assistant.gemini "review this for bugs" --files src/assistant/usage.py tests/test_usage.py
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
DEFAULT_MODEL = "gemini-3.5-flash-lite"  # gemini-3.8-flash timed out at 90 s on a two-file review


class GeminiError(RuntimeError):
    pass


def ask(prompt: str, key_file: str | Path, model: str = DEFAULT_MODEL, timeout: float = 90) -> str:
    try:
        key = Path(key_file).read_text().strip()
        req = urllib.request.Request(
            URL.format(model), data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode(),
            headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as exc:  # noqa: BLE001 - one error type for callers; never echoes the key
        raise GeminiError(f"{type(exc).__name__}: {exc}") from exc


def main() -> int:
    from .config import load_config

    p = argparse.ArgumentParser(description="Ask Gemini, optionally with files attached.")
    p.add_argument("prompt")
    p.add_argument("--files", nargs="*", default=[], help="files to include, pasted into the prompt")
    p.add_argument("--model", default=DEFAULT_MODEL)
    args = p.parse_args()
    key_file = load_config().brain.gemini_key_file
    if not key_file:
        raise SystemExit("set [brain].gemini_key_file in config.toml")
    parts = [args.prompt] + [f"\n--- {f} ---\n{Path(f).read_text(encoding='utf-8')}" for f in args.files]
    try:
        print(ask("\n".join(parts), key_file, args.model))
    except GeminiError as exc:
        raise SystemExit(f"Gemini failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
