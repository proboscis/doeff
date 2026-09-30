//! DOEFF106 の Python の枝: 層の置き場の `.py` の file の環境変数の読み(`os.environ`・`os.getenv`)を見つける(agora-redesign #1907・
//! #1898 の子 (b))。Hy の file の生の副作用は Hy の定義の索引(hy_index の raw_catalog)が集めるが、Python の file はその索引の外で、
//! 業務の層の `.py` が環境変数を読んでも何も当たらなかった。
//!
//! 読むのは Python の字句(rustpython の lexer)だけ — 文字列と註の中は別の字句なので数えない。当てる形:
//!   * `os.environ`・`os.getenv`(`import os as o` の別名 `o.environ` も)
//!   * `from os import environ` / `getenv`(`as` の別名も)の後の素の名 `environ`・`getenv`
//!
//! 環境変数のほかの生の副作用(file・時計・network)は、ここでは当てない — 範囲を環境変数の読みに絞る(#1907 の決め — 広げると当たりが
//! 増えうるので、要る時に別の変更で足す)。当たった位置を包む関数の名は構文木の関数の範囲から引く(無ければ module の直下)。

use std::collections::{BTreeMap, BTreeSet};

use rustpython_ast::{Mod, Ranged, Stmt};
use rustpython_parser::lexer::lex;
use rustpython_parser::{parse, Mode, Tok};

/// 環境変数を読む `os` の属性の名。
const ENV_NAMES: &[&str] = &["environ", "getenv"];

/// 見つけた環境変数の読み 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnvRead {
    /// source の byte の位置(始まり・終わり)。
    pub start: usize,
    pub end: usize,
    /// 読みの綴り(`os.environ`・`os.getenv`)。
    pub name: String,
    /// 包む関数の名(`外.内` — 無ければ `<module>`)。
    pub definition: String,
}

/// 関数の範囲と名(入れ子は `外.内`)を集めるため。
fn functions(body: &[Stmt], prefix: &str, out: &mut Vec<(usize, usize, String)>) {
    for statement in body {
        let (name, inner) = match statement {
            Stmt::FunctionDef(node) => (node.name.to_string(), &node.body),
            Stmt::AsyncFunctionDef(node) => (node.name.to_string(), &node.body),
            Stmt::ClassDef(node) => (node.name.to_string(), &node.body),
            _ => continue,
        };
        let full = if prefix.is_empty() { name } else { format!("{}.{}", prefix, name) };
        let range = statement.range();
        out.push((range.start().to_usize(), range.end().to_usize(), full.clone()));
        functions(inner, &full, out);
    }
}

/// 位置を包むいちばん内側の関数の名を返すため。
fn enclosing(spans: &[(usize, usize, String)], at: usize) -> String {
    spans
        .iter()
        .filter(|(start, end, _)| *start <= at && at < *end)
        .max_by_key(|(start, _, _)| *start)
        .map(|(_, _, name)| name.clone())
        .unwrap_or_else(|| "<module>".to_string())
}

/// `.py` の source の環境変数の読みを見つけるため(字句に読めない file は空 — 構文の誤りは別の規則が出す)。
pub fn env_reads(source: &str) -> Vec<EnvRead> {
    let Ok(tokens) = lex(source, Mode::Module).collect::<Result<Vec<_>, _>>() else {
        return Vec::new();
    };
    let mut spans = Vec::new();
    if let Ok(Mod::Module(module)) = parse(source, Mode::Module, "<module>") {
        functions(&module.body, "", &mut spans);
    }
    let name_of = |i: usize| match tokens.get(i) {
        Some((Tok::Name { name }, _)) => Some(name.as_str()),
        _ => None,
    };
    // `import os as o` の別名と、`from os import environ as e` の素の名の別名を先に集める。
    let mut os_names: BTreeSet<String> = BTreeSet::from(["os".to_string()]);
    let mut bare: BTreeMap<String, String> = BTreeMap::new();
    let mut in_import = BTreeSet::new();
    for i in 0..tokens.len() {
        match &tokens[i].0 {
            Tok::Import if name_of(i + 1) == Some("os") && matches!(tokens.get(i + 2), Some((Tok::As, _))) => {
                if let Some(alias) = name_of(i + 3) {
                    os_names.insert(alias.to_string());
                }
            }
            Tok::From if name_of(i + 1) == Some("os") && matches!(tokens.get(i + 2), Some((Tok::Import, _))) => {
                let mut j = i + 3;
                while let Some((token, _)) = tokens.get(j) {
                    if matches!(token, Tok::Newline | Tok::Semi) {
                        break;
                    }
                    in_import.insert(j);
                    if let Some(name) = name_of(j).filter(|n| ENV_NAMES.contains(n)) {
                        let alias = if matches!(tokens.get(j + 1), Some((Tok::As, _))) { name_of(j + 2) } else { None };
                        bare.insert(alias.unwrap_or(name).to_string(), name.to_string());
                        if alias.is_some() {
                            in_import.insert(j + 2);
                        }
                    }
                    j += 1;
                }
            }
            _ => {}
        }
    }
    let mut out = Vec::new();
    for i in 0..tokens.len() {
        if in_import.contains(&i) {
            continue;
        }
        let Some(name) = name_of(i) else { continue };
        let after_dot = i > 0 && matches!(tokens[i - 1].0, Tok::Dot);
        let range = tokens[i].1;
        if os_names.contains(name) && !after_dot && matches!(tokens.get(i + 1), Some((Tok::Dot, _))) {
            if let Some(attribute) = name_of(i + 2).filter(|a| ENV_NAMES.contains(a)) {
                let end = tokens[i + 2].1.end().to_usize();
                out.push(EnvRead {
                    start: range.start().to_usize(),
                    end,
                    name: format!("os.{}", attribute),
                    definition: enclosing(&spans, range.start().to_usize()),
                });
            }
        } else if let Some(original) = bare.get(name).filter(|_| !after_dot) {
            out.push(EnvRead {
                start: range.start().to_usize(),
                end: range.end().to_usize(),
                name: format!("os.{}", original),
                definition: enclosing(&spans, range.start().to_usize()),
            });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(source: &str) -> Vec<(String, String)> {
        env_reads(source).into_iter().map(|r| (r.name, r.definition)).collect()
    }

    #[test]
    fn os_environ_and_getenv_are_found_with_their_function() {
        let source = "import os\n\ndef mode():\n    return os.environ[\"MODE\"]\n\nclass C:\n    def f(self):\n        return os.getenv(\"X\")\n";
        assert_eq!(names(source), vec![("os.environ".into(), "mode".into()), ("os.getenv".into(), "C.f".into())]);
    }

    #[test]
    fn aliases_and_bare_imports_are_found() {
        let source = "import os as o\nfrom os import environ, getenv as ge\nA = o.environ.get(\"A\")\nB = environ[\"B\"]\nC = ge(\"C\")\n";
        assert_eq!(
            names(source),
            vec![("os.environ".into(), "<module>".into()), ("os.environ".into(), "<module>".into()), ("os.getenv".into(), "<module>".into())]
        );
    }

    #[test]
    fn strings_comments_and_other_attributes_are_not_found() {
        // 反例: 文字列・註の中の綴り・os の別の属性・別の物の environ 属性は環境変数の読みではない。
        let source = "import os\n# os.environ を読まない\nS = \"os.getenv\"\nP = os.path.join(\"a\", \"b\")\nQ = request.environ\n";
        assert!(names(source).is_empty(), "{:?}", names(source));
    }
}
