//! 群の綴りに結んだ名(DOEFF148 の `:names-bound-in [<path> …]`)— 名指した Hy の file の中で、群の綴りに当たる値に結んだ名も群の
//! 綴りにする部品。結び = 宣言の `:environ {"名" "値" …}` の組の名(doeff-cluster の job の宣言 — 値は参照の綴り)と、最上位の
//! `(val 名 値)`・`(setv 名 値)` の名(と Python へ mangle した名)。足した名に当たる値に結んだ名も、増えなくなるまで足す(宣言の参照の名
//! → それを持つ定数の名 → その定数を並べた列)。
//! 秘密の置き場を指す参照の名を群が正規表現の写しで持つと、新しい参照の名(キーの入れ替えの旧いキーの参照など)を宣言に足しても群が
//! 守らず、他の file で読んでも赤にならない(agora-controllers の群 web-push-private-key)。
//! 導くのは architecture.hy を読む時(architecture.rs の names_bound_in)で、この file は結びの読みと導きの純な部品だけを持つ
//! (architecture に頼らない — DOEFF148 の判じ confined_spellings.rs は architecture の群を読むので、ここに置くと依存が輪になる)。

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader, StrKind};

use super::names::hy_mangle;
use super::spelling_scope::code_text;

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

#[cfg(test)]
mod tests {
    use super::*;

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
}
