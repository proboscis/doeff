"""宛先(URL・model・通信の形・API キー)の解決。

決め方はここ 1 点。seimf(zeus の GPU で動く Jev 互換の判定器)へ乗り換える時は環境変数
`JEV_BASE_URL` を 1 つ変えるだけで、この handler を積む全部の呼び手が付いてくる。

解き方(上が勝つ):
1. 環境変数 `JEV_BASE_URL` / `JEV_MODEL` / `JEV_WIRE` / `JEV_API_KEY` / `JEV_API_KEY_FILE`
2. 設定 file `~/.config/jev/client.json`(欄 base_url / model / wire / api_key_file)
3. 既定 = TypeSafe 直(`/v1/systemone`・model `jev-latest`)。キーは `TYPESAFE_API_KEY` →
   `~/.config/jev/api_key`。gateway(Vercel AI Gateway の evaluation-model)を名指した時は
   model `typesafe-ai/jev`・キーは `AI_GATEWAY_API_KEY` → `~/jev_key`。

`resolve_target` は純粋(環境と file の読みは引数で受け取る)。process の環境から解く入口は
`target_from_process_environment`(組み立て点だけが呼ぶ I/O)。
"""

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

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
    source: str  # env / file / default(記録用)

    @property
    def host(self) -> str:
        rest = self.base_url.split("//", 1)
        return rest[1].split("/", 1)[0] if len(rest) == 2 else self.base_url


def key_required(target: JevTarget) -> bool:
    """TypeSafe と Vercel はキー必須。それ以外(seimf)は無くても送る。"""
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
    """宛先を解く(純粋)。`read_text(path)` は file の中身(無ければ None)を返す注入された読み手。"""
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
    """`~` を展開して file を読む。無ければ None(組み立て点が resolve_target に渡す読み手)。"""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        return None
    return value or None


def target_from_process_environment(*, wire: str | None = None) -> JevTarget:
    """組み立て点(CLI・hook の入口)だけが呼ぶ: この process の環境と home の file から宛先を解く。"""
    return resolve_target(os.environ, read_text_from_disk, wire=wire)
