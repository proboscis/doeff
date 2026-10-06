//! DOEFF208 — 本番の code が、記録の変更の待ちを job を起こす手段にする所(利用者 2026-10-06 23:0x "記録に書いて記録をポーリングする
//! 設計を本当にやめてくれ、linterでみつけて禁止したい" / "記録は記録、起動は起動" / "redisでもrabbitmqでも使っていいから、イベントに
//! 即応してほしい。"・agora-redesign #3834)。
//!
//! 当たるのは、architecture.hy に `:business-fakes` を書いた repo の本番の code の Hy の file(`business_fakes::production_code` — DOEFF143 の
//! 本番の入口・DOEFF207 の job でない書き手と同じ範囲の宣言。検・模擬の環境・読まない file は外れる)。次の 2 つを当たりにする:
//! - 記録の変更を出来事の源にする所 — doeff-records の合図の源の工場(`read-signal-handler`・`records-signal-handler`)の呼び。
//! - 記録の変更を待つ所 — `WatchChanges`・`WatchEvents` を、秒(`:timeout` か位置の引数の 3 つ目)が literal の 0 でない値で出す
//!   (変数・式・展開 `#*` / `#**` も「0 と言えない」ので当たる)。秒を書かない形は既定の 0(待たずに 1 回読む)なので当たらない。
//!
//! 当たらない物: 待たずに 1 回読む(秒 0 — 受け手が起動や繋ぎ直しの時に記録を読んで追いつく形)・記録への書き(落とさないための入力の行)・
//! 記録の効果に答える handler(節の頭が `WatchChanges` / `WatchEvents` の defhandler)の中で出し直す待ち・match の pattern の綴り・
//! doeff-records の package(dir `doeff_records`)の中 — この規則は使い手を見る。効果と源の持ち主は、出来事の基盤へ替える時に源そのものを消す。

use std::collections::BTreeSet;
use std::path::Path;

use doeff_indexer::hy_index::reader::{Form, Node, Prefix, Reader};
use rayon::prelude::*;

use super::architecture::BusinessFakes;
use super::business_fakes::production_code;
use super::hy_files;
use super::paths::relative_path;
use crate::position::{LineIndex, Range};

/// 記録の変更を出来事の源にする工場(doeff-records の event_source.hy)— 呼びの頭の綴りの最後の段。
const SIGNAL_SOURCES: &[&str] = &["read-signal-handler", "records-signal-handler"];
/// 記録の変更の待ちの効果(doeff-records の effects.hy)— どちらも秒は位置の引数の 3 つ目(`tables cursor timeout` / `stream after timeout`)。
const RECORD_WAITS: &[&str] = &["WatchChanges", "WatchEvents"];
const TIMEOUT_POSITION: usize = 2;
/// 効果と源の持ち主の package の dir(この下は母集団の外)。
const OWNER_PACKAGE_DIR: &str = "doeff_records";

/// 当たりの種類(閉じた集合)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WaitUse {
    /// 合図の源の工場の呼び(頭の綴りの最後の段)。
    SignalSource { factory: String },
    /// 秒が 0 でない記録の変更の待ち(効果の名・秒の綴り)。
    LongPoll { effect: String, timeout: String },
}

/// 当たり 1 つ(根からの path・呼びの頭の位置・囲む top level の定義の名・種類)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WaitFinding {
    pub rel: String,
    pub range: Range,
    pub definition: String,
    pub what: WaitUse,
}

impl WaitFinding {
    /// 鍵の細目(定義の名::頭の綴り)。
    pub fn detail(&self) -> String {
        let head = match &self.what {
            WaitUse::SignalSource { factory } => factory,
            WaitUse::LongPoll { effect, .. } => effect,
        };
        format!("{}::{}", self.definition, head)
    }

    /// 主体(何が・どこで)。
    pub fn subject(&self) -> String {
        match &self.what {
            WaitUse::SignalSource { factory } => format!("定義 {} の ({} …)", self.definition, factory),
            WaitUse::LongPoll { effect, timeout } => format!("定義 {} の ({} … :timeout {})", self.definition, effect, timeout),
        }
    }

    /// 違反の文。
    pub fn describe(&self) -> String {
        match &self.what {
            WaitUse::SignalSource { .. } => format!(
                "{} — 記録の変更を出来事の源にする(記録の変更の待ちで job を起こす)。記録は記録、起動は起動 — 起こすのは出来事の基盤の知らせにする",
                self.subject()
            ),
            WaitUse::LongPoll { .. } => format!(
                "{} — 記録の変更を秒 0 でない待ちで待つ(記録の変更の待ちで job を起こす)。記録は記録、起動は起動 — 待たずに 1 回読む(秒 0)か、出来事の基盤の知らせで起こす",
                self.subject()
            ),
        }
    }
}

/// 頭の綴りの最後の段(`event-source.read-signal-handler` → `read-signal-handler`)。
fn last_segment(spelled: &str) -> &str {
    spelled.rsplit('.').next().unwrap_or(spelled)
}

/// `#_` で読み捨てた form を除いた中身。
fn visible(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect()
}

fn spelled<'s>(source: &'s str, form: &Form) -> &'s str {
    source.get(form.span.start..form.span.end).unwrap_or("")
}

/// 呼びの頭の記号の綴りの最後の段(呼びでなければ None)。
fn call_head<'s>(source: &'s str, form: &Form) -> Option<&'s str> {
    let items = form.paren_items()?;
    let head = visible(items).into_iter().next()?;
    matches!(head.node, Node::Symbol).then(|| last_segment(spelled(source, head)))
}

/// 記録の変更の待ちに答える defhandler か(直下の節の頭が待ちの効果)。
fn answers_record_waits(source: &str, items: &[&Form]) -> bool {
    items.first().is_some_and(|head| spelled(source, head) == "defhandler")
        && items.iter().skip(2).any(|clause| call_head(source, clause).is_some_and(|h| RECORD_WAITS.contains(&h)))
}

fn is_keyword(source: &str, form: &Form, word: &str) -> bool {
    matches!(form.node, Node::Keyword) && spelled(source, form) == word
}

/// `(match 主題 pattern 本体 pattern :if 条件 本体 …)` の pattern の位置(中身の添字)。
fn match_patterns(source: &str, items: &[&Form]) -> BTreeSet<usize> {
    if items.first().map(|head| spelled(source, head)) != Some("match") {
        return BTreeSet::new();
    }
    std::iter::successors(Some(2), |&at| Some(if items.get(at + 1).is_some_and(|f| is_keyword(source, f, ":if")) { at + 4 } else { at + 2 }))
        .take_while(|&at| at < items.len())
        .collect()
}

/// 秒の値が literal の 0 か(`0`・`0.0`・`0.` など)。
fn literal_zero(source: &str, form: &Form) -> bool {
    matches!(form.node, Node::Number) && spelled(source, form).replace('_', "").parse::<f64>().is_ok_and(|v| v == 0.0)
}

/// 待ちの効果の引数(頭の後ろ)から、0 と言えない秒の綴りを返す(0 か、秒を書かない既定の 0 なら None)。
fn nonzero_timeout(source: &str, args: &[&Form]) -> Option<String> {
    // 鍵の引数は値と組で飛ばし、位置の引数の添字だけを並べる。
    let positional: Vec<usize> = std::iter::successors(Some(0), |&at| args.get(at).map(|f| if matches!(f.node, Node::Keyword) { at + 2 } else { at + 1 }))
        .take_while(|&at| at < args.len())
        .filter(|&at| !matches!(args[at].node, Node::Keyword))
        .collect();
    let keyword = args.iter().position(|f| is_keyword(source, f, ":timeout")).and_then(|at| args.get(at + 1).copied());
    let unpacked = args.iter().find(|f| matches!(f.node, Node::Prefixed { prefix: Prefix::Unpack | Prefix::UnpackMapping, .. }));
    let value = keyword.or_else(|| positional.get(TIMEOUT_POSITION).map(|&at| args[at]));
    match (value, unpacked) {
        (Some(value), _) => (!literal_zero(source, value)).then(|| spelled(source, value).to_string()),
        (None, Some(unpacked)) => Some(spelled(source, unpacked).to_string()),
        (None, None) => None,
    }
}

/// form の木の当たり(byte の範囲と種類)。記録の待ちに答える defhandler の中と match の pattern は見ない。
fn hits(source: &str, form: &Form) -> Vec<(usize, usize, WaitUse)> {
    match &form.node {
        Node::Seq { items, .. } => {
            let items = visible(items);
            if answers_record_waits(source, &items) {
                return Vec::new();
            }
            let own = form.paren_items().and(items.first()).filter(|head| matches!(head.node, Node::Symbol)).and_then(|head| {
                let name = last_segment(spelled(source, head));
                let at = |what: WaitUse| (head.span.start, head.span.end, what);
                if SIGNAL_SOURCES.contains(&name) {
                    Some(at(WaitUse::SignalSource { factory: name.to_string() }))
                } else if RECORD_WAITS.contains(&name) {
                    nonzero_timeout(source, &items[1..]).map(|timeout| at(WaitUse::LongPoll { effect: name.to_string(), timeout }))
                } else {
                    None
                }
            });
            let patterns = match_patterns(source, &items);
            let inner = items.iter().enumerate().filter(|(at, _)| !patterns.contains(at)).flat_map(|(_, item)| hits(source, item));
            own.into_iter().chain(inner).collect()
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => hits(source, inner),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().flat_map(|part| hits(source, part)).collect(),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {
            Vec::new()
        }
    }
}

/// top level の form の名(`(defk 名 …)`・`(setv 名 …)` は名・ほかは頭の綴り)。
fn definition_name(source: &str, form: &Form) -> String {
    let items = form.paren_items().map(visible).unwrap_or_default();
    let head = items.first().map(|h| spelled(source, h)).unwrap_or("");
    let named = head.starts_with("def") || matches!(head, "setv" | "val" | "var");
    items
        .iter()
        .skip(1)
        .find(|f| matches!(f.node, Node::Symbol))
        .filter(|_| named)
        .map(|f| spelled(source, f).to_string())
        .unwrap_or_else(|| head.to_string())
}

/// file 1 つ(根からの path と中身)の当たり。
pub fn judge(rel: &str, source: &str) -> Vec<WaitFinding> {
    let lines = LineIndex::new(source);
    let mut reader = Reader::new(source, 0, source.len());
    reader
        .read_all()
        .iter()
        .flat_map(|form| {
            let definition = definition_name(source, form);
            hits(source, form)
                .into_iter()
                .map(move |(start, end, what)| (definition.clone(), start, end, what))
                .collect::<Vec<_>>()
        })
        .map(|(definition, start, end, what)| WaitFinding { rel: rel.to_string(), range: lines.range(start, end), definition, what })
        .collect()
}

/// 母集団の file か — 本番の code(`:business-fakes` の宣言)で、効果と源の持ち主の package の外。
fn in_population(rel: &str, decl: &BusinessFakes) -> bool {
    production_code(rel, decl) && !rel.split('/').any(|segment| segment == OWNER_PACKAGE_DIR)
}

/// repo の本番の code の Hy の file を全部判じる(当たり・読めない file の理由)。
pub fn find(root: &Path, decl: &BusinessFakes) -> (Vec<WaitFinding>, Vec<String>) {
    let files: Vec<(String, std::path::PathBuf)> = hy_files::collect(root)
        .into_iter()
        .filter_map(|path| relative_path(root, &path).map(|rel| (rel, path)))
        .filter(|(rel, _)| in_population(rel, decl))
        .collect();
    let judged: Vec<Result<Vec<WaitFinding>, String>> = files
        .into_par_iter()
        .map(|(rel, path)| match std::fs::read_to_string(&path) {
            Ok(source) => Ok(judge(&rel, &source)),
            Err(error) => Err(format!("{}: 読めない: {}", rel, error)),
        })
        .collect();
    let (found, errors): (Vec<_>, Vec<_>) = judged.into_iter().partition(Result::is_ok);
    (found.into_iter().flat_map(Result::unwrap_or_default).collect(), errors.into_iter().filter_map(Result::err).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn details(source: &str) -> Vec<String> {
        judge("x.hy", source).iter().map(WaitFinding::detail).collect()
    }

    #[test]
    fn signal_sources_are_hit_with_or_without_the_module_name() {
        let source = "(defk a [b] (with-handlers [(read-signal-handler B s)] b))\n(defk c [b] (with-handlers [(event-source.records-signal-handler B s)] b))\n\
                      (import doeff_records.event_source [read-signal-handler])\n";
        assert_eq!(details(source), vec!["a::read-signal-handler", "c::records-signal-handler"]);
    }

    #[test]
    fn waits_are_hit_unless_the_timeout_is_a_literal_zero() {
        let source = "(defk a [c left] (WatchChanges #(\"t\") c :timeout left))\n(defk b [c] (WatchEvents \"s\" c 5))\n\
                      (defk d [c] (WatchChanges #(\"t\") c :timeout 0.0))\n(defk e [c] (WatchEvents \"s\" 0 :timeout 0))\n\
                      (defk f [c] (WatchChanges #(\"t\") c))\n(defk g [c] (WatchChanges #(\"t\") c :limit 5 :timeout 0))\n\
                      (defk h [kw] (WatchChanges #** kw))\n(defk i [c] (WatchChanges #(\"t\") c #_ 5))\n";
        assert_eq!(details(source), vec!["a::WatchChanges", "b::WatchEvents", "h::WatchChanges"]);
        let found = judge("x.hy", source);
        assert_eq!(found[0].what, WaitUse::LongPoll { effect: "WatchChanges".into(), timeout: "left".into() });
        assert_eq!(found[0].range.start.line, 0);
    }

    #[test]
    fn handlers_that_answer_waits_and_match_patterns_are_not_hit() {
        let source = "(defhandler relay\n  (WatchChanges [tables cursor timeout limit] (resume (WatchChanges tables cursor :timeout timeout))))\n\
                      (defk waited [ask] (match ask (WatchChanges :timeout t) (float t) (WatchEvents :timeout t) :if (> t 0) t _ 0.0))\n\
                      (defhandler other\n  (Ping [] (resume (WatchEvents \"s\" 0 :timeout 3))))\n";
        assert_eq!(details(source), vec!["other::WatchEvents"]);
    }
}
