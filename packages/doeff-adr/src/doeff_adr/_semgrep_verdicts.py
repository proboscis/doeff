"""installed の defsemgrep の答え(例の組 1 つに当たった規則の id)を引く鍵と、答えを process の外に残す置き場。

同じ設定の規則の例は 1 回の semgrep にまとめて確かめる(registry._installed_verdict)が、残る費用は semgrep の
1 回の起動そのもの(約 8 秒)で、agora の日次は pytest の session と設定ごとに、設定も例も変わらない日にも毎回払っていた。呼び手が
環境変数 SEMGREP_VERDICTS_ENV で置き場の dir を名指せば、答えを ``<dir>/<鍵>.json`` に残し、次の process はそこから読む。名指しが
無ければ残さない(process の中だけで覚える)— doeff-adr は日次の名も置き場の根も知らない。

鍵は semgrep の版・設定の中身・例の中身(と置き場の形の版)で、semgrep と設定の path は入れない。日次は回ごとに HOME を空の dir に替え、
semgrep(``uv tool install``)も設定(clone)も毎回違う path に在るので、path を入れると毎回外れる。中身で引くので、規則か例を 1 つ
変えた回は別の鍵になり semgrep が回り直す — 古い答えは読まれない。
"""

import hashlib
import json
import os
import tempfile
from functools import cache
from importlib.metadata import Distribution
from pathlib import Path

from doeff_adr._env_places import SEMGREP_VERDICTS_ENV, environment_value

# 鍵の材料か置き場の file の形を変えたら上げる(古い版の答えは別の鍵になり読まれない)。
VERDICT_FORMAT = 1


class MalformedVerdictError(ValueError):
    """置き場の答えの file の形が違う(規則の id の文字列の list でない)。"""


def verdict_store() -> Path | None:
    """答えを残す置き場の dir(環境変数 SEMGREP_VERDICTS_ENV の値)— 名指しが無い・空なら None(process の外に残さない)。"""
    named = (environment_value(SEMGREP_VERDICTS_ENV) or "").strip()
    return Path(named).expanduser() if named else None


@cache
def semgrep_identity(semgrep: str) -> str:
    """semgrep の素性 — 版が同じなら path が違っても同じ答えを出すので、鍵には path でなく版を入れる。

    版は、実行の file(symlink を解いた先)が在る venv の dist-info から読む(``uv tool install`` も project の venv も
    ``<venv>/bin/semgrep`` と ``<venv>/lib/python*/site-packages/semgrep-*.dist-info`` の形)。``semgrep --version`` は網へ
    新しい版を問いに行き 37 秒かかった(2026-10-11 手元)ので使わない。venv の形でない入れ方(dist-info が 1 つに決まらない)では、
    解いた path を素性にする — 版ごとに path の変わる入れ方(Homebrew の Cellar ほか)なら版の違いを見分け、同じ path で入れ替える
    入れ方でも、その path の答えを別の path の答えと混ぜない。
    """
    executable = Path(semgrep).resolve()
    dist_infos = sorted(
        (executable.parent.parent / "lib").glob("python*/site-packages/semgrep-*.dist-info")
    )
    if len(dist_infos) == 1:
        return f"semgrep=={Distribution.at(dist_infos[0]).version}"
    return f"semgrep@{executable}"


def fixture_set_key(identity: str, config_digest: str, fixtures: tuple[dict[str, str], ...]) -> str:
    """答えを引く鍵 — semgrep の版・設定の中身・例の中身のどれかが変われば別の鍵になり、古い答えを使い回さないため。"""
    material = json.dumps([VERDICT_FORMAT, identity, config_digest, list(fixtures)], sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def read_verdict(store: Path, key: str) -> frozenset[str] | None:
    """前の process が置き場に残した鍵の答え — 在れば semgrep を起こさずに済ませるため(無ければ None)。"""
    path = store / f"{key}.json"
    return _decode(path) if path.is_file() else None


def write_verdicts(store: Path, verdicts: dict[str, frozenset[str]]) -> None:
    """答えを鍵ごとの file に書く — 隣の session が同じ鍵を同時に書いても読み手が書きかけを読まないよう、同じ dir の一時の file に
    書いてから置き換える。"""
    store.mkdir(parents=True, exist_ok=True)
    for key, rule_ids in verdicts.items():
        with tempfile.NamedTemporaryFile(
            "w", dir=store, prefix=f".{key}.", suffix=".tmp", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(sorted(rule_ids), handle)
        os.replace(handle.name, store / f"{key}.json")


def _decode(path: Path) -> frozenset[str]:
    """答えの file を規則の id の組にする — 形の違う file を黙って「何も当たらない」と読まず、path を名指して止めるため。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(rule_id, str) for rule_id in value):
        raise MalformedVerdictError(
            f"semgrep の答えの file の形が違う(規則の id の文字列の list でない): {path}"
        )
    return frozenset(value)
