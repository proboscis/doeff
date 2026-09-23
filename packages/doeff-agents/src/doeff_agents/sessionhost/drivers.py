"""agent の種類ごとに起動する実行ファイルの名の定義元と、その在否の探索(ADR-DOE-AGENTS-012 R61)。

card acp:kanban-issue:ki-f250d67a7157: pool の pod の host の PATH に codex が無いのに、node の行は
固定の能力の表を申告し続け、codex の手番が ``LaunchFailed — [Errno 2] No such file or directory:
'codex'`` で 35 通落ちた。起動の argv[0] は 3 か所の直書き(impls の argv builder)で、探索は
``subprocess.Popen(argv, env=子の env)`` が子の env の PATH で暗黙に行っていた — 名の定義も探索の
手順も、申告の側から見えなかった。

ここは **host(impls の argv・host.hy の drivers.list)と agentd(acp/effects.py・acp/handlers.py)の
両方が import する中立の置き場**。impls/*.hy は acp/effects.py を import しない作法なので、名の定義を
acp の側に置くと host が写しを持つことになる(第 2 の定義点)。

- :data:`DRIVER_EXECUTABLE` — 種類 → 実行ファイルの名(argv[0])の唯一の定義。
- :func:`driver_path_in` — ある env で実行ファイルが見つかる path。Popen の探索と同じ手順
  (``os.get_exec_path(env)`` の PATH・PATH が無ければ ``os.defpath``)なので、ここで見つかる ⇔
  同じ env の Popen が起動できる(実行権の無い file・dir は見つからない側)。
- :func:`driver_listing` — host の読み口 ``drivers.list`` の答えの項の列。

結果は保持しない(cache も module の変数も持たない): 稼働中に消えた実行ファイル・後から置かれた実行
ファイルを、次の問い合わせがそのまま観測する。``--version`` のような起動の試しはしない。
"""

# pyright: strict
import os
import shutil
from collections.abc import Mapping
from typing import TypedDict

#: agent の種類(charter.agent_type の語)→ 起動する実行ファイルの名(argv[0])。種類の集合は
#: acp/effects.py の能力の表(AGENT_CAPABILITIES)と同じ(検が一致を固定する)。
DRIVER_EXECUTABLE: dict[str, str] = {"claude": "claude", "codex": "codex"}


class DriverListingItem(TypedDict):
    """``drivers.list`` の答えの 1 項(wire の綴り)。path = None は「この env では見つからない」。"""

    agent_type: str
    executable: str
    path: str | None


def driver_path_in(word: str, env: Mapping[str, str]) -> str | None:
    """env で ``word`` を起動した時に使われる実行ファイルの path(見つからなければ None)。

    ``subprocess.Popen(argv, env=env)`` と同じ探索: 探す dir は ``os.get_exec_path(env)``
    (env の PATH・無ければ ``os.defpath``)、``/`` を含む word はその path そのものを検める。"""
    search_path = os.pathsep.join(os.get_exec_path(dict(env)))
    return shutil.which(word, path=search_path)


def driver_listing(env: Mapping[str, str]) -> list[DriverListingItem]:
    """:data:`DRIVER_EXECUTABLE` の種類ごとの在否(種類の名の順)— 問われるたびに探す。"""
    return [
        DriverListingItem(
            agent_type=agent_type,
            executable=executable,
            path=driver_path_in(executable, env),
        )
        for agent_type, executable in sorted(DRIVER_EXECUTABLE.items())
    ]
