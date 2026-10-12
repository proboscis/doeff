//! DOEFF002: Mutable Attribute Naming
//!
//! Class attributes that are assigned outside of __init__, __post_init__ or __set_name__
//! must be prefixed with mut_ (public) or _mut (private), whatever the type of the value.
//! __set_name__ is a descriptor's initialization: Python calls it once, when the owner class
//! is created (PEP 487). Every method counts (`def` and `async def`), with every body nested in
//! it (`utils::nested_bodies`) and tuple / list / starred targets.
//!
//! The attribute has to be the class's own. A write to an attribute that a base class outside the repo
//! defines (Textual's `App.sub_title`, which the framework redraws the header for) cannot be renamed
//! to `_mut_`, so it is not reported (agora-redesign #2951). `rules::class_inheritance` says whose
//! attribute a name is: what the class and its bases in the same repo place (init, `__new__`, the
//! methods they call as `self.method()`, class fields) is the class's own and is reported as before;
//! what none of them place, while a base the linter cannot see into exists, is read as that base's
//! attribute; the names the table `data/framework_attributes.json` gives for a base are its
//! attributes even when the class sets them in `__init__` too.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::{LintRule, RuleReach};
use super::class_inheritance::{self_writes, FileClasses, Phase};
use crate::utils::{each_statement, has_dataclass_decorator};
use rustpython_ast::{Expr, Mod, Stmt, StmtClassDef};
use std::collections::HashSet;

pub struct MutableAttributeNamingRule;

impl MutableAttributeNamingRule {
    pub fn new() -> Self {
        Self
    }

    fn check_mutable_naming(attr_name: &str) -> bool {
        if attr_name.starts_with("__") {
            true // Dunder attributes are special
        } else if attr_name.starts_with('_') {
            attr_name.starts_with("_mut")
        } else {
            attr_name.starts_with("mut_")
        }
    }

    /// The `self` attributes the class's methods assign (`rules::class_inheritance::self_writes` — the one
    /// place that reads them), as (name, offset, is it an initialization write).
    fn collect_class_attributes(class_def: &StmtClassDef) -> Vec<(String, usize, bool)> {
        let mut attributes: Vec<(String, usize, bool)> = self_writes(class_def)
            .into_iter()
            .map(|write| (write.name, write.offset, write.phase == Phase::Init))
            .collect();
        let is_dataclass = has_dataclass_decorator(class_def);

        let mut dataclass_fields_with_defaults = Vec::new();
        if is_dataclass {
            for stmt in &class_def.body {
                if let Stmt::AnnAssign(ann_assign) = stmt {
                    if ann_assign.value.is_some() {
                        if let Expr::Name(name) = &*ann_assign.target {
                            dataclass_fields_with_defaults.push(name.id.as_str().to_string());
                        }
                    }
                }
            }
        }

        if is_dataclass {
            let mut assigned_fields = HashSet::new();
            for (attr_name, _, is_init) in &attributes {
                if !is_init && dataclass_fields_with_defaults.contains(attr_name) {
                    assigned_fields.insert(attr_name.clone());
                }
            }

            for field_name in assigned_fields {
                for stmt in &class_def.body {
                    if let Stmt::AnnAssign(ann_assign) = stmt {
                        if let Expr::Name(name) = &*ann_assign.target {
                            if name.id.as_str() == field_name {
                                attributes.push((
                                    field_name.clone(),
                                    ann_assign.range.start().to_usize(),
                                    false,
                                ));
                                break;
                            }
                        }
                    }
                }
            }
        }

        attributes
    }

    /// The reports of one class: the first assignment of each attribute that is written outside the
    /// initialization methods and is badly named, except the attributes a base class owns.
    fn check_class(
        &self,
        class_def: &StmtClassDef,
        context: &RuleContext,
        file_classes: &mut Option<FileClasses>,
        violations: &mut Vec<Violation>,
    ) {
        let mut seen = HashSet::new();
        let flagged: Vec<(String, usize)> = Self::collect_class_attributes(class_def)
            .into_iter()
            .filter(|(attr_name, _, is_init)| !is_init && seen.insert(attr_name.clone()) && !Self::check_mutable_naming(attr_name))
            .map(|(attr_name, offset, _)| (attr_name, offset))
            .collect();
        if flagged.is_empty() {
            return;
        }
        // A class without a base owns every attribute it assigns; the file's classes are read only when a base might own one.
        let inheritance = (!class_def.bases.is_empty())
            .then(|| file_classes.get_or_insert_with(|| FileClasses::of(context.ast, context.file_path)).inheritance(class_def));

        for (attr_name, offset) in flagged {
            if inheritance.as_ref().is_some_and(|inheritance| inheritance.is_inherited(&attr_name)) {
                continue;
            }
            let prefix = if attr_name.starts_with('_') && !attr_name.starts_with("__") {
                "_mut"
            } else {
                "mut_"
            };

            violations.push(Violation::new(
                self.rule_id().to_string(),
                format!(
                    "Attribute '{}' in class '{}' is mutated outside __init__/__post_init__/__set_name__. \
                     Mutable attributes must be prefixed with '{}'.",
                    attr_name, class_def.name, prefix
                ),
                offset,
                context.file_path.to_string(),
                Severity::Error,
            ));
        }
    }
}

impl LintRule for MutableAttributeNamingRule {
    fn rule_id(&self) -> &str {
        "DOEFF002"
    }

    fn description(&self) -> &str {
        "Mutable attributes must be prefixed with mut_ (public) or _mut (private)"
    }

    /// The classes of the whole file are read once (whether a base class is in the same repo or outside needs the file's imports
    /// and classes), so the file is the unit; the body hands the rule the module's first statement once per file.
    fn reach(&self) -> RuleReach {
        RuleReach::Module
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        if let Mod::Module(module) = context.ast {
            let mut file_classes: Option<FileClasses> = None;
            each_statement(&module.body, &mut |stmt| {
                if let Stmt::ClassDef(class_def) = stmt {
                    self.check_class(class_def, context, &mut file_classes, &mut violations);
                }
            });
        }

        violations
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_parser::{parse, Mode};

    fn check_code(code: &str) -> Vec<Violation> {
        check_at("test.py", code)
    }

    /// The rule runs once per file (`RuleReach::Module`): the body hands it the module's first statement.
    fn check_at(file_path: &str, code: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, file_path).unwrap();
        let rule = MutableAttributeNamingRule::new();

        match &ast {
            Mod::Module(module) => match module.body.first() {
                Some(stmt) => rule.check(&RuleContext { stmt, file_path, source: code, ast: &ast }),
                None => Vec::new(),
            },
            _ => Vec::new(),
        }
    }

    /// The attribute names reported, in the order of the reports.
    fn reported_names(violations: &[Violation]) -> Vec<String> {
        violations
            .iter()
            .filter_map(|violation| violation.message.split('\'').nth(1).map(str::to_string))
            .collect()
    }

    #[test]
    fn test_mutable_attribute_without_prefix() {
        let code = r#"
class MyClass:
    def __init__(self):
        self.value = 0

    def update(self):
        self.value = 1
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("'value'"));
    }

    #[test]
    fn test_properly_named_mutable_attributes() {
        let code = r#"
class MyClass:
    def __init__(self):
        self.mut_counter = 0

    def update(self):
        self.mut_counter += 1
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_private_mutable_attribute() {
        let code = r#"
class MyClass:
    def __init__(self):
        self._value = 0

    def update(self):
        self._value = 1
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("_mut"));
    }

    #[test]
    fn test_descriptor_set_name_is_initialization() {
        // Python calls __set_name__ once, when the owner class is created (PEP 487): what it
        // assigns is the descriptor's initial state, as in __init__.
        let code = r#"
class Field:
    def __set_name__(self, owner, name):
        self._held = f"_{name}"

    def __get__(self, instance, owner=None):
        return instance.__dict__[self._held]
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_value_kept_on_first_use_is_mutated() {
        // Assigned again after construction whatever its type: str and bool count as list does.
        let code = r#"
class Reader:
    def __init__(self):
        self._absent = None
        self.missed = False

    def absent(self):
        if self._absent is None:
            self._absent = read()
        return self._absent

    def facts(self):
        self.missed = True
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 2);
        assert!(violations[0].message.contains("'_absent'"));
        assert!(violations[1].message.contains("'missed'"));
    }

    #[test]
    fn test_assignments_in_async_methods_and_every_nested_body_are_counted() {
        // 失敗ケース(設計検証 lt-JAMKZ0NP8ZTQ52AHNGW67SGKPP の盲検 B): async def の method と、try の else・finally・match の
        // case・for の else・組の左辺・method の中の関数の代入を数えず、9 つのうち plain の 1 つだけを所見にしていた。
        let code = r#"
class Reader:
    def __init__(self):
        self._absent = None

    async def absent_receiver(self):
        if self._absent is None:
            self._absent = await read_absent()
        return self._absent

    def in_try_else(self):
        try:
            pass
        except ValueError:
            pass
        else:
            self._a = 1

    def in_finally(self):
        try:
            pass
        finally:
            self._b = 1

    def in_match(self, value):
        match value:
            case 1:
                self._c = 1

    def in_for_else(self):
        for _ in range(1):
            pass
        else:
            self._d = 1

    def in_tuple(self):
        self._e, [self._f, *self._g] = 1, [2, 3]

    def in_nested_function(self):
        def later():
            self._h = 1
        later()

    async def in_async_with(self):
        async with lock():
            self._i = 1

    def plain(self):
        self._j = 1
"#;
        let names: HashSet<String> = check_code(code)
            .iter()
            .filter_map(|violation| violation.message.split('\'').nth(1).map(str::to_string))
            .collect();
        let expected: HashSet<String> = ["_absent", "_a", "_b", "_c", "_d", "_e", "_f", "_g", "_h", "_i", "_j"]
            .iter()
            .map(|name| name.to_string())
            .collect();
        assert_eq!(names, expected);
    }

    #[test]
    fn test_a_nested_class_assigns_to_its_own_instance() {
        // method の中の class の method の self は、その入れ子の class の object — 外の class の属性に数えず、入れ子の class の属性として 1 度だけ鳴る。
        let code = r#"
class Outer:
    def make(self):
        class Inner:
            def set(self):
                self.value = 1
        return Inner
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("in class 'Inner'"), "{}", violations[0].message);
    }

    #[test]
    fn test_assignments_in_init_bodies_stay_initialization() {
        // 初期化の method の入れ子の本体(try の finally・with)の代入は、今までどおり初期化。
        let code = r#"
class Pool:
    def __init__(self):
        try:
            self.handle = open_it()
        finally:
            self.opened = True
"#;
        assert_eq!(check_code(code).len(), 0);
    }

    // ---- agora-redesign #2951: 継承元(repo の外の基底)が持つ属性への書き ----

    #[test]
    fn an_attribute_the_class_sets_in_init_and_rewrites_elsewhere_is_reported_whatever_the_base() {
        // 失敗ケース(鳴るべき形): class 自身の属性 — init で置き、別の method で書き換える。基底が repo の外の class でも鳴る。
        let code = r#"
from textual.app import App


class Monitor(App):
    def __init__(self):
        super().__init__()
        self.rows = []

    def tick(self):
        self.rows = [1]
"#;
        assert_eq!(reported_names(&check_code(code)), vec!["rows"]);
    }

    #[test]
    fn a_write_to_an_attribute_of_a_base_outside_the_repo_is_not_reported() {
        // 失敗ケース(鳴らないべき形): repo の外の基底が持つ属性への書き。class は置いていない(Textual は `sub_title` への書きで header を変える)。
        let code = r#"
from textual.app import App


class Monitor(App):
    def tick(self):
        self.sub_title = "updated"
"#;
        assert_eq!(check_code(code).len(), 0);
    }

    #[test]
    fn a_base_outside_the_repo_that_the_table_does_not_list_owns_what_the_class_does_not_place() {
        // 表に無い基底(属性の全部が見えない)でも、class が置いていない名は基底の物 — どの framework でも同じ形。
        let code = r#"
from some_framework import Widget


class Pane(Widget):
    def refresh_view(self):
        self.border_title = "x"
        self.cursor += 1
"#;
        assert_eq!(check_code(code).len(), 0);
    }

    #[test]
    fn a_framework_attribute_in_the_table_is_the_bases_even_when_init_sets_it_too() {
        // 実例: monitor_app.py の MonitorApp は `sub_title` を `__init__` でも置き、tick ごとに書き換える。表に在る名は継承元の物。
        let code = r#"
from textual.app import App


class MonitorApp(App):
    def __init__(self, interval):
        super().__init__()
        self.interval = interval
        self.sub_title = f"refresh {interval}s"

    def tick(self):
        self.sub_title = "updated"
        self.interval = 5
"#;
        // sub_title は App の物(鳴らさない)。interval は class が置いた自分の物(鳴らす)。
        assert_eq!(reported_names(&check_code(code)), vec!["interval"]);
    }

    #[test]
    fn the_same_name_set_in_init_is_the_classes_own_when_the_base_is_not_in_the_table() {
        let code = r#"
from some_framework import Thing


class Holder(Thing):
    def __init__(self):
        self.sub_title = "a"

    def tick(self):
        self.sub_title = "b"
"#;
        assert_eq!(reported_names(&check_code(code)), vec!["sub_title"]);
    }

    #[test]
    fn the_table_is_read_through_aliases_attributes_and_subscripts() {
        for header in [
            "import textual.app as ta\n\nclass X(ta.App):",
            "import textual.app\n\nclass X(textual.app.App):",
            "from textual import app\n\nclass X(app.App):",
            "from textual.app import App as TextualApp\n\nclass X(TextualApp):",
            "from textual.screen import Screen\n\nclass X(Screen[None]):",
        ] {
            let code = format!("{header}\n    def __init__(self):\n        self.title = 'a'\n\n    def tick(self):\n        self.title = 'b'\n");
            assert_eq!(check_code(&code).len(), 0, "{header}");
        }
    }

    #[test]
    fn attributes_a_class_and_its_bases_in_the_same_file_place_are_the_classes_own() {
        let code = r#"
class Counter:
    def __init__(self):
        self.count = 0


class Pulse(Counter):
    def bump(self):
        self.count += 1
"#;
        assert_eq!(reported_names(&check_code(code)), vec!["count"]);
    }

    #[test]
    fn an_attribute_first_created_outside_init_is_the_classes_own_when_every_base_is_visible() {
        let code = r#"
from abc import ABC
from typing import Generic, TypeVar

T = TypeVar("T")


class Plain:
    pass


class A(Plain):
    def m(self):
        self.a = 1


class B(object):
    def m(self):
        self.b = 1


class C(ABC):
    def m(self):
        self.c = 1


class D(Generic[T]):
    def m(self):
        self.d = 1
"#;
        assert_eq!(reported_names(&check_code(code)), vec!["a", "b", "c", "d"]);
    }

    #[test]
    fn a_base_in_the_same_file_that_has_an_outside_base_passes_it_on() {
        let code = r#"
from some_framework import Thing


class Middle(Thing):
    def __init__(self):
        self.kept = 0


class Leaf(Middle):
    def m(self):
        self.kept = 1
        self.from_thing = 2
"#;
        // kept は Middle が置いた物(鳴らす)、from_thing はだれも置いていない — Thing の物。
        assert_eq!(reported_names(&check_code(code)), vec!["kept"]);
    }

    #[test]
    fn what_a_method_called_from_init_places_is_the_classes_own() {
        // 実例: rope の MemoryDB は `__init__` が `self._load_files()` を呼び、_load_files が `self._files` を置く。基底は repo の外の class
        // (Python 2 の UserDict.DictMixin)でも、init から呼ぶ method が置いた名は class 自身の物。
        let code = r#"
from some_framework import Thing


class Store(Thing):
    def __init__(self):
        super().__init__()
        self._load()

    def _load(self):
        self._files = {}
        self._warm()

    def _warm(self):
        self._cache = {}

    def refresh(self):
        self._files = {}
        self._cache = {}
        self._never_placed = 1
"#;
        // _files と _cache は init から(2 段)呼ぶ method が置いた class 自身の物(鳴らす)。_never_placed はだれも置かない — Thing の物。
        assert_eq!(reported_names(&check_code(code)), vec!["_files", "_cache"]);
    }

    #[test]
    fn a_base_that_cannot_be_named_is_one_whose_attributes_are_not_visible() {
        let code = r#"
class Built(make_base(), *more):
    def m(self):
        self.x = 1
"#;
        assert_eq!(check_code(code).len(), 0);
    }

    #[test]
    fn what_new_and_the_class_fields_place_is_the_classes_own() {
        let code = r#"
from some_framework import Thing


class Marker(Thing):
    def __new__(cls, holder):
        self = super().__new__(cls)
        self.holder = holder
        return self


class Fielded(Thing):
    count: int = 0
    __slots__ = ("slot",)

    def m(self):
        self.count += 1
        self.slot = 1
"#;
        assert_eq!(reported_names(&check_code(code)), vec!["holder", "count", "slot"]);
    }

    #[test]
    fn a_cycle_of_bases_ends() {
        let code = r#"
class A(B):
    def m(self):
        self.a = 1


class B(A):
    def m(self):
        self.b = 1
"#;
        assert_eq!(reported_names(&check_code(code)), vec!["a", "b"]);
    }

    #[test]
    fn a_class_nested_in_a_function_is_reported_once_through_the_file_run() {
        let code = "def make():\n    class Local:\n        def __init__(self):\n            self.n = 0\n\n        def bump(self):\n            self.n += 1\n    return Local\n";
        let rules: Vec<Box<dyn LintRule>> = vec![Box::new(MutableAttributeNamingRule::new())];
        let result = crate::lint_source("test.py", code, &rules);
        assert_eq!(result.violations.len(), 1);
    }

    // 同じ repo の別の file の基底(import が指す file)。`.git` を持つ dir が repo の根。

    fn repo_with(files: &[(&str, &str)]) -> tempfile::TempDir {
        let repo = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(repo.path().join(".git")).unwrap();
        for (path, source) in files {
            let target = repo.path().join(path);
            std::fs::create_dir_all(target.parent().unwrap()).unwrap();
            std::fs::write(target, source).unwrap();
        }
        repo
    }

    fn check_file(repo: &tempfile::TempDir, path: &str) -> Vec<Violation> {
        let full = repo.path().join(path);
        check_at(full.to_str().unwrap(), &std::fs::read_to_string(&full).unwrap())
    }

    const COUNTER_BASE: &str = "class Counter:\n    def __init__(self):\n        self.count = 0\n";

    #[test]
    fn a_base_in_another_file_of_the_repo_places_its_attributes_for_the_class() {
        for import in [
            "from pkg.base import Counter",
            "from .base import Counter",
            // pkg/__init__.py の再 export(`from .base import Counter`)を辿る
            "from pkg import Counter",
        ] {
            let source = format!("{import}\n\n\nclass Pulse(Counter):\n    def bump(self):\n        self.count += 1\n        self.lazy = 1\n");
            let repo = repo_with(&[
                ("src/pkg/__init__.py", "from .base import Counter\n"),
                ("src/pkg/base.py", COUNTER_BASE),
                ("src/pkg/child.py", &source),
            ]);
            // count は Counter が置いた物(鳴らす)、lazy はだれも置かない — 基底が全部見えているので class 自身の物(鳴らす)。
            assert_eq!(reported_names(&check_file(&repo, "src/pkg/child.py")), vec!["count", "lazy"], "{import}");
        }
    }

    #[test]
    fn a_base_reached_through_a_module_alias_or_a_package_name_is_found() {
        for header in [
            "import pkg.base as b\n\nclass Pulse(b.Counter):",
            "from pkg import base\n\nclass Pulse(base.Counter):",
            "import pkg.base\n\nclass Pulse(pkg.base.Counter):",
        ] {
            let source = format!("{header}\n    def bump(self):\n        self.count += 1\n");
            let repo = repo_with(&[("src/pkg/__init__.py", ""), ("src/pkg/base.py", COUNTER_BASE), ("src/pkg/child.py", &source)]);
            assert_eq!(reported_names(&check_file(&repo, "src/pkg/child.py")), vec!["count"], "{header}");
        }
    }

    #[test]
    fn a_base_in_another_file_that_has_an_outside_base_passes_it_on() {
        let repo = repo_with(&[
            ("pkg/__init__.py", ""),
            ("pkg/base.py", "from some_framework import Thing\n\n\nclass Middle(Thing):\n    def __init__(self):\n        self.kept = 0\n"),
            (
                "pkg/child.py",
                "from pkg.base import Middle\n\n\nclass Leaf(Middle):\n    def m(self):\n        self.kept = 1\n        self.from_thing = 2\n",
            ),
        ]);
        assert_eq!(reported_names(&check_file(&repo, "pkg/child.py")), vec!["kept"]);
    }

    #[test]
    fn a_base_that_the_repo_does_not_hold_or_cannot_read_is_outside() {
        let missing_relative = "from .nowhere import Base\n\n\nclass A(Base):\n    def m(self):\n        self.x = 1\n";
        let broken_file = "from pkg.broken import Base\n\n\nclass A(Base):\n    def m(self):\n        self.x = 1\n";
        let not_a_class = "from pkg.helpers import Base\n\n\nclass A(Base):\n    def m(self):\n        self.x = 1\n";
        let repo = repo_with(&[
            ("pkg/__init__.py", ""),
            ("pkg/broken.py", "class Base(:\n"),
            ("pkg/helpers.py", "def Base():\n    pass\n"),
            ("pkg/missing.py", missing_relative),
            ("pkg/unreadable.py", broken_file),
            ("pkg/function.py", not_a_class),
        ]);
        for file in ["pkg/missing.py", "pkg/unreadable.py", "pkg/function.py"] {
            assert_eq!(check_file(&repo, file).len(), 0, "{file}");
        }
    }

    #[test]
    fn a_module_inside_a_package_is_not_an_import_root() {
        // `pkg/logging.py` は、pkg の中の file の `import logging` が指す module ではない(stdlib の logging)。
        let repo = repo_with(&[
            ("pkg/__init__.py", ""),
            ("pkg/logging.py", "class Handler:\n    def __init__(self):\n        self.level = 0\n"),
            ("pkg/child.py", "from logging import Handler\n\n\nclass Quiet(Handler):\n    def m(self):\n        self.level = 1\n"),
        ]);
        assert_eq!(check_file(&repo, "pkg/child.py").len(), 0);
    }
}



