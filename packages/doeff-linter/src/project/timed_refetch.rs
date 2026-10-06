//! DOEFF209 — 本番の code の Program が、同じ物を時間の待ちを挟んで繰り返し取りに行く所(利用者 2026-10-06 "so anything that require
//! polling, are to be fixed. polling is a last resort" / "記録に書いて記録をポーリングする設計を本当にやめてくれ、linterでみつけて禁止したい"
//! / "この設計思想をdoeff-linter/defjevruleで強制してほしい"・agora-redesign #3834)。
//!
//! 母集団は DOEFF208 と同じ宣言(architecture.hy の `:business-fakes` の本番の code の Hy の file — `business_fakes::production_code`)。
//! DOEFF208 と違い doeff-records の package の中も外さない(この規則は待ちの書き手そのものを見る — 効果と源の持ち主も時間で取り直す loop を
//! 持てば当たる)。
//!
//! 見る物は「起きる刻の出所」(Delay と sleep の綴りだけでは、今 + 決まった秒に ArmTimer を掛けて TimerFired で起きる形を取りこぼす):
//! 1. 時間の待ちの綴り — `(Delay 秒)`・`(WaitTicks _ 秒 …)`(秒が literal の 0 の譲りと、`(- 期限 今)` の期限の待ちは外す)・
//!    `ArmTimer` / `ArmedTimer` の刻(位置の 2 つ目か `:at`)が `(+ 今 …)` を含む物(今 = 同じ定義の中で GetTime・now-ms・clock-ms と、
//!    引数の無い定義で本文がそれらを呼ぶ物〔`(defk wall-ms-now [] …)`〕から結んだ名と、その名を 1 つだけ受ける変換の呼びで結んだ名)か、
//!    `(+ 今 …)` から結んだ名(`(<- at (time-of-epoch-ms (+ now …)))` の at)を含む物。繰り返しの中の待ちには、繰り返しの中で結んだ今
//!    だけを数える(繰り返しの前に 1 度読んだ今から求めた刻は、毎回同じ刻で、時間で取り直す形ではない)。
//! 2. 補助の定義 — 1 度目に読んで集め、2 度目から呼び手を当てる(名簿で持たず、変わりが無くなるまで読み直す):
//!    - 刻を渡す補助: 引数が(結び直しを辿って)刻の位置へ流れる定義。呼び手の引数が `(+ 今 …)` を含めば、その呼びが待ちの綴り。
//!    - 今を受ける補助: 刻が `(+ 引数 …)` か、`(+ 引数 …)` から結んだ名を含む定義。呼び手が今の名をその引数に渡せば、その呼びが待ちの綴り。
//!    - 待ちを返す補助: 繰り返しの外に待ちの綴りを持つ定義。呼びが待ちの綴り。
//!    呼びの頭は同じ file の定義か、`(import module [名])` の名で引き、引けない `a.b` の綴りは名の一致が 1 つだけの時に引く。
//! 3. 繰り返し — 待ちの綴りが `while`・`loop` の本体・`event-loop` の節の中に在るか、定義が自分を呼ぶ(再帰)。1 度だけ掛けて
//!    抜ける物は当てない(その定義は待ちを返す補助になり、繰り返しの中の呼び手が当たる)。
//!    別の task を立てる呼び(`Spawn`・名に spawn を持つ包み)の中は見ない — 渡した Program の待ちはその task の 1 度の待ちで、渡した
//!    Program が自分で繰り返すなら、その定義の中で当たる。
//!    本番の handler(`defhandler`)の節が繰り返しの外の待ちで(分岐を通らずに必ず)答える効果も待ちを返す補助と同じに扱い、その効果を繰り返しの中で出す所が
//!    当たる(周期の間の眠りを効果 AwaitNextTick にして handler で Delay する形 — WatchChanges・WatchEvents に答える節は DOEFF208 の側)。
//!    Python の `time.sleep` / `asyncio.sleep`(module の名つきの `sleep`)も眠りに数える。
//! 4. ライブラリへ問い直しの間隔を渡す鍵の引数 `:poll-seconds`(literal の 0 でない値)は、繰り返しを問わず当てる(繰り返すのはライブラリ)。
//!
//! 当たらない物: 起きる刻が行や予定から導かれる期限の待ち(刻に `(+ 今 …)` を含まない)・変わりを待つ時の上限の秒(WatchChanges /
//! WatchEvents の秒 — DOEFF208 の側)・要求の timeout・`match` の pattern・handler の節の頭(`(Delay [秒] …)`)・quote と `#_` の中・
//! 時間切れの期限(tag が top level の名で、その tag と比べる分岐の本体が全部 繰り返しを抜ける `(stop …)` の物 — 止めの合図を待つ上限の
//! 秒・cisco-c8 の決定 2026-10-07。来た後に同じ読みを繰り返す本体が在る tag は外さない — stopping_tags)。
//!
//! 区分(当たりの鍵の細目の最後の段と本文): `periodic`(純粋に時間で取り直す)と `retry`(届かない後の取り直し — 待ちの綴り・掛ける期限の
//! tag・囲む match の節の型の綴りに retry / unreachable / unavailable が在る。補助の呼びは補助の中の待ちが全部 retry の時)。
//!
//! 場所に書いて通す理由: 当たりの行か、その直前に続く註だけの行に `; 時間で取り直す理由: <語>`(DOEFF111 の `; defk にできない: <理由>` と
//! 同じ置き方)。語は閉じた集合 — `相手に変更の知らせが無い: <相手> — <何で確かめたか>`(相手の名と確かめの根拠〔相手の API の文書の節・
//! 相手の code の行・試した命令と結果〕のどちらも空でない。相手が変更の知らせを持つと分かっている相手〔PARTIES_WITH_CHANGE_NOTICE —
//! Kubernetes・coordinator の GET /watch・doeff-events の基盤・記録の service の変更の合図〕なら通さない — Mac の調整役の条件 2026-10-07)・
//! `書くだけ: 生存の印` / `書くだけ: 報告` / `書くだけ: 期限の延長`・
//! `届かない間だけの繋ぎ直し`(区分 retry の当たりだけ — 届かない印か失敗の答えが在る間だけ間を置いて繋がるかを試し、data を取りに
//! 行かず、戻ったら印を消す形)。語の外・区分の合わない語は通さず、当たりの本文に訳を足す。

use std::collections::{BTreeSet, HashMap};
use std::path::Path;

use doeff_indexer::hy_index::reader::{Form, Node, Prefix, Reader};
use rayon::prelude::*;

use super::architecture::BusinessFakes;
use super::business_fakes::production_code;
use super::hy_files;
use super::paths::relative_path;
use crate::position::{LineIndex, Range};

/// 今を返す呼びの頭(最後の段)。
const CLOCK_HEADS: &[&str] = &["GetTime", "now-ms", "clock-ms"];
/// 期限を掛ける効果と期限の値(刻は位置の 2 つ目か `:at`)。
const TIMER_SINKS: &[&str] = &["ArmTimer", "ArmedTimer"];
const TIMER_AT_POSITION: usize = 1;
/// 時間で眠る効果と、秒の位置の引数の添字。
const SLEEPS: &[(&str, usize)] = &[("Delay", 0), ("WaitTicks", 1)];
/// 分岐の form(handler の節の中で、この下の待ちは必ずの待ちではない)。
const BRANCHES: &[&str] = &["if", "when", "unless", "cond", "match", "try", "and", "or", "while", "loop"];
/// 記録の変更の待ちの効果(秒は DOEFF208 の側 — これに答える handler の節の待ちは数えない)。
const RECORD_WAITS: &[&str] = &["WatchChanges", "WatchEvents"];
/// ライブラリへ問い直しの間隔を渡す鍵の引数。
const POLL_KEY: &str = ":poll-seconds";
/// 繰り返しの form と、繰り返す中身の最初の添字。
/// `for` は数えない — 有限の集まりを 1 つずつ回す形で、時間で取り直す形ではない(届かない時の数回の試しは while か再帰で書かれる)。
const REPEATING: &[(&str, usize)] = &[("while", 1), ("loop", 1), ("event-loop", 2)];
/// 呼べる定義の頭(待ちを返す補助になり得る物)。
const CALLABLE_HEADS: &[&str] = &["defk", "deff", "defn", "defp", "defn/a"];
/// 届かない後の取り直しを名乗る綴り(小文字で比べる)。
const RETRY_WORDS: &[&str] = &["retry", "unreachable", "unavailable"];
/// 場所に書いて通す理由の註の目印。
pub const REASON_MARKER: &str = "時間で取り直す理由:";
/// 補助の読み直しの上限(補助の連なりの深さ — 超えたら打ち切る)。
const MAX_ROUNDS: usize = 16;

/// 当たりの区分(閉じた集合)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Refetch {
    /// 純粋に時間で取り直す。
    Periodic,
    /// 届かない後の取り直し(今 + retry 秒の期限)。
    Retry,
}

impl Refetch {
    /// 鍵の細目の最後の段。
    pub fn word(self) -> &'static str {
        match self {
            Refetch::Periodic => "periodic",
            Refetch::Retry => "retry",
        }
    }

    /// 本文の区分の名。
    pub fn label(self) -> &'static str {
        match self {
            Refetch::Periodic => "時間で取り直す",
            Refetch::Retry => "届かない後の取り直し",
        }
    }
}

/// 書くだけの loop の種類(閉じた集合)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WriteOnly {
    Liveness,
    Report,
    LeaseExtension,
}

/// 場所に書いて通す理由(閉じた語彙)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Reason {
    /// 相手に変更の知らせが無い(相手の名と、何で確かめたか — どちらも空でない)。
    NoChangeNotice { party: String, evidence: String },
    /// 書くだけ(生存の印・報告・期限の延長)。
    WriteOnly(WriteOnly),
    /// 届かない間だけの繋ぎ直し(区分 retry だけ)。
    ReconnectWhileUnreachable,
}

/// 「相手に変更の知らせが無い」の語の頭。
pub const NO_CHANGE_NOTICE: &str = "相手に変更の知らせが無い:";
/// 相手の名と確かめの根拠の区切り。
pub const EVIDENCE_SEPARATOR: char = '—';

/// 変更の知らせを持つと分かっている相手(閉じた一覧 — 表に出す名, 照らす綴り)。照らし方: 書いた相手の名を小文字にし、空白を除き、`_` を
/// `-` に揃えた綴りが、照らす綴りのどれかを含めば当てる(Mac の調整役の条件 2026-10-07・agora-redesign #3834)。
pub const PARTIES_WITH_CHANGE_NOTICE: &[(&str, &[&str])] = &[
    ("Kubernetes", &["kubernetes", "k8s", "kube-apiserver"]),
    ("coordinator の GET /watch", &["coordinator", "/watch"]),
    ("doeff-events の基盤", &["doeff-events"]),
    ("記録の service の変更の合図", &["記録のservice", "記録の変更の合図", "agora-record", "doeff-records"]),
];

/// 相手の名が、変更の知らせを持つと分かっている相手なら、その表に出す名。
pub fn party_with_change_notice(party: &str) -> Option<&'static str> {
    let spelled: String = party.chars().filter(|c| !c.is_whitespace()).collect::<String>().to_lowercase().replace('_', "-");
    PARTIES_WITH_CHANGE_NOTICE.iter().find(|(_, words)| words.iter().any(|w| spelled.contains(w))).map(|(name, _)| *name)
}

/// 理由を通さない訳(閉じた集合)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReasonRefusal {
    /// 閉じた語彙の外(書いた語そのもの)。
    OutsideVocabulary(String),
    /// 「相手に変更の知らせが無い」に相手の名か確かめの根拠が無い。
    MissingPartyOrEvidence,
    /// 相手が変更の知らせを持つと分かっている(表に出す名)。
    PartyHasChangeNotice(&'static str),
}

/// 理由の語を読む。
pub fn parse_reason(text: &str) -> Result<Reason, ReasonRefusal> {
    let text = text.trim();
    if let Some(rest) = text.strip_prefix(NO_CHANGE_NOTICE) {
        let (party, evidence) = rest.split_once(EVIDENCE_SEPARATOR).map(|(p, e)| (p.trim(), e.trim())).unwrap_or((rest.trim(), ""));
        if party.is_empty() || evidence.is_empty() {
            return Err(ReasonRefusal::MissingPartyOrEvidence);
        }
        if let Some(known) = party_with_change_notice(party) {
            return Err(ReasonRefusal::PartyHasChangeNotice(known));
        }
        return Ok(Reason::NoChangeNotice { party: party.to_string(), evidence: evidence.to_string() });
    }
    match text {
        "書くだけ: 生存の印" => Ok(Reason::WriteOnly(WriteOnly::Liveness)),
        "書くだけ: 報告" => Ok(Reason::WriteOnly(WriteOnly::Report)),
        "書くだけ: 期限の延長" => Ok(Reason::WriteOnly(WriteOnly::LeaseExtension)),
        "届かない間だけの繋ぎ直し" => Ok(Reason::ReconnectWhileUnreachable),
        _ => Err(ReasonRefusal::OutsideVocabulary(text.to_string())),
    }
}

/// 当たりの場所に書いた理由の判じ(無い・通す・通さない訳)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReasonRead {
    Absent,
    Accepted,
    Rejected(String),
}

/// 待ちの綴りの元(補助の中の待ちを呼び手の本文に出すため)。
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Origin {
    pub rel: String,
    /// 1 から数えた行。
    pub line: usize,
    pub text: String,
    pub kind: Refetch,
}

/// 当たりの綴りの種類(閉じた集合)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SiteKind {
    /// Delay・WaitTicks の眠り。
    Sleep,
    /// 今 + 決まった秒の期限(直の ArmTimer / ArmedTimer か、刻を渡す補助・今を受ける補助の呼び)。
    Timer,
    /// 待ちを返す補助の呼び(補助の中の待ちの元)。
    Helper(Vec<Origin>),
    /// ライブラリへ問い直しの間隔を渡す鍵の引数。
    PollSeconds,
}

/// 当たり 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefetchFinding {
    pub rel: String,
    pub range: Range,
    pub definition: String,
    pub head: String,
    pub kind: SiteKind,
    pub refetch: Refetch,
    /// 場所に書いた理由が通らなかった訳(書いていなければ None)。
    pub rejected_reason: Option<String>,
    /// 同じ定義の中の同じ頭・同じ区分の、通らない当たりの残りの行(1 から数えた行 — 当たりは鍵ごとに 1 つにまとめ、最初の場所に出す)。
    pub also_at: Vec<usize>,
}

impl RefetchFinding {
    /// 鍵の細目(定義の名::頭の綴り::区分)。
    pub fn detail(&self) -> String {
        format!("{}::{}::{}", self.definition, self.head, self.refetch.word())
    }

    /// 主体(何が・どこで)。
    pub fn subject(&self) -> String {
        match &self.kind {
            SiteKind::PollSeconds => format!("定義 {} の ({} … :poll-seconds …)", self.definition, self.head),
            _ => format!("定義 {} の ({} …)", self.definition, self.head),
        }
    }

    /// 違反の文。
    pub fn describe(&self) -> String {
        let what = match &self.kind {
            SiteKind::Sleep => "時間で眠ってから繰り返す".to_string(),
            SiteKind::Timer => "今 + 決まった秒の期限を繰り返しの中で掛ける".to_string(),
            SiteKind::Helper(origins) => {
                let shown: Vec<String> = origins.iter().take(3).map(|o| format!("{}:{} {}", o.rel, o.line, o.text)).collect();
                let more = if origins.len() > 3 { format!(" ほか {} か所", origins.len() - 3) } else { String::new() };
                format!("中で時間の待ちを掛ける補助を繰り返しの中で呼ぶ(待ちの元: {}{})", shown.join("・"), more)
            }
            SiteKind::PollSeconds => "ライブラリへ問い直しの間隔を渡す(ライブラリが時間で問い直す)".to_string(),
        };
        let rejected = self.rejected_reason.as_ref().map(|why| format!("。書いた理由は通らない: {}", why)).unwrap_or_default();
        let also = if self.also_at.is_empty() {
            String::new()
        } else {
            format!("(同じ定義の同じ形が {} 行にも在る — どれも同じ直しか理由が要る)", self.also_at.iter().map(|l| l.to_string()).collect::<Vec<_>>().join("・"))
        };
        format!(
            "{} — 区分 {}: {}{}。同じ物を時間の待ちを挟んで繰り返し取りに行く — 相手の変わりの知らせ(出来事)で起きる形にする。相手が変わりを知らせる手段を持たない所だけ、その行か直前の註に `; {} <語>` を書く{}",
            self.subject(),
            self.refetch.label(),
            what,
            also,
            REASON_MARKER,
            rejected
        )
    }
}

fn last_segment(spelled: &str) -> &str {
    spelled.rsplit('.').next().unwrap_or(spelled)
}

/// 名の比べの形(Hy の `-` と `_` を同じに読む)。
fn norm(name: &str) -> String {
    name.replace('-', "_")
}

/// 記号の綴りの頭の段(`beat.seconds` → `beat`)を比べの形にした物。
fn root_name(spelled: &str) -> String {
    norm(spelled.split('.').next().unwrap_or(spelled))
}

fn visible(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect()
}

fn spelled<'s>(source: &'s str, form: &Form) -> &'s str {
    source.get(form.span.start..form.span.end).unwrap_or("")
}

fn head_symbol<'s>(source: &'s str, items: &[&Form]) -> Option<&'s str> {
    items.first().filter(|h| matches!(h.node, Node::Symbol)).map(|h| spelled(source, h))
}

fn is_keyword(source: &str, form: &Form, word: &str) -> bool {
    matches!(form.node, Node::Keyword) && norm(spelled(source, form)) == norm(word)
}

fn literal_zero(source: &str, form: &Form) -> bool {
    matches!(form.node, Node::Number) && spelled(source, form).replace('_', "").parse::<f64>().is_ok_and(|v| v == 0.0)
}

/// 別の task を立てる呼びか(doeff の `Spawn` と、名に spawn を持つ包み — `spawn-work` など)。
fn spawns(head: &str) -> bool {
    last_segment(head).to_lowercase().contains("spawn")
}

fn has_retry_word(text: &str) -> bool {
    let lower = text.to_lowercase();
    RETRY_WORDS.iter().any(|w| lower.contains(w))
}

/// 呼びの引数(頭の後ろ)から、鍵 `keyword` の値か位置 `position` の引数を返す。
fn argument<'f>(source: &str, args: &[&'f Form], position: usize, keyword: Option<&str>) -> Option<&'f Form> {
    if let Some(word) = keyword {
        if let Some(at) = args.iter().position(|f| is_keyword(source, f, word)) {
            return args.get(at + 1).copied();
        }
    }
    let positional: Vec<usize> = std::iter::successors(Some(0), |&at| args.get(at).map(|f| if matches!(f.node, Node::Keyword) { at + 2 } else { at + 1 }))
        .take_while(|&at| at < args.len())
        .filter(|&at| !matches!(args[at].node, Node::Keyword))
        .collect();
    positional.get(position).map(|&at| args[at])
}

/// form の木の子(quote の中は見ない)。
fn children(form: &Form) -> Vec<&Form> {
    match &form.node {
        Node::Seq { items, .. } => visible(items),
        Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } => Vec::new(),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => vec![inner.as_ref()],
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().map(|b| b.as_ref()).collect(),
        _ => Vec::new(),
    }
}

/// form の中の記号のどれかの頭の段が names に在るか。
fn mentions(source: &str, form: &Form, names: &BTreeSet<String>) -> bool {
    match form.node {
        Node::Symbol => names.contains(&root_name(spelled(source, form))),
        _ => children(form).into_iter().any(|c| mentions(source, c, names)),
    }
}

/// `(+ a …)` の引数の記号のうち names に在る物(form の木の全部)。
fn plus_operands(source: &str, form: &Form, names: &BTreeSet<String>, found: &mut BTreeSet<String>) {
    if let Some(items) = form.paren_items().map(visible) {
        if head_symbol(source, &items) == Some("+") {
            for item in &items[1..] {
                if matches!(item.node, Node::Symbol) {
                    let name = root_name(spelled(source, item));
                    if names.contains(&name) {
                        found.insert(name);
                    }
                }
            }
        }
    }
    for child in children(form) {
        plus_operands(source, child, names, found);
    }
}

fn has_now_plus(source: &str, form: &Form, clocks: &BTreeSet<String>) -> bool {
    let mut found = BTreeSet::new();
    plus_operands(source, form, clocks, &mut found);
    !found.is_empty()
}

/// `(- 期限 … 今)` の形(期限の待ち)を含むか。
fn deadline_difference(source: &str, form: &Form, clocks: &BTreeSet<String>) -> bool {
    let own = form.paren_items().map(visible).is_some_and(|items| {
        head_symbol(source, &items) == Some("-")
            && items.iter().skip(2).any(|i| matches!(i.node, Node::Symbol) && clocks.contains(&root_name(spelled(source, i))))
    });
    own || children(form).into_iter().any(|c| deadline_difference(source, c, clocks))
}

/// `(match 主題 pattern 本体 pattern :if 条件 本体 …)` の (pattern の添字, 本体の添字)。
fn match_clauses(source: &str, items: &[&Form]) -> Vec<(usize, usize)> {
    let mut clauses = Vec::new();
    let mut at = 2;
    while at < items.len() {
        let guarded = items.get(at + 1).is_some_and(|f| is_keyword(source, f, ":if"));
        let body = if guarded { at + 3 } else { at + 1 };
        clauses.push((at, body));
        at = body + 1;
    }
    clauses
}

/// 定義の引数の名(比べの形・並びの順 — `*`・`/` は数えない)。
fn parameters(source: &str, items: &[&Form]) -> Vec<String> {
    let Some(vector) = items.iter().skip(2).find_map(|f| f.bracket_items()) else { return Vec::new() };
    fn name_of(source: &str, form: &Form) -> Option<String> {
        match &form.node {
            Node::Symbol => Some(spelled(source, form).to_string()),
            Node::Annotated { target: Some(target), .. } => name_of(source, target),
            Node::Prefixed { prefix: Prefix::Unpack | Prefix::UnpackMapping, inner: Some(inner) } => name_of(source, inner),
            Node::Seq { .. } => form.bracket_items().and_then(|inner| visible(inner).first().and_then(|f| name_of(source, f))),
            _ => None,
        }
    }
    visible(vector)
        .into_iter()
        .filter(|f| !matches!(f.node, Node::Annotated { target: None, .. }))
        .filter_map(|f| name_of(source, f))
        .filter(|name| name != "*" && name != "/")
        .map(|name| norm(&name))
        .collect()
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

/// top level の定義 1 つ。
struct Definition {
    name: String,
    key: String,
    params: Vec<String>,
    callable: bool,
    form: usize,
}

/// 母集団の file 1 つ(読んだ form・定義・import の名 → module)。
struct Unit {
    rel: String,
    source: String,
    forms: Vec<Form>,
    definitions: Vec<Definition>,
    by_key: HashMap<String, usize>,
    imports: HashMap<String, String>,
}

impl Unit {
    fn read(rel: String, source: String) -> Unit {
        let forms = Reader::new(&source, 0, source.len()).read_all();
        let mut definitions = Vec::new();
        let mut imports = HashMap::new();
        for (at, form) in forms.iter().enumerate() {
            let items = form.paren_items().map(visible).unwrap_or_default();
            let head = head_symbol(&source, &items).unwrap_or("");
            if head == "import" {
                read_imports(&source, &items[1..], &mut imports);
            }
            let name = definition_name(&source, form);
            let callable = CALLABLE_HEADS.contains(&head);
            definitions.push(Definition { key: norm(&name), params: if callable { parameters(&source, &items) } else { Vec::new() }, name, callable, form: at });
        }
        let by_key = definitions.iter().enumerate().filter(|(_, d)| d.callable).map(|(at, d)| (d.key.clone(), at)).collect();
        Unit { rel, source, forms, definitions, by_key, imports }
    }
}

/// `(import module [名 名 :as 別名 …] module …)` の名 → module。
fn read_imports(source: &str, items: &[&Form], into: &mut HashMap<String, String>) {
    let mut module: Option<String> = None;
    for item in items {
        match &item.node {
            Node::Symbol => module = Some(spelled(source, item).to_string()),
            Node::Seq { .. } => {
                if let (Some(module), Some(names)) = (module.as_ref(), item.bracket_items()) {
                    let names = visible(names);
                    let mut at = 0;
                    while at < names.len() {
                        if matches!(names[at].node, Node::Symbol) {
                            let original = spelled(source, names[at]);
                            let aliased = names.get(at + 1).is_some_and(|f| is_keyword(source, f, ":as"));
                            let local = if aliased { names.get(at + 2).map(|f| spelled(source, f)).unwrap_or(original) } else { original };
                            into.insert(norm(local), format!("{}::{}", module, norm(original)));
                            at += if aliased { 3 } else { 1 };
                        } else {
                            at += 1;
                        }
                    }
                }
            }
            _ => {}
        }
    }
}

type DefId = (usize, usize);

/// 補助の表(読み直しのたびに育つ)。
#[derive(Default, Clone, PartialEq, Eq)]
struct Helpers {
    /// 刻を渡す補助 — (引数の添字, 引数の名)。
    passthrough: HashMap<DefId, BTreeSet<(usize, String)>>,
    /// 今を受ける補助 — (引数の添字, 引数の名, 区分)。
    now_param: HashMap<DefId, BTreeSet<(usize, String, Refetch)>>,
    /// 待ちを返す補助 — 待ちの元。
    waits: HashMap<DefId, BTreeSet<Origin>>,
    /// 本番の handler の節が繰り返しの外の時間の待ちで答える効果(比べの形の名)— 待ちの元。
    effects: HashMap<String, BTreeSet<Origin>>,
}

/// module の綴り → file(path の後ろの段の一致が 1 つだけの物)・定義の名 → 定義(全体で 1 つだけの物)。
struct Index {
    modules: HashMap<String, Vec<usize>>,
    unique: HashMap<String, Vec<DefId>>,
    /// 今を返す呼びの頭(比べの形)— CLOCK_HEADS と、引数の無い定義で本文がそれを呼ぶ物(`(defk wall-ms-now [] …)`)。
    clock_heads: BTreeSet<String>,
    /// 時間切れの期限の tag(比べの形 — stopping_tags)。
    stopping_tags: BTreeSet<String>,
}

impl Index {
    fn new(units: &[Unit]) -> Index {
        let mut modules: HashMap<String, Vec<usize>> = HashMap::new();
        let mut unique: HashMap<String, Vec<DefId>> = HashMap::new();
        for (file, unit) in units.iter().enumerate() {
            let stem = unit.rel.strip_suffix(".hy").unwrap_or(&unit.rel);
            let parts: Vec<&str> = stem.split('/').collect();
            for from in 0..parts.len() {
                modules.entry(norm(&parts[from..].join("."))).or_default().push(file);
            }
            for (def, d) in unit.definitions.iter().enumerate().filter(|(_, d)| d.callable) {
                unique.entry(d.key.clone()).or_default().push((file, def));
            }
        }
        let mut clock_heads: BTreeSet<String> = CLOCK_HEADS.iter().map(|h| norm(h)).collect();
        loop {
            let found: Vec<String> = units
                .iter()
                .flat_map(|unit| {
                    unit.definitions
                        .iter()
                        .filter(|d| d.callable && d.params.is_empty() && !clock_heads.contains(&d.key))
                        .filter(|d| calls_any(&unit.source, &unit.forms[d.form], &clock_heads))
                        .map(|d| d.key.clone())
                })
                .collect();
            if found.is_empty() {
                break;
            }
            clock_heads.extend(found);
        }
        Index { modules, unique, clock_heads, stopping_tags: stopping_tags(units) }
    }

    /// file `file` の呼びの頭の綴りが指す定義。
    fn resolve(&self, units: &[Unit], file: usize, head: &str) -> Option<DefId> {
        let key = norm(last_segment(head));
        let unit = &units[file];
        if !head.contains('.') {
            if let Some(&def) = unit.by_key.get(&key) {
                return Some((file, def));
            }
            let imported = unit.imports.get(&key)?;
            let (module, original) = imported.split_once("::")?;
            let files = self.modules.get(&norm(module)).filter(|f| f.len() == 1)?;
            return units[files[0]].by_key.get(original).map(|&def| (files[0], def));
        }
        self.unique.get(&key).filter(|found| found.len() == 1).map(|found| found[0])
    }
}

/// 時間切れの期限の tag — top level で結んだ名(`(val STOP-WAIT-TAG "…")`)のうち、その tag と比べる分岐(`(cond (= tag 名) 本体 …)`・
/// `(if (= tag 名) 本体 …)`)が母集団に 1 つ以上在り、その本体が全部 繰り返しを抜ける `(stop …)` の物。止めの合図を待つ上限の秒の期限は、
/// 来たら待ちを打ち切って抜けるだけで、同じ物を取りに行く繰り返しではない(cisco-c8 の決定 2026-10-07・agora-redesign #3834)。来た後に
/// 同じ読みを繰り返す本体が 1 つでも在る tag は外さない。
fn stopping_tags(units: &[Unit]) -> BTreeSet<String> {
    let mut seen: HashMap<String, bool> = HashMap::new();
    for unit in units {
        let source = unit.source.as_str();
        for form in &unit.forms {
            tag_branches(source, form, &mut |tested, body| {
                let exits = body.paren_items().map(visible).is_some_and(|items| head_symbol(source, &items) == Some("stop"));
                for name in tested {
                    *seen.entry(name).or_insert(true) &= exits;
                }
            });
        }
    }
    let constants: BTreeSet<String> = units
        .iter()
        .flat_map(|unit| {
            let source = unit.source.as_str();
            unit.forms.iter().filter_map(move |form| {
                let items = form.paren_items().map(visible)?;
                let head = head_symbol(source, &items)?;
                let name = items.get(1).filter(|f| matches!(f.node, Node::Symbol) && matches!(head, "val" | "setv"))?;
                Some(norm(spelled(source, name)))
            })
        })
        .collect();
    seen.into_iter().filter(|(name, exits)| *exits && constants.contains(name)).map(|(name, _)| name).collect()
}

/// form の木の、`(= a b)` で比べる分岐の (比べた記号の名〔比べの形・最後の段〕, 本体) を visit に渡す(`cond` の組と `if` の then)。
fn tag_branches<'f>(source: &str, form: &'f Form, visit: &mut impl FnMut(Vec<String>, &'f Form)) {
    if let Some(items) = form.paren_items().map(visible) {
        let pairs: Vec<(&Form, &Form)> = match head_symbol(source, &items) {
            Some("cond") => items[1..].chunks(2).filter(|pair| pair.len() == 2).map(|pair| (pair[0], pair[1])).collect(),
            Some("if") if items.len() >= 3 => vec![(items[1], items[2])],
            _ => Vec::new(),
        };
        for (test, body) in pairs {
            let Some(compared) = test.paren_items().map(visible).filter(|t| head_symbol(source, t) == Some("=")) else { continue };
            let names: Vec<String> =
                compared[1..].iter().filter(|f| matches!(f.node, Node::Symbol)).map(|f| norm(last_segment(spelled(source, f)))).collect();
            visit(names, body);
        }
    }
    for child in children(form) {
        tag_branches(source, child, visit);
    }
}

/// 定義 1 つの中の待ちの綴り。
#[derive(Debug, Clone)]
struct Site {
    start: usize,
    end: usize,
    head: String,
    kind: SiteKind,
    refetch: Refetch,
    repeated: bool,
}

/// 定義 1 つを読んだ結果。
#[derive(Default)]
struct Facts {
    sites: Vec<Site>,
    passthrough: BTreeSet<(usize, String)>,
    now_param: BTreeSet<(usize, String, Refetch)>,
}

/// 定義 1 つを歩く道具。
struct Walk<'a> {
    units: &'a [Unit],
    index: &'a Index,
    helpers: &'a Helpers,
    file: usize,
    me: DefId,
    params: &'a [String],
    /// 定義の中の全部の結びから読んだ今の名(繰り返しの外の待ちの綴りに使う)。
    anywhere: Moments,
    /// 繰り返しの中の結びだけから読んだ今の名(繰り返しの中の待ちの綴りに使う — 繰り返しの前に 1 度読んだ今から求めた刻は、
    /// 繰り返しのたびに同じ刻で、時間で取り直す形ではない)。
    in_loop: Moments,
    recursive: bool,
    derived: Vec<BTreeSet<String>>,
    /// 引数ごとに、`(+ 引数 …)` から結んだ名(`(<- at (time-of-epoch-ms (+ now retry-ms)))` の at — 今を受ける補助の刻)。
    plus_derived: Vec<BTreeSet<String>>,
    facts: Facts,
}

impl<'a> Walk<'a> {
    fn source(&self) -> &'a str {
        &self.units[self.file].source
    }

    fn moments(&self, in_loop: bool) -> &Moments {
        if in_loop { &self.in_loop } else { &self.anywhere }
    }

    /// 刻の式 `at` を判じる(今 + 秒なら Some — 中身は結んだ式が届かない後の取り直しを名乗るか)。今を受ける・刻を渡す引数を集める。
    fn time_argument(&mut self, at: &Form, kind: Refetch, in_loop: bool) -> Option<bool> {
        let source = self.source();
        let moments = self.moments(in_loop);
        if has_now_plus(source, at, &moments.clocks) || mentions(source, at, &moments.relative) {
            return Some(mentions(source, at, &moments.retry_named));
        }
        let params: BTreeSet<String> = self.params.iter().filter(|p| !self.anywhere.clocks.contains(*p)).cloned().collect();
        let mut plus = BTreeSet::new();
        plus_operands(source, at, &params, &mut plus);
        for (index, name) in self.params.iter().enumerate() {
            if params.contains(name) && mentions(source, at, &self.plus_derived[index]) {
                plus.insert(name.clone());
            }
        }
        if !plus.is_empty() {
            for (index, name) in self.params.iter().enumerate().filter(|(_, n)| plus.contains(*n)) {
                self.facts.now_param.insert((index, name.clone(), kind));
            }
            return None;
        }
        for (index, name) in self.params.iter().enumerate() {
            if mentions(source, at, &self.derived[index]) {
                self.facts.passthrough.insert((index, name.clone()));
            }
        }
        None
    }

    fn walk(&mut self, form: &Form, in_loop: bool, retry_hint: bool) {
        let source = self.source();
        let Some(items) = form.paren_items().map(visible) else {
            for child in children(form) {
                self.walk(child, in_loop, retry_hint);
            }
            return;
        };
        let head = head_symbol(source, &items).map(str::to_string);
        if head.as_deref().is_some_and(spawns) {
            // 別の task に渡す Program の中の待ちは、その task の 1 度の待ち — 渡す側の繰り返しでも、渡す側の待ちでもない(渡した
            // Program が自分で繰り返すなら、その定義の中で当たる)。
            return;
        }
        if let Some(head) = head.as_deref() {
            self.site_of(form, &items, head, in_loop, retry_hint);
        }
        let start = head.as_deref().and_then(|h| REPEATING.iter().find(|(name, _)| *name == h)).map(|(_, from)| *from);
        let clauses = if head.as_deref() == Some("match") { match_clauses(source, &items) } else { Vec::new() };
        for (at, item) in items.iter().enumerate() {
            if clauses.iter().any(|(pattern, _)| *pattern == at) {
                continue;
            }
            let pattern_hint =
                clauses.iter().find(|(_, body)| *body == at).is_some_and(|(pattern, _)| has_retry_word(spelled(source, items[*pattern])));
            let inside = in_loop || start.is_some_and(|from| at >= from);
            self.walk(item, inside, retry_hint || pattern_hint);
        }
    }

    fn push(&mut self, at: &Form, head: &str, kind: SiteKind, refetch: Refetch, repeated: bool) {
        self.facts.sites.push(Site { start: at.span.start, end: at.span.end, head: last_segment(head).to_string(), kind, refetch, repeated });
    }

    /// 呼び 1 つが待ちの綴りか。
    fn site_of(&mut self, form: &Form, items: &[&Form], head: &str, in_loop: bool, retry_hint: bool) {
        let source = self.source();
        let repeated = in_loop || self.recursive;
        let name = last_segment(head);
        let args = &items[1..];
        if args.first().is_some_and(|f| f.bracket_items().is_some()) {
            // handler の節の頭(`(Delay [秒] …)`)・`fn` / `let` の引数の並び — 呼びではない。
            return;
        }
        let named_retry = retry_hint || has_retry_word(spelled(source, form));
        let kind_of = |retry: bool| if retry { Refetch::Retry } else { Refetch::Periodic };
        if let Some(at) = args.iter().position(|f| is_keyword(source, f, POLL_KEY)) {
            if args.get(at + 1).is_some_and(|v| !literal_zero(source, v)) && !head.starts_with("def") {
                self.push(items[0], head, SiteKind::PollSeconds, Refetch::Periodic, repeated);
            }
        }
        let sleep_position =
            SLEEPS.iter().find(|(sleep, _)| *sleep == name).map(|(_, at)| *at).or_else(|| (name == "sleep" && head.contains('.')).then_some(0));
        if let Some(position) = sleep_position {
            let seconds = argument(source, args, position, None);
            if seconds.is_some_and(|s| !literal_zero(source, s) && !deadline_difference(source, s, &self.anywhere.clocks)) {
                self.push(items[0], head, SiteKind::Sleep, kind_of(named_retry), repeated);
            }
            return;
        }
        if TIMER_SINKS.contains(&name) {
            let tag = argument(source, args, 0, Some(":tag")).filter(|t| matches!(t.node, Node::Symbol));
            if tag.is_some_and(|t| self.index.stopping_tags.contains(&norm(last_segment(spelled(source, t))))) {
                // 時間切れの期限(来たら繰り返しを抜けるだけ)— 同じ物を取りに行く繰り返しではない。
                return;
            }
            let refetch = kind_of(named_retry);
            if let Some(at) = argument(source, args, TIMER_AT_POSITION, Some(":at")) {
                if let Some(bound_retry) = self.time_argument(at, refetch, in_loop) {
                    self.push(items[0], head, SiteKind::Timer, kind_of(named_retry || bound_retry), repeated);
                }
            }
            return;
        }
        let target = self.index.resolve(self.units, self.file, head);
        if target == Some(self.me) {
            return;
        }
        let Some(target) = target else {
            // 定義でない頭 — 本番の handler の節が時間の待ちで答える効果なら、その効果を出す所が待ちを返す補助の呼びと同じ。
            if let Some(origins) = self.helpers.effects.get(&norm(name)).cloned() {
                let all_retry = origins.iter().all(|o| o.kind == Refetch::Retry);
                let refetch = kind_of(retry_hint || all_retry || has_retry_word(name));
                self.push(items[0], head, SiteKind::Helper(origins.into_iter().collect()), refetch, repeated);
            }
            return;
        };
        let mut named_retry = named_retry;
        let mut timer = false;
        for (index, param) in self.helpers.passthrough.get(&target).cloned().unwrap_or_default() {
            if let Some(at) = argument(source, args, index, Some(&format!(":{}", param))) {
                if let Some(bound_retry) = self.time_argument(at, kind_of(named_retry), in_loop) {
                    timer = true;
                    named_retry |= bound_retry;
                }
            }
        }
        let refetch = kind_of(named_retry);
        for (index, param, kind) in self.helpers.now_param.get(&target).cloned().unwrap_or_default() {
            let Some(at) = argument(source, args, index, Some(&format!(":{}", param))) else { continue };
            if !matches!(at.node, Node::Symbol) {
                continue;
            }
            let passed = root_name(spelled(source, at));
            if self.moments(in_loop).clocks.contains(&passed) {
                timer = true;
            } else if let Some(mine) = self.params.iter().position(|p| *p == passed) {
                self.facts.now_param.insert((mine, passed, kind));
            }
        }
        if timer {
            let kinds: BTreeSet<Refetch> = self.helpers.now_param.get(&target).map(|s| s.iter().map(|(_, _, k)| *k).collect()).unwrap_or_default();
            let refetch = if named_retry || (!kinds.is_empty() && kinds.iter().all(|k| *k == Refetch::Retry)) { Refetch::Retry } else { refetch };
            self.push(items[0], head, SiteKind::Timer, refetch, repeated);
            return;
        }
        if let Some(origins) = self.helpers.waits.get(&target) {
            let all_retry = origins.iter().all(|o| o.kind == Refetch::Retry);
            let refetch = kind_of(retry_hint || all_retry || has_retry_word(name));
            self.push(items[0], head, SiteKind::Helper(origins.iter().cloned().collect()), refetch, repeated);
        }
    }
}

/// 定義の中の名の結び(`(<- 名 [型] 式)`・`(val 名 式)`・`(setv 名 式)`・`(var 名 式)`・`(:= 名 式)`)を出てくる順に。
fn bindings<'f>(source: &str, form: &'f Form, in_loop: bool, out: &mut Vec<Binding<'f>>) {
    let Some(items) = form.paren_items().map(visible) else {
        for child in children(form) {
            bindings(source, child, in_loop, out);
        }
        return;
    };
    {
        // `:=` はHy の reader が keyword として読むので、頭は記号に限らず綴りで比べる。
        let head = items.first().map(|h| spelled(source, h)).unwrap_or("");
        let bound = match head {
            "<-" if items.len() >= 3 => Some((items[1], *items.last().unwrap())),
            "val" | "setv" | "var" | ":=" if items.len() == 3 => Some((items[1], items[2])),
            _ => None,
        };
        if let Some((name, expr)) = bound.filter(|(name, _)| matches!(name.node, Node::Symbol)) {
            out.push(Binding { name: norm(spelled(source, name)), expr, in_loop });
        }
    }
    let start = head_symbol(source, &items).and_then(|h| REPEATING.iter().find(|(name, _)| *name == h)).map(|(_, from)| *from);
    for (at, child) in items.iter().enumerate() {
        bindings(source, child, in_loop || start.is_some_and(|from| at >= from), out);
    }
}

/// 結び 1 つ(名・式・繰り返しの中か)。
struct Binding<'f> {
    name: String,
    expr: &'f Form,
    in_loop: bool,
}

/// 今の名と、今 + 秒から結んだ名(`(<- at (time-of-epoch-ms (+ now …)))` の at)。
#[derive(Default)]
struct Moments {
    clocks: BTreeSet<String>,
    relative: BTreeSet<String>,
    /// relative のうち、結んだ式の綴りが届かない後の取り直しを名乗る名。
    retry_named: BTreeSet<String>,
}

impl Moments {
    fn read<'f>(source: &str, clock_heads: &BTreeSet<String>, binds: impl Iterator<Item = &'f Binding<'f>> + Clone) -> Moments {
        let clocks = clock_names(source, clock_heads, binds.clone());
        let mut relative = BTreeSet::new();
        let mut retry_named = BTreeSet::new();
        for bind in binds {
            if has_now_plus(source, bind.expr, &clocks) || mentions(source, bind.expr, &relative) {
                relative.insert(bind.name.clone());
                if has_retry_word(spelled(source, bind.expr)) || mentions(source, bind.expr, &retry_named) {
                    retry_named.insert(bind.name.clone());
                }
            }
        }
        Moments { clocks, relative, retry_named }
    }
}

/// 今の名(今を返す呼び・今の名を 1 つだけ受ける変換の呼びで結んだ名)。
fn clock_names<'f>(source: &str, clock_heads: &BTreeSet<String>, binds: impl Iterator<Item = &'f Binding<'f>>) -> BTreeSet<String> {
    let mut clocks = BTreeSet::new();
    for bind in binds {
        let Some(items) = bind.expr.paren_items().map(visible) else { continue };
        let Some(head) = head_symbol(source, &items) else { continue };
        let args: Vec<&&Form> = items[1..].iter().filter(|f| !matches!(f.node, Node::Keyword)).collect();
        let clock = clock_heads.contains(&norm(last_segment(head)))
            || (args.len() == 1 && matches!(args[0].node, Node::Symbol) && clocks.contains(&root_name(spelled(source, args[0]))));
        if clock {
            clocks.insert(bind.name.clone());
        }
    }
    clocks
}

/// 定義 1 つを今の補助の表で読む。
fn read_definition(units: &[Unit], index: &Index, helpers: &Helpers, me: DefId) -> Facts {
    let unit = &units[me.0];
    let definition = &unit.definitions[me.1];
    let form = &unit.forms[definition.form];
    let source = unit.source.as_str();
    let mut binds = Vec::new();
    bindings(source, form, false, &mut binds);
    let anywhere = Moments::read(source, &index.clock_heads, binds.iter());
    let in_loop = Moments::read(source, &index.clock_heads, binds.iter().filter(|b| b.in_loop));
    let plus_derived = definition
        .params
        .iter()
        .map(|param| {
            let own: BTreeSet<String> = [param.clone()].into();
            let mut names = BTreeSet::new();
            for bind in &binds {
                let mut found = BTreeSet::new();
                plus_operands(source, bind.expr, &own, &mut found);
                if !found.is_empty() || mentions(source, bind.expr, &names) {
                    names.insert(bind.name.clone());
                }
            }
            names
        })
        .collect();
    let derived = definition
        .params
        .iter()
        .map(|param| {
            let mut names: BTreeSet<String> = [param.clone()].into();
            for bind in &binds {
                if mentions(source, bind.expr, &names) {
                    names.insert(bind.name.clone());
                }
            }
            names
        })
        .collect();
    let recursive = definition.callable && calls_itself(source, form, &definition.key, true);
    let mut walk = Walk { units, index, helpers, file: me.0, me, params: &definition.params, anywhere, in_loop, recursive, derived, plus_derived, facts: Facts::default() };
    walk.walk(form, false, false);
    walk.facts
}

/// form の木に、頭の最後の段が heads に在る呼びが在るか。
fn calls_any(source: &str, form: &Form, heads: &BTreeSet<String>) -> bool {
    let own = form.paren_items().map(visible).is_some_and(|items| head_symbol(source, &items).is_some_and(|h| heads.contains(&norm(last_segment(h)))));
    own || children(form).into_iter().any(|c| calls_any(source, c, heads))
}

/// 定義の本文が自分を呼ぶか(頭の form そのものは数えない)。
fn calls_itself(source: &str, form: &Form, key: &str, top: bool) -> bool {
    let own = !top && form.paren_items().map(visible).is_some_and(|items| head_symbol(source, &items).is_some_and(|h| !h.contains('.') && norm(h) == key));
    own || children(form).into_iter().any(|c| calls_itself(source, c, key, false))
}

/// 補助の表を、変わりが無くなるまで読み直して作る。
fn settle(units: &[Unit], index: &Index) -> (Helpers, Vec<(DefId, Facts)>) {
    let ids: Vec<DefId> = units.iter().enumerate().flat_map(|(file, unit)| (0..unit.definitions.len()).map(move |def| (file, def))).collect();
    let mut helpers = Helpers::default();
    for _ in 0..MAX_ROUNDS {
        let read: Vec<(DefId, Facts)> = ids.par_iter().map(|&id| (id, read_definition(units, index, &helpers, id))).collect();
        let mut next = Helpers::default();
        for (id, facts) in &read {
            for (effect, origins) in clause_waits(&units[id.0], &units[id.0].definitions[id.1], facts) {
                next.effects.entry(effect).or_default().extend(origins);
            }
            if !units[id.0].definitions[id.1].callable {
                continue;
            }
            if !facts.passthrough.is_empty() {
                next.passthrough.insert(*id, facts.passthrough.clone());
            }
            if !facts.now_param.is_empty() {
                next.now_param.insert(*id, facts.now_param.clone());
            }
            let once: BTreeSet<Origin> = facts
                .sites
                .iter()
                .filter(|s| !s.repeated && s.kind != SiteKind::PollSeconds)
                .flat_map(|s| match &s.kind {
                    SiteKind::Helper(origins) => origins.clone(),
                    _ => vec![site_origin(&units[id.0], s)],
                })
                .collect();
            if !once.is_empty() {
                next.waits.insert(*id, once);
            }
        }
        if next == helpers {
            return (helpers, read);
        }
        helpers = next;
    }
    let read = ids.par_iter().map(|&id| (id, read_definition(units, index, &helpers, id))).collect();
    (helpers, read)
}

/// defhandler の節ごとに、繰り返しの外の待ちの綴りの元(効果の名は比べの形)。記録の変更の待ち(WatchChanges・WatchEvents)に答える節は
/// 数えない — 変わりを待つ時の上限の秒は DOEFF208 の側。
fn clause_waits(unit: &Unit, definition: &Definition, facts: &Facts) -> Vec<(String, BTreeSet<Origin>)> {
    let source = unit.source.as_str();
    let items = unit.forms[definition.form].paren_items().map(visible).unwrap_or_default();
    if head_symbol(source, &items) != Some("defhandler") {
        return Vec::new();
    }
    items
        .iter()
        .skip(2)
        .filter_map(|clause| {
            let parts = clause.paren_items().map(visible)?;
            let effect = head_symbol(source, &parts)?;
            parts.get(1).and_then(|f| f.bracket_items())?;
            if RECORD_WAITS.contains(&last_segment(effect)) {
                return None;
            }
            let mut plain = BTreeSet::new();
            for statement in &parts[2..] {
                unconditional_heads(source, statement, &mut plain);
            }
            let origins: BTreeSet<Origin> = facts
                .sites
                .iter()
                .filter(|s| !s.repeated && s.kind != SiteKind::PollSeconds && plain.contains(&s.start))
                .flat_map(|s| match &s.kind {
                    SiteKind::Helper(origins) => origins.clone(),
                    _ => vec![site_origin(unit, s)],
                })
                .collect();
            (!origins.is_empty()).then(|| (norm(last_segment(effect)), origins))
        })
        .collect()
}

/// 分岐の form(`if`・`when`・`unless`・`cond`・`match`・`try`・`and`・`or`)を通らずに届く呼びの頭の位置(節が必ず待つ所 — 失敗の枝だけで
/// 待つ節は、その効果を待ちにしない)。
fn unconditional_heads(source: &str, form: &Form, into: &mut BTreeSet<usize>) {
    let Some(items) = form.paren_items().map(visible) else { return };
    let head = head_symbol(source, &items);
    if head.is_some_and(|h| BRANCHES.contains(&h) || spawns(h)) {
        return;
    }
    if let Some(first) = items.first().filter(|_| head.is_some()) {
        into.insert(first.span.start);
    }
    for item in &items[1..] {
        unconditional_heads(source, item, into);
    }
}

fn site_origin(unit: &Unit, site: &Site) -> Origin {
    let line = unit.source[..site.start.min(unit.source.len())].matches('\n').count() + 1;
    let line_text = unit.source.lines().nth(line - 1).unwrap_or("").trim();
    Origin { rel: unit.rel.clone(), line, text: line_text.chars().take(80).collect(), kind: site.refetch }
}

/// 当たりの行か、その直前に続く註だけの行の理由の註を読む。
pub fn reason_at(source: &str, line: usize, refetch: Refetch) -> ReasonRead {
    let lines: Vec<&str> = source.lines().collect();
    let mut candidates = vec![lines.get(line).copied().unwrap_or("")];
    let mut at = line;
    while at > 0 && lines[at - 1].trim_start().starts_with(';') {
        at -= 1;
        candidates.push(lines[at]);
    }
    let Some(text) = candidates.iter().find_map(|l| l.find(REASON_MARKER).map(|from| &l[from + REASON_MARKER.len()..])) else {
        return ReasonRead::Absent;
    };
    match parse_reason(text) {
        Ok(Reason::ReconnectWhileUnreachable) if refetch != Refetch::Retry => ReasonRead::Rejected(
            "「届かない間だけの繋ぎ直し」は区分 届かない後の取り直し(届かない印か失敗の答えが在る間だけ試す所)にだけ書ける".to_string(),
        ),
        Ok(_) => ReasonRead::Accepted,
        Err(ReasonRefusal::OutsideVocabulary(word)) => ReasonRead::Rejected(format!(
            "「{}」は閉じた語彙の外({} <相手> {} <何で確かめたか> / 書くだけ: 生存の印・報告・期限の延長 / 届かない間だけの繋ぎ直し)",
            word, NO_CHANGE_NOTICE, EVIDENCE_SEPARATOR
        )),
        Err(ReasonRefusal::MissingPartyOrEvidence) => ReasonRead::Rejected(format!(
            "「{}」には相手の名と、{} の後に何で確かめたか(相手の API の文書の節・相手の code の行・試した命令と結果)の両方が要る",
            NO_CHANGE_NOTICE.trim_end_matches(':'),
            EVIDENCE_SEPARATOR
        )),
        Err(ReasonRefusal::PartyHasChangeNotice(known)) => {
            ReasonRead::Rejected(format!("この相手は変更の知らせを持つ({}) — その知らせで起きる形にする", known))
        }
    }
}

/// 母集団の file を読んだ物から当たりを出す(検の入口 — file の中身を直に渡す)。
pub fn judge_sources(files: Vec<(String, String)>) -> Vec<RefetchFinding> {
    let units: Vec<Unit> = files.into_par_iter().map(|(rel, source)| Unit::read(rel, source)).collect();
    let index = Index::new(&units);
    let (_, read) = settle(&units, &index);
    let mut found: Vec<RefetchFinding> = read
        .into_iter()
        .flat_map(|(id, facts)| {
            let unit = &units[id.0];
            let lines = LineIndex::new(&unit.source);
            let definition = unit.definitions[id.1].name.clone();
            facts
                .sites
                .into_iter()
                .filter(|s| s.repeated || s.kind == SiteKind::PollSeconds)
                .filter_map(|s| {
                    let range = lines.range(s.start, s.end);
                    let rejected_reason = match reason_at(&unit.source, range.start.line as usize, s.refetch) {
                        ReasonRead::Accepted => return None,
                        ReasonRead::Absent => None,
                        ReasonRead::Rejected(why) => Some(why),
                    };
                    Some(RefetchFinding {
                        rel: unit.rel.clone(),
                        range,
                        definition: definition.clone(),
                        head: s.head,
                        kind: s.kind,
                        refetch: s.refetch,
                        rejected_reason,
                        also_at: Vec::new(),
                    })
                })
                .collect::<Vec<_>>()
        })
        .collect();
    found.sort_by(|a, b| (a.rel.as_str(), a.range.start.line, a.range.start.character).cmp(&(b.rel.as_str(), b.range.start.line, b.range.start.character)));
    let mut merged: Vec<RefetchFinding> = Vec::new();
    let mut first: HashMap<(String, String), usize> = HashMap::new();
    for finding in found {
        let key = (finding.rel.clone(), finding.detail());
        match first.get(&key) {
            Some(&at) => merged[at].also_at.push(finding.range.start.line as usize + 1),
            None => {
                first.insert(key, merged.len());
                merged.push(finding);
            }
        }
    }
    merged
}

/// repo の本番の code の Hy の file を全部判じる(当たり・読めない file の理由)。
pub fn find(root: &Path, decl: &BusinessFakes) -> (Vec<RefetchFinding>, Vec<String>) {
    let files: Vec<(String, std::path::PathBuf)> = hy_files::collect(root)
        .into_iter()
        .filter_map(|path| relative_path(root, &path).map(|rel| (rel, path)))
        .filter(|(rel, _)| production_code(rel, decl))
        .collect();
    let read: Vec<Result<(String, String), String>> = files
        .into_par_iter()
        .map(|(rel, path)| std::fs::read_to_string(&path).map(|source| (rel.clone(), source)).map_err(|error| format!("{}: 読めない: {}", rel, error)))
        .collect();
    let (sources, errors): (Vec<_>, Vec<_>) = read.into_iter().partition(Result::is_ok);
    (judge_sources(sources.into_iter().filter_map(Result::ok).collect()), errors.into_iter().filter_map(Result::err).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn details(files: &[(&str, &str)]) -> Vec<String> {
        judge_sources(files.iter().map(|(r, s)| (r.to_string(), s.to_string())).collect()).iter().map(RefetchFinding::detail).collect()
    }

    #[test]
    fn timers_on_now_plus_seconds_in_loops_are_hit_and_one_shot_deadlines_are_not() {
        let source = "(defk poll [s]\n  (while True\n    (<- now (GetTime))\n    (<- (ArmTimer TAG (+ now (timedelta :seconds s))))\n    (<- (WaitForEvent TimerFired))\n    (<- (Read))))\n\
                      (defk once [s]\n  (<- now (GetTime))\n  (<- (ArmTimer TAG (+ now (timedelta :seconds s)))))\n\
                      (defk until [row]\n  (while True\n    (<- (ArmTimer TAG row.deadline))\n    (<- (Read))))\n\
                      (defk sleeps []\n  (while True (<- (Delay 5.0)) (<- (Delay 0)) (<- (Read))))\n";
        assert_eq!(details(&[("a.hy", source)]), vec!["poll::ArmTimer::periodic", "sleeps::Delay::periodic"]);
    }

    #[test]
    fn helpers_are_collected_then_their_callers_in_loops_are_hit() {
        let helper = "(defk arm-next [s]\n  (<- now (GetTime))\n  (<- (ArmTimer TAG (+ now (timedelta :seconds s)))))\n\
                      (defk at-ms [tag moment]\n  (<- at (time-of-epoch-ms moment))\n  (ArmedTimer tag at))\n\
                      (defk wanted [now wait]\n  #((ArmedTimer :tag PULSE :at (+ now wait))))\n";
        let caller = "(import app.help [arm-next at-ms wanted])\n\
                      (defk loop-a [s] (while True (<- (arm-next s)) (<- (Read))))\n\
                      (defk loop-b [] (while True (<- now int (now-ms)) (<- (at-ms RETRY-TAG (+ now 2000))) (<- (Read))))\n\
                      (defk loop-c [] (event-loop [x X (start)] (Moved) (do (<- now (GetTime)) (! (wanted now 5)))))\n\
                      (defk loop-d [row] (while True (<- (at-ms END-TAG row.end)) (<- (Read))))\n";
        assert_eq!(
            details(&[("app/help.hy", helper), ("app/use.hy", caller)]),
            vec!["loop-a::arm-next::periodic", "loop-b::at-ms::retry", "loop-c::wanted::periodic"]
        );
    }

    #[test]
    fn moments_fixed_before_the_loop_are_not_hit_and_moments_bound_in_the_loop_are() {
        let source = "(defk armed-at [tag moment]\n  (<- at (time-of-epoch-ms moment))\n  (ArmedTimer tag at))\n\
                      (defk run []\n  (<- started (now-ms))\n  (val end-at (+ started 1000))\n  (var again-at None)\n  (while True\n\
                      \x20   (<- (armed-at END-TAG end-at))\n    (<- (armed-at PATIENCE-TAG (+ started 500)))\n    (<- now (now-ms))\n\
                      \x20   (:= again-at (+ now 2000))\n    (<- (armed-at WAKE-TAG again-at))\n    (<- (Read))))\n";
        assert_eq!(details(&[("a.hy", source)]), vec!["run::armed-at::periodic"]);
        let found = judge_sources(vec![("a.hy".into(), source.into())]);
        assert_eq!(found[0].range.start.line, 12);
    }

    #[test]
    fn now_bound_through_names_and_clock_helpers_are_followed() {
        let source = "(defk wall-ms-now []\n  (<- t (GetTime))\n  (epoch-ms-of-time t))\n\
                      (defk retry-deadlines [now retry-ms]\n  (<- at (time-of-epoch-ms (+ now retry-ms)))\n  #((ArmedTimer RETRY-TAG at)))\n\
                      (defk wanted [now retry-ms]\n  (<- retries (retry-deadlines now retry-ms))\n  retries)\n\
                      (defk run []\n  (while True\n    (<- now (wall-ms-now))\n    (<- (rearm (wanted now 2000)))\n    (<- (Read))))\n";
        assert_eq!(details(&[("a.hy", source)]), vec!["run::wanted::retry"]);
    }

    #[test]
    fn same_shape_twice_in_one_definition_is_one_finding() {
        let source = "(defk ticked []\n  (<- now (GetTime))\n  (<- (ArmTimer TAG (+ now (timedelta :seconds 5)))))\n\
                      (defk run []\n  (event-loop [s S (! (ticked))]\n    (Moved) (! (ticked))\n    (TimerFired) (! (ticked))))\n";
        let found = judge_sources(vec![("a.hy".into(), source.into())]);
        assert_eq!(found.iter().map(RefetchFinding::detail).collect::<Vec<_>>(), vec!["run::ticked::periodic"]);
        assert_eq!((found[0].range.start.line, found[0].also_at.clone()), (5, vec![7]));
    }

    #[test]
    fn effects_answered_by_a_timed_wait_and_module_sleeps_are_followed() {
        let handler = "(defk tick-pause [s]\n  (<- (Delay s)))\n\
                       (defhandler pauses\n  (AwaitNextTick [s]\n    (<- (tick-pause s))\n    (resume None))\n  \
                       (WatchChanges [t c timeout]\n    (<- (Delay timeout))\n    (resume None)))\n";
        let user = "(defk run []\n  (while True\n    (<- (AwaitNextTick 1.0))\n    (<- (WatchChanges T C 5))\n    (<- (Read))))\n\
                    (defhandler os [p]\n  (Wait [x]\n    (while (alive? p)\n      (time.sleep 0.02))\n    (resume None)))\n";
        assert_eq!(details(&[("h.hy", handler), ("u.hy", user)]), vec!["run::AwaitNextTick::periodic", "os::sleep::periodic"]);
    }

    /// cisco-c8 の決定 2026-10-07(agora-redesign #3834): 止めの合図を待つ上限の期限(来たら繰り返しを抜けるだけ — agora-controllers の
    /// task_attempt.hy の pumping-loop の deadline-passed の形)は当てない。同じ形でも、期限が来た後に同じ読みを繰り返す本体なら当たる。
    #[test]
    fn a_deadline_that_only_ends_the_wait_is_not_hit_but_one_that_reads_again_is() {
        let shape = |after_stop_wait: &str| {
            format!(
                "(val DEADLINE-TAG \"t:deadline\")\n(val STOP-WAIT-TAG \"t:stop-wait\")\n\
                 (defk deadline-passed [p]\n  (<- read (stopped-by p))\n  (when (not (finished? read))\n    (<- now (GetTime))\n    \
                 (<- (ArmTimer STOP-WAIT-TAG (+ now (timedelta :seconds STOP-WAIT-SECONDS)))))\n  read)\n\
                 (defk pumping-loop [first]\n  (event-loop [p P first]\n    (Moved) (! (read-on p))\n    (TimerFired :tag tag) (cond\n      \
                 (= tag DEADLINE-TAG) (let [read (! (deadline-passed p))] (if (! (finished? read)) (stop read) read))\n      \
                 (= tag STOP-WAIT-TAG) {}\n      True p)))\n",
                after_stop_wait
            )
        };
        assert!(details(&[("a.hy", &shape("(stop p)"))]).is_empty(), "{:?}", details(&[("a.hy", &shape("(stop p)"))]));
        assert_eq!(details(&[("a.hy", &shape("(! (read-on p))"))]), vec!["pumping-loop::deadline-passed::periodic"]);
    }

    #[test]
    fn reasons_are_a_closed_vocabulary() {
        // 相手と確かめの根拠が在れば通る。
        assert_eq!(
            parse_reason(" 相手に変更の知らせが無い: 預かり所 — custody の API の文書 3 節に watch が無い"),
            Ok(Reason::NoChangeNotice { party: "預かり所".into(), evidence: "custody の API の文書 3 節に watch が無い".into() })
        );
        // 根拠が無い・相手が無い = 通さない。
        for text in ["相手に変更の知らせが無い: 預かり所", "相手に変更の知らせが無い: 預かり所 — ", "相手に変更の知らせが無い: — 文書 3 節"] {
            assert_eq!(parse_reason(text), Err(ReasonRefusal::MissingPartyOrEvidence), "{}", text);
        }
        // 変更の知らせを持つと分かっている相手 = 通さない(大小文字・空白・別名を問わない)。
        for (party, known) in [
            ("Kubernetes", "Kubernetes"),
            ("kubernetes の API", "Kubernetes"),
            ("K8s", "Kubernetes"),
            ("doeff-cluster の Coordinator", "coordinator の GET /watch"),
            ("doeff_events の基盤", "doeff-events の基盤"),
            ("記録の service", "記録の service の変更の合図"),
        ] {
            let text = format!("相手に変更の知らせが無い: {} — 文書を読んだ", party);
            assert_eq!(parse_reason(&text), Err(ReasonRefusal::PartyHasChangeNotice(known)), "{}", text);
        }
        assert_eq!(parse_reason("書くだけ: 生存の印"), Ok(Reason::WriteOnly(WriteOnly::Liveness)));
        // 閉じた語彙の外の語は通さない(閉じた語彙から外した綴りもここに入る)。
        assert_eq!(parse_reason("仕方ない"), Err(ReasonRefusal::OutsideVocabulary("仕方ない".into())));
        let known = "(defk a []\n  (while True\n    ;; 時間で取り直す理由: 相手に変更の知らせが無い: Kubernetes — 試した\n    (<- (Delay 1.0))\n    (<- (Read))))\n";
        assert!(matches!(reason_at(known, 3, Refetch::Periodic), ReasonRead::Rejected(why) if why.contains("この相手は変更の知らせを持つ")));
        let source = "(defk a []\n  (while True\n    ;; 時間で取り直す理由: 届かない間だけの繋ぎ直し\n    (<- (Delay 1.0))\n    (<- (Read))))\n";
        assert!(matches!(reason_at(source, 3, Refetch::Periodic), ReasonRead::Rejected(_)));
        assert_eq!(reason_at(source, 3, Refetch::Retry), ReasonRead::Accepted);
        assert_eq!(reason_at(source, 1, Refetch::Retry), ReasonRead::Absent);
    }
}
