//! DOEFF004: No os.environ Access
//!
//! Forbid direct access to environment variables.
//!
//! os を指す名・os.environ を指す名・os.getenv を指す名は、file の import 文から作る表(utils の OsEnvNames)で解く —
//! `import os as x` の `x.environ` / `x.getenv`、`from os import environ [as e]` の `environ` / `e`、
//! `from os import getenv [as g]` の `getenv` / `g` も、素の `os.environ` / `os.getenv` と同じに当たる(agora-redesign #3012)。
//! 表は file に 1 度だけ作るので、この規則は module 全体を見る(RuleReach::Module — 本体は file に 1 度だけ当てる)。
//! 文は utils の each_statement で全部を 1 度ずつ辿り(入れ子の本体の一覧は本体の再帰と同じ 1 か所)、文ごとに自分の式だけを見る。
//! 式・pattern は種類を全部辿る(網羅の match)。手で並べた種類の外を黙って飛ばすと、`or` の中ほかを見落とす
//! (agora-redesign #2832)。

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::{LintRule, RuleReach};
use crate::utils::{each_statement, OsEnvNames};
use rustpython_ast::{
    Arguments, Comprehension, ExceptHandler, Expr, Keyword, MatchCase, Mod, Pattern, Stmt,
    TypeParam, WithItem,
};

pub struct NoOsEnvironRule;

impl NoOsEnvironRule {
    pub fn new() -> Self {
        Self
    }
}

/// 文 1 つの式を辿り、当たりを積む。
struct EnvironReads<'a> {
    file_path: &'a str,
    /// file の import 文から作った、環境変数の読みを指す名の表。
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

    /// 文の自分の式(入れ子の文の本体は除く — each_statement が 1 つずつ渡す)。
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
            // os.environ["KEY"](別名の x.environ["KEY"]・e["KEY"] も)
            Expr::Subscript(subscript) => {
                if self.names.is_environ(&subscript.value) {
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
            // os.environ.get() / os.getenv()(別名の x.environ.get()・e.get()・x.getenv()・g() も)
            Expr::Call(call) => {
                if let Expr::Attribute(attr) = &*call.func {
                    if self.names.is_environ(&attr.value) {
                        self.hit(
                            format!(
                                "Calling os.environ.{}() is forbidden. \
                                 Use dependency injection to receive configuration values.",
                                attr.attr
                            ),
                            call.range.start().to_usize(),
                        );
                    }
                }
                if self.names.is_getenv(&call.func) {
                    self.hit(
                        "os.getenv() is forbidden. \
                         Use dependency injection to receive configuration values."
                            .to_string(),
                        call.range.start().to_usize(),
                    );
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

    /// 名の表を file の import 文から 1 度だけ作るので、module 全体を見る(本体は file に 1 度だけ当てる — 文ごとに当てると、
    /// 文の数だけ表を作り直す)。
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

    /// 規則を、linter 本体(lib の lint_source)と同じ単位で当てる — 規則の見る単位(reach)どおりに文を渡す
    /// (module なら file に 1 度・文なら入れ子の文まで 1 つずつ・部分木なら上の段の文だけ)。
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

    /// 当たりの文(message)の並び。
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

    /// 入れ子の文(関数・class の method)の中の読みは 1 度ずつ数える(2 度数えない)。本体の再帰を通した数は lib の検
    /// (関数の中の読みは 1 度だけ)も見る。
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

    // 別名の読み(agora-redesign #3012)— 直す前は名が文字どおり `os` の時だけ見ていて、どれも 0 件だった。
    // 別名でも当たりの文は素の os.environ / os.getenv の時と同じ。

    /// `import os as x` の 3 形(x.environ.get()・x.environ[...]・x.getenv())。
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

    /// `from os import environ` の素の名 environ(environ.get()・environ[...])。
    #[test]
    fn test_reads_through_from_os_import_environ_are_seen() {
        let code = r#"
from os import environ
a = environ.get("A")
b = environ["B"]
"#;
        assert_eq!(messages(code), vec![ENVIRON_GET, ENVIRON_SUBSCRIPT]);
    }

    /// `from os import environ as e` の別名 e(e.get()・e[...])。
    #[test]
    fn test_reads_through_from_os_import_environ_as_alias_are_seen() {
        let code = r#"
from os import environ as e
a = e.get("A")
b = e["B"]
"#;
        assert_eq!(messages(code), vec![ENVIRON_GET, ENVIRON_SUBSCRIPT]);
    }

    /// `from os import getenv` の素の名 getenv と、`from os import getenv as g` の別名 g。
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

    /// 関数の中の import 文の別名も、その関数の中の読み(と、表は file の全体で 1 つなので file の他の所の読み)に当たる。
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

    /// os を指さない名は当たらない: 別の module の environ・getenv、os 以外の module の別名の environ 属性、
    /// 相対の `from .os import environ`、`import os.path as p` の p、"environ" を鍵に持つ辞書。
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
