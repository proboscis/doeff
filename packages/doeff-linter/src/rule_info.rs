//! 規則ごとの題・文・直し方(人が読む出力と agent の hook の文言)。Python の規則(DOEFF001〜031)の表と、
//! 層の規則(DOEFF101〜108 — 文言の正本は `project::rule::ProjectRule`)をここで 1 つの口にする。

use crate::project::rule::ProjectRule;

/// Rule info with description and fix suggestion
pub struct RuleInfo {
    pub name: &'static str,
    pub description: &'static str,
    pub fix: &'static str,
    /// 短い日本語の名(違反の形。エディタの一覧の見出しに使う)。
    pub label: &'static str,
}

/// 規則の ID から題・文・直し方を引く(知らない ID は "Unknown Rule")。
pub fn get_rule_info(rule_id: &str) -> RuleInfo {
    if let Some(rule) = ProjectRule::parse(rule_id) {
        return RuleInfo { name: rule.title(), description: rule.statement(), fix: rule.hint(), label: rule.label() };
    }
    match rule_id {
        "DOEFF001" => RuleInfo {
            name: "Builtin Shadowing",
            description: "A function parameter or variable shadows a Python builtin (e.g., `list`, `dict`, `id`).",
            fix: "Rename the variable to avoid shadowing: `items` instead of `list`, `mapping` instead of `dict`.",
            label: "変数名が builtin を隠す",
        },
        "DOEFF002" => RuleInfo {
            name: "Mutable Attribute Naming",
            description: "A mutable class attribute (list, dict, set) doesn't follow the `_mut_` naming convention.",
            fix: "Prefix mutable attributes with `_mut_`: `self._mut_items = []` instead of `self.items = []`.",
            label: "可変な属性名に _mut_ が無い",
        },
        "DOEFF003" => RuleInfo {
            name: "Max Mutable Attributes",
            description: "A class has too many mutable attributes, indicating potential design issues.",
            fix: "Refactor the class to reduce mutable state, or split into smaller classes.",
            label: "可変な属性が多すぎる",
        },
        "DOEFF004" => RuleInfo {
            name: "No os.environ Access",
            description: "Direct access to `os.environ` breaks dependency injection principles.",
            fix: "Inject configuration as function parameters or use a config dataclass instead.",
            label: "os.environ を直に読む",
        },
        "DOEFF005" => RuleInfo {
            name: "No Setter Methods",
            description: "Setter methods (set_*, @property.setter) violate immutability principles.",
            fix: "Use immutable patterns: return new instances with modified values instead of mutating.",
            label: "setter method がある",
        },
        "DOEFF006" => RuleInfo {
            name: "No Tuple Returns",
            description: "Returning raw tuples reduces code readability and type safety.",
            fix: "Use a dataclass or NamedTuple: `@dataclass class Result: value: int; error: str`.",
            label: "生の tuple を返している",
        },
        "DOEFF007" => RuleInfo {
            name: "No Mutable Argument Mutations",
            description: "Mutating function arguments (list.append, dict.update) causes side effects.",
            fix: "Create a copy first: `items = items.copy(); items.append(x)` or return new collections.",
            label: "引数の可変な値を書き換える",
        },
        "DOEFF008" => RuleInfo {
            name: "No Dataclass Attribute Mutation",
            description: "Mutating dataclass attributes after creation breaks immutability.",
            fix: "Use `frozen=True` dataclasses and `dataclasses.replace()` to create modified copies.",
            label: "dataclass の属性を書き換える",
        },
        "DOEFF009" => RuleInfo {
            name: "Missing Return Type Annotation",
            description: "Functions without return type annotations reduce code clarity and type safety.",
            fix: "Add return type: `def foo() -> int:` or `def bar() -> None:` for no return value.",
            label: "返り値の型注釈が無い",
        },
        "DOEFF010" => RuleInfo {
            name: "Test File Placement",
            description: "Test files should be in a `tests/` directory, not mixed with source code.",
            fix: "Move test files to a dedicated `tests/` directory at the project root.",
            label: "テストが tests/ の外にある",
        },
        "DOEFF011" => RuleInfo {
            name: "No Flag/Mode Arguments",
            description: "Functions and dataclasses use flag/mode arguments instead of callbacks or protocol objects.",
            fix: "Accept a callback or protocol object. Example: instead of `def process(data, use_cache: bool)`, use `def process(data, cache: CacheProtocol)` or `def process(data, get_cached: Callable[[Data], Result])`.",
            label: "flag・mode の引数を使っている",
        },
        "DOEFF012" => RuleInfo {
            name: "No Append Loop Pattern",
            description: "Empty list initialization followed by for-loop append obscures the data transformation pipeline.",
            fix: "Use list comprehension: `data = [process(x) for x in items]`. For complex logic, extract to a named function. If mutation is required (queue/stack ops, BFS/DFS, dynamic algorithms), add `# noqa: DOEFF012` to the for-loop line.",
            label: "空リストへ append するループ",
        },
        "DOEFF013" => RuleInfo {
            name: "Prefer Maybe Monad",
            description: "Optional[X] or X | None type annotations should use doeff's Maybe monad for explicit null handling.",
            fix: "Use `Maybe[X]` instead of `Optional[X]`. Import with `from doeff import Maybe, Some, NOTHING`. Use `Maybe.from_optional(value)` to convert existing Optional values.",
            label: "Maybe でなく Optional を使う",
        },
        "DOEFF014" => RuleInfo {
            name: "No Try-Except Blocks",
            description: "Using try-except blocks hides error handling flow. Use doeff's error handling effects instead.",
            fix: "Use `Safe(program)` to get a Result, `program.recover(fallback)` for fallbacks, `program.first_success(alt1, alt2)` for alternatives, or `Catch(program, handler)` to transform errors.",
            label: "try-except を使っている",
        },
        "DOEFF015" => RuleInfo {
            name: "No Zero-Argument Program Entrypoints",
            description: "Program entrypoints should not be created by zero-argument factory functions.",
            fix: "Pass explicit arguments to make configuration visible: `process(data=input, threshold=0.5)`.",
            label: "引数無しの Program の入口",
        },
        "DOEFF016" => RuleInfo {
            name: "No Relative Imports",
            description: "Relative imports make code harder to understand and refactor.",
            fix: "Use absolute imports: `from mypackage.module import func` instead of `from .module import func`.",
            label: "相対 import を使っている",
        },
        "DOEFF017" => RuleInfo {
            name: "No Program Type Parameters",
            description: "@do functions should accept type T, not Program[T]. Program[T] prevents auto-unwrapping.",
            fix: "Change parameter type from `Program[T]` to `T`. If intentional (Program transforms), suppress with `# noqa: DOEFF017`.",
            label: "引数の型が Program[T]",
        },
        "DOEFF018" => RuleInfo {
            name: "No Ask in Try Block",
            description: "Using `yield Ask(...)` inside try blocks can cause unexpected behavior.",
            fix: "Move the Ask outside the try block, or use doeff's error handling effects like `Safe()` or `recover()`.",
            label: "try の中で Ask を呼ぶ",
        },
        "DOEFF019" => RuleInfo {
            name: "No Ask with Fallback",
            description: "Using fallback values with Ask defeats the purpose of dependency injection.",
            fix: "Remove the fallback and ensure dependencies are properly provided at runtime.",
            label: "Ask に既定値を付けている",
        },
        "DOEFF020" => RuleInfo {
            name: "Program Naming Convention",
            description: "Program type variables should use 'p_' prefix for consistency.",
            fix: "Rename the variable: `data_program` → `p_data`.",
            label: "Program の変数名が p_ で始まらない",
        },
        "DOEFF021" => RuleInfo {
            name: "No __all__ Declaration",
            description: "This project defaults to exporting everything from modules.",
            fix: "Remove the `__all__` declaration. If needed for specific reasons, use `# noqa: DOEFF021`.",
            label: "__all__ を宣言している",
        },
        "DOEFF022" => RuleInfo {
            name: "Prefer @do Decorated Functions",
            description: "Functions should use @do decorator to enable structured effects and logging with `yield slog`.",
            fix: "Add @do decorator and use `yield slog(\"message\", key=value)` for structured logging. If intentional, suppress with `# noqa: DOEFF022`.",
            label: "@do を付けていない関数",
        },
        "DOEFF023" => RuleInfo {
            name: "Pipeline Marker Required",
            description: "@do functions used to create Program entrypoints must have `# doeff: pipeline` marker.",
            fix: "Add `# doeff: pipeline` marker after @do decorator, def line, or in docstring to acknowledge pipeline-oriented programming.",
            label: "pipeline の目印が無い",
        },
        "DOEFF024" => RuleInfo {
            name: "No Recover with Ask",
            description: "Using `recover()` with `ask()` defeats dependency injection.",
            fix: "Ensure dependencies are properly provided instead of using fallbacks.",
            label: "recover と ask を組み合わせる",
        },
        "DOEFF030" => RuleInfo {
            name: "Ask Result Type Annotation",
            description: "Results of `yield ask(...)` must be assigned to typed variables, and callable injections must use Protocol keys with @impl providers.",
            fix: "Use `value: Type = yield ask(\"key\")`. For callable injection, define a Protocol, ask with that Protocol, and provide an `@impl(Protocol)` function.",
            label: "ask の結果に型注釈が無い",
        },
        "DOEFF031" => RuleInfo {
            name: "No Redundant @do Wrapper Entrypoints",
            description: "Avoid creating Program entrypoints by calling @do wrappers that only forward args to a single yielded call and return it.",
            fix: "Replace `p_x: Program[...] = wrapper(...)` with `p_x: Program[...] = underlying(...)` using the same arguments. If the wrapper is intentional (naming/tracing), add `# noqa: DOEFF031`.",
            label: "@do の入口が素通しの wrapper",
        },
        "DOEFF032" => RuleInfo {
            name: "Rule Population Declaration Breach",
            description: "A module of a layer that exempts rules (architecture.hy `:exempt`) imports a business module listed in the layer's `:forbid-modules`, or the package's architecture.hy cannot be read to decide the file's rule population.",
            fix: "Move the business code to a layer that does not exempt rules, or fix the architecture.hy that owns this file. The exemption is for code outside Programs only (e.g. modules loaded by `.pth` at Python startup).",
            label: "規則を外した層に業務の code が在る",
        },
        "NOQA001" => RuleInfo {
            name: "Malformed noqa Comment",
            description: "The noqa comment format appears incorrect and may not suppress the intended rule.",
            fix: "Use ` - ` (space-dash-space) to separate the rule ID from explanation. Example: `# noqa: DOEFF001 - reason`",
            label: "noqa の書き方が誤っている",
        },
        _ => RuleInfo {
            name: "Unknown Rule",
            description: "Unknown rule violation.",
            fix: "Check the documentation for more information.",
            label: "知らない規則",
        },
    }
}
