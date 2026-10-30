"""worker の 1 拍の判断(plan・records-after・statuses)・drain の頼み(await-drained・drain-outcome・ready-of)・HTTP 要求の作り(http-request)の型の宣言の失敗ケース。

使い手の repo の模擬の世界(worker の入れ替えと drain の検)は、この 7 つの名を import して 1 拍ずつ回す。今までの .pyi は、
素の `tuple`・`dict` を要素の型なしで宣言していた:
- plan・statuses の答え = `_Program[tuple, object]`、records-after の答え = `_Program[dict, object]` — 答えの要素(Action・JobStatus・JobRecord)が
  使い手の側で Unknown になり、`(for [a actions] …)` の `a` と、その先の欄の読みが全部 Unknown の赤になる(使い手の 1 つの検で 500 件を超えた)。
- 引数の `desired: tuple`・`records: dict`・`query: dict`・`answer: dict` — import した名そのものが partially unknown になる赤が、使い手の
  import の行に出る(使い手では直せない — 元の型が不明なため)。
→ 7 つの名の引数と答えに要素の型を書き(.hy の契約)、.pyi は道具で作り直す。

ここでは、使い手の書き方をまねた検査用の module に strict の赤が出ない事(.pyi を外す・要素の型を消すと赤になる)と、7 つの名の宣言に型の引数の無い
総称が残っていない事を確かめる。宣言と .hy の一致は tests/test_generated_stubs.py の一致の検査が見る。
"""

import ast
import shutil
from pathlib import Path

import pytest
from doeff_hy._static_stub import strict_errors

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster"

# 使い手の模擬の世界の 1 拍をまねた形: plan の答えの Action を 1 つずつ読み、記憶へ数え、状態の行の欄を読み、drain の頼みの答えを読み、
# HTTP 要求を作る。答えの要素の型が見えなければ、for の変数・欄の読みが Unknown の赤になる。
WORKER_BEAT_USERS = """\
(require doeff-hy.macros [defk <-])
(import doeff_cluster._shared.intent.job_model [JobSpec])
(import doeff_cluster._shared.protocol.inbox [http-request])
(import doeff_cluster._worker.core.drain_client [await-drained drain-outcome ready-of])
(import doeff_cluster._worker.core.policy [plan records-after statuses])
(import doeff_cluster._worker.intent.worker_model [JobRecord StartJob WarmLaunch WorkerPolicy WorldView])

(defk probe-started [now desired world records warm]
  {:pre [(: now int) (: desired (get tuple #(JobSpec ...))) (: world WorldView) (: records (get dict #(str JobRecord)))
         (: warm (get tuple #(WarmLaunch ...)))]
   :post [(: % (get tuple #(str ...)))] :tags {:context "probe" :role "judgment"}}
  "plan の答えの Action のうち起こす物の job の名を読む(Action の要素の型が使い手に見えるか)。"
  (<- actions (plan now desired world records (WorkerPolicy) warm))
  (tuple (gfor action actions :if (isinstance action StartJob) action.spec.name)))

(defk probe-attempts [now records actions]
  {:pre [(: now int) (: records (get dict #(str JobRecord))) (: actions (get tuple #(StartJob ...)))]
   :post [(: % (get dict #(str int)))] :tags {:context "probe" :role "judgment"}}
  "records-after の答えの JobRecord の欄を読む(記憶の値の型が使い手に見えるか)。"
  (<- recorded (records-after now records actions))
  (dfor #(name record) (.items recorded) name record.attempts))

(defk probe-phases [now desired world records]
  {:pre [(: now int) (: desired (get tuple #(JobSpec ...))) (: world WorldView) (: records (get dict #(str JobRecord)))]
   :post [(: % (get tuple #(str ...)))] :tags {:context "probe" :role "judgment"}}
  "statuses の答えの JobStatus の欄を読む(状態の行の型が使い手に見えるか)。"
  (<- rows (statuses now desired world records (WorkerPolicy)))
  (tuple (gfor row rows f"{row.name}={row.detail}")))

(defk probe-request [method path]
  {:pre [(: method str) (: path str)] :post [(: % (get tuple #(str ...)))] :tags {:context "probe" :role "protocol"}}
  "http-request の答え Request の欄を読む(引数の query・body が型つきで受けられるか)。"
  (<- request (http-request method path {} {"name" "atlas" "capacity" 10}))
  request.parts)

(defk probe-drain [name]
  {:pre [(: name str)] :post [(: % (| str None))] :tags {:context "probe" :role "judgment"}}
  "await-drained の答えを drain-outcome・ready-of の引数へ渡す(頼みの答えの型が 3 つの名で揃っているか)。"
  (<- answer (await-drained name 90.0 2.0))
  (if (ready-of answer None) "ready" (drain-outcome answer 1.0 90.0)))
"""

# 型の引数を書かないと使い手の strict で中身が Unknown になる総称の名。
GENERIC_NAMES = frozenset({"list", "tuple", "dict", "set", "frozenset"})

# 使い手が import する 7 つの名(module → 関数の名)。
USED_FUNCTIONS = {
    "_worker/core/policy": ("plan", "records_after", "statuses"),
    "_shared/protocol/inbox": ("http_request",),
    "_worker/core/drain_client": ("await_drained", "drain_outcome", "ready_of"),
}

# 使い手が答えを読む関数の宣言(module → 関数の名 → 期待する答えの型)。
EXPECTED_RETURNS = {
    "_worker/core/policy": {
        "plan": "_Program[tuple[Action, ...], object]",
        "records_after": "_Program[dict[str, JobRecord], object]",
        "statuses": "_Program[tuple[JobStatus, ...], object]",
    },
    "_worker/core/drain_client": {
        "await_drained": "_Program[dict[str, object], object]",
    },
}


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")
def test_users_of_the_worker_beat_get_no_unknown_types(tmp_path: Path) -> None:
    errors = strict_errors(tmp_path, WORKER_BEAT_USERS)
    assert errors == (), errors


def _functions(module: str) -> dict[str, ast.FunctionDef]:
    declared = ast.parse((PACKAGE / f"{module}.pyi").read_text(encoding="utf-8"))
    return {node.name: node for node in declared.body if isinstance(node, ast.FunctionDef)}


def _bare_generics(annotation: ast.expr) -> list[str]:
    """注記の中で、型の引数を付けずに書かれた総称の名(`dict`・`tuple | X` の tuple など)。"""
    subscripted = [node.value for node in ast.walk(annotation) if isinstance(node, ast.Subscript)]
    return [
        node.id
        for node in ast.walk(annotation)
        if isinstance(node, ast.Name)
        and node.id in GENERIC_NAMES
        and not any(node is value for value in subscripted)
    ]


@pytest.mark.parametrize(
    ("module", "name"),
    [(module, name) for module, names in USED_FUNCTIONS.items() for name in names],
    ids=[f"{module}.{name}" for module, names in USED_FUNCTIONS.items() for name in names],
)
def test_the_used_functions_declare_no_generic_without_type_arguments(
    module: str, name: str
) -> None:
    function = _functions(module).get(name)
    assert function is not None, f"{module}.pyi に {name} が無い"
    arguments = [*function.args.args, *function.args.kwonlyargs]
    annotations = [a.annotation for a in arguments if a.annotation is not None]
    if function.returns is not None:
        annotations.append(function.returns)
    assert {ast.unparse(a): _bare_generics(a) for a in annotations if _bare_generics(a)} == {}


@pytest.mark.parametrize(
    ("module", "name"),
    [(module, name) for module, names in EXPECTED_RETURNS.items() for name in names],
    ids=[f"{module}.{name}" for module, names in EXPECTED_RETURNS.items() for name in names],
)
def test_the_used_functions_declare_typed_answers(module: str, name: str) -> None:
    function = _functions(module).get(name)
    assert function is not None, f"{module}.pyi に {name} が無い"
    assert function.returns is not None
    assert ast.unparse(function.returns) == ast.unparse(
        ast.parse(EXPECTED_RETURNS[module][name], mode="eval").body
    )
