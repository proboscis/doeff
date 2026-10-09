"""Service の一覧(effect ReadServices・coordinator の GET /resources/Service)を読む利用側の型の宣言の失敗ケース。

利用側の repo では、2 か所が doeff-cluster の Service の一覧を読む:
- 宣言の道具の handler は GET /resources/Service を自分で送り、本番の client と同じ関数 service-facts-of-view で ServiceFact の tuple にする。
- worker の名簿を扱う handler は effect ReadServices の答えを受ける。
今までの宣言では、利用側の strict の型検査に、利用側では直せない赤が出ていた(宣言の道具の handler の module で 6 件):
- service-facts-of-view の .pyi が `def service_facts_of_view(items: list) -> tuple` — 引数も答えも型の引数の無い素の list / tuple で、
  import した名が partially unknown になり、答えの要素の欄が Unknown になる。引数は JSON の行の list を手で読む形なので、利用側は
  wire の型で読み込んだ本文を dict へ戻して渡すしかなく、その行も Unknown の赤になる(利用側で JSON を手で絞ると doeff-linter の
  DOEFF120 に当たる)。
- 本番の handler の services-read と sim の read-services の答えが `tuple | ServicesUnreachable`(素の tuple)。
- effect ReadServices の基底が素の EffectBase で、`(<- services (ReadServices))` の答えの型が利用側に見えない(Any になる)。
→ 一覧の本文を defwire の型(ServiceListWire)で読み込み、service-facts-of-view はその値を受けて ServiceFact の tuple を返し、
  ReadServices は答えの型(ServicesAnswer)を持つ。.pyi は道具で作り直す。
ここでは、利用側の 2 つの形をまねた検査用の module に strict の赤が出ない事と、生成の .pyi の宣言が型の引数を持つ事を確かめる。
宣言と .hy の一致は tests/test_generated_stubs.py の一致の検査が見る。
"""

import ast
import shutil
from pathlib import Path

import pytest

from doeff_hy.static_stub import strict_errors

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster"

# 利用側の 2 つの形: 宣言の道具の handler が本文の文字列を doeff-cluster の wire の型で読み込み、本番と同じ関数で ServiceFact の
# tuple にする形と、名簿を扱う handler が effect の答えを「届かない」と「一覧」に分けて欄を読む形。
SERVICE_LIST_USERS = """\
(require doeff-hy.macros [defk <-])
(import doeff_hy.wire [Malformed parse-json])
(import doeff_cluster.shared.intent.detached_model [ReadServices ServiceFact ServiceListWire ServicesUnreachable])
(import doeff_cluster.shared.protocol.detached [service-facts-of-view])

(defk probe-listed [text]
  {:pre [(: text str)] :post [(: % (get tuple #(ServiceFact ...)))] :tags {:context "probe" :role "foundation"}}
  "GET /resources/Service の本文の文字列を wire の型で読み込み、本番の client と同じ関数で ServiceFact の tuple にする。"
  (<- wire (| ServiceListWire Malformed) (parse-json ServiceListWire text))
  (match wire
    (Malformed :fields fields) (raise (RuntimeError (.join "・" (gfor f fields f.field))))
    _ (do (<- facts (service-facts-of-view wire))
          facts)))

(defk probe-failures [text]
  {:pre [(: text str)] :post [(: % (get dict #(str int)))] :tags {:context "probe" :role "judgment"}}
  "ServiceFact の欄を読む(欄の型が分かる)。"
  (<- facts (probe-listed text))
  (dfor fact facts :if (is-not fact.failures None) fact.name fact.failures))

(defk probe-read []
  {:pre [] :post [(: % (get tuple #(str ...)))] :tags {:context "probe" :role "protocol"}}
  "ReadServices の答えを「届かない」と「一覧」に分けて読む。"
  (<- services (ReadServices))
  (match services
    (ServicesUnreachable :detail detail) #(detail)
    _ (tuple (gfor fact services fact.name))))
"""


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")
def test_users_of_the_service_list_get_no_unknown_types(tmp_path: Path) -> None:
    errors = strict_errors(tmp_path, SERVICE_LIST_USERS)
    assert errors == (), errors


# 型の引数を書かないと利用側の strict で中身が Unknown になる総称の名。
GENERIC_NAMES = frozenset({"list", "tuple", "dict", "set", "frozenset"})

# 利用側が使う関数の宣言(module → 関数の名 → 期待する答えの型)。
EXPECTED_RETURNS = {
    "shared/protocol/detached": {
        "service_facts_of_view": "_Program[tuple[ServiceFact, ...], object]",
        "services_read": "_Program[tuple[ServiceFact, ...] | ServicesUnreachable, object]",
    },
    "sim/local": {
        "read_services": "_Program[tuple[ServiceFact, ...] | ServicesUnreachable, object]",
    },
}


def _declared(module: str) -> ast.Module:
    return ast.parse((PACKAGE / f"{module}.pyi").read_text(encoding="utf-8"))


def _bare_generics(annotation: ast.expr) -> list[str]:
    """注記の中で、型の引数を付けずに書かれた総称の名(`list`・`tuple | X` の tuple など)。"""
    subscripted = [node.value for node in ast.walk(annotation) if isinstance(node, ast.Subscript)]
    return [
        node.id
        for node in ast.walk(annotation)
        if isinstance(node, ast.Name) and node.id in GENERIC_NAMES and not any(node is value for value in subscripted)
    ]


def _functions(module: str) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in _declared(module).body if isinstance(node, ast.FunctionDef)}


@pytest.mark.parametrize(
    ("module", "name"),
    [(module, name) for module, names in EXPECTED_RETURNS.items() for name in names],
    ids=[f"{module}.{name}" for module, names in EXPECTED_RETURNS.items() for name in names],
)
def test_the_service_list_readers_declare_typed_answers(module: str, name: str) -> None:
    function = _functions(module).get(name)
    assert function is not None, f"{module}.pyi に {name} が無い"
    assert function.returns is not None
    annotations = [function.returns] + [a.annotation for a in function.args.args if a.annotation is not None]
    assert {ast.unparse(a): _bare_generics(a) for a in annotations if _bare_generics(a)} == {}
    assert ast.unparse(function.returns) == ast.unparse(ast.parse(EXPECTED_RETURNS[module][name], mode="eval").body)


def test_read_services_declares_its_answer_type() -> None:
    # effect の答えの型は基底の型の引数(`_doeff_effect_base[答え]`)— 利用側の `(<- services (ReadServices))` が答えの型を得る所。
    declared = _declared("shared/intent/detached_model")
    classes = {node.name: node for node in declared.body if isinstance(node, ast.ClassDef)}
    aliases = {
        node.target.id: ast.unparse(node.value)
        for node in declared.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and ast.unparse(node.annotation) == "TypeAlias"
        and node.value is not None
    }
    bases = [ast.unparse(base) for base in classes["ReadServices"].bases]
    assert bases == ["_doeff_effect_base[ServicesAnswer]"]
    assert aliases.get("ServicesAnswer") == "tuple[ServiceFact, ...] | ServicesUnreachable"
