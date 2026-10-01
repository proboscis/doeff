"""service_model.hy の公開面の型(型検査のための宣言 — 実行時は service_model.hy を読む)。

service_model.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.service_model` の名が全部 Unknown になる。
defsystem の展開は `doeff_cluster.service_model.system_of` / `job` / `CallShape` を呼ぶので、defsystem を
書いた file ごとに、書き手に直せない赤(Return type is unknown・Type of "system_of" is unknown ほか)が
出ていた。ここで型を宣言する(doeff_hy/wire.pyi と同じ形)。

- defrecord(CallShape・Job・System・Declaration)は凍った・キーワード引数だけの dataclass。
- deff(job・system-of・job-named ほか)は普通の関数。defk(foundation-needs-refusal・callables-in)は呼ぶと Program を返す。
- 欄の型は service_model.hy の注記と :pre / :post の検めに合わせる(注記が素の dict / tuple の所は、構成子と展開が実際に
  入れる要素の型で書く)。宣言の行(Declaration.rows)と identity は coordinator へ渡す JSON の形なので、組み立てる
  system-declaration・identity-of が書く鍵どおりの TypedDict で書く(readiness・update・runtimeEnv は在る時だけの鍵)。
- runtime_env_model.hy も Hy の module で型の宣言がまだ無い。EnvVar・RuntimeEnv をそこから import すると Unknown に
  引きずられるので、この module が読む欄だけを Protocol(_EnvVarView・_RuntimeEnvView)で書く — 実物の EnvVar・
  RuntimeEnv(凍った dataclass)は欄の形でこれを満たす。runtime_env_model に宣言を置いたら実物の型へ置き換える。
"""

from collections.abc import Callable
from dataclasses import Field, dataclass
from typing import Any, ClassVar, NotRequired, Protocol, TypedDict, runtime_checkable

from doeff import Program
from doeff_hy.json_value import JsonValue

UPDATE_FORMS: tuple[str, ...]

class _EnvVarView(Protocol):
    """子の環境変数 1 つ(runtime_env_model.EnvVar の読む欄)。"""

    @property
    def name(self) -> str: ...
    @property
    def value(self) -> str: ...

class _RuntimeEnvView(Protocol):
    """実行環境の宣言(runtime_env_model.RuntimeEnv の、宣言の組み立てが読む欄)。"""

    @property
    def env_vars(self) -> tuple[_EnvVarView, ...]: ...

class _Identity(TypedDict):
    """呼び出しの形の正規 JSON(identity-of の答え)— function = module:qualname・args / kwargs = 引数の正規の値。"""

    function: str
    args: list[JsonValue]
    kwargs: dict[str, JsonValue]

class _RunSpec(TypedDict):
    """宣言の行の run(service の Program の参照と表示)。"""

    kind: str
    program: str
    identity: _Identity
    versions: dict[str, str]
    describe: str

class _DeclarationRow(TypedDict):
    """coordinator へ渡す宣言の行 1 つ(system-declaration が job ごとに書く)。"""

    name: str
    revision: str
    needs: list[str]
    run: _RunSpec
    environ: dict[str, str]
    readiness: NotRequired[dict[str, float]]
    update: NotRequired[str]
    runtimeEnv: NotRequired[dict[str, JsonValue]]

@dataclass(frozen=True, kw_only=True)
class CallShape:
    """Program を作った呼び出しの形(defsystem の展開が残す)。function = 呼んだ関数・args = 位置の引数の値・kwargs = 名の引数の値。"""

    function: Callable[..., object]
    args: list[object]
    kwargs: dict[str, object]

@dataclass(frozen=True, kw_only=True)
class Job:
    """系の job 1 つ(常駐の service)。program = Program の値・needs = 要る能力の名・environ = 子の環境変数(名の順)。"""

    name: str
    program: object
    call: CallShape
    needs: frozenset[str]
    readiness: dict[str, float] | None
    update: str
    environ: tuple[_EnvVarView, ...]

@dataclass(frozen=True, kw_only=True)
class System:
    """系 = job の組(defsystem の関数が返す値)。name = 系の名・jobs = Job の tuple(名は重ならない)。"""

    name: str
    jobs: tuple[Job, ...]

@dataclass(frozen=True, kw_only=True)
class Declaration:
    """system-declaration の答え: rows = coordinator へ渡す宣言の行・programs = 行が参照する詰めた Program(sha → 文字列)。"""

    rows: list[_DeclarationRow]
    programs: dict[str, str]

@runtime_checkable
class RecordArgument(Protocol):
    """系の引数に渡せる record(defrecord・dataclass の値)の印 — 欄の宣言 __dataclass_fields__ を持つ値。"""

    __dataclass_fields__: ClassVar[dict[str, Field[Any]]]

def function_reference(function: Callable[..., object], where: str) -> str: ...
def canonical_record(value: RecordArgument, where: str) -> dict[str, JsonValue]: ...
def canonical_argument(value: object, where: str) -> JsonValue: ...
def identity_of(call: CallShape, where: str) -> _Identity: ...
def describe_identity(identity: _Identity) -> str: ...
def job(
    name: str,
    program: object,
    *,
    call: CallShape,
    needs: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None,
    readiness: dict[str, float] | None = None,
    update: str = "recreate",
    environ: dict[str, str] | None = None,
) -> Job: ...
def system_of(name: str, jobs: tuple[Job, ...]) -> System: ...
def job_named(system: System, name: str) -> Job | None: ...
def foundation_needs_refusal(
    system: System, foundation: Callable[..., object]
) -> Program[str | None, Any]: ...
def callables_in(values: list[object]) -> Program[list[Callable[..., object]], Any]: ...
def environ_overlay_refusal(system: System, environ: dict[str, dict[str, str]]) -> str | None: ...
def system_declaration(
    system: System,
    revision: str,
    runtime_env: _RuntimeEnvView | None = None,
    environ: dict[str, dict[str, str]] | None = None,
    *,
    versions: dict[str, str],
) -> Declaration: ...
def resolve_value(path: str) -> Callable[..., object] | System: ...
def resolve(path: str) -> Callable[..., object]: ...
