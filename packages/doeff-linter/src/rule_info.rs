//! Per-rule title, description, and fix (wording for human-readable output and the agent hook). One interface combining the table for the Python rules (DOEFF001-031) and
//! the layer rules (DOEFF101-108 -- the source of truth for their wording is `project::rule::ProjectRule`).

use crate::project::rule::ProjectRule;

/// Rule info with description and fix suggestion
pub struct RuleInfo {
    pub name: &'static str,
    pub description: &'static str,
    pub fix: &'static str,
    /// Short English name (the shape of the violation. Used as the heading in the editor's list).
    pub label: &'static str,
}

/// Looks up the title, description, and fix from a rule ID (an unknown ID gives "Unknown Rule").
pub fn get_rule_info(rule_id: &str) -> RuleInfo {
    if let Some(rule) = ProjectRule::parse(rule_id) {
        return RuleInfo { name: rule.title(), description: rule.statement(), fix: rule.hint(), label: rule.label() };
    }
    match rule_id {
        "DOEFF001" => RuleInfo {
            name: "Builtin Shadowing",
            description: "A function parameter or variable shadows a Python builtin (e.g., `list`, `dict`, `id`).",
            fix: "Rename the variable to avoid shadowing: `items` instead of `list`, `mapping` instead of `dict`.",
            label: "Variable name shadows a builtin",
        },
        "DOEFF002" => RuleInfo {
            name: "Mutable Attribute Naming",
            description: "An attribute assigned outside `__init__` / `__post_init__` / `__set_name__` (of any type, not only list, dict, set) doesn't follow the `mut_` / `_mut` naming convention. Only the class's own attributes count: what a base class outside the repo owns (Textual's `App.sub_title`) is not reported.",
            fix: "Prefix attributes assigned after initialization with `mut_` / `_mut`: `self._mut_missed = True` instead of `self.missed = True`. An attribute a framework base class owns cannot be renamed: list it in `data/framework_attributes.json` (it is already not reported when `__init__` and the methods called from it do not set it).",
            label: "Mutable attribute name lacks _mut_",
        },
        "DOEFF003" => RuleInfo {
            name: "Max Mutable Attributes",
            description: "A class has too many mutable attributes, indicating potential design issues.",
            fix: "Refactor the class to reduce mutable state, or split into smaller classes.",
            label: "Too many mutable attributes",
        },
        "DOEFF004" => RuleInfo {
            name: "No os.environ Access",
            description: "Direct access to `os.environ` breaks dependency injection principles.",
            fix: "Inject configuration as function parameters or use a config dataclass instead.",
            label: "Reads os.environ directly",
        },
        "DOEFF005" => RuleInfo {
            name: "No Setter Methods",
            description: "Setter methods (set_*, @property.setter) violate immutability principles.",
            fix: "Use immutable patterns: return new instances with modified values instead of mutating.",
            label: "Has a setter method",
        },
        "DOEFF006" => RuleInfo {
            name: "No Tuple Returns",
            description: "Returning raw tuples reduces code readability and type safety.",
            fix: "Use a dataclass or NamedTuple: `@dataclass class Result: value: int; error: str`.",
            label: "Returns a raw tuple",
        },
        "DOEFF007" => RuleInfo {
            name: "No Mutable Argument Mutations",
            description: "Mutating function arguments (list.append, dict.update) causes side effects.",
            fix: "Create a copy first: `items = items.copy(); items.append(x)` or return new collections.",
            label: "Mutates a mutable argument",
        },
        "DOEFF008" => RuleInfo {
            name: "No Dataclass Attribute Mutation",
            description: "Mutating dataclass attributes after creation breaks immutability.",
            fix: "Use `frozen=True` dataclasses and `dataclasses.replace()` to create modified copies.",
            label: "Mutates a dataclass attribute",
        },
        "DOEFF009" => RuleInfo {
            name: "Missing Return Type Annotation",
            description: "Functions without return type annotations reduce code clarity and type safety.",
            fix: "Add return type: `def foo() -> int:` or `def bar() -> None:` for no return value.",
            label: "Missing return type annotation",
        },
        "DOEFF010" => RuleInfo {
            name: "Test File Placement",
            description: "A test file sits inside an importable package (a directory with `__init__.py`) outside a `tests/` directory, so it ships with the package.",
            fix: "Move the test file under a `tests/` directory, or rename the module if it is not a test.",
            label: "Test is outside tests/ inside the package",
        },
        "DOEFF011" => RuleInfo {
            name: "No Flag/Mode Arguments",
            description: "Functions and dataclasses use flag/mode arguments instead of callbacks or protocol objects.",
            fix: "Accept a callback or protocol object. Example: instead of `def process(data, use_cache: bool)`, use `def process(data, cache: CacheProtocol)` or `def process(data, get_cached: Callable[[Data], Result])`.",
            label: "Uses a flag/mode argument",
        },
        "DOEFF012" => RuleInfo {
            name: "No Append Loop Pattern",
            description: "Empty list initialization followed by for-loop append obscures the data transformation pipeline.",
            fix: "Use list comprehension: `data = [process(x) for x in items]`. For complex logic, extract to a named function. If mutation is required (queue/stack ops, BFS/DFS, dynamic algorithms), add `# noqa: DOEFF012` to the for-loop line.",
            label: "Loop that appends to an empty list",
        },
        "DOEFF013" => RuleInfo {
            name: "Prefer Maybe Monad",
            description: "Optional[X] or X | None type annotations should use doeff's Maybe monad for explicit null handling.",
            fix: "Use `Maybe[X]` instead of `Optional[X]`. Import with `from doeff import Maybe, Some, NOTHING`. Use `Maybe.from_optional(value)` to convert existing Optional values.",
            label: "Uses Optional instead of Maybe",
        },
        "DOEFF014" => RuleInfo {
            name: "No Try-Except Blocks",
            description: "Using try-except blocks hides error handling flow. Use doeff's error handling effects instead.",
            fix: "Use `Safe(program)` to get a Result, `program.recover(fallback)` for fallbacks, `program.first_success(alt1, alt2)` for alternatives, or `Catch(program, handler)` to transform errors.",
            label: "Uses try-except",
        },
        "DOEFF015" => RuleInfo {
            name: "No Zero-Argument Program Entrypoints",
            description: "Program entrypoints should not be created by zero-argument factory functions.",
            fix: "Pass explicit arguments to make configuration visible: `process(data=input, threshold=0.5)`.",
            label: "Program entry point without arguments",
        },
        "DOEFF016" => RuleInfo {
            name: "No Relative Imports",
            description: "Relative imports make code harder to understand and refactor.",
            fix: "Use absolute imports: `from mypackage.module import func` instead of `from .module import func`.",
            label: "Uses a relative import",
        },
        "DOEFF017" => RuleInfo {
            name: "No Program Type Parameters",
            description: "@do functions should accept type T, not Program[T]. Program[T] prevents auto-unwrapping.",
            fix: "Change parameter type from `Program[T]` to `T`. If intentional (Program transforms), suppress with `# noqa: DOEFF017`.",
            label: "Argument type is Program[T]",
        },
        "DOEFF018" => RuleInfo {
            name: "No Ask in Try Block",
            description: "Using `yield Ask(...)` inside try blocks can cause unexpected behavior.",
            fix: "Move the Ask outside the try block, or use doeff's error handling effects like `Safe()` or `recover()`.",
            label: "Calls Ask inside try",
        },
        "DOEFF019" => RuleInfo {
            name: "No Ask with Fallback",
            description: "Using fallback values with Ask defeats the purpose of dependency injection.",
            fix: "Remove the fallback and ensure dependencies are properly provided at runtime.",
            label: "Ask given a default value",
        },
        "DOEFF020" => RuleInfo {
            name: "Program Naming Convention",
            description: "Program type variables should use 'p_' prefix for consistency.",
            fix: "Rename the variable: `data_program` → `p_data`.",
            label: "Program variable name does not start with p_",
        },
        "DOEFF021" => RuleInfo {
            name: "No __all__ Declaration",
            description: "This project defaults to exporting everything from modules.",
            fix: "Remove the `__all__` declaration. If needed for specific reasons, use `# noqa: DOEFF021`.",
            label: "Declares __all__",
        },
        "DOEFF022" => RuleInfo {
            name: "Prefer @do Decorated Functions",
            description: "Functions should use @do decorator to enable structured effects and logging with `yield slog`.",
            fix: "Add @do decorator and use `yield slog(\"message\", key=value)` for structured logging. If intentional, suppress with `# noqa: DOEFF022`.",
            label: "Function without @do",
        },
        "DOEFF023" => RuleInfo {
            name: "Pipeline Marker Required",
            description: "@do functions used to create Program entrypoints must have `# doeff: pipeline` marker.",
            fix: "Add `# doeff: pipeline` marker after @do decorator, def line, or in docstring to acknowledge pipeline-oriented programming.",
            label: "Missing pipeline marker",
        },
        "DOEFF024" => RuleInfo {
            name: "No Recover with Ask",
            description: "Using `recover()` with `ask()` defeats dependency injection.",
            fix: "Ensure dependencies are properly provided instead of using fallbacks.",
            label: "Combines recover with ask",
        },
        "DOEFF030" => RuleInfo {
            name: "Ask Result Type Annotation",
            description: "Results of `yield ask(...)` must be assigned to typed variables, and callable injections must use Protocol keys with @impl providers.",
            fix: "Use `value: Type = yield ask(\"key\")`. For callable injection, define a Protocol, ask with that Protocol, and provide an `@impl(Protocol)` function.",
            label: "ask result has no type annotation",
        },
        "DOEFF031" => RuleInfo {
            name: "No Redundant @do Wrapper Entrypoints",
            description: "Avoid creating Program entrypoints by calling @do wrappers that only forward args to a single yielded call and return it.",
            fix: "Replace `p_x: Program[...] = wrapper(...)` with `p_x: Program[...] = underlying(...)` using the same arguments. If the wrapper is intentional (naming/tracing), add `# noqa: DOEFF031`.",
            label: "@do entry is a pass-through wrapper",
        },
        "DOEFF032" => RuleInfo {
            name: "Rule Population Declaration Breach",
            description: "A module of a layer that exempts rules (architecture.hy `:exempt`) imports a business module listed in the layer's `:forbid-modules`, or the package's architecture.hy cannot be read to decide the file's rule population.",
            fix: "Move the business code to a layer that does not exempt rules, or fix the architecture.hy that owns this file. The exemption is for code outside Programs only (e.g. modules loaded by `.pth` at Python startup).",
            label: "Business code in a layer with the rule removed",
        },
        "NOQA001" => RuleInfo {
            name: "Malformed noqa Comment",
            description: "The noqa comment format appears incorrect and may not suppress the intended rule.",
            fix: "Use ` - ` (space-dash-space) to separate the rule ID from explanation. Example: `# noqa: DOEFF001 - reason`",
            label: "Malformed noqa",
        },
        _ => RuleInfo {
            name: "Unknown Rule",
            description: "Unknown rule violation.",
            fix: "Check the documentation for more information.",
            label: "Unknown rule",
        },
    }
}
