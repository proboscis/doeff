"""doeff-cluster の検の実行環境。

- 検は Hy の ``test_*.hy``(deftest)。集め手は root の ini の ``doeff_hy_test_files``(doeff-adr の plugin の 1 点)— この conftest は集めない。
  ``doeff_adr_hy_files``(executable ADR の pattern)には足さない — 足すと根の母集団の配線の検め(executable ADR の在処)が
  この package の検を「根で集めていない ADR」と数える(package の母集団は ``make test-packages`` が別に走らせる)。
- module 名は ``tests.<名>``(``tests/__init__.py`` の親 = この package の dir からの名 — doeff-adr の
  ``_import_base_for_path``)。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

# doeff-effect-analyzer の Python の front end(foundation_check が使う開発の道具 — doeff-cluster の実行時の依存ではない)。
# maturin の混ぜた project で、front end は Rust の拡張なしで読めるので、入っていなければ python/ を import の路に足す
# (packages/doeff-effect-analyzer/tests/python/conftest.py と同じ扱い)。
if importlib.util.find_spec("doeff_effect_analyzer") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "doeff-effect-analyzer" / "python"))

import pytest
from doeff_core_effects.scheduler import scheduled

from doeff import Program, run

# 契約テストでない deftest の名: handler は各 deftest が本体の中で被せる。
PLAIN = "plain"


@pytest.fixture
def doeff_interpreter_name() -> str:
    return PLAIN


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name: str) -> Callable[[Program], object]:
    """deftest の Program を、:interpreters の名の handler の組の下で scheduler つきで 1 回回す。

    契約テストは deftest の ``:interpreters`` で handler を差し替える。名 → 組み立ての表は
    coordinator_contract_handlers.hy・host_reads_contract_handlers.hy・runtime_facts_contract_handlers.hy・
    file_contract_handlers.hy・tree_contract_handlers.hy が持ち、
    ここはその表を引くだけ(名の無い deftest は今までどおり素のまま回す)。
    """
    if doeff_interpreter_name == PLAIN:
        compose: Callable[[Program], Program] = lambda program: program
    else:
        import hy  # noqa: F401  - Hy の module を import できるようにする

        from tests.coordinator_contract_handlers import INTERPRETERS as COORDINATOR_INTERPRETERS
        from tests.file_contract_handlers import INTERPRETERS as FILE_INTERPRETERS
        from tests.host_reads_contract_handlers import INTERPRETERS as HOST_READS_INTERPRETERS
        from tests.runtime_facts_contract_handlers import INTERPRETERS as RUNTIME_FACTS_INTERPRETERS
        from tests.tree_contract_handlers import INTERPRETERS as TREE_INTERPRETERS

        compositions: dict[str, Callable[[Program], Program]] = {
            **COORDINATOR_INTERPRETERS,
            **FILE_INTERPRETERS,
            **HOST_READS_INTERPRETERS,
            **RUNTIME_FACTS_INTERPRETERS,
            **TREE_INTERPRETERS,
        }
        compose = compositions[doeff_interpreter_name]

    def interpret(program: Program) -> object:
        return run(scheduled(compose(program)))

    return interpret


@pytest.fixture(scope="session")
def served_coordinator(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """本物の coordinator の process 1 つを検の間で共有する(test_detached.hy の served の組・起動に数秒かかる)。

    scope は session: Hy の file の検は Module の node の下に無いので、module の scope を取れない。
    """
    import hy  # noqa: F401  - Hy の module を import できるようにする

    from tests.served_fixtures import start_coordinator

    url, process = start_coordinator(tmp_path_factory.mktemp("served"))
    yield url
    process.terminate()
    process.wait(timeout=30)


@pytest.fixture(scope="session")
def job_child_code_store(tmp_path_factory: pytest.TempPathFactory) -> None:
    """実行環境の job の子(丁寧な模擬の rig が本物の子 process で起こす物)が読む Hy の code を、既定の置き場に入れておく。

    本番の job の子は doeff_cluster を root の venv から読み、そこの code は準備が焼くか、最初の子が書く(子は bytecode を書ける)。
    rig の子は doeff_cluster を root の外の作業木から読み、作業木にも root にも書かない(PYTHONDONTWRITEBYTECODE=1)ので、
    起きるたびに Hy を source から compile し直していた(1 本 39 file・#2833)。ここで書ける子 1 本が job の子の入口を import し、
    Hy の code の置き場に足りない code を足す — 本番で最初の子が書く分を、先に 1 本で受け持つ。job の子は許可表で絞った環境で起き、
    HOME から同じ既定の置き場を引く。子は置き場を source の隣に使える .pyc が無い時だけ見るので、root の中に準備した bytecode を
    読む道は変わらない。2 回目の実行からは足す物がほぼ無い。

    書き手の子は環境を継ぎ(写さない — 環境を読まない)、書く設定だけを命令行で変える: `-X pycache_prefix` で .pyc を一時の dir へ
    (作業木には書かない)、`sys.dont_write_bytecode = False` で、根の conftest の固定が立てた PYTHONDONTWRITEBYTECODE を
    この子の中でだけ打ち消す(Hy の code の置き場への書きはこの値を見る)。

    scope は session: Hy の file の検は Module の node の下に無いので、module の scope を取れない(served_coordinator と同じ)。
    """
    import hy  # noqa: F401  - Hy の module を import できるようにする

    from doeff_cluster.worker.protocol.declared import JOB_ENTRY

    prefix = tmp_path_factory.mktemp("job-child-code")
    subprocess.run(
        [
            sys.executable,
            "-X",
            f"pycache_prefix={prefix}",
            "-c",
            f"import sys; sys.dont_write_bytecode = False; import hy, {JOB_ENTRY}",
        ],
        check=True,
        timeout=300,
    )
