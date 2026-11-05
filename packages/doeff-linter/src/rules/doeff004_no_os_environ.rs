//! DOEFF004: No os.environ Access
//!
//! Forbid direct access to environment variables.
//!
//! Names pointing to os, names pointing to os.environ, and names pointing to os.getenv are resolved with a table built from the file's import statements (utils' OsEnvNames) —
//! `x.environ` / `x.getenv` for `import os as x`, `environ` / `e` for `from os import environ [as e]`,
//! and `getenv` / `g` for `from os import getenv [as g]` all match the same as a plain `os.environ` / `os.getenv`.
//! The table is built once per file, so this rule looks at the whole module (RuleReach::Module — the body is applied once per file).
//! Statements are all visited once each with utils' each_statement (the list of nested bodies is in the same one place as the body recursion), and each statement looks only at its own expressions.
//! Expressions and patterns are visited for every kind (an exhaustive match). Silently skipping kinds outside a hand-written list would miss things such as the inside of an `or`.
//!
//! What it matches is the reference to os.environ / os.getenv itself (reading environment variables
//! outside a foundation handler is a violation the linter finds. Reading environment variables belongs only inside a foundation handler. The layer declaration's `:exempt` excludes that place).
//! One reference is one finding — the message is split by that form:
//!   * Subscript `os.environ[...]` (read, write, del) and method calls `os.environ.<method>(...)` and `os.getenv(...)` — the message stays as before.
//!   * `os.environ` on the right of `in` / `not in` — a read that checks presence.
//!   * `os.getenv` and `os.environ.<name>` referenced without being called (`g = os.getenv`, `f(os.environ.get)`).
//!   * `os.environ` appearing anywhere else (an argument, assignment, return, `dict(os.environ)`, `{**os.environ}`, a ternary branch, `os.environ = …`) — used as a value.
//! The `os.environ` inside a subscript, call, or `in` match is not counted separately (one read is not counted twice). Rebinding a plain name (`environ = …`,
//! `del environ`) does not touch os, so it does not match (the name table is one per whole file — a read of a plain name after rebinding still matches as before).

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::{LintRule, RuleReach};
use crate::utils::{each_statement, OsEnvNames};
use rustpython_ast::{
    Arguments, CmpOp, Comprehension, ExceptHandler, Expr, ExprContext, Keyword, MatchCase, Mod,
    Pattern, Ranged, Stmt, TypeParam, WithItem,
};

/// The second half of the message for the newly matched forms (used as a value, checking presence, referenced without being called) — adds where to read to the earlier message's recommendation (dependency injection).
const READ_IN_FOUNDATION: &str = "Read environment variables only in a foundation handler; \
     use dependency injection to receive configuration values.";

/// The entry point of an environment-variable read that an expression points to itself.
enum Referent {
    /// os.environ (`<name of os>.environ`, or a plain name pointing to environ)
    Environ,
    /// os.getenv (`<name of os>.getenv`, or a plain name pointing to getenv)
    Getenv,
}

pub struct NoOsEnvironRule;

impl NoOsEnvironRule {
    pub fn new() -> Self {
        Self
    }
}

/// Visits the expressions of one statement and accumulates matches.
struct EnvironReads<'a> {
    file_path: &'a str,
    /// A table, built from the file's import statements, of names that point to environment-variable reads.
    names: &'a OsEnvNames,
    violations: Vec<Violation>,
}

impl<'a> EnvironReads<'a> {
    fn hit(&mut self, message: String, offset: usize) {
        self.violations.push(Violation::new(
            "DOEFF004".to_string(),
            message,
            offset,
            self.file_path.to_string(),
            Severity::Error,
        ));
    }

    /// A statement's own expressions (excluding the bodies of nested statements — each_statement passes them one at a time).
    fn stmt(&mut self, stmt: &Stmt) {
        match stmt {
            Stmt::FunctionDef(func) => {
                self.exprs(&func.decorator_list);
                self.arguments(&func.args);
                self.optional(func.returns.as_deref());
                self.type_params(&func.type_params);
            }
            Stmt::AsyncFunctionDef(func) => {
                self.exprs(&func.decorator_list);
                self.arguments(&func.args);
                self.optional(func.returns.as_deref());
                self.type_params(&func.type_params);
            }
            Stmt::ClassDef(class_def) => {
                self.exprs(&class_def.decorator_list);
                self.exprs(&class_def.bases);
                self.keywords(&class_def.keywords);
                self.type_params(&class_def.type_params);
            }
            Stmt::Return(ret) => self.optional(ret.value.as_deref()),
            Stmt::Delete(delete) => self.exprs(&delete.targets),
            Stmt::Assign(assign) => {
                self.exprs(&assign.targets);
                self.expr(&assign.value);
            }
            Stmt::TypeAlias(alias) => {
                self.expr(&alias.name);
                self.type_params(&alias.type_params);
                self.expr(&alias.value);
            }
            Stmt::AugAssign(aug) => {
                self.expr(&aug.target);
                self.expr(&aug.value);
            }
            Stmt::AnnAssign(ann) => {
                self.expr(&ann.target);
                self.expr(&ann.annotation);
                self.optional(ann.value.as_deref());
            }
            Stmt::For(for_stmt) => {
                self.expr(&for_stmt.target);
                self.expr(&for_stmt.iter);
            }
            Stmt::AsyncFor(for_stmt) => {
                self.expr(&for_stmt.target);
                self.expr(&for_stmt.iter);
            }
            Stmt::While(while_stmt) => self.expr(&while_stmt.test),
            Stmt::If(if_stmt) => self.expr(&if_stmt.test),
            Stmt::With(with_stmt) => self.with_items(&with_stmt.items),
            Stmt::AsyncWith(with_stmt) => self.with_items(&with_stmt.items),
            Stmt::Match(match_stmt) => {
                self.expr(&match_stmt.subject);
                self.cases(&match_stmt.cases);
            }
            Stmt::Raise(raise) => {
                self.optional(raise.exc.as_deref());
                self.optional(raise.cause.as_deref());
            }
            Stmt::Try(try_stmt) => self.handlers(&try_stmt.handlers),
            Stmt::TryStar(try_stmt) => self.handlers(&try_stmt.handlers),
            Stmt::Assert(assert) => {
                self.expr(&assert.test);
                self.optional(assert.msg.as_deref());
            }
            Stmt::Expr(expr_stmt) => self.expr(&expr_stmt.value),
            Stmt::Import(_)
            | Stmt::ImportFrom(_)
            | Stmt::Global(_)
            | Stmt::Nonlocal(_)
            | Stmt::Pass(_)
            | Stmt::Break(_)
            | Stmt::Continue(_) => {}
        }
    }

    /// Whether an expression points to os.environ / os.getenv itself. A plain name counts only when read (Load) — rebinding (`environ = …`) and deleting (`del environ`) only
    /// rename the name and do not touch os. `os.environ = …` and `del os.environ` write an attribute of os, so they match.
    fn referent(&self, expr: &Expr) -> Option<Referent> {
        let rebinds_a_name =
            matches!(expr, Expr::Name(name) if !matches!(name.ctx, ExprContext::Load));
        if rebinds_a_name {
            None
        } else if self.names.is_environ(expr) {
            Some(Referent::Environ)
        } else if self.names.is_getenv(expr) {
            Some(Referent::Getenv)
        } else {
            None
        }
    }

    /// One expression. If it is os.environ / os.getenv itself, it matches as a reference that appears outside a subscript, call, or `in` (without visiting inside),
    /// and otherwise it is visited by kind.
    fn expr(&mut self, expr: &Expr) {
        let at = expr.range().start().to_usize();
        match self.referent(expr) {
            // os.environ used as a value (an argument, assignment, return, dict(os.environ), {**os.environ}, a ternary branch, os.environ = …, etc.)
            Some(Referent::Environ) => self.hit(
                format!(
                    "Using os.environ as a value is forbidden. {}",
                    READ_IN_FOUNDATION
                ),
                at,
            ),
            // os.getenv referenced without being called (g = os.getenv, f(os.getenv))
            Some(Referent::Getenv) => self.hit(
                format!(
                    "Referring to os.getenv without calling it is forbidden. {}",
                    READ_IN_FOUNDATION
                ),
                at,
            ),
            None => self.parts(expr),
        }
    }

    /// Visits inside an expression that is not os.environ / os.getenv itself.
    fn parts(&mut self, expr: &Expr) {
        match expr {
            // os.environ["KEY"] (also the alias x.environ["KEY"], e["KEY"], and write `= …` and del) — the os.environ at the base of the subscript is not counted separately
            Expr::Subscript(subscript) => {
                if self.names.is_environ(&subscript.value) {
                    self.hit(
                        "Direct access to os.environ is forbidden. \
                         Use dependency injection to receive configuration values."
                            .to_string(),
                        subscript.range.start().to_usize(),
                    );
                } else {
                    self.expr(&subscript.value);
                }
                self.expr(&subscript.slice);
            }
            // os.environ.get() / os.getenv() (also the aliases x.environ.get(), e.get(), x.getenv(), g(), and the write .update(), etc.) —
            // the called os.environ.<method> and os.getenv are not counted separately
            Expr::Call(call) => {
                match &*call.func {
                    Expr::Attribute(attr) if self.names.is_environ(&attr.value) => self.hit(
                        format!(
                            "Calling os.environ.{}() is forbidden. \
                             Use dependency injection to receive configuration values.",
                            attr.attr
                        ),
                        call.range.start().to_usize(),
                    ),
                    func if self.names.is_getenv(func) => self.hit(
                        "os.getenv() is forbidden. \
                         Use dependency injection to receive configuration values."
                            .to_string(),
                        call.range.start().to_usize(),
                    ),
                    func => self.expr(func),
                }
                self.exprs(&call.args);
                self.keywords(&call.keywords);
            }
            Expr::BoolOp(bool_op) => self.exprs(&bool_op.values),
            Expr::NamedExpr(named) => {
                self.expr(&named.target);
                self.expr(&named.value);
            }
            Expr::BinOp(bin_op) => {
                self.expr(&bin_op.left);
                self.expr(&bin_op.right);
            }
            Expr::UnaryOp(unary) => self.expr(&unary.operand),
            Expr::Lambda(lambda) => {
                self.arguments(&lambda.args);
                self.expr(&lambda.body);
            }
            Expr::IfExp(if_exp) => {
                self.expr(&if_exp.test);
                self.expr(&if_exp.body);
                self.expr(&if_exp.orelse);
            }
            Expr::Dict(dict) => {
                for key in dict.keys.iter().flatten() {
                    self.expr(key);
                }
                self.exprs(&dict.values);
            }
            Expr::Set(set) => self.exprs(&set.elts),
            Expr::ListComp(comp) => {
                self.expr(&comp.elt);
                self.comprehensions(&comp.generators);
            }
            Expr::SetComp(comp) => {
                self.expr(&comp.elt);
                self.comprehensions(&comp.generators);
            }
            Expr::DictComp(comp) => {
                self.expr(&comp.key);
                self.expr(&comp.value);
                self.comprehensions(&comp.generators);
            }
            Expr::GeneratorExp(comp) => {
                self.expr(&comp.elt);
                self.comprehensions(&comp.generators);
            }
            Expr::Await(await_expr) => self.expr(&await_expr.value),
            Expr::Yield(yield_expr) => self.optional(yield_expr.value.as_deref()),
            Expr::YieldFrom(yield_from) => self.expr(&yield_from.value),
            // "KEY" in os.environ / "KEY" not in os.environ — a read that checks presence (the os.environ on the right is not counted separately)
            Expr::Compare(compare) => {
                self.expr(&compare.left);
                for (op, right) in compare.ops.iter().zip(&compare.comparators) {
                    if matches!(op, CmpOp::In | CmpOp::NotIn) && self.names.is_environ(right) {
                        self.hit(
                            format!(
                                "Testing membership in os.environ is forbidden. {}",
                                READ_IN_FOUNDATION
                            ),
                            right.range().start().to_usize(),
                        );
                    } else {
                        self.expr(right);
                    }
                }
            }
            Expr::FormattedValue(formatted) => {
                self.expr(&formatted.value);
                self.optional(formatted.format_spec.as_deref());
            }
            Expr::JoinedStr(joined) => self.exprs(&joined.values),
            // os.environ.<name> referenced without being called (get = os.environ.get, os.environ.copy) — the base os.environ is not counted separately
            Expr::Attribute(attr) => {
                if self.names.is_environ(&attr.value) {
                    self.hit(
                        format!(
                            "Referring to os.environ.{} without calling it is forbidden. {}",
                            attr.attr, READ_IN_FOUNDATION
                        ),
                        attr.range.start().to_usize(),
                    );
                } else {
                    self.expr(&attr.value);
                }
            }
            Expr::Starred(starred) => self.expr(&starred.value),
            Expr::List(list) => self.exprs(&list.elts),
            Expr::Tuple(tuple) => self.exprs(&tuple.elts),
            Expr::Slice(slice) => {
                self.optional(slice.lower.as_deref());
                self.optional(slice.upper.as_deref());
                self.optional(slice.step.as_deref());
            }
            Expr::Constant(_) | Expr::Name(_) => {}
        }
    }

    fn exprs(&mut self, exprs: &[Expr]) {
        for expr in exprs {
            self.expr(expr);
        }
    }

    fn optional(&mut self, expr: Option<&Expr>) {
        if let Some(expr) = expr {
            self.expr(expr);
        }
    }

    fn keywords(&mut self, keywords: &[Keyword]) {
        for keyword in keywords {
            self.expr(&keyword.value);
        }
    }

    fn arguments(&mut self, args: &Arguments) {
        for arg in args.posonlyargs.iter().chain(&args.args).chain(&args.kwonlyargs) {
            self.optional(arg.def.annotation.as_deref());
            self.optional(arg.default.as_deref());
        }
        for arg in args.vararg.iter().chain(&args.kwarg) {
            self.optional(arg.annotation.as_deref());
        }
    }

    fn comprehensions(&mut self, generators: &[Comprehension]) {
        for generator in generators {
            self.expr(&generator.target);
            self.expr(&generator.iter);
            self.exprs(&generator.ifs);
        }
    }

    fn with_items(&mut self, items: &[WithItem]) {
        for item in items {
            self.expr(&item.context_expr);
            self.optional(item.optional_vars.as_deref());
        }
    }

    fn handlers(&mut self, handlers: &[ExceptHandler]) {
        for ExceptHandler::ExceptHandler(handler) in handlers {
            self.optional(handler.type_.as_deref());
        }
    }

    fn cases(&mut self, cases: &[MatchCase]) {
        for case in cases {
            self.pattern(&case.pattern);
            self.optional(case.guard.as_deref());
        }
    }

    fn pattern(&mut self, pattern: &Pattern) {
        match pattern {
            Pattern::MatchValue(value) => self.expr(&value.value),
            Pattern::MatchSequence(sequence) => self.patterns(&sequence.patterns),
            Pattern::MatchMapping(mapping) => {
                self.exprs(&mapping.keys);
                self.patterns(&mapping.patterns);
            }
            Pattern::MatchClass(class) => {
                self.expr(&class.cls);
                self.patterns(&class.patterns);
                self.patterns(&class.kwd_patterns);
            }
            Pattern::MatchAs(as_pattern) => {
                if let Some(inner) = &as_pattern.pattern {
                    self.pattern(inner);
                }
            }
            Pattern::MatchOr(or_pattern) => self.patterns(&or_pattern.patterns),
            Pattern::MatchSingleton(_) | Pattern::MatchStar(_) => {}
        }
    }

    fn patterns(&mut self, patterns: &[Pattern]) {
        for pattern in patterns {
            self.pattern(pattern);
        }
    }

    fn type_params(&mut self, params: &[TypeParam]) {
        for param in params {
            match param {
                TypeParam::TypeVar(type_var) => self.optional(type_var.bound.as_deref()),
                TypeParam::ParamSpec(_) | TypeParam::TypeVarTuple(_) => {}
            }
        }
    }
}

impl LintRule for NoOsEnvironRule {
    fn rule_id(&self) -> &str {
        "DOEFF004"
    }

    fn description(&self) -> &str {
        "Forbid direct access to environment variables"
    }

    /// The name table is built only once from the file's import statements, so it looks at the whole module (the body is applied once per file — applying per statement would
    /// rebuild the table as many times as there are statements).
    fn reach(&self) -> RuleReach {
        RuleReach::Module
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let Mod::Module(module) = context.ast else {
            return Vec::new();
        };
        let names = OsEnvNames::of_module(context.ast);
        let mut reads = EnvironReads {
            file_path: context.file_path,
            names: &names,
            violations: Vec::new(),
        };
        each_statement(&module.body, &mut |stmt| reads.stmt(stmt));
        reads.violations
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rules::base::RuleReach;
    use crate::utils::each_statement;
    use rustpython_ast::{Mod, Stmt};
    use rustpython_parser::{parse, Mode};

    /// Applies the rule in the same unit as the linter proper (lib's lint_source) — passes statements according to the rule's viewing unit (reach)
    /// (for a module, once per file; for a statement, one at a time down to nested statements; for a subtree, only the statements of the upper level).
    fn check_code(code: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, "test.py").unwrap();
        let rule = NoOsEnvironRule::new();
        let Mod::Module(module) = &ast else {
            return Vec::new();
        };
        let check = |stmt: &Stmt| {
            rule.check(&RuleContext {
                stmt,
                file_path: "test.py",
                source: code,
                ast: &ast,
            })
        };
        match rule.reach() {
            RuleReach::Module => module.body.first().map(check).unwrap_or_default(),
            RuleReach::Statement => {
                let mut violations = Vec::new();
                each_statement(&module.body, &mut |stmt| violations.extend(check(stmt)));
                violations
            }
            RuleReach::Subtree => module.body.iter().flat_map(check).collect(),
        }
    }

    /// The sequence of matched messages.
    fn messages(code: &str) -> Vec<String> {
        check_code(code).into_iter().map(|violation| violation.message).collect()
    }

    const ENVIRON_SUBSCRIPT: &str = "Direct access to os.environ is forbidden. \
         Use dependency injection to receive configuration values.";
    const ENVIRON_GET: &str = "Calling os.environ.get() is forbidden. \
         Use dependency injection to receive configuration values.";
    const GETENV: &str = "os.getenv() is forbidden. \
         Use dependency injection to receive configuration values.";

    #[test]
    fn test_os_environ_subscript() {
        let code = r#"
import os
api_key = os.environ["API_KEY"]
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_os_environ_get() {
        let code = r#"
import os
api_key = os.environ.get("API_KEY")
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_os_getenv() {
        let code = r#"
import os
api_key = os.getenv("API_KEY")
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_no_environ_access() {
        let code = r#"
def get_config(api_key: str) -> dict:
    return {"api_key": api_key}
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    /// Matches a read inside an `or` (before the fix, the expression visit had no BoolOp and found 0).
    #[test]
    fn test_read_inside_or_is_seen() {
        let code = r#"
import os
root = os.environ.get("XDG_CACHE_HOME", "").strip() or "~/.cache"
"#;
        assert_eq!(check_code(code).len(), 1);
    }

    /// Matches a read inside expressions outside the hand-written list (unary operations, lists, dicts, f-strings, lambdas, comprehensions, named expressions, function defaults and annotations).
    #[test]
    fn test_reads_inside_every_kind_of_expression_are_seen() {
        let code = r#"
import os
a = not os.getenv("A")
b = [os.getenv("B")]
c = {"k": os.environ["C"]}
d = f"{os.environ.get('D')}"
e = lambda: os.getenv("E")
f = [x for x in os.environ.get("F", "").split(",")]
if (i := os.getenv("I")):
    pass
def g(root=os.getenv("G")) -> os.environ["T"]:
    pass
"#;
        assert_eq!(check_code(code).len(), 9);
    }

    /// A read inside a nested statement (a function, a class's method) is counted once each (not twice). The count through the body recursion is also checked by lib's test
    /// (a read inside a function is only once).
    #[test]
    fn test_reads_in_nested_statements_are_counted_once_each() {
        let code = r#"
import os
def store_root():
    return os.environ.get("STORE")
class Settings:
    def home(self):
        return os.getenv("HOME")
"#;
        assert_eq!(check_code(code).len(), 2);
    }

    // Reads through an alias — before the fix, only names that were literally `os` were looked at, and all of these found 0.
    // Even with an alias, the matched message is the same as for a plain os.environ / os.getenv.

    /// The 3 forms of `import os as x` (x.environ.get(), x.environ[...], x.getenv()).
    #[test]
    fn test_reads_through_import_os_as_alias_are_seen() {
        let code = r#"
import os as x
a = x.environ.get("A")
b = x.environ["B"]
c = x.getenv("C")
"#;
        assert_eq!(messages(code), vec![ENVIRON_GET, ENVIRON_SUBSCRIPT, GETENV]);
    }

    /// The plain name environ of `from os import environ` (environ.get(), environ[...]).
    #[test]
    fn test_reads_through_from_os_import_environ_are_seen() {
        let code = r#"
from os import environ
a = environ.get("A")
b = environ["B"]
"#;
        assert_eq!(messages(code), vec![ENVIRON_GET, ENVIRON_SUBSCRIPT]);
    }

    /// The alias e of `from os import environ as e` (e.get(), e[...]).
    #[test]
    fn test_reads_through_from_os_import_environ_as_alias_are_seen() {
        let code = r#"
from os import environ as e
a = e.get("A")
b = e["B"]
"#;
        assert_eq!(messages(code), vec![ENVIRON_GET, ENVIRON_SUBSCRIPT]);
    }

    /// The plain name getenv of `from os import getenv`, and the alias g of `from os import getenv as g`.
    #[test]
    fn test_reads_through_from_os_import_getenv_and_its_alias_are_seen() {
        let code = r#"
from os import getenv
from os import getenv as g
a = getenv("A")
b = g("B")
"#;
        assert_eq!(messages(code), vec![GETENV, GETENV]);
    }

    /// An alias from an import statement inside a function also matches reads inside that function (and, since the table is one per whole file, reads elsewhere in the file).
    #[test]
    fn test_aliases_imported_inside_a_function_are_seen() {
        let code = r#"
def settings():
    import os as x
    from os import environ as e, getenv as g
    return x.environ["A"], e.get("B"), g("C")
"#;
        assert_eq!(messages(code), vec![ENVIRON_SUBSCRIPT, ENVIRON_GET, GETENV]);
    }

    /// Names that do not point to os do not match: environ and getenv of another module, an environ attribute of an alias of a non-os module,
    /// a relative `from .os import environ`, p of `import os.path as p`, a dict that has "environ" as a key.
    #[test]
    fn test_names_not_bound_to_os_are_not_seen() {
        let code = r#"
from mymod import environ
from mymod import getenv as g
from .os import environ as rel
import mymod as x
import os.path as p
environ.get("A")
environ["B"]
g("C")
rel["D"]
x.environ.get("E")
x.getenv("F")
p.environ["G"]
d = {"environ": 1}
d["environ"]
request.environ.get("H")
"#;
        assert_eq!(messages(code), Vec::<String>::new());
    }

    /// Writes (`os.environ[...] = ...`) and deletes (`del os.environ[...]`) also match as direct touches of environment variables.
    #[test]
    fn test_writes_and_deletes_are_seen() {
        let code = r#"
import os
os.environ["A"] = "1"
del os.environ["B"]
"#;
        assert_eq!(check_code(code).len(), 2);
    }

    // References to os.environ / os.getenv themselves (reading environment variables outside a foundation handler is a violation)
    // — before the fix, only the subscript and call forms were looked at, and all the new forms below found 0.

    const ENVIRON_VALUE: &str = "Using os.environ as a value is forbidden. \
         Read environment variables only in a foundation handler; \
         use dependency injection to receive configuration values.";
    const ENVIRON_MEMBERSHIP: &str = "Testing membership in os.environ is forbidden. \
         Read environment variables only in a foundation handler; \
         use dependency injection to receive configuration values.";
    const GETENV_REFERENCE: &str = "Referring to os.getenv without calling it is forbidden. \
         Read environment variables only in a foundation handler; \
         use dependency injection to receive configuration values.";

    /// A statement of os.environ.<name> referenced without being called.
    fn environ_member_reference(member: &str) -> String {
        format!(
            "Referring to os.environ.{} without calling it is forbidden. \
             Read environment variables only in a foundation handler; \
             use dependency injection to receive configuration values.",
            member
        )
    }

    /// os.environ passed as a call argument (positional or keyword).
    #[test]
    fn test_environ_passed_as_an_argument_is_seen() {
        let code = r#"
import os
run(os.environ)
spawn(env=os.environ)
copied = dict(os.environ)
"#;
        assert_eq!(messages(code), vec![ENVIRON_VALUE; 3]);
    }

    /// os.environ as the value of an assignment, an annotated assignment, or a return.
    #[test]
    fn test_environ_assigned_or_returned_is_seen() {
        let code = r#"
import os
env = os.environ
typed: dict = os.environ
def current():
    return os.environ
"#;
        assert_eq!(messages(code), vec![ENVIRON_VALUE; 3]);
    }

    /// An unpacked os.environ (`{**os.environ}`, `[*os.environ]`, `f(**os.environ)`).
    #[test]
    fn test_environ_unpacked_is_seen() {
        let code = r#"
import os
merged = {**os.environ, "A": "1"}
keys = [*os.environ]
run(**os.environ)
"#;
        assert_eq!(messages(code), vec![ENVIRON_VALUE; 3]);
    }

    /// os.environ as a ternary branch or an `or` value.
    #[test]
    fn test_environ_in_a_conditional_branch_is_seen() {
        let code = r#"
import os
a = given if given else os.environ
b = os.environ if fresh else {}
c = given or os.environ
"#;
        assert_eq!(messages(code), vec![ENVIRON_VALUE; 3]);
    }

    /// os.environ as an argument default (of a function and a lambda).
    #[test]
    fn test_environ_as_a_default_value_is_seen() {
        let code = r#"
import os
def run(env=os.environ):
    pass
pick = lambda env=os.environ: env
"#;
        assert_eq!(messages(code), vec![ENVIRON_VALUE; 2]);
    }

    /// os.environ on the right of `in` / `not in` (inside an expression and in an if condition).
    #[test]
    fn test_membership_in_environ_is_seen() {
        let code = r#"
import os
present = "A" in os.environ
absent = "B" not in os.environ
if "C" in os.environ:
    pass
"#;
        assert_eq!(messages(code), vec![ENVIRON_MEMBERSHIP; 3]);
    }

    /// os.getenv referenced without being called (assignment, argument, return).
    #[test]
    fn test_getenv_referred_without_calling_is_seen() {
        let code = r#"
import os
read = os.getenv
lookup(os.getenv)
def reader():
    return os.getenv
"#;
        assert_eq!(messages(code), vec![GETENV_REFERENCE; 3]);
    }

    /// A method of os.environ referenced without being called (`get = os.environ.get`, `f(os.environ.copy)`).
    #[test]
    fn test_environ_method_referred_without_calling_is_seen() {
        let code = r#"
import os
get = os.environ.get
snapshot(os.environ.copy)
"#;
        assert_eq!(
            messages(code),
            vec![
                environ_member_reference("get"),
                environ_member_reference("copy")
            ]
        );
    }

    /// Matches in the same form even through an alias: x.environ and x.getenv of `import os as x`, and the plain names of `from os import environ [as e]` and
    /// `from os import getenv [as g]`.
    #[test]
    fn test_references_through_aliases_are_seen() {
        let code = r#"
import os as x
from os import environ
from os import environ as e
from os import getenv
from os import getenv as g
run(x.environ)
run(environ)
present = "A" in e
read = x.getenv
lookup(getenv)
lookup(g)
"#;
        assert_eq!(
            messages(code),
            vec![
                ENVIRON_VALUE,
                ENVIRON_VALUE,
                ENVIRON_MEMBERSHIP,
                GETENV_REFERENCE,
                GETENV_REFERENCE,
                GETENV_REFERENCE,
            ]
        );
    }

    /// The forms of writes are one finding each: a subscript write or del (the earlier subscript statement), a write method (the earlier call statement),
    /// and rebinding os.environ itself (`|=`, `=` — a statement using it as a value).
    #[test]
    fn test_writes_are_seen_once_each() {
        let code = r#"
import os
os.environ["A"] = "1"
del os.environ["B"]
os.environ.update(C="1")
os.environ.setdefault("D", "1")
os.environ.pop("E")
os.environ |= {"F": "1"}
os.environ = {}
"#;
        let calling = |method: &str| {
            format!(
                "Calling os.environ.{}() is forbidden. \
                 Use dependency injection to receive configuration values.",
                method
            )
        };
        assert_eq!(
            messages(code),
            vec![
                ENVIRON_SUBSCRIPT.to_string(),
                ENVIRON_SUBSCRIPT.to_string(),
                calling("update"),
                calling("setdefault"),
                calling("pop"),
                ENVIRON_VALUE.to_string(),
                ENVIRON_VALUE.to_string(),
            ]
        );
    }

    /// One read stays one finding: the os.environ inside a subscript, call, or `in` is not counted separately. Each nested read is one finding.
    #[test]
    fn test_one_read_is_counted_once() {
        let code = r#"
import os
a = os.environ["A"]
b = os.environ.get("B")
c = os.getenv("C")
d = os.environ.get("D", os.environ["E"])
e = os.environ.get("F", "").strip()
f = "G" in os.environ.keys()
run(os.environ["H"])
"#;
        assert_eq!(
            messages(code),
            vec![
                ENVIRON_SUBSCRIPT,
                ENVIRON_GET,
                GETENV,
                ENVIRON_GET,
                ENVIRON_SUBSCRIPT,
                ENVIRON_GET,
                "Calling os.environ.keys() is forbidden. \
                 Use dependency injection to receive configuration values.",
                ENVIRON_SUBSCRIPT,
            ]
        );
    }

    /// environ and getenv that do not point to os do not match, even as a value, in `in`, or as a reference: an attribute of another object (request.environ, my.environ,
    /// my.getenv), the string "environ", a plain name with no import, another attribute of os.
    #[test]
    fn test_references_not_bound_to_os_are_not_seen() {
        let code = r#"
import os
run(request.environ)
present = "A" in request.environ
env = my.environ
read = my.getenv
key = "environ"
label = "os.environ"
run(environ)
lookup(getenv)
present = "B" in environ
home = os.path.expanduser("~")
sep = os.sep
"#;
        assert_eq!(messages(code), Vec::<String>::new());
    }

    /// Rebinding a plain name (assignment, a for binding, a named expression, del) does not touch os, so it does not match.
    #[test]
    fn test_rebinding_a_bare_name_is_not_seen() {
        let code = r#"
from os import environ, getenv
environ = {}
getenv = None
for environ in []:
    pass
if (environ := {}):
    pass
del environ
"#;
        assert_eq!(messages(code), Vec::<String>::new());
    }
}
