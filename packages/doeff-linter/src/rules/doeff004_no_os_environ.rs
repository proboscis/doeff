//! DOEFF004: No os.environ Access
//!
//! Forbid direct access to environment variables.
//!
//! この規則は渡された文 1 つの式だけを見る。入れ子の文(関数・クラス・if の中ほか)は linter 本体の文ごとの再帰
//! (lib の check_stmt_recursive)が 1 つずつ渡すので、ここでは降りない — 降りると同じ当たりを 2 度数える。
//! 式・pattern は種類を全部辿る(網羅の match)。手で並べた種類の外を黙って飛ばすと、`or` の中ほかを見落とす
//! (agora-redesign #2832)。

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{
    Arguments, Comprehension, ExceptHandler, Expr, Keyword, MatchCase, Pattern, Stmt, TypeParam,
    WithItem,
};

pub struct NoOsEnvironRule;

impl NoOsEnvironRule {
    pub fn new() -> Self {
        Self
    }

    fn is_os_environ(expr: &Expr) -> bool {
        if let Expr::Attribute(attr) = expr {
            if attr.attr.as_str() == "environ" {
                if let Expr::Name(name) = &*attr.value {
                    return name.id.as_str() == "os";
                }
            }
        }
        false
    }
}

/// 文 1 つの式を辿り、当たりを積む。
struct EnvironReads<'a> {
    file_path: &'a str,
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

    /// 文の自分の式(入れ子の文の本体は除く — 本体の再帰が渡す)。
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

    fn expr(&mut self, expr: &Expr) {
        match expr {
            // os.environ["KEY"]
            Expr::Subscript(subscript) => {
                if NoOsEnvironRule::is_os_environ(&subscript.value) {
                    self.hit(
                        "Direct access to os.environ is forbidden. \
                         Use dependency injection to receive configuration values."
                            .to_string(),
                        subscript.range.start().to_usize(),
                    );
                }
                self.expr(&subscript.value);
                self.expr(&subscript.slice);
            }
            // os.environ.get() / os.getenv()
            Expr::Call(call) => {
                if let Expr::Attribute(attr) = &*call.func {
                    if NoOsEnvironRule::is_os_environ(&attr.value) {
                        self.hit(
                            format!(
                                "Calling os.environ.{}() is forbidden. \
                                 Use dependency injection to receive configuration values.",
                                attr.attr
                            ),
                            call.range.start().to_usize(),
                        );
                    }
                    if let Expr::Name(name) = &*attr.value {
                        if name.id.as_str() == "os" && attr.attr.as_str() == "getenv" {
                            self.hit(
                                "os.getenv() is forbidden. \
                                 Use dependency injection to receive configuration values."
                                    .to_string(),
                                call.range.start().to_usize(),
                            );
                        }
                    }
                }
                self.expr(&call.func);
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
            Expr::Compare(compare) => {
                self.expr(&compare.left);
                self.exprs(&compare.comparators);
            }
            Expr::FormattedValue(formatted) => {
                self.expr(&formatted.value);
                self.optional(formatted.format_spec.as_deref());
            }
            Expr::JoinedStr(joined) => self.exprs(&joined.values),
            Expr::Attribute(attr) => self.expr(&attr.value),
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

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut reads = EnvironReads {
            file_path: context.file_path,
            violations: Vec::new(),
        };
        reads.stmt(context.stmt);
        reads.violations
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_ast::Mod;
    use rustpython_parser::{parse, Mode};

    fn check_code(code: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, "test.py").unwrap();
        let rule = NoOsEnvironRule::new();
        let mut violations = Vec::new();

        if let Mod::Module(module) = &ast {
            for stmt in &module.body {
                let context = RuleContext {
                    stmt,
                    file_path: "test.py",
                    source: code,
                    ast: &ast,
                };
                violations.extend(rule.check(&context));
            }
        }

        violations
    }

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

    /// `or` の中の読みに当たる(直す前は式の辿りが BoolOp を持たず 0 件)。
    #[test]
    fn test_read_inside_or_is_seen() {
        let code = r#"
import os
root = os.environ.get("XDG_CACHE_HOME", "").strip() or "~/.cache"
"#;
        assert_eq!(check_code(code).len(), 1);
    }

    /// 手で並べた種類の外の式(単項演算・リスト・辞書・f-string・lambda・内包・名つき式・関数の既定値と注釈)の中の読みに当たる。
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

    /// 規則 1 つに渡した文では入れ子の文の本体を見ない(本体の再帰が 1 つずつ渡す — 降りると 2 度数える)。
    /// 本体の再帰を通した数は lib の検(関数の中の読みは 1 度だけ)が見る。
    #[test]
    fn test_the_rule_alone_does_not_descend_into_nested_statements() {
        let code = r#"
import os
def store_root():
    return os.environ.get("STORE")
class Settings:
    def home(self):
        return os.getenv("HOME")
"#;
        assert_eq!(check_code(code).len(), 0);
    }

    /// 書き込み(`os.environ[...] = ...`)と消し(`del os.environ[...]`)も環境変数への直の触れ方として当たる。
    #[test]
    fn test_writes_and_deletes_are_seen() {
        let code = r#"
import os
os.environ["A"] = "1"
del os.environ["B"]
"#;
        assert_eq!(check_code(code).len(), 2);
    }
}
