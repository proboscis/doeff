//! DOEFF150・151: 使わないと決めた綴りと呼び(agora-redesign #1193 — agora-controllers の check_vocabulary・check_controller_clock の
//! 移し先)。
//!
//! 語と呼びの表は repo の architecture.hy の `:retired-words`・`:retired-calls` にだけ在り、ここには書かない。判定は file 1 つを
//! 読めば決まるので repo 全体の索引を組まない — 名指しの path(`focus`)が在ればその下の file だけを読み、無ければ宣言の glob の
//! 頭の dir だけを歩く。
//!   * DOEFF150 `:in lines` — 行ごとに、語として単独で在る :words(前後が英字・`_`・`-` でない所)と :patterns の正規表現。
//!     :rule-lines の綴りを含む行(規則そのものを述べる行)は数えない。註・文字列・文書も数える(語の規則は文書にも効く)。
//!   * DOEFF150 `:in names` — 定義の名だけ(Hy は `def…` の形と `setv`・`val`・`var` の左辺・Python は def と class の名)。
//!   * DOEFF151 — Hy の file の `(呼び …)` の形の呼び(頭の記号が :calls のどれか)。註・文字列・`#_` で読み捨てた form は数えない。

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use doeff_indexer::hy_index::reader::{Form, Node, Reader};
use rayon::prelude::*;
use regex::Regex;
use walkdir::WalkDir;

use super::architecture::{RetiredCalls, RetiredWords, WordPlace};
use super::relative_path;

/// 歩かない dir(隠し dir と生成物 — 宣言の glob の頭の dir より下で)。
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// 使わないと決めた綴りの当たり 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WordHit {
    pub rel: String,
    /// 当たった綴りの byte の範囲。
    pub start: usize,
    pub end: usize,
    pub group: String,
    /// 当たった綴り(語か、正規表現に当たった字面)。
    pub spelling: String,
    /// 登録簿の鍵の細目(:words は語・:patterns は群の名)。
    pub detail: String,
    pub instead: String,
    pub place: WordPlace,
    /// :in names の時の定義の名。
    pub name: Option<String>,
}

/// 使わないと決めた呼びの当たり 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallHit {
    pub rel: String,
    pub start: usize,
    pub end: usize,
    pub group: String,
    pub call: String,
    pub instead: String,
}

/// 読んだ file 1 つの当たり(読めなければ理由)。
pub struct FileHits {
    pub rel: String,
    pub path: PathBuf,
    pub source: String,
    pub words: Vec<WordHit>,
    pub calls: Vec<CallHit>,
}

/// glob を repo の根に錨を下ろして当てる(`**` は 0 個以上の段・`*` は段の中の任意の綴り・`/` の無い型は根の直下の file)。
fn anchored(glob: &str, rel: &str) -> bool {
    let parts: Vec<&str> = glob.split('/').filter(|p| !p.is_empty()).collect();
    let path: Vec<&str> = rel.split('/').collect();
    super::segments_match(&parts, &path)
}

fn selected(rel: &str, files: &[String], except: &[String]) -> bool {
    files.iter().any(|g| anchored(g, rel)) && !except.iter().any(|g| anchored(g, rel))
}

/// glob の頭の、`*` を含まない段(歩き始める所)。
fn literal_head(glob: &str) -> String {
    glob.split('/').filter(|p| !p.is_empty()).take_while(|p| !p.contains('*')).collect::<Vec<_>>().join("/")
}

/// path の下の file(path が file ならそれだけ)。隠し dir と生成物の dir は歩かない。
fn files_under(path: &Path) -> Vec<PathBuf> {
    if path.is_file() {
        return vec![path.to_path_buf()];
    }
    if !path.is_dir() {
        return Vec::new();
    }
    WalkDir::new(path)
        .follow_links(false)
        .into_iter()
        .filter_entry(|entry| {
            let name = entry.file_name().to_string_lossy();
            entry.depth() == 0 || !entry.file_type().is_dir() || !(name.starts_with('.') || SKIPPED_DIRS.contains(&name.as_ref()))
        })
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .map(|entry| entry.into_path())
        .collect()
}

/// 判じる file の候補(根からの path の順・重なりなし)— focus が在ればその下だけ、無ければ宣言の glob の頭の dir だけを歩く。
fn candidates(root: &Path, words: &[RetiredWords], calls: &[RetiredCalls], focus: Option<&[PathBuf]>) -> Vec<(String, PathBuf)> {
    let starts: Vec<PathBuf> = match focus {
        Some(paths) => paths.to_vec(),
        None => {
            let globs = words.iter().flat_map(|g| g.files.iter()).chain(calls.iter().flat_map(|g| g.files.iter()));
            globs.map(|g| root.join(literal_head(g))).collect()
        }
    };
    let mut found: Vec<(String, PathBuf)> = starts
        .iter()
        .flat_map(|start| files_under(start))
        .filter_map(|path| relative_path(root, &path))
        .map(|rel| {
            let path = root.join(&rel);
            (rel, path)
        })
        .collect();
    found.sort();
    found.dedup_by(|a, b| a.0 == b.0);
    found
}

/// 語として単独で在るか(前後の文字が英字・`_`・`-` でない)。
fn stands_alone(line: &str, at: usize, word: &str) -> bool {
    let glued = |c: char| c.is_ascii_alphabetic() || c == '_' || c == '-';
    let before = line[..at].chars().next_back().is_some_and(glued);
    let after = line[at + word.len()..].chars().next().is_some_and(glued);
    !before && !after
}

/// 綴り text の中の、語 word が単独で在る最初の位置。
fn find_word(text: &str, word: &str) -> Option<usize> {
    text.match_indices(word).map(|(at, _)| at).find(|&at| stands_alone(text, at, word))
}

/// 群を 1 つの行(か定義の名)に当てる。当たりごとに (相対の開始・終了・綴り・細目)。
fn match_group(text: &str, group: &RetiredWords, patterns: &[Regex]) -> Vec<(usize, usize, String, String)> {
    let mut out = Vec::new();
    for word in &group.words {
        if let Some(at) = find_word(text, word) {
            out.push((at, at + word.len(), word.clone(), word.clone()));
        }
    }
    // 正規表現は群で 1 件(同じ行に 2 つの型が当たっても 1 行と数える — 鍵の細目が群の名なので、件数は行の数)。
    if let Some(m) = patterns.iter().find_map(|pattern| pattern.find(text)) {
        out.push((m.start(), m.end(), m.as_str().to_string(), group.name.clone()));
    }
    out
}

/// 行ごとの当たり(:in lines)。
fn line_hits(rel: &str, source: &str, group: &RetiredWords, patterns: &[Regex]) -> Vec<WordHit> {
    let mut out = Vec::new();
    let mut offset = 0;
    for line in source.split_inclusive('\n') {
        let body = line.trim_end_matches(['\n', '\r']);
        if !group.rule_lines.iter().any(|marker| body.contains(marker.as_str())) {
            for (start, end, spelling, detail) in match_group(body, group, patterns) {
                out.push(WordHit {
                    rel: rel.to_string(),
                    start: offset + start,
                    end: offset + end,
                    group: group.name.clone(),
                    spelling,
                    detail,
                    instead: group.instead.clone(),
                    place: WordPlace::Lines,
                    name: None,
                });
            }
        }
        offset += line.len();
    }
    out
}

/// Hy の form の木から定義の名の記号を集める(`def…` の形と `setv`・`val`・`var` の最初の名 — decorator の `[…]` と `#^ 型` は飛ばす)。
fn hy_definition_names(source: &str, form: &Form, out: &mut Vec<(usize, usize)>) {
    let text = |f: &Form| source.get(f.span.start..f.span.end).unwrap_or("");
    if let Some(items) = form.paren_items() {
        let items: Vec<&Form> = items.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect();
        let defines = items.first().is_some_and(|head| {
            matches!(head.node, Node::Symbol) && {
                let h = text(head);
                h.starts_with("def") || matches!(h, "setv" | "val" | "var")
            }
        });
        if defines {
            let name = items.iter().skip(1).find_map(|f| match &f.node {
                Node::Symbol => Some((f.span.start, f.span.end)),
                Node::Annotated { target: Some(target), .. } if matches!(target.node, Node::Symbol) => Some((target.span.start, target.span.end)),
                Node::Seq { .. } | Node::Annotated { .. } => None,
                _ => Some((0, 0)),
            });
            if let Some((start, end)) = name.filter(|(s, e)| e > s) {
                out.push((start, end));
            }
        }
    }
    match &form.node {
        Node::Seq { items, .. } => items.iter().for_each(|item| hy_definition_names(source, item, out)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => hy_definition_names(source, inner, out),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| hy_definition_names(source, part, out)),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
    }
}

/// 定義の名の範囲(Hy と Python。ほかの file は無し)。
fn definition_names(rel: &str, source: &str) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    if rel.ends_with(".hy") {
        let mut reader = Reader::new(source, 0, source.len());
        for form in &reader.read_all() {
            hy_definition_names(source, form, &mut out);
        }
    } else if rel.ends_with(".py") {
        static PYTHON_DEFINITION: OnceLock<Regex> = OnceLock::new();
        let python = PYTHON_DEFINITION.get_or_init(|| Regex::new(r"(?m)^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+([A-Za-z_][A-Za-z0-9_]*)").expect("固定の正規表現"));
        out.extend(python.captures_iter(source).filter_map(|c| c.get(1)).map(|m| (m.start(), m.end())));
    }
    out.sort();
    out
}

/// 定義の名の当たり(:in names)。
fn name_hits(rel: &str, source: &str, group: &RetiredWords, patterns: &[Regex]) -> Vec<WordHit> {
    let mut out = Vec::new();
    for (start, end) in definition_names(rel, source) {
        let name = &source[start..end];
        for (s, e, spelling, detail) in match_group(name, group, patterns) {
            out.push(WordHit {
                rel: rel.to_string(),
                start: start + s,
                end: start + e,
                group: group.name.clone(),
                spelling,
                detail,
                instead: group.instead.clone(),
                place: WordPlace::Names,
                name: Some(name.to_string()),
            });
        }
    }
    out
}

/// Hy の form の木から、頭の記号が calls のどれかの呼びを集める。
fn hy_calls(source: &str, form: &Form, group: &RetiredCalls, rel: &str, out: &mut Vec<CallHit>) {
    if let Some(head) = form.paren_items().and_then(|items| items.iter().find(|f| !matches!(f.node, Node::Discarded))) {
        let spelled = source.get(head.span.start..head.span.end).unwrap_or("");
        if matches!(head.node, Node::Symbol) && group.calls.iter().any(|c| c == spelled) {
            out.push(CallHit {
                rel: rel.to_string(),
                start: head.span.start,
                end: head.span.end,
                group: group.name.clone(),
                call: spelled.to_string(),
                instead: group.instead.clone(),
            });
        }
    }
    match &form.node {
        Node::Seq { items, .. } => items.iter().for_each(|item| hy_calls(source, item, group, rel, out)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => hy_calls(source, inner, group, rel, out),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| hy_calls(source, part, group, rel, out)),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
    }
}

/// 正規表現を組み終えた群(file ごとに組み直さない — agora の全体で 1 度だけ組む)。
pub struct Prepared<'a> {
    words: Vec<(&'a RetiredWords, Vec<Regex>)>,
    calls: &'a [RetiredCalls],
}

impl<'a> Prepared<'a> {
    pub fn new(words: &'a [RetiredWords], calls: &'a [RetiredCalls]) -> Prepared<'a> {
        // 読む時(architecture.rs)に確かめた正規表現なので、ここで読めないことは無い。
        let words = words.iter().map(|g| (g, g.patterns.iter().filter_map(|p| Regex::new(p).ok()).collect())).collect();
        Prepared { words, calls }
    }

    /// file rel をどれかの群が見るか。
    fn wants(&self, rel: &str) -> bool {
        self.words.iter().any(|(g, _)| selected(rel, &g.files, &g.except)) || self.calls.iter().any(|g| rel.ends_with(".hy") && selected(rel, &g.files, &g.except))
    }
}

/// file 1 つ(根からの path rel と中身)を全部の群に当てる(1 file の実行 — 正規表現はここで組む)。
pub fn judge(rel: &str, source: &str, words: &[RetiredWords], calls: &[RetiredCalls]) -> (Vec<WordHit>, Vec<CallHit>) {
    judge_prepared(rel, source, &Prepared::new(words, calls))
}

/// file 1 つを組み終えた群に当てる。
fn judge_prepared(rel: &str, source: &str, prepared: &Prepared) -> (Vec<WordHit>, Vec<CallHit>) {
    let mut word_hits = Vec::new();
    for (group, patterns) in prepared.words.iter().filter(|(g, _)| selected(rel, &g.files, &g.except)) {
        word_hits.extend(match group.place {
            WordPlace::Lines => line_hits(rel, source, group, patterns),
            WordPlace::Names => name_hits(rel, source, group, patterns),
        });
    }
    let mut call_hits = Vec::new();
    let call_groups: Vec<&RetiredCalls> = prepared.calls.iter().filter(|g| rel.ends_with(".hy") && selected(rel, &g.files, &g.except)).collect();
    if !call_groups.is_empty() {
        let mut reader = Reader::new(source, 0, source.len());
        let forms = reader.read_all();
        for group in call_groups {
            for form in &forms {
                hy_calls(source, form, group, rel, &mut call_hits);
            }
        }
    }
    word_hits.sort_by_key(|h| (h.start, h.detail.clone()));
    call_hits.sort_by_key(|h| h.start);
    (word_hits, call_hits)
}

/// 宣言の群に当たる file を読んで判じる(focus が在ればその下の file だけ)。読めない file は理由を返す。
pub fn find(root: &Path, words: &[RetiredWords], calls: &[RetiredCalls], focus: Option<&[PathBuf]>) -> (Vec<FileHits>, Vec<String>) {
    let prepared = Prepared::new(words, calls);
    let wanted: Vec<(String, PathBuf)> = candidates(root, words, calls, focus).into_iter().filter(|(rel, _)| prepared.wants(rel)).collect();
    let judged: Vec<Result<Option<FileHits>, String>> = wanted
        .into_par_iter()
        .map(|(rel, path)| match std::fs::read_to_string(&path) {
            Ok(source) => {
                let (word_hits, call_hits) = judge_prepared(&rel, &source, &prepared);
                Ok((!word_hits.is_empty() || !call_hits.is_empty()).then(|| FileHits { rel, path, source, words: word_hits, calls: call_hits }))
            }
            Err(error) => Err(format!("{}: 読めない: {}", rel, error)),
        })
        .collect();
    let mut found = Vec::new();
    let mut errors = Vec::new();
    for result in judged {
        match result {
            Ok(Some(hits)) => found.push(hits),
            Ok(None) => {}
            Err(error) => errors.push(error),
        }
    }
    (found, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn words(words: &[&str], patterns: &[&str], place: WordPlace, rule_lines: &[&str]) -> RetiredWords {
        RetiredWords {
            name: "g".into(),
            words: words.iter().map(|w| w.to_string()).collect(),
            patterns: patterns.iter().map(|p| p.to_string()).collect(),
            files: vec!["**/*".into()],
            except: Vec::new(),
            rule_lines: rule_lines.iter().map(|w| w.to_string()).collect(),
            place,
            instead: "代わり".into(),
        }
    }

    #[test]
    fn globs_are_anchored_at_the_root() {
        assert!(anchored("README.md", "README.md"));
        assert!(!anchored("README.md", "docs/README.md"), "/ の無い型は根の直下だけ");
        assert!(anchored("controllers/**/*.hy", "controllers/a/b.hy"));
        assert!(anchored("controllers/**/*.hy", "controllers/b.hy"));
        assert!(!anchored("controllers/**/*.hy", "scripts/b.hy"));
        assert_eq!(literal_head("controllers/**/*.hy"), "controllers");
        assert_eq!(literal_head(".agents/code-quality.json"), ".agents/code-quality.json");
    }

    #[test]
    fn words_stand_alone_and_rule_lines_are_skipped() {
        let group = words(&["mail", "席", "auth home"], &[], WordPlace::Lines, &["使わない"]);
        let source = "email address\nmail を送る\n使わない語 = mail\n席へ届ける\nmailbox と mail-box\nauth home は退役\n";
        let (hits, _) = judge("a.md", source, &[group], &[]);
        let found: Vec<(&str, &str)> = hits.iter().map(|h| (&source[h.start..h.end], h.detail.as_str())).collect();
        assert_eq!(found, vec![("mail", "mail"), ("席", "席"), ("auth home", "auth home")]);
    }

    #[test]
    fn patterns_hit_once_per_line_and_key_by_group() {
        let group = words(&[], &[r"会話\s*[（(]\s*(?:意味は|=)\s*agent", r"(?i)semantically\s+agent"], WordPlace::Lines, &[]);
        let (hits, _) = judge("a.md", "会話(意味は agent)と 会話(= agent)は semantically agent\n会話は agent の手番の列を持つ\n", &[group], &[]);
        assert_eq!(hits.len(), 1, "同じ行に 2 つの型が当たっても 1 行と数える");
        assert_eq!(hits[0].detail, "g");
    }

    #[test]
    fn names_are_read_from_definitions_only() {
        let group = words(&[], &["(?i)conversation"], WordPlace::Names, &[]);
        let source = "(defk attend-conversation [x] \"conversation\")\n(defclass [(dataclass :frozen True)] ConversationRow [])\n\
                      (setv CONVERSATION-KIND \"conversation\")\n(defn #^ str chat-of [row] row.chat) ; conversation の綴りは註だけ\n\
                      (defrecord ChatSlice (#^ str chat))\n";
        let (hits, _) = judge("controllers/chat/rows.hy", source, &[group.clone()], &[]);
        let names: Vec<&str> = hits.iter().map(|h| h.name.as_deref().unwrap_or("")).collect();
        assert_eq!(names, vec!["attend-conversation", "ConversationRow", "CONVERSATION-KIND"]);
        let (python, _) = judge("controllers/chat/rows.py", "def read_conversation(x):\n    pass\nclass ChatRow:\n    pass\n", &[group], &[]);
        assert_eq!(python.iter().map(|h| h.name.as_deref().unwrap_or("")).collect::<Vec<_>>(), vec!["read_conversation"]);
    }

    #[test]
    fn calls_are_heads_of_forms_not_comments_or_strings() {
        let group = RetiredCalls { name: "clock".into(), calls: vec!["Now".into(), "time.time".into()], files: vec!["**/*.hy".into()], except: Vec::new(), instead: "x".into() };
        let source = ";; 効果 (Now) は退役した\n(setv a (Now))\n(setv b \"(Now)\")\n(setv c (time.time))\n#_(Now)\n(setv d Now)\n";
        let (_, hits) = judge("a.hy", source, &[], &[group.clone()]);
        assert_eq!(hits.iter().map(|h| h.call.as_str()).collect::<Vec<_>>(), vec!["Now", "time.time"]);
        let (_, none) = judge("a.py", "Now()\n", &[], &[group]);
        assert!(none.is_empty(), "Python の file は数えない");
    }
}
