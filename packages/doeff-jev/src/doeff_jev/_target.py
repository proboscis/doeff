"""Resolution of the destination (URL, model, wire format, API key).

This is the single place where it is decided. When switching to seimf (a Jev-compatible judge running on zeus's GPU), changing the single environment variable
`JEV_BASE_URL` is enough, and every caller that stacks this handler follows.

Resolution order (the earlier wins):
1. Environment variables `JEV_BASE_URL` / `JEV_MODEL` / `JEV_WIRE` / `JEV_API_KEY` / `JEV_API_KEY_FILE`
2. Config file `~/.config/jev/client.json` (fields base_url / model / wire / api_key_file)
3. Default = direct TypeSafe (`/v1/systemone`, model `jev-latest`). The key is `TYPESAFE_API_KEY` ->
   `~/.config/jev/api_key`. When gateway (the evaluation-model of Vercel AI Gateway) is named explicitly,
   model `typesafe-ai/jev`, and the key is `AI_GATEWAY_API_KEY` -> `~/jev_key`.

`resolve_target` is pure (reading the environment and files is received through arguments). The entry that resolves from the process environment is
`target_from_process_environment` (I/O called only when `judge_stack`, which is not passed a destination, builds its stack). Both are internal parts of the handler and are
not exposed from the entry point (ADR-DOE-API-001 R1: callers either leave the destination to `judge_stack` or pass the type `JevTarget`).
"""

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from doeff import run, with_handlers
from doeff_core_effects import subprocess_handler
from doeff_core_effects import environment_mapping

Wire = Literal["direct", "gateway"]

DIRECT_URL = "https://api.typesafe.ai/v1/systemone"
DIRECT_MODEL = "jev-latest"
GATEWAY_URL = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"
GATEWAY_MODEL = "typesafe-ai/jev"
GATEWAY_HOST = "ai-gateway.vercel.sh"
TYPESAFE_HOST = "api.typesafe.ai"

CONFIG_FILE = "~/.config/jev/client.json"
DIRECT_KEY_FILE = "~/.config/jev/api_key"
GATEWAY_KEY_FILE = "~/jev_key"

ReadText = Callable[[str], str | None]


@dataclass(frozen=True)
class JevTarget:
    base_url: str
    model: str
    wire: Wire
    api_key: str | None
    source: str  # env / file / default (for the journal)

    @property
    def host(self) -> str:
        rest = self.base_url.split("//", 1)
        return rest[1].split("/", 1)[0] if len(rest) == 2 else self.base_url


def key_required(target: JevTarget) -> bool:
    """TypeSafe and Vercel require a key. Others (seimf) are sent even without one."""
    return target.host in (TYPESAFE_HOST, GATEWAY_HOST)


def _wire_of(url: str, declared: str | None) -> Wire:
    if declared in ("direct", "gateway"):
        return declared  # type: ignore[return-value]
    return "gateway" if GATEWAY_HOST in url else "direct"


def _config_from_text(text: str | None) -> dict[str, object]:
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def resolve_target(env: Mapping[str, str], read_text: ReadText, *,
                   wire: str | None = None) -> JevTarget:
    """Resolve the destination (pure). `read_text(path)` is an injected reader that returns the file contents (None if absent)."""
    file_cfg = _config_from_text(read_text(CONFIG_FILE))
    declared_wire = env.get("JEV_WIRE") or _str(file_cfg.get("wire")) or wire
    url = env.get("JEV_BASE_URL") or _str(file_cfg.get("base_url"))
    source = "env" if env.get("JEV_BASE_URL") else ("file" if file_cfg.get("base_url") else "default")
    if not url:
        url = GATEWAY_URL if declared_wire == "gateway" else DIRECT_URL
    resolved_wire = _wire_of(url, declared_wire)
    model = env.get("JEV_MODEL") or _str(file_cfg.get("model")) or (
        GATEWAY_MODEL if resolved_wire == "gateway" else DIRECT_MODEL)

    key: str | None = env.get("JEV_API_KEY") or None
    if not key:
        key_file = env.get("JEV_API_KEY_FILE") or _str(file_cfg.get("api_key_file"))
        if key_file:
            key = read_text(key_file)
    if not key:
        if resolved_wire == "gateway":
            key = env.get("AI_GATEWAY_API_KEY") or read_text(GATEWAY_KEY_FILE)
        else:
            key = env.get("TYPESAFE_API_KEY") or read_text(DIRECT_KEY_FILE)
    return JevTarget(base_url=url, model=model, wire=resolved_wire,
                     api_key=(key.strip() if key else None) or None, source=source)


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def read_text_from_disk(path: str) -> str | None:
    """Expand `~` and read the file. None if absent (the reader the assembly point passes to resolve_target)."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        return None
    return value or None


def target_from_process_environment(*, wire: str | None = None) -> JevTarget:
    """Called only by judge_stack when no destination is passed (the form called by the assembly-point CLI / hook entry): resolve the destination from this process's environment and home files.

    The environment is not read directly from os.environ; it is received through the mapping of ReadEnvironment answered by the foundation handler (subprocess_handler)
    (doeff-linter DOEFF004)。"""
    environ: dict[str, str] = run(with_handlers([subprocess_handler], environment_mapping()))
    return resolve_target(environ, read_text_from_disk, wire=wire)
