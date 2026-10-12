//! DOEFF148: 書いてよい file を決めた綴り(agora-redesign #1373・#1436 — 元は agora-controllers の一時の判定
//! controllers/screen/tests/intake_only_rules.hy の 3 の一部・4・6・7 の語・8 の前半・9)。
//!
//! architecture.hy の `:confined-spellings [(confined-spelling "名" :patterns [r"…"] :files [..] :except [..] :why "…") …]` の群ごとに、
//! `:files` に当たり `:except` に当たらない Hy・Python の file を読み、註を落とした本文(文字列は残す)に `:patterns` のどれかが
//! 当たれば file ごとに 1 件出す(当たった数を message に書く・位置は最初の当たり)。`:except` はこの綴りを書いてよい file で、
//! 空なら `:files` のどこにも書かない綴り。`:files` に当たる file が 1 つも無ければ architecture.hy の位置で 1 件(`missing` —
//! 母集団 0 を緑にしない)。
//! DOEFF146(:single-point-vocabulary)と違い、文字列の中も数え、Python の file も読む — 外の口の動詞 `"POST"`・route の綴り
//! `"/api/intake"`・effect の宣言の file(effects.py)は、文字列か Python の中に在る。
//!
//! 群の綴りに結んだ名(`:names-bound-in [<path> …]`): 名指した Hy の file の中で、群の綴りに当たる値に結んだ名も群の綴りにする —
//! 宣言の `:environ {"名" "値" …}` の組の名(doeff-cluster の job の宣言 — 値は参照の綴り)と、最上位の `(val 名 "値")`・`(setv 名 "値")`
//! の名(と Python へ mangle した名)。足した名に当たる値に結んだ名も、増えなくなるまで足す(宣言の参照の名 → それを持つ定数の名)。
//! 秘密の置き場を指す参照の名を群が正規表現の写しで持つと、新しい参照の名(キーの入れ替えの旧いキーの参照など)を宣言に足しても群が
//! 守らず、他の file で読んでも赤にならない(agora-controllers の群 web-push-private-key・設計検証 lt-T1R7826ZQ8C78H02ZGKEASNRF0 の盲検 A)。
//! 導くのは architecture.hy を読む時(architecture.rs の names_bound_in)で、この file は結びの読みと導きの純な部品を持つ。

use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader, StrKind};
use doeff_indexer::hy_index::Range;

use super::architecture::ConfinedSpelling;
use super::names::hy_mangle;
use super::spelling_scope::{code_text, line_of, selected_files};

/// 結びの形(閉じた 2 つ)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BindingForm {
    /// 宣言の `:environ {"名" "値" …}` の組(名は文字列 — 環境変数の名・値は文字列の literal)。
    Environ,
    /// 最上位の `(val 名 値)` / `(setv 名 値)`(名は Hy の記号 — Python の名へ mangle した綴りも数える・値は form の綴り)。
    Constant,
}

/// 名と値の結び 1 つ(:names-bound-in の file の中)。value は当てる綴り — :environ の組は文字列の値、定数は値の form の註を落とした
/// 綴り(参照の名の列 `#("…" "…")`・組み立て `(+ 名 "…")` も当てる — 文の綴りで判じる DOEFF148 と同じ読み)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Binding {
    pub name: String,
    pub value: String,
    pub form: BindingForm,
    /// 結びの在る file(repo の根からの path)。
    pub origin: String,
}

/// 群の綴りに結んだ名 1 つと、その名の綴りの正規表現(Hy の記号は mangle した名も)と、結びの在る file。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BoundName {
    pub name: String,
    pub patterns: Vec<String>,
    pub origin: String,
}

/// Hy の file rel の source の結びを読む(宣言の `:environ` の組はどの深さでも・`(val|setv 名 値)` は最上位だけ — 関数の中の束縛の名は
/// その関数の外で意味を持たない)。註は落としてから読む。括弧か文字列が閉じていなければ None。
pub fn bindings_of(rel: &str, source: &str) -> Option<Vec<Binding>> {
    let code = code_text(rel, source);
    let mut reader = Reader::new(&code, 0, code.len());
    let forms = reader.read_all();
    if !reader.issues.is_empty() {
        return None;
    }
    let mut out = Vec::new();
    for form in &forms {
        if let Some(binding) = constant_of(rel, &code, form) {
            out.push(binding);
        }
        environ_pairs(rel, &code, form, &mut out);
    }
    Some(out)
}

/// 群の綴り patterns(正規表現)に当たる値に結んだ名を、結びの順に導く(足した名の綴りに当たる値に結んだ名も、増えなくなるまで)。
pub fn names_bound_to(patterns: &[String], bindings: &[Binding]) -> Vec<BoundName> {
    let mut regexes: Vec<regex::Regex> = patterns.iter().filter_map(|p| regex::Regex::new(p).ok()).collect();
    let mut out: Vec<BoundName> = Vec::new();
    loop {
        let mut grew = false;
        for binding in bindings {
            if out.iter().any(|n| n.name == binding.name) || !regexes.iter().any(|re| re.is_match(&binding.value)) {
                continue;
            }
            let mut spellings = vec![binding.name.clone()];
            match binding.form {
                BindingForm::Constant => {
                    let mangled = hy_mangle(&binding.name);
                    if mangled != binding.name {
                        spellings.push(mangled);
                    }
                }
                BindingForm::Environ => {}
            }
            let found: Vec<String> = spellings.iter().map(|s| literal_pattern(s)).collect();
            regexes.extend(found.iter().filter_map(|p| regex::Regex::new(p).ok()));
            out.push(BoundName { name: binding.name.clone(), patterns: found, origin: binding.origin.clone() });
            grew = true;
        }
        if !grew {
            return out;
        }
    }
}

/// 名の綴りに当たる正規表現(端が語の字なら `\b` を付ける — `KEY_REF` は `KEY_REFS` に当たらない。Hy の `-` は語の字でないので、
/// `KEY-REF-ENV` は `MY-KEY-REF-ENV` にも当たる — 手で書いた `\bKEY-REF-ENV\b` の写しと同じ広さ。広く当たる向きは赤で見え、黙らない)。
fn literal_pattern(name: &str) -> String {
    let word = |c: char| c.is_alphanumeric() || c == '_';
    let head = if name.chars().next().is_some_and(word) { r"\b" } else { "" };
    let tail = if name.chars().last().is_some_and(word) { r"\b" } else { "" };
    format!("{}{}{}", head, regex::escape(name), tail)
}

/// form の綴り。
fn text<'s>(source: &'s str, form: &Form) -> &'s str {
    source.get(form.span.start..form.span.end).unwrap_or("")
}

/// 文字列の literal の値(普通の文字列・r"…"・bracket 文字列 — f-string と bytes は値が決まらないので読まない)。
fn string_of(source: &str, form: &Form) -> Option<String> {
    match &form.node {
        Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, body } => source.get(body.start..body.end).map(|t| t.replace("\\\"", "\"")),
        Node::Str { kind: StrKind::Format | StrKind::Bytes | StrKind::FormatBracket, .. }
        | Node::Seq { .. }
        | Node::Symbol
        | Node::Keyword
        | Node::Number
        | Node::Prefixed { .. }
        | Node::Annotated { .. }
        | Node::Discarded
        | Node::Tagged { .. } => None,
    }
}

/// 読み捨てを除いた列の中身(列でなければ None)。
fn live_items(form: &Form) -> Option<Vec<&Form>> {
    match &form.node {
        Node::Seq { items, .. } => Some(items.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect()),
        Node::Symbol
        | Node::Keyword
        | Node::Str { .. }
        | Node::Number
        | Node::Prefixed { .. }
        | Node::Annotated { .. }
        | Node::Discarded
        | Node::Tagged { .. } => None,
    }
}

/// 最上位の `(val 名 値)` / `(setv 名 値)`(名に型の注記 `#^ T 名` が付いても読む・値は form の綴り)。
fn constant_of(rel: &str, source: &str, form: &Form) -> Option<Binding> {
    let items = form.paren_items()?.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect::<Vec<_>>();
    let [head, name, value] = items.as_slice() else { return None };
    if !matches!(head.node, Node::Symbol) || !matches!(text(source, head), "val" | "setv") {
        return None;
    }
    let name = match &name.node {
        Node::Symbol => text(source, name),
        Node::Annotated { target: Some(target), .. } if matches!(target.node, Node::Symbol) => text(source, target),
        Node::Annotated { .. }
        | Node::Seq { .. }
        | Node::Keyword
        | Node::Str { .. }
        | Node::Number
        | Node::Prefixed { .. }
        | Node::Discarded
        | Node::Tagged { .. } => return None,
    };
    Some(Binding { name: name.to_string(), value: text(source, value).to_string(), form: BindingForm::Constant, origin: rel.to_string() })
}

/// form の中の `:environ {"名" "値" …}` の組をどの深さでも集める(doeff-cluster の job の宣言の行 — defsystem の :environ は文字列の
/// 鍵と文字列の値の dict の literal だけを受ける)。
fn environ_pairs(rel: &str, source: &str, form: &Form, out: &mut Vec<Binding>) {
    let Some(items) = live_items(form) else {
        return;
    };
    for (index, item) in items.iter().enumerate() {
        let is_environ = matches!(item.node, Node::Keyword) && text(source, item) == ":environ";
        if let (true, Some(Node::Seq { delim: Delim::Brace, items: entries })) = (is_environ, items.get(index + 1).map(|f| &f.node)) {
            let entries: Vec<&Form> = entries.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect();
            for pair in entries.chunks(2) {
                if let [key, value] = pair {
                    if let (Some(name), Some(value)) = (string_of(source, key), string_of(source, value)) {
                        out.push(Binding { name, value, form: BindingForm::Environ, origin: rel.to_string() });
                    }
                }
            }
        }
        environ_pairs(rel, source, item, out);
    }
}

/// 当たりの種類(閉じた 2 つ)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfinedProblem {
    /// 書いてよい file の外に count 件在る。
    Outside { count: usize },
    /// `:files` に当たる file が無い。
    Missing,
}

/// 当たり 1 つ(file × 群)。
#[derive(Debug, Clone)]
pub struct ConfinedFinding {
    pub rel: String,
    /// 群の名(登録簿の鍵の細目 — missing は `<群>:missing`)。
    pub group: String,
    pub why: String,
    pub range: Range,
    pub problem: ConfinedProblem,
    /// 最初の当たりの綴り(missing は None — 導いた名で赤になった時に、どの名かを知らせに書く)。
    pub first: Option<String>,
    /// 群の :names-bound-in から導いた名(知らせの説明に書く — 書いていない名がなぜ群の綴りかを architecture.hy の外から読めるように)。
    pub bound: Vec<String>,
}

/// 群ごとに `:except` の外の当たりを探す(群の宣言の順・path の順)。読めなかった file は errors へ積む。focus(命令の行の名指し)が
/// 在れば、その下の file だけを読む — 当たりは file ごとに 1 件でその file に付くので、答えは全部を読んで名指しで絞った時と同じ
/// (母集団 0 の知らせは glob で決まり、読まない)。1 file の commit の hook で群ごとに母集団を全部読んでいた(agora-redesign #1418)。
pub fn find(root: &Path, groups: &[ConfinedSpelling], architecture_rel: &str, focus: Option<&[PathBuf]>) -> (Vec<ConfinedFinding>, Vec<String>) {
    let mut out = Vec::new();
    let mut errors = Vec::new();
    for group in groups {
        let regexes: Vec<regex::Regex> = group.patterns.iter().filter_map(|p| regex::Regex::new(p).ok()).collect();
        let population = selected_files(root, &group.files, &[]);
        if population.is_empty() {
            out.push(ConfinedFinding {
                rel: architecture_rel.to_string(),
                group: group.name.clone(),
                why: group.why.clone(),
                range: group.range,
                problem: ConfinedProblem::Missing,
                first: None,
                bound: group.bound_names.clone(),
            });
            continue;
        }
        for rel in population
            .iter()
            .filter(|rel| !group.except.iter().any(|p| super::paths::glob_matches(p, rel)))
            .filter(|rel| focus.is_none_or(|only| only.iter().any(|p| root.join(rel.as_str()).starts_with(p))))
        {
            let source = match std::fs::read_to_string(root.join(rel)) {
                Ok(source) => source,
                Err(error) => {
                    errors.push(format!("{}: 読めない: {}", rel, error));
                    continue;
                }
            };
            let code = code_text(rel, &source);
            let mut hits: Vec<(usize, &str)> = regexes.iter().flat_map(|re| re.find_iter(&code).map(|m| (m.start(), m.as_str()))).collect();
            if hits.is_empty() {
                continue;
            }
            hits.sort_unstable();
            hits.dedup_by_key(|(start, _)| *start);
            let line = line_of(&code, hits[0].0);
            let at = doeff_indexer::hy_index::Position { line, character: 0 };
            out.push(ConfinedFinding {
                rel: rel.clone(),
                group: group.name.clone(),
                why: group.why.clone(),
                range: Range { start: at, end: at },
                problem: ConfinedProblem::Outside { count: hits.len() },
                first: Some(hits[0].1.to_string()),
                bound: group.bound_names.clone(),
            });
        }
    }
    (out, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn group(patterns: &[&str], files: &[&str], except: &[&str]) -> ConfinedSpelling {
        ConfinedSpelling {
            name: "g".to_string(),
            patterns: patterns.iter().map(|p| p.to_string()).collect(),
            files: files.iter().map(|p| p.to_string()).collect(),
            except: except.iter().map(|p| p.to_string()).collect(),
            names_bound_in: Vec::new(),
            bound_names: Vec::new(),
            why: "理由".to_string(),
            range: Range::default(),
        }
    }

    /// 宣言の `:environ` の組(文字列の値)はどの深さでも、`(val|setv 名 値)`(値は form の綴り・註は落とす)は最上位だけを読む。
    #[test]
    fn bindings_are_environ_pairs_and_top_level_constants() {
        let source = "(defsystem s [f]\n  \"系\"\n  (job (job f) :replicas 1 :environ {\"KEY_REF\" \"secret:ns/vapid/k\" \"PORT\" \"8872\"}))\n\
                      (val KEY-REF-ENV \"KEY_REF\")\n(setv #^ str TYPED \"KEY_REF\")\n(val REFS #(KEY-REF-ENV ; 註の KEY_REF\n  \"KEY_REF\"))\n\
                      (defk read [] (val local \"KEY_REF\") local)\n";
        let names: Vec<(String, String, BindingForm, String)> =
            bindings_of("s/keys.hy", source).unwrap().into_iter().map(|b| (b.name, b.value, b.form, b.origin)).collect();
        let at = |name: &str, value: &str, form: BindingForm| (name.to_string(), value.to_string(), form, "s/keys.hy".to_string());
        // 註は byte の数だけの空白になる(spelling_scope::code_text — 位置を保つ)。
        let refs = format!("#(KEY-REF-ENV {}\n  \"KEY_REF\")", " ".repeat("; 註の KEY_REF".len()));
        assert_eq!(
            names,
            vec![
                at("KEY_REF", "secret:ns/vapid/k", BindingForm::Environ),
                at("PORT", "8872", BindingForm::Environ),
                at("KEY-REF-ENV", "\"KEY_REF\"", BindingForm::Constant),
                at("TYPED", "\"KEY_REF\"", BindingForm::Constant),
                at("REFS", &refs, BindingForm::Constant),
            ]
        );
        assert_eq!(bindings_of("s/keys.hy", "(val A \"x\""), None);
    }

    /// 群の綴りに当たる値に結んだ名を導き、導いた名に当たる値に結んだ名も導く(宣言の参照の名 → それを持つ定数の名と mangle した名 →
    /// その定数を並べた列・組み立てた名 — 盲検の後の見直しで、値が literal の定数だけを読むと参照の列の名が黙って抜けると分かった)。
    /// 名は語の境で当てる(KEY_REF は KEY_REFS に当たらない)。
    #[test]
    fn names_bound_to_the_spelling_are_derived_until_none_grow() {
        let source = "(val KEY-REF-ENV \"KEY_REF\")\n(val OTHER-ENV \"PORT\")\n(val REFS #(KEY-REF-ENV))\n\
                      (val OLD-REF-ENV (+ KEY-REF-ENV \"_OLD\"))\n\
                      (defsystem s [f] \"系\" (job (job f) :replicas 1 :environ {\"KEY_REF\" \"secret:ns/vapid/k\" \"PORT\" \"8872\"}))\n";
        let bindings = bindings_of("s/keys.hy", source).unwrap();
        let derived = names_bound_to(&[r"vapid".to_string()], &bindings);
        let bound = |name: &str, patterns: &[&str]| BoundName {
            name: name.to_string(),
            patterns: patterns.iter().map(|p| p.to_string()).collect(),
            origin: "s/keys.hy".to_string(),
        };
        assert_eq!(
            derived,
            vec![
                bound("KEY_REF", &[r"\bKEY_REF\b"]),
                bound("KEY-REF-ENV", &[r"\bKEY\-REF\-ENV\b", r"\bKEY_REF_ENV\b"]),
                bound("REFS", &[r"\bREFS\b"]),
                bound("OLD-REF-ENV", &[r"\bOLD\-REF\-ENV\b", r"\bOLD_REF_ENV\b"]),
            ]
        );
        let all: Vec<regex::Regex> = derived.iter().flat_map(|n| n.patterns.iter()).map(|p| regex::Regex::new(p).unwrap()).collect();
        assert!(all.iter().any(|re| re.is_match("(supplied-setting KEY-REF-ENV)")));
        assert!(!all.iter().any(|re| re.is_match("KEY_REFSET は別の名")));
        assert_eq!(names_bound_to(&[r"nowhere".to_string()], &bindings), vec![]);
    }

    #[test]
    fn a_spelling_in_a_string_is_counted_but_not_in_a_comment() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("s")).unwrap();
        std::fs::write(dir.path().join("s/a.hy"), "; \"/api/intake\" は退いた\n(setv u \"/api/intake\")\n").unwrap();
        std::fs::write(dir.path().join("s/b.py"), "# /api/intake\nx = 1\n").unwrap();
        let (hits, errors) = find(dir.path(), &[group(&[r"/api/intake"], &["s/**"], &[])], "architecture.hy", None);
        assert!(errors.is_empty(), "{:?}", errors);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "s/a.hy");
        assert_eq!(hits[0].range.start.line, 1);
        assert_eq!(hits[0].problem, ConfinedProblem::Outside { count: 1 });
    }

    #[test]
    fn the_except_files_may_hold_the_spelling() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("s")).unwrap();
        std::fs::write(dir.path().join("s/a.hy"), "(PostIntake x)\n(PostIntake y)\n").unwrap();
        std::fs::write(dir.path().join("s/b.hy"), "(PostIntake x)\n").unwrap();
        let (hits, _) = find(dir.path(), &[group(&[r"\bPostIntake\b"], &["s/**"], &["s/b.hy"])], "architecture.hy", None);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "s/a.hy");
        assert_eq!(hits[0].problem, ConfinedProblem::Outside { count: 2 });
    }

    #[test]
    fn an_empty_population_is_missing_not_green() {
        let dir = tempfile::tempdir().unwrap();
        let (hits, _) = find(dir.path(), &[group(&[r"x"], &["gone/**"], &[])], "architecture.hy", None);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "architecture.hy");
        assert_eq!(hits[0].problem, ConfinedProblem::Missing);
    }

    #[test]
    fn named_paths_read_only_the_named_files() {
        // agora-redesign #1418: 名指しの下の file だけを読む — 名指しの外の読めない file(UTF-8 でない)を読まず、当たりは名指しの分だけ。
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("s")).unwrap();
        std::fs::write(dir.path().join("s/a.hy"), "(PostIntake x)\n").unwrap();
        std::fs::write(dir.path().join("s/b.hy"), "(PostIntake y)\n").unwrap();
        std::fs::write(dir.path().join("s/broken.hy"), [0xff_u8, 0xfe]).unwrap();
        let groups = [group(&[r"\bPostIntake\b"], &["s/**"], &[])];
        let (all, all_errors) = find(dir.path(), &groups, "architecture.hy", None);
        assert_eq!(all.iter().map(|h| h.rel.as_str()).collect::<Vec<_>>(), vec!["s/a.hy", "s/b.hy"]);
        assert_eq!(all_errors.len(), 1, "{:?}", all_errors);
        let named = [dir.path().join("s/a.hy")];
        let (hits, errors) = find(dir.path(), &groups, "architecture.hy", Some(&named));
        assert!(errors.is_empty(), "{:?}", errors);
        assert_eq!(hits.iter().map(|h| h.rel.as_str()).collect::<Vec<_>>(), vec!["s/a.hy"]);
    }
}
