//! 意味の規則(DOEFF201・202)— 決定的な規則では読めない「コードが何をしているか」を Jev(TypeSafe の System One の model)に問う。
//! 宛先(URL・model・通信の形・API キー)の決め方は doeff の packages/doeff-jev/src/doeff_jev/target.py の写し(下の resolve_target)、
//! 通信の形(direct = TypeSafe の /v1/systemone・gateway = Vercel AI Gateway の evaluation-model v4)は同じ package の wire.py の写し。
//!
//! - 問いの文(instructions・criteria)はこの file の 1 か所の宣言(英語のまま — jev-lint の questions.py の J2・J3 と同じ内容)。
//!   DOEFF201・202・205 の問いには、repo の architecture.hy の :semantic-lines(どこからが違反かの線引きの文と鳴る例・鳴らない例)を
//!   instructions の lines に入れる(SemanticQuestion::wire_with — 文と例の定義元は architecture.hy・agora-redesign #1909)。
//! - gateway を呼ぶのは `--semantic` / `--semantic-all` の時だけ。決定的な規則の実行(エディタの保存ごと・hook)は cache を読むだけ。
//! - cache の答えが無い定義は違反にせず「未判定」の数に出す(合格に倒さない)。
//! - 重さは warning か info だけ(当たり外れを測り終えるまで error にしない — 設定でも選べない)。
//! - 較正の見張り: 問いを撃つ実行ごとに、既知の正例と反例(data/semantic_calibration.json)を 1 回ずつ問い、確率が幅の外なら cache を捨てて警告する。
//! API キーの値は、設定・出力・log・cache の鍵に書かない。
//!
//! Jev の呼び出しを覚える代理(repo proboscis/jev-proxy — 2026-10-01 に doeff の packages/doeff-jev-proxy から移した・agora-redesign #843・#1919):
//! - 宛先は repo ごとの設定 `[tool.doeff-linter.semantic] proxy_url` で向ける(機体全体の環境変数にしない — 会社の repo は向けない)。
//!   env の JEV_BASE_URL が在ればそちらが勝つ。代理へは代理の token(proxy_token_file)だけを送り、TypeSafe のキーは送らない。
//! - 決定的な規則の全体の実行(hook・引数なしの実行)は、手元の cache に無い定義を代理に「覚えている時だけ」問い、返った答えを手元の
//!   cache に書く。本物の Jev は呼ばない。問いは定義 1 つずつではなく、代理の鍵(proxy_key — 本文を正規化した sha256)の束
//!   (POST <proxy_url>/peek・PEEK_BATCH 個ずつ)で撃つ — 定義が数千ある repo でも往復が数回で済み、本文を送らない。
//!   代理に届かない・時間切れ(proxy_peek_timeout_ms・既定 DEFAULT_PROXY_PEEK_TIMEOUT_MS)の時は手元の cache だけで動く。
//! - 較正の見張りの問いは覚えを使わない(Cache-Control: no-cache — model の中身が変わったことを代理の覚えが隠さないため)。
//! - `--semantic-changed` は、指定の file のうち手元の cache に答えの無い定義(= 中身が変わった定義)だけを問う。
//!   読み取りで壊れた箇所を含む定義(書きかけ)はどの実行でも問わない。

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use rayon::prelude::*;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use super::architecture::{LineExample, SemanticLine};
use super::settings::{LayerDescription, LayerId};

/// 意味の問いの種類(閉じた集合)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum SemanticQuestion {
    /// J2: 定義が要求の言い換えを越えて業務の判断をしているか(protocol の層に当てる)。
    BusinessDecision,
    /// J3: 定義が通信の手段を知っているか(core の層に当てる)。
    TransportKnowledge,
    /// deff が本当に素の関数でなければならない理由を、architecture.hy の種類の一覧 + none から選ぶ(Choice)。
    PlainCallable,
    /// 処理を持つ method のある class が value / external-world / stateful / other のどれか(Choice・DOEFF204)。
    ClassRole,
    /// 役が judgment / program の定義が、入力の形の検めと業務の判断を混ぜているか(Choice・DOEFF205)。
    MixedConcerns,
}

impl SemanticQuestion {
    /// 全部の問い。
    pub const ALL: [SemanticQuestion; 2] = [SemanticQuestion::BusinessDecision, SemanticQuestion::TransportKnowledge];

    /// architecture.hy の :semantic-lines で線引きを入れられる問い(DOEFF201・202・205 — agora-redesign #1909)。
    pub const LINED: [SemanticQuestion; 3] = [SemanticQuestion::BusinessDecision, SemanticQuestion::TransportKnowledge, SemanticQuestion::MixedConcerns];

    /// 規則の ID(DOEFF201 …)。
    pub fn id(self) -> &'static str {
        match self {
            SemanticQuestion::BusinessDecision => "DOEFF201",
            SemanticQuestion::TransportKnowledge => "DOEFF202",
            SemanticQuestion::PlainCallable => "DOEFF203",
            SemanticQuestion::ClassRole => "DOEFF204",
            SemanticQuestion::MixedConcerns => "DOEFF205",
        }
    }

    /// 線引きを入れた問い(agora-redesign #1909)— architecture.hy の :semantic-lines のうちこの問いに当たる線引きの名・文・鳴る例・鳴らない例を
    /// instructions に入れ、線引きの読み方と答えの向きを lines_note に書く。例は `{"code", "why"}` の組(why を書いた例だけ why を持つ・#1995)で、
    /// code は定義の source と同じく :tags を消して渡す(申告に引きずられないため)。
    /// instructions は鍵の object ではなく、1 つの鍵の object を宣言の順に並べた列で送る(#1995 — object の鍵は名の順に並び、DOEFF205 では
    /// 一般の注が線引きの後ろに来ていた): 問いの部品(instruction_parts の順)→ lines_note → lines。線引きが最後に来て一般の文に勝つ順になる。
    /// 当たる線引きが無ければ wire() のまま — 宣言の無い repo の問いと cache のキーは変わらない。
    pub fn wire_with(self, lines: &[SemanticLine]) -> Value {
        let examples = |all: &[LineExample]| -> Vec<Value> {
            all.iter()
                .map(|example| match &example.why {
                    Some(why) => json!({"code": strip_tags(&example.code), "why": why}),
                    None => json!({"code": strip_tags(&example.code)}),
                })
                .collect()
        };
        let mine: Vec<Value> = lines
            .iter()
            .filter(|line| line.rules.contains(&self))
            .map(|line| {
                json!({
                    "name": line.name,
                    "text": line.text,
                    "violating_examples": examples(&line.fires),
                    "complying_examples": examples(&line.silent),
                })
            })
            .collect();
        let mut question = self.wire();
        let Some(note) = self.lines_note().filter(|_| !mine.is_empty()) else {
            return question;
        };
        let ordered: Vec<Value> = self
            .instruction_parts()
            .into_iter()
            .chain([("lines_note", Value::String(note.to_string())), ("lines", Value::Array(mine))])
            .map(|(name, content)| Value::Object(serde_json::Map::from_iter([(name.to_string(), content)])))
            .collect();
        question["instructions"] = Value::Array(ordered);
        question
    }

    /// 線引きを入れる問い(LINED)の instructions の部品を宣言の順に(鍵・中身 — 問いの文は英語でここ 1 か所)。wire() は部品を鍵の object に
    /// して送り、wire_with はこの順の列で送る。
    /// 線引きを入れない問い(DOEFF203・204)の instructions は wire() の 1 か所に在り、ここは空。
    fn instruction_parts(self) -> Vec<(&'static str, Value)> {
        match self {
            SemanticQuestion::BusinessDecision => vec![
                ("question", json!("Does the code in `definition.source` make a business decision, beyond rephrasing a request into the other party's way of talking?")),
                (
                    "business_decision_examples",
                    json!([
                        "deciding who is allowed to do something",
                        "enforcing a domain rule such as which participants a chat may have or where a reply may be posted",
                        "choosing recipients or a business outcome"
                    ]),
                ),
                (
                    "not_business_decision_examples",
                    json!([
                        "building URLs, headers or request bodies",
                        "parsing JSON, HTTP status codes or process output into typed values",
                        "turning transport failures into error values",
                        "retry and timeout policy",
                        "checking the shape of a payload or reading configuration"
                    ]),
                ),
            ],
            // 枠は 2 つを同じ重さで問う(agora-redesign #2059): 通信の手段と、外の data の型の無い形(dict・JSON の値を欄名で読む・組む)。前の枠は
            // 通信の手段だけで、json.loads を書かない欄名の読みを repo の線引きと例で足しても、似た形の別の定義へ広がらなかった(#2044・#2052)。
            // 欄の既定値は「欄名で読む」の内に数えるだけで、既定に倒すのが誰の仕事かは言わない(それは DOEFF201 の判断の問い)。
            SemanticQuestion::TransportKnowledge => vec![
                (
                    "question",
                    json!("Does the code in `definition.source` know either of these two things, which belong outside the core: (a) how communication is carried out: URLs or URL paths and query strings, HTTP methods, status codes or headers, JSON encoding and decoding, SQL, or network endpoint addresses; or (b) the untyped shape of outside data: reading fields of an untyped dict or JSON value by field name, parsing such a value into typed values, or building a dict by field names to become a JSON value?"),
                ),
                (
                    "note",
                    json!("(b) weighs the same as (a). Reading, parsing and building untyped dicts or JSON values by field name is shape checking, the protocol side's job; the core receives and returns only typed values. (b) counts whether or not the code calls json.loads or json.dumps: the value may have been decoded elsewhere and passed in as a dict, or be encoded elsewhere after the code builds it. A default for a missing field does not make the read typed; the code still reads the field by name. TOML, YAML and other document formats count the same as JSON. A dict that is not outside data does not count: a constant table in the code, or a tally the definition builds from typed values."),
                ),
                (
                    "transport_knowledge_examples",
                    json!([
                        "sending an HTTP request: a URL built from a base and a route, headers, and a body encoded with json.dumps",
                        "taking a dict that was decoded elsewhere (no json.loads in the code) and reading `payload[\"body\"]`, `(.get payload \"from\" \"\")` or `(get row \"accountKey\")` to build a typed value or to choose one",
                        "building `{\"name\" name \"state\" state}` by field names and passing it on to be stored or sent as JSON"
                    ]),
                ),
                (
                    "not_transport_knowledge_examples",
                    json!([
                        "a core function that receives typed values (records, enums, tuples of records), decides by their attributes with a business rule, and returns a typed value; the protocol side turned the outside data into those values and turns the result back",
                        "asking an outside system through a business-level effect or port, such as `(<- reply (SendMail draft))`, without its URL, field names or encoding"
                    ]),
                ),
            ],
            // 注は線引き 4(agora-controllers の architecture.hy)の例外と同じ文(#1995 — 前の注は isinstance と dict の読みを例外なしに形の確認と
            // 言い、線引きと食い違った: union の枝分けの _on-text・模擬の世界の dict の list-nodes・任せた確認の答えで断る open-chat-room が高く出た)。
            SemanticQuestion::MixedConcerns => vec![
                ("question", json!("What does the definition in `definition.source` do? `layer` describes what code in its layer should know and not know.")),
                (
                    "note",
                    json!("Shape checking means validating untyped input that came from outside: reading keys out of dicts or JSON payloads, isinstance checks, and empty, missing, length, format or vocabulary checks on those raw values before they can be used. These are not shape checking: branching on the cases of a typed union (for example isinstance between the declared alternatives of `A | B | None`); reading an untyped dict that did not come from outside (a constant in the code, or the definition's own tally); and a branch that only returns a refusal value built from the answer (the problem text) of one function that the shape check was left to. Business judgment means deciding by business rules (who may do what, which outcome, which write). Comparing already-typed values by a business rule is judgment, not shape checking."),
                ),
            ],
            SemanticQuestion::PlainCallable | SemanticQuestion::ClassRole => Vec::new(),
        }
    }

    /// 部品を鍵の object にした instructions。
    fn instructions_of(parts: Vec<(&'static str, Value)>) -> Value {
        Value::Object(parts.into_iter().map(|(name, content)| (name.to_string(), content)).collect())
    }

    /// 線引きの読み方と答えの向き(問いの文は英語でここ 1 か所)。線引きを入れない問いは None。
    fn lines_note(self) -> Option<&'static str> {
        match self {
            SemanticQuestion::BusinessDecision => Some(
                "`lines` are the boundaries this repository adopted for this question; where they differ from the general examples above, follow the lines. Code like a line's `violating_examples` makes a business decision (answer true); code like its `complying_examples` does not (answer false). An example's `why` says what in the line decides it; judge the definition by that reason, not by how much its code looks like the example.",
            ),
            SemanticQuestion::TransportKnowledge => Some(
                "`lines` are the boundaries this repository adopted for this question; where they differ from the general wording above, follow the lines. Code like a line's `violating_examples` knows how communication is carried out (answer true); code like its `complying_examples` does not (answer false). An example's `why` says what in the line decides it; judge the definition by that reason, not by how much its code looks like the example.",
            ),
            SemanticQuestion::MixedConcerns => Some(
                "`lines` are the boundaries this repository adopted for this question; where they differ from the note above, follow the lines. Code like a line's `violating_examples` is `mixed`; code like its `complying_examples` is not `mixed`. An example's `why` says what in the line decides it; judge the definition by that reason, not by how much its code looks like the example.",
            ),
            SemanticQuestion::PlainCallable | SemanticQuestion::ClassRole => None,
        }
    }

    /// DOEFF203 の問い(Choice)。criteria は architecture.hy の受け入れる理由・受け入れない理由の型(名 → 説明)と none。問いの文は英語でここ 1 か所。
    pub fn plain_callable_wire(accepted: &[(String, String)], rejected: &[(String, String)]) -> Value {
        let mut criteria = serde_json::Map::new();
        for (name, description) in accepted {
            criteria.insert(name.clone(), Value::String(format!("Acceptable reason: {}", description)));
        }
        for (name, description) in rejected {
            criteria.insert(name.clone(), Value::String(format!("Not an acceptable reason: {}", description)));
        }
        criteria.insert(
            "none".to_string(),
            Value::String("No listed reason fits, and the stated reason does not show that code outside doeff must call this definition as a plain function; it could be a doeff Program (defk).".to_string()),
        );
        json!({
            "type": "choice",
            "instructions": {
                "question": "Is the reason in `stated_reason` an acceptable reason for the definition in `definition.source` to be a plain Python callable (deff) instead of a doeff Program (defk)? Choose the listed reason that actually describes the situation, judged by the code and by who calls it.",
                "note": "A plain callable is acceptable only when code outside doeff calls it directly with a fixed signature that cannot run a Program. Reading configuration or environment variables, test helpers, and assembling handler lists are not acceptable: those can be doeff Programs (Ask and other effects, deftest, a defk that returns the handlers)."
            },
            "criteria": Value::Object(criteria)
        })
    }

    /// Jev へ渡す問い(direct の形 — type は noul。gateway の形へは gateway_question で写す)。jev-lint の questions.py の J2・J3 と同じ内容。
    pub fn wire(self) -> Value {
        match self {
            SemanticQuestion::BusinessDecision => json!({
                "type": "noul",
                "instructions": Self::instructions_of(self.instruction_parts()),
                "criteria": {
                    "true": "The code decides a business matter (permission, domain rule, recipient, business outcome).",
                    "false": "The code only translates: it builds or reads the wire form, maps failures, or checks shapes."
                }
            }),
            SemanticQuestion::PlainCallable => Self::plain_callable_wire(&[], &[]),
            SemanticQuestion::MixedConcerns => json!({
                "type": "choice",
                "instructions": Self::instructions_of(self.instruction_parts()),
                "criteria": {
                    "mixed": "It both checks the shape of untyped input and makes business decisions in the same definition.",
                    "shape-only": "It only checks or parses the shape of untyped input; it makes no business decision.",
                    "judgment-only": "It only makes business decisions from typed values; it does not check the shape of untyped input.",
                    "neither": "None of the above (for example plain data plumbing or formatting)."
                }
            }),
            SemanticQuestion::ClassRole => json!({
                "type": "choice",
                "instructions": {
                    "question": "What is the class in `definition.source` in a doeff (algebraic effects) Hy code base? `definition.fields` lists its declared fields with their type annotations.",
                    "note": "Judge by what the methods do with the fields, not by the class name. A class that only receives a client, store, connection or session from outside and calls it in its methods is external-world even when no raw I/O call is visible."
                },
                "criteria": {
                    "value": "A value class: methods only compute new values from its own fields (like Point2D.norm or add); no fields are changed and no outside system is reached.",
                    "external-world": "A window to the outside world: it holds a client, store, connection, file handle or session (usually passed in from outside) as a field and its methods use it to read or write the outside world.",
                    "stateful": "It holds changing state: its methods change its own fields or change the state of another object passed to it (append, update, assignment).",
                    "other": "None of the above (for example a protocol adapter required by a library, or a pure namespace of helpers)."
                }
            }),
            SemanticQuestion::TransportKnowledge => json!({
                "type": "noul",
                "instructions": Self::instructions_of(self.instruction_parts()),
                "criteria": {
                    "true": "The code knows (a) or (b): for example a URL, a query string, an HTTP request or status code, json.loads or json.dumps of a wire body, an endpoint URL field, or fields of an untyped dict or JSON value read or built by field name, with or without json.loads.",
                    "false": "The code only receives, decides with and returns typed values and business-level request effects; naming an outside system without its transport details or its field names does not count."
                }
            }),
        }
    }

    /// 問いの意味(説明の文に差し込む日本語)。
    pub fn meaning(self) -> &'static str {
        match self {
            SemanticQuestion::BusinessDecision => "要求の言い換えを越えて、業務の判断(誰に許すか・業務の決まり・宛先・業務の結果)をしている",
            SemanticQuestion::TransportKnowledge => "通信の手段(URL や query・HTTP の method や status・JSON の wire・SQL・宛先の address)か、外の data の型の無い形(dict・JSON の値を欄名で読む・組む)を知っている",
            SemanticQuestion::PlainCallable => "名乗った理由の種類では、素の関数でなければならない理由にならない見込み",
            SemanticQuestion::ClassRole => "処理を持つ method のある class が、外の世界の窓口か状態を持つ物の見込み",
            SemanticQuestion::MixedConcerns => "判断の定義が、入力の形の検めと業務の判断を混ぜている見込み",
        }
    }
}

/// `[tool.doeff-linter.semantic]` の問い 1 つの設定(読んだ形)。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct QuestionSection {
    /// 問いを当てる層の名。
    #[serde(default)]
    pub layers: Vec<String>,
    /// この確率以上で warning。
    pub warning: Option<f64>,
    /// この確率以上で info。
    pub info: Option<f64>,
    /// 置けない欄(Jev の判定は error にしない)— 知らない鍵として読み飛ばさず、書かれていれば設定の誤りにするために読む。
    pub error: Option<f64>,
}

/// `[tool.doeff-linter.semantic]`(読んだ形)。重さは warning と info だけで、error の欄は無い。宛先・model・キーはここに書かない
/// (doeff-jev と同じ決め方 — 環境変数 JEV_* と ~/.config/jev/client.json)。例外は Jev の呼び出しを覚える代理の宛先 proxy_url(repo ごとに向ける)。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct SemanticSection {
    #[serde(default)]
    pub business_decision: QuestionSection,
    #[serde(default)]
    pub transport_knowledge: QuestionSection,
    /// DOEFF203: 理由を受け入れるかの閾値(受け入れない答えの確率で warning / info)。
    pub plain_callable: Option<PlainCallableSection>,
    /// DOEFF204: Jev が external-world / stateful を選んだ確率の閾値(warning_min・info_min)。
    pub class_role: Option<PlainCallableSection>,
    /// DOEFF205: 問う定義の役・物差しの層・閾値。
    pub mixed_concerns: Option<MixedConcernsSection>,
    pub workers: Option<usize>,
    pub timeout_seconds: Option<u64>,
    pub source_limit: Option<usize>,
    /// Jev の呼び出しを覚える代理の宛先(例 http://jev-proxy.example:8878/v1/systemone)。無ければ代理を使わない。
    pub proxy_url: Option<String>,
    /// 代理の身元の token の file(既定 ~/.config/jev/proxy-token)。
    pub proxy_token_file: Option<String>,
    /// 覚えている時だけの問いの時間の上限(ms・既定 5000 — 全部の束を合わせた上限)。
    pub proxy_peek_timeout_ms: Option<u64>,
    /// 誤判定の一覧の dir(repo の根からの相対・1 鍵 1 file・2 行目から後が人の判定の理由)。載った Jev の当たりは出さず、件数にも入れない。
    #[serde(default)]
    pub false_positives: Vec<String>,
    /// 人が「本当の違反」と判定した当たりの一覧の dir(形は誤判定の一覧と同じ)。違反の出し方は変えず、当たり外れを測る正例として読む。
    #[serde(default)]
    pub true_positives: Vec<String>,
}

/// `[tool.doeff-linter.semantic] mixed_concerns`(読んだ形)。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct MixedConcernsSection {
    /// 問う定義の役(タグの :role — 既定 judgment・program)。
    #[serde(default)]
    pub roles: Vec<String>,
    /// 物差しの層(その層の説明を Jev に渡す — 例 core)。
    pub layer: String,
    /// Jev が mixed を選び、その確率がこれ以上で warning(既定 0.7)。
    pub warning_min: Option<f64>,
    /// これ以上で info(既定 0.5)。
    pub info_min: Option<f64>,
}

/// DOEFF205 の設定(検めた後)。
#[derive(Debug, Clone)]
pub struct MixedConcernsSettings {
    pub roles: BTreeSet<String>,
    pub layer: LayerId,
    pub warning_min: f64,
    pub info_min: f64,
}

/// 代理の設定(検めた後)。
#[derive(Debug, Clone)]
pub struct ProxySettings {
    pub url: String,
    pub token_file: String,
    pub peek_timeout: Duration,
}

/// 代理の token の file の既定の置き場。
pub const DEFAULT_PROXY_TOKEN_FILE: &str = "~/.config/jev/proxy-token";
/// 覚えている時だけの問いの時間の上限の既定(ms — 全部の束を合わせた上限。遅い網の機体から数千の鍵を送っても収まる長さ)。
pub const DEFAULT_PROXY_PEEK_TIMEOUT_MS: u64 = 5000;
/// 覚えている時だけの問いの束 1 つの鍵の数(代理の上限 20000 の内 — 鍵 1 つは 67 byte 前後)。
pub const PEEK_BATCH: usize = 1000;
/// 代理の鍵の決まりの版(proboscis/jev-proxy の src/doeff_jev_proxy/key.hy の KEY-VERSION と同じ)。
pub const PROXY_KEY_VERSION: &str = "jev-proxy-key-1";

/// `[tool.doeff-linter.semantic] plain_callable`(読んだ形)。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct PlainCallableSection {
    /// Jev が受け入れない答え(受け入れない型か none)を選び、その確率がこれ以上で warning(既定 0.4)。
    pub warning_min: Option<f64>,
    /// 受け入れる答えを選んでも、受け入れない答えの確率の和がこれ以上なら info(既定 0.4)。
    pub info_min: Option<f64>,
}

/// DOEFF203 の設定(検めた後)。
#[derive(Debug, Clone, Copy)]
pub struct PlainCallableSettings {
    pub warning_min: f64,
    pub info_min: f64,
}

/// 問い 1 つの設定(検めた後)。
#[derive(Debug, Clone)]
pub struct QuestionSettings {
    pub layers: BTreeSet<LayerId>,
    pub warning: f64,
    pub info: f64,
}

/// 意味の規則の設定(検めた後)。
#[derive(Debug, Clone)]
pub struct SemanticSettings {
    pub plain_callable: Option<PlainCallableSettings>,
    pub class_role: Option<PlainCallableSettings>,
    pub mixed_concerns: Option<MixedConcernsSettings>,
    pub questions: BTreeMap<SemanticQuestion, QuestionSettings>,
    pub workers: usize,
    pub timeout: Duration,
    pub source_limit: usize,
    /// Jev の呼び出しを覚える代理(無ければ使わない)。
    pub proxy: Option<ProxySettings>,
    /// 誤判定の一覧の dir(repo の根からの相対)。
    pub false_positives: Vec<String>,
    /// 人が本当の違反と判定した当たりの一覧の dir(repo の根からの相対)。
    pub true_positives: Vec<String>,
    /// Jev の問いに入れる線引き(architecture.hy の :semantic-lines — 無ければ空で、問いは今のまま・agora-redesign #1909)。
    /// TOML の節には書かず、設定の組み立て(config.rs)が architecture.hy から取り込む — 文の定義元は architecture.hy の 1 か所。
    pub lines: Vec<SemanticLine>,
}


impl SemanticSettings {
    /// 読んだ節を検める(層の名は find で引く・閾値は 0〜1 で info ≤ warning)。
    pub fn validate(section: &SemanticSection, find: &mut dyn FnMut(&str, &str) -> Option<LayerId>, problems: &mut Vec<String>) -> SemanticSettings {
        let mut questions = BTreeMap::new();
        for (question, part, name, (warning, info)) in [
            (SemanticQuestion::BusinessDecision, &section.business_decision, "business_decision", (0.8, 0.6)),
            (SemanticQuestion::TransportKnowledge, &section.transport_knowledge, "transport_knowledge", (0.6, 0.4)),
        ] {
            let layers: BTreeSet<LayerId> =
                part.layers.iter().filter_map(|layer| find(layer, &format!("semantic.{}.layers", name))).collect();
            if part.error.is_some() {
                problems.push(format!("semantic.{}.error: Jev の判定の重さは warning と info だけ(error にしない)", name));
            }
            let warning = part.warning.unwrap_or(warning);
            let info = part.info.unwrap_or(info);
            if !(0.0..=1.0).contains(&warning) || !(0.0..=1.0).contains(&info) || info > warning {
                problems.push(format!("semantic.{}: 閾値は 0〜1 で info ≤ warning(warning = {}・info = {})", name, warning, info));
            }
            if !layers.is_empty() {
                questions.insert(question, QuestionSettings { layers, warning, info });
            }
        }
        let mut thresholds = |section: Option<&PlainCallableSection>, name: &str, warning: f64, info: f64| {
            section.map(|p| {
                let settings = PlainCallableSettings { warning_min: p.warning_min.unwrap_or(warning), info_min: p.info_min.unwrap_or(info) };
                if !(0.0..=1.0).contains(&settings.warning_min) || !(0.0..=1.0).contains(&settings.info_min) {
                    problems.push(format!("semantic.{}: 閾値は 0〜1(warning_min = {}・info_min = {})", name, settings.warning_min, settings.info_min));
                }
                settings
            })
        };
        let plain_callable = thresholds(section.plain_callable.as_ref(), "plain_callable", 0.4, 0.4);
        let class_role = thresholds(section.class_role.as_ref(), "class_role", 0.7, 0.5);
        let mixed_concerns = section.mixed_concerns.as_ref().and_then(|m| {
            let layer = find(&m.layer, "semantic.mixed_concerns.layer")?;
            let settings = MixedConcernsSettings {
                roles: if m.roles.is_empty() { ["judgment", "program"].iter().map(|r| r.to_string()).collect() } else { m.roles.iter().cloned().collect() },
                layer,
                warning_min: m.warning_min.unwrap_or(0.7),
                info_min: m.info_min.unwrap_or(0.5),
            };
            if !(0.0..=1.0).contains(&settings.warning_min) || !(0.0..=1.0).contains(&settings.info_min) || settings.info_min > settings.warning_min {
                problems.push(format!(
                    "semantic.mixed_concerns: 閾値は 0〜1 で info_min ≤ warning_min(warning_min = {}・info_min = {})",
                    settings.warning_min, settings.info_min
                ));
            }
            Some(settings)
        });
        let proxy = section.proxy_url.as_ref().and_then(|url| {
            if !(url.starts_with("http://") || url.starts_with("https://")) {
                problems.push(format!("semantic.proxy_url は http:// か https:// の URL: {}", url));
                return None;
            }
            Some(ProxySettings {
                url: url.clone(),
                token_file: section.proxy_token_file.clone().unwrap_or_else(|| DEFAULT_PROXY_TOKEN_FILE.to_string()),
                peek_timeout: Duration::from_millis(section.proxy_peek_timeout_ms.unwrap_or(DEFAULT_PROXY_PEEK_TIMEOUT_MS)),
            })
        });
        if section.proxy_url.is_none() && (section.proxy_token_file.is_some() || section.proxy_peek_timeout_ms.is_some()) {
            problems.push("semantic.proxy_token_file・proxy_peek_timeout_ms は proxy_url と一緒に書く".to_string());
        }
        SemanticSettings {
            proxy,
            false_positives: section.false_positives.clone(),
            true_positives: section.true_positives.clone(),
            lines: Vec::new(),
            plain_callable,
            class_role,
            mixed_concerns,
            questions,
            workers: section.workers.unwrap_or(8).clamp(1, 32),
            timeout: Duration::from_secs(section.timeout_seconds.unwrap_or(30)),
            source_limit: section.source_limit.unwrap_or(1800),
        }
    }

    /// DOEFF203: 重さを決める — 受け入れない答えを選び確率が warning_min 以上なら warning、受け入れない答えを選んだがそれ未満か、
    /// 受け入れる答えを選んでも受け入れない答えの確率の和が info_min 以上なら info。error にはしない。
    pub fn plain_callable_severity(&self, chosen_accepted: bool, chosen_probability: f64, rejected_total: f64) -> Option<crate::models::Severity> {
        let spec = self.plain_callable?;
        match chosen_accepted {
            false if chosen_probability >= spec.warning_min => Some(crate::models::Severity::Warning),
            false => Some(crate::models::Severity::Info),
            true if rejected_total >= spec.info_min => Some(crate::models::Severity::Info),
            true => None,
        }
    }

    /// DOEFF204: Jev が選んだ答えと確率から重さを決める — external-world / stateful を warning_min 以上で warning、info_min 以上で info。
    /// value と other は出さない。error にはしない。
    pub fn class_role_severity(&self, chosen: &str, probability: f64) -> Option<crate::models::Severity> {
        let spec = self.class_role?;
        match chosen {
            "external-world" | "stateful" if probability >= spec.warning_min => Some(crate::models::Severity::Warning),
            "external-world" | "stateful" if probability >= spec.info_min => Some(crate::models::Severity::Info),
            _ => None,
        }
    }

    /// DOEFF205: Jev が mixed を選んだ確率から重さを決める(ほかの答えは出さない・error にはしない)。
    pub fn mixed_concerns_severity(&self, chosen: &str, probability: f64) -> Option<crate::models::Severity> {
        let spec = self.mixed_concerns.as_ref()?;
        match chosen {
            "mixed" if probability >= spec.warning_min => Some(crate::models::Severity::Warning),
            "mixed" if probability >= spec.info_min => Some(crate::models::Severity::Info),
            _ => None,
        }
    }

    /// 確率から重さを決める(閾値に届かなければ None)。
    pub fn severity(&self, question: SemanticQuestion, probability: f64) -> Option<crate::models::Severity> {
        let spec = self.questions.get(&question)?;
        if probability >= spec.warning {
            Some(crate::models::Severity::Warning)
        } else if probability >= spec.info {
            Some(crate::models::Severity::Info)
        } else {
            None
        }
    }
}

/// 問い 1 つ分の定義(state と cache の鍵)。
#[derive(Debug, Clone)]
pub struct SemanticItem {
    pub question: SemanticQuestion,
    /// Jev へ渡す問いの JSON(DOEFF203 は理由の種類の一覧を含む)。
    pub question_json: Value,
    /// DOEFF203: 名乗った理由の種類(他の問いは None)。
    pub declared: Option<String>,
    pub rel: String,
    pub path: PathBuf,
    pub name: String,
    pub kind: &'static str,
    pub range: doeff_indexer::hy_index::Range,
    pub layer: LayerId,
    pub state: Value,
    pub key: String,
    /// 定義の source が読み取りで壊れていない(書きかけでない)か — 壊れた定義は問わない。
    pub readable: bool,
}

/// 定義の source が Hy の読み取りで壊れた箇所(閉じない括弧・対応しない閉じ括弧・閉じない文字列)を持たないか。
pub fn readable(source: &str) -> bool {
    let mut reader = doeff_indexer::hy_index::reader::Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    !forms.is_empty() && reader.issues.is_empty()
}

/// 申告の :tags を source から消す(申告を見せると Jev が引きずられる — jev-lint の TAGS と同じ)。
fn strip_tags(source: &str) -> String {
    static TAGS: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    TAGS.get_or_init(|| Regex::new(r"\s*:tags\s+\{[^}]*\}").unwrap_or_else(|_| Regex::new("$^").unwrap())).replace_all(source, "").into_owned()
}

/// 文字数で切る(char の境目で)。
fn truncate(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

/// 決まった綴りの JSON(cache の鍵のため — object の鍵を並べる)。
fn canonical(value: &Value) -> String {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let parts: Vec<String> = keys.into_iter().map(|k| format!("{}:{}", Value::String(k.clone()), canonical(&map[k]))).collect();
            format!("{{{}}}", parts.join(","))
        }
        Value::Array(items) => format!("[{}]", items.iter().map(canonical).collect::<Vec<_>>().join(",")),
        other => other.to_string(),
    }
}

/// 問いの本文の代理の鍵(64 桁の小文字の 16 進)= sha256(PROXY_KEY_VERSION + "\n" + 決まった綴りの本文)。代理の決まり(proboscis/jev-proxy
/// の src/doeff_jev_proxy/key.hy の normalize-request — object の鍵を符号位置の順に並べ・区切りの空白なし・
/// 文字は UTF-8 のまま)と同じ鍵になる。本文は model を持つこと(代理は model の無い本文に既定の名を足してから綴る)。小数は綴りが
/// 言語で違うので同じ鍵にならない(外れるだけで、別の問いの答えには当たらない — linter の本文は小数を持たない)。
/// 同じ鍵になることは、代理の見本(正本 = proboscis/jev-proxy の tests/key_contract.json・写し = この package の
/// tests/proxy_key_contract.json)を両方の repo の検が読んで確かめる。
pub fn proxy_key(body: &Value) -> String {
    let mut hasher = Sha256::new();
    hasher.update(PROXY_KEY_VERSION.as_bytes());
    hasher.update(b"\n");
    hasher.update(canonical(body).as_bytes());
    hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect()
}

/// 定義 1 つの state と cache の鍵を作る。鍵 = sha256(model・問いの JSON(線引きを入れた物)・層の説明・タグを消した source)。申告の役は鍵に入れない。
#[allow(clippy::too_many_arguments)]
pub fn item(
    settings: &SemanticSettings,
    model: &str,
    question: SemanticQuestion,
    rel: &str,
    path: &Path,
    name: &str,
    kind: &'static str,
    range: doeff_indexer::hy_index::Range,
    source: &str,
    layer: LayerId,
    layer_name: &str,
    description: &LayerDescription,
) -> SemanticItem {
    let stripped = truncate(&strip_tags(source), settings.source_limit);
    let layer_json = json!({
        "name": layer_name,
        "summary": description.summary,
        "knows": description.knows,
        "does_not_know": description.does_not_know,
        "test": description.question,
    });
    let state = json!({
        "definition": {"name": name, "kind": kind, "file": rel, "source": stripped},
        "layer": layer_json,
    });
    let question_json = question.wire_with(&settings.lines);
    let mut hasher = Sha256::new();
    for part in [model.to_string(), canonical(&question_json), canonical(&layer_json), stripped] {
        hasher.update(part.as_bytes());
        hasher.update(b"\n");
    }
    let key = hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect();
    SemanticItem { question, question_json, declared: None, rel: rel.to_string(), path: path.to_path_buf(), name: name.to_string(), kind, range, layer, state, key, readable: readable(source) }
}

/// DOEFF203 の定義 1 つの state と cache の鍵を作る(state = 定義・書かれた理由。鍵 = sha256(model・問いの JSON・state))。
#[allow(clippy::too_many_arguments)]
pub fn plain_callable_item(
    settings: &SemanticSettings,
    model: &str,
    accepted: &[(String, String)],
    rejected: &[(String, String)],
    rel: &str,
    path: &Path,
    name: &str,
    kind: &'static str,
    range: doeff_indexer::hy_index::Range,
    source: &str,
    stated_kind: Option<&str>,
    stated: &str,
) -> SemanticItem {
    let stripped = truncate(&strip_tags(source), settings.source_limit);
    let question_json = SemanticQuestion::plain_callable_wire(accepted, rejected);
    let state = json!({
        "definition": {"name": name, "kind": kind, "file": rel, "source": stripped},
        "stated_reason": {"text": stated, "kind": stated_kind},
    });
    let mut hasher = Sha256::new();
    for part in [model.to_string(), canonical(&question_json), canonical(&state)] {
        hasher.update(part.as_bytes());
        hasher.update(b"\n");
    }
    let key = hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect();
    SemanticItem {
        question: SemanticQuestion::PlainCallable,
        question_json,
        declared: stated_kind.map(str::to_string),
        rel: rel.to_string(),
        path: path.to_path_buf(),
        name: name.to_string(),
        kind,
        range,
        layer: LayerId(0),
        state,
        key,
        readable: readable(source),
    }
}

/// DOEFF204 の class 1 つの state と cache の鍵を作る(state = class の source(タグを消して切る)と欄の宣言)。
#[allow(clippy::too_many_arguments)]
pub fn class_item(
    settings: &SemanticSettings,
    model: &str,
    rel: &str,
    path: &Path,
    name: &str,
    range: doeff_indexer::hy_index::Range,
    source: &str,
    fields: &[String],
) -> SemanticItem {
    let stripped = truncate(&strip_tags(source), settings.source_limit);
    let question = SemanticQuestion::ClassRole;
    let question_json = question.wire();
    let state = json!({
        "definition": {"name": name, "kind": "defclass", "file": rel, "source": stripped, "fields": fields},
    });
    let mut hasher = Sha256::new();
    for part in [model.to_string(), canonical(&question_json), canonical(&state)] {
        hasher.update(part.as_bytes());
        hasher.update(b"\n");
    }
    let key = hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect();
    SemanticItem {
        question,
        question_json,
        declared: None,
        rel: rel.to_string(),
        path: path.to_path_buf(),
        name: name.to_string(),
        kind: "defclass",
        range,
        layer: LayerId(0),
        state,
        key,
        readable: readable(source),
    }
}

/// DOEFF205 の定義 1 つの state と cache の鍵を作る(state = 定義の source(タグを消して切る)と物差しの層の説明・問いは線引きを入れた物)。
#[allow(clippy::too_many_arguments)]
pub fn mixed_item(
    settings: &SemanticSettings,
    model: &str,
    rel: &str,
    path: &Path,
    name: &str,
    kind: &'static str,
    range: doeff_indexer::hy_index::Range,
    source: &str,
    layer: LayerId,
    layer_name: &str,
    description: &LayerDescription,
) -> SemanticItem {
    let stripped = truncate(&strip_tags(source), settings.source_limit);
    let question = SemanticQuestion::MixedConcerns;
    let question_json = question.wire_with(&settings.lines);
    let state = json!({
        "definition": {"name": name, "kind": kind, "file": rel, "source": stripped},
        "layer": {"name": layer_name, "summary": description.summary, "knows": description.knows, "does_not_know": description.does_not_know},
    });
    let mut hasher = Sha256::new();
    for part in [model.to_string(), canonical(&question_json), canonical(&state)] {
        hasher.update(part.as_bytes());
        hasher.update(b"\n");
    }
    let key = hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect();
    SemanticItem { question, question_json, declared: None, rel: rel.to_string(), path: path.to_path_buf(), name: name.to_string(), kind, range, layer, state, key, readable: readable(source) }
}

/// 閾値と比べる確率 — 較正の見張りと、人の判定との突き合わせ(`labeled`)の 2 か所がこの 1 つを使う。Noul は答えの確率、選ぶ形の問いは
/// 的の語の確率(DOEFF204 = external-world・DOEFF205 = mixed)。選ぶ形の答えの `probability` は選んだ語の確率なので、そのまま使うと
/// judgment-only 0.97・mixed 0.02 の答えが 0.97 に見える(agora-redesign #1944・#1994 — 205 の閾値の表がこの読み違いの上で作られた)。
pub fn target_probability(question: SemanticQuestion, answer: &Answer) -> f64 {
    match question {
        SemanticQuestion::ClassRole => answer.probabilities.as_ref().and_then(|p| p.get("external-world").copied()).unwrap_or(0.0),
        SemanticQuestion::MixedConcerns => answer.probabilities.as_ref().and_then(|p| p.get("mixed").copied()).unwrap_or(0.0),
        SemanticQuestion::BusinessDecision | SemanticQuestion::TransportKnowledge | SemanticQuestion::PlainCallable => answer.probability,
    }
}

/// Jev の答え(確率・答えに載った費用 USD と token 数・答えた model)。手元の cache に書く形。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Answer {
    pub probability: f64,
    /// 答えに載った費用(USD)— 上流が答えに載せた時だけ Some(Vercel の AI Gateway の providerMetadata.gateway.cost)。TypeSafe 直は
    /// 載せないので None で、0 と区別する(agora-redesign #1892)。以前の cache の欄 cost_usd は、載らない時も 0 と書いていたので読まない
    /// (読むと「費用 0」と「不明」が混ざる — 前の答えは不明として読む)。
    #[serde(default)]
    pub reported_cost_usd: Option<f64>,
    /// 入力の token 数(答えの usage に在る時だけ Some — 無い答えを 0 と書かない)。
    #[serde(default)]
    pub input_tokens: Option<u64>,
    /// 出力の token 数(答えの usage に在る時だけ Some — 単価を掛けて費用を見積もる材料・#1892 の案 B)。
    #[serde(default)]
    pub output_tokens: Option<u64>,
    /// 答えた model の版つきの名(direct の答えの model — 例 jev-1.13.0。gateway は返さない)。
    #[serde(default)]
    pub served_model: Option<String>,
    /// Choice の答え(選んだ種類)。
    #[serde(default)]
    pub choice: Option<String>,
    /// Choice の種類ごとの確率。
    #[serde(default)]
    pub probabilities: Option<BTreeMap<String, f64>>,
}

/// 代理の覚えの使い方(代理への見出し Cache-Control)。代理でない宛先には何も足さない。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Freshness {
    /// 覚えていれば覚えた答え・無ければ本物の Jev(見出しなし)。
    Remembered,
    /// 覚えを使わず本物の Jev に問い直す(no-cache — 較正の見張り)。
    Fresh,
}

/// 覚えている時だけの問いの束の結果。
#[derive(Debug, Clone, PartialEq)]
pub enum PeekedMany {
    /// 代理が覚えていた答え(代理の鍵 → 答え — 覚えていない鍵は載らない・本物の Jev は呼んでいない)。
    Remembered(BTreeMap<String, Answer>),
    /// 代理に届かない・時間切れ・代理でない宛先・読めない答え(理由)。
    Unreachable(String),
}

/// Jev へ問う口(本物は HTTP・検では偽物)。
pub trait Gateway: Sync {
    /// 1 つの定義に 1 つの問いを撃つ(答えと、その回が上流に払わせた分)。
    fn ask(&self, state: &Value, question: &Value, freshness: Freshness) -> Result<Asked, String>;
    /// 問いの本文の代理の鍵(proxy_key)。宛先が代理でなければ None(覚えている時だけの問いは代理にだけ撃つ)。
    fn proxy_key(&self, state: &Value, question: &Value) -> Option<String>;
    /// 代理の鍵の束を代理に「覚えている時だけ」問う(本物の Jev を呼ばない・撃ち直さない)。代理でない宛先は Unreachable。
    fn peek_many(&self, keys: &[String], timeout: Duration) -> PeekedMany;
    /// 宛先の model の名(cache の鍵と出力のため)。
    fn model(&self) -> String;
}

// ---- 宛先の決め方(doeff の packages/doeff-jev/src/doeff_jev/target.py の写し — 決め方は target.py の docstring の順と既定値のまま)----
// 解き方(上が勝つ):
// 1. 環境変数 JEV_BASE_URL / JEV_MODEL / JEV_WIRE / JEV_API_KEY / JEV_API_KEY_FILE
// 2. 設定 file ~/.config/jev/client.json(欄 base_url / model / wire / api_key_file)
// 3. 既定 = TypeSafe 直(/v1/systemone・model jev-latest)。キーは TYPESAFE_API_KEY → ~/.config/jev/api_key。
//    gateway(Vercel AI Gateway の evaluation-model)を名指した時は model typesafe-ai/jev・キーは AI_GATEWAY_API_KEY → ~/jev_key。

pub const DIRECT_URL: &str = "https://api.typesafe.ai/v1/systemone";
pub const DIRECT_MODEL: &str = "jev-latest";
pub const GATEWAY_URL: &str = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model";
pub const GATEWAY_MODEL: &str = "typesafe-ai/jev";
const GATEWAY_HOST: &str = "ai-gateway.vercel.sh";
const CONFIG_FILE: &str = "~/.config/jev/client.json";
const DIRECT_KEY_FILE: &str = "~/.config/jev/api_key";
const GATEWAY_KEY_FILE: &str = "~/jev_key";

/// 通信の形。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Wire {
    Direct,
    Gateway,
}

/// 解いた宛先(キーの値は Debug に出さない)。
#[derive(Clone)]
pub struct JevTarget {
    pub base_url: String,
    pub model: String,
    pub wire: Wire,
    api_key: Option<String>,
    /// env / file / default / repo(記録用 — repo = repo の設定の代理)。
    pub source: &'static str,
    /// 宛先が Jev の呼び出しを覚える代理か(覚えている時だけの問いと no-cache の見出しは代理にだけ送る)。
    pub proxy: bool,
}

impl std::fmt::Debug for JevTarget {
    /// キーの値を出さない Debug。
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("JevTarget")
            .field("base_url", &self.base_url)
            .field("model", &self.model)
            .field("wire", &self.wire)
            .field("api_key", &self.api_key.as_ref().map(|_| "<set>"))
            .field("source", &self.source)
            .field("proxy", &self.proxy)
            .finish()
    }
}

/// URL と申告から通信の形を決める(target.py の _wire_of)。
fn wire_of(url: &str, declared: Option<&str>) -> Wire {
    match declared {
        Some("direct") => Wire::Direct,
        Some("gateway") => Wire::Gateway,
        _ if url.contains(GATEWAY_HOST) => Wire::Gateway,
        _ => Wire::Direct,
    }
}

/// 宛先を解く(純粋 — 環境と file の読みは引数で受け取る。target.py の resolve_target と同じ順)。
pub fn resolve_target(env: &dyn Fn(&str) -> Option<String>, read_text: &dyn Fn(&str) -> Option<String>, wire: Option<&str>) -> JevTarget {
    let file_cfg: serde_json::Map<String, Value> =
        read_text(CONFIG_FILE).and_then(|text| serde_json::from_str::<Value>(&text).ok()).and_then(|v| v.as_object().cloned()).unwrap_or_default();
    let file_str = |key: &str| file_cfg.get(key).and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_string);
    let env_str = |key: &str| env(key).filter(|s| !s.is_empty());
    let declared_wire = env_str("JEV_WIRE").or_else(|| file_str("wire")).or_else(|| wire.map(str::to_string));
    let mut url = env_str("JEV_BASE_URL").or_else(|| file_str("base_url"));
    let source = if env_str("JEV_BASE_URL").is_some() {
        "env"
    } else if file_str("base_url").is_some() {
        "file"
    } else {
        "default"
    };
    if url.is_none() {
        url = Some(if declared_wire.as_deref() == Some("gateway") { GATEWAY_URL } else { DIRECT_URL }.to_string());
    }
    let url = url.unwrap_or_default();
    let resolved_wire = wire_of(&url, declared_wire.as_deref());
    let model = env_str("JEV_MODEL").or_else(|| file_str("model")).unwrap_or_else(|| {
        match resolved_wire {
            Wire::Gateway => GATEWAY_MODEL,
            Wire::Direct => DIRECT_MODEL,
        }
        .to_string()
    });
    let mut key = env_str("JEV_API_KEY");
    if key.is_none() {
        if let Some(key_file) = env_str("JEV_API_KEY_FILE").or_else(|| file_str("api_key_file")) {
            key = read_text(&key_file);
        }
    }
    if key.is_none() {
        key = match resolved_wire {
            Wire::Gateway => env_str("AI_GATEWAY_API_KEY").or_else(|| read_text(GATEWAY_KEY_FILE)),
            Wire::Direct => env_str("TYPESAFE_API_KEY").or_else(|| read_text(DIRECT_KEY_FILE)),
        };
    }
    JevTarget { base_url: url, model, wire: resolved_wire, api_key: key.map(|k| k.trim().to_string()).filter(|k| !k.is_empty()), source, proxy: false }
}

/// repo の設定の代理を重ねて宛先を解く(純粋)。env の JEV_BASE_URL が在ればそれが勝つ(代理を使わない)。代理へは代理の token だけを
/// 送る(TYPESAFE_API_KEY などの本物のキーは送らない)。model は解いた direct の model(gateway を解いていれば direct の既定)。
pub fn resolve_repo_target(env: &dyn Fn(&str) -> Option<String>, read_text: &dyn Fn(&str) -> Option<String>, proxy: Option<&ProxySettings>) -> JevTarget {
    let base = resolve_target(env, read_text, None);
    match proxy {
        Some(proxy) if base.source != "env" => JevTarget {
            base_url: proxy.url.clone(),
            model: match base.wire {
                Wire::Direct => base.model,
                Wire::Gateway => DIRECT_MODEL.to_string(),
            },
            wire: Wire::Direct,
            api_key: read_text(&proxy.token_file).map(|k| k.trim().to_string()).filter(|k| !k.is_empty()),
            source: "repo",
            proxy: true,
        },
        _ => base,
    }
}

/// `~` を展開して file を読む(無ければ None — target.py の read_text_from_disk)。
pub fn read_text_from_disk(path: &str) -> Option<String> {
    let expanded = match path.strip_prefix("~/") {
        Some(rest) => PathBuf::from(std::env::var("HOME").ok()?).join(rest),
        None => PathBuf::from(path),
    };
    std::fs::read_to_string(expanded).ok().map(|t| t.trim().to_string()).filter(|t| !t.is_empty())
}

/// この process の環境と home の file から宛先を解く(組み立て点だけが呼ぶ I/O — target.py の target_from_process_environment)。
pub fn target_from_process_environment() -> JevTarget {
    resolve_target(&|key| std::env::var(key).ok(), &read_text_from_disk, None)
}

/// この process の環境・home の file・repo の代理の設定から宛先を解く(組み立て点だけが呼ぶ I/O)。
pub fn target_for_repo(proxy: Option<&ProxySettings>) -> JevTarget {
    resolve_repo_target(&|key| std::env::var(key).ok(), &read_text_from_disk, proxy)
}

/// 問いを gateway の綴りへ写す(noul は boolean — wire.py の _gateway_question)。
fn gateway_question(question: &Value) -> Value {
    let mut out = question.clone();
    if let Some(map) = out.as_object_mut() {
        if map.get("type").and_then(Value::as_str) == Some("noul") {
            map.insert("type".into(), Value::String("boolean".into()));
        }
    }
    out
}

/// Jev の口を HTTP で呼ぶ本物(direct と gateway の両方の形 — wire.py の prepare と parse の写し)。
pub struct HttpGateway {
    target: JevTarget,
    agent: ureq::Agent,
}

impl HttpGateway {
    /// 宛先から口を作る。TypeSafe と Vercel の宛先でキーが無ければ理由を返す(値は出さない)。
    pub fn new(target: JevTarget, timeout: Duration) -> Result<HttpGateway, String> {
        if target.proxy && target.api_key.is_none() {
            return Err("Jev の API キーが無い(代理の token の file — [tool.doeff-linter.semantic] proxy_token_file・既定 ~/.config/jev/proxy-token)".to_string());
        }
        let needs_key = target.base_url.contains("api.typesafe.ai") || target.base_url.contains(GATEWAY_HOST);
        if needs_key && target.api_key.is_none() {
            return Err(match target.wire {
                Wire::Direct => "Jev の API キーが無い(JEV_API_KEY・JEV_API_KEY_FILE・TYPESAFE_API_KEY・~/.config/jev/api_key)".to_string(),
                Wire::Gateway => "Jev の API キーが無い(JEV_API_KEY・JEV_API_KEY_FILE・AI_GATEWAY_API_KEY・~/jev_key)".to_string(),
            });
        }
        Ok(HttpGateway { target, agent: ureq::AgentBuilder::new().timeout(timeout).build() })
    }
}

impl HttpGateway {
    /// 問いの本文(direct と gateway の形 — 覚えている時だけの問いも同じ本文を送るので、代理の鍵が揃う)。
    fn body(&self, state: &Value, question: &Value) -> Value {
        match self.target.wire {
            Wire::Direct => json!({"state": state, "questions": {"q": question}, "model": self.target.model}),
            Wire::Gateway => json!({"state": state, "questions": {"q": gateway_question(question)}}),
        }
    }
}

impl Gateway for HttpGateway {
    /// 1 回問う(429・5xx は 3 回まで間を空けて撃ち直す)。代理には Fresh の時だけ Cache-Control: no-cache を付ける。代理の答えの
    /// 見出し x-jev-proxy が覚えた答え・相乗りを名乗れば、その回は上流を呼んでいない(費用と token を数えない)。
    fn ask(&self, state: &Value, question: &Value, freshness: Freshness) -> Result<Asked, String> {
        let body = self.body(state, question);
        let mut last = String::new();
        for attempt in 0..4u64 {
            let mut request = self.agent.post(&self.target.base_url).set("content-type", "application/json");
            if let Some(key) = &self.target.api_key {
                request = request.set("authorization", &format!("Bearer {}", key));
            }
            if self.target.proxy && freshness == Freshness::Fresh {
                request = request.set("cache-control", "no-cache");
            }
            if self.target.wire == Wire::Gateway {
                request = request
                    .set("ai-gateway-protocol-version", "0.0.1")
                    .set("ai-evaluation-model-specification-version", "4")
                    .set("ai-model-id", &self.target.model);
            }
            match request.send_string(&body.to_string()) {
                Ok(ok) => {
                    let remembered = self.target.proxy && answered_from_memory(ok.header("x-jev-proxy"));
                    let text = ok.into_string().map_err(|e| format!("答えを読めない: {}", e))?;
                    let answer = parse_answer(&text)?;
                    let charge = charge_of(&answer, remembered);
                    return Ok(Asked { answer, charge });
                }
                Err(ureq::Error::Status(code, answer)) if [429, 500, 502, 503, 504, 529].contains(&code) && attempt < 3 => {
                    last = format!("HTTP {}: {}", code, answer.into_string().unwrap_or_default().chars().take(200).collect::<String>());
                    std::thread::sleep(Duration::from_millis(1500 * (attempt + 1)));
                }
                Err(ureq::Error::Status(code, answer)) => {
                    return Err(format!("HTTP {}: {}", code, answer.into_string().unwrap_or_default().chars().take(300).collect::<String>()))
                }
                Err(error) => return Err(format!("Jev に届かない: {}", error)),
            }
        }
        Err(last)
    }

    /// 代理の鍵(代理の宛先の時だけ — 本文は ask と同じ綴り)。
    fn proxy_key(&self, state: &Value, question: &Value) -> Option<String> {
        self.target.proxy.then(|| proxy_key(&self.body(state, question)))
    }

    /// 鍵の束を POST <proxy_url>/peek で覚えている時だけ問う(撃ち直さない)。代理でない宛先には撃たない(本物の Jev を呼ばないため)。
    fn peek_many(&self, keys: &[String], timeout: Duration) -> PeekedMany {
        if !self.target.proxy {
            return PeekedMany::Unreachable("宛先が代理でない(覚えている時だけの問いは代理にだけ撃つ)".to_string());
        }
        let url = format!("{}/peek", self.target.base_url.trim_end_matches('/'));
        let mut request = self.agent.post(&url).timeout(timeout).set("content-type", "application/json");
        if let Some(key) = &self.target.api_key {
            request = request.set("authorization", &format!("Bearer {}", key));
        }
        match request.send_string(&json!({ "keys": keys }).to_string()) {
            Ok(ok) => match ok.into_string().map_err(|e| e.to_string()).and_then(|text| parse_remembered(&text)) {
                Ok(answers) => PeekedMany::Remembered(answers),
                Err(reason) => PeekedMany::Unreachable(format!("代理の答えを読めない: {}", reason)),
            },
            Err(ureq::Error::Status(code, _)) => PeekedMany::Unreachable(format!("代理が HTTP {} を返した", code)),
            Err(error) => PeekedMany::Unreachable(format!("代理に届かない: {}", error)),
        }
    }

    /// 宛先の model の名。
    fn model(&self) -> String {
        self.target.model.clone()
    }
}

/// 答えの JSON から確率を読む(direct は answers.q.noul・gateway は answers.q.probability — wire.py の _parse_answer)。
pub fn parse_answer(text: &str) -> Result<Answer, String> {
    let value: Value = serde_json::from_str(text).map_err(|e| format!("答えが JSON でない: {}", e))?;
    let raw = value.pointer("/answers/q").ok_or_else(|| format!("答えに answers.q が無い: {}", text.chars().take(200).collect::<String>()))?;
    let choice = raw.get("choice").and_then(Value::as_str).map(str::to_string);
    let probabilities: Option<BTreeMap<String, f64>> =
        raw.get("probabilities").and_then(Value::as_object).map(|m| m.iter().filter_map(|(k, v)| v.as_f64().map(|p| (k.clone(), p))).collect());
    let probability = match raw.get("noul").or_else(|| raw.get("probability")).and_then(Value::as_f64) {
        Some(p) => p,
        None if choice.is_some() => choice.as_ref().and_then(|c| probabilities.as_ref()?.get(c).copied()).unwrap_or(0.0),
        None => return Err(format!("答えに noul の確率も choice も無い: {}", text.chars().take(200).collect::<String>())),
    };
    let reported_cost_usd =
        value.pointer("/providerMetadata/gateway/cost").and_then(|c| c.as_str().and_then(|s| s.parse::<f64>().ok()).or_else(|| c.as_f64()));
    let usage = value.get("usage");
    let tokens = |snake: &str, camel: &str| usage.and_then(|u| u.get(snake).or_else(|| u.get(camel))).and_then(Value::as_u64);
    let input_tokens = tokens("input_tokens", "inputTokens");
    let output_tokens = tokens("output_tokens", "outputTokens");
    let served_model = value.get("model").and_then(Value::as_str).map(str::to_string);
    Ok(Answer { probability, reported_cost_usd, input_tokens, output_tokens, served_model, choice, probabilities })
}

/// 1 回の問いが上流に払わせた分(答えの欄とは別 — proxy の覚えた答えは元の問いの費用を本文に持つが、この回は上流を呼んでいない)。
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Charge {
    /// 上流を呼び、答えに費用が載っていた(USD)。
    Reported(f64),
    /// 上流を呼んだが、答えに費用が載っていない(TypeSafe 直 — 0 ではなく不明)。
    Unreported,
    /// 上流を呼んでいない(proxy の覚えた答え・同じ問いへの相乗り・覚えている時だけの問い)— この回の費用と token は 0。
    Remembered,
}

/// 1 回の問いの答えと、その回の費用。
#[derive(Debug, Clone, PartialEq)]
pub struct Asked {
    pub answer: Answer,
    pub charge: Charge,
}

/// proxy の答えの見出し x-jev-proxy が「上流を呼ばずに答えた」印か(hit = 覚えた答え・coalesced = 同じ問いへの相乗り — proboscis/jev-proxy の
/// service.hy の見出しの語)。見出しが無い(proxy でない宛先)・miss・refreshed は上流を呼んだ。
pub fn answered_from_memory(marker: Option<&str>) -> bool {
    matches!(marker, Some("hit") | Some("coalesced"))
}

/// 1 回の問いの費用を決める(純粋): 上流を呼んでいなければ Remembered、呼んで答えに費用が載っていれば Reported、載っていなければ Unreported。
pub fn charge_of(answer: &Answer, remembered: bool) -> Charge {
    match (remembered, answer.reported_cost_usd) {
        (true, _) => Charge::Remembered,
        (false, Some(cost)) => Charge::Reported(cost),
        (false, None) => Charge::Unreported,
    }
}

/// 代理の覚えている時だけの問いの束の答え {"answers": {鍵: Jev の答えの本文}} を読む(1 つでも読めなければ全体を読めないとする)。
pub fn parse_remembered(text: &str) -> Result<BTreeMap<String, Answer>, String> {
    let value: Value = serde_json::from_str(text).map_err(|e| format!("答えが JSON でない: {}", e))?;
    let answers = value.get("answers").and_then(Value::as_object).ok_or_else(|| "答えに answers の object が無い".to_string())?;
    answers.iter().map(|(key, answer)| parse_answer(&answer.to_string()).map(|a| (key.clone(), a))).collect()
}

/// cache の置き場(repo の根の .doeff-linter/semantic-cache/)。
pub fn cache_dir(root: &Path) -> PathBuf {
    root.join(".doeff-linter").join("semantic-cache")
}

/// cache から答えを読む(無い・読めない時は None)。
pub fn read_cache(root: &Path, key: &str) -> Option<Answer> {
    let text = std::fs::read_to_string(cache_dir(root).join(format!("{}.json", key))).ok()?;
    serde_json::from_str(&text).ok()
}

/// 答えを cache へ書く。
fn write_cache(root: &Path, key: &str, answer: &Answer) -> Result<(), String> {
    let dir = cache_dir(root);
    std::fs::create_dir_all(&dir).map_err(|e| format!("cache の dir を作れない: {}", e))?;
    let text = serde_json::to_string(answer).map_err(|e| e.to_string())?;
    std::fs::write(dir.join(format!("{}.json", key)), text).map_err(|e| format!("cache を書けない: {}", e))
}

/// 較正の見張りの例 1 つ(data/semantic_calibration.json)。
#[derive(Debug, Clone, Deserialize)]
pub struct CalibrationExample {
    pub rule: String,
    pub id: String,
    pub expect: bool,
    pub name: String,
    pub kind: String,
    pub path: String,
    pub layer: String,
    pub source: String,
    /// DOEFF204 の例の欄の宣言(`名: 型`)。
    #[serde(default)]
    pub fields: Vec<String>,
}

/// 同梱の較正の例。
#[derive(Debug, Clone, Deserialize)]
struct CalibrationFile {
    items: Vec<CalibrationExample>,
}

/// 正例の確率の下限と、反例の確率の上限(既定の幅)。
pub const CALIBRATION_POSITIVE_MIN: f64 = 0.8;
pub const CALIBRATION_NEGATIVE_MAX: f64 = 0.2;

/// 同梱の較正の例を読む。
pub fn calibration_examples() -> Vec<CalibrationExample> {
    serde_json::from_str::<CalibrationFile>(include_str!("../../data/semantic_calibration.json")).map(|f| f.items).unwrap_or_default()
}

/// 問いを撃つ実行の結果。
#[derive(Debug, Clone, Default, Serialize)]
pub struct SemanticSummary {
    pub model: String,
    /// 通信の形と宛先の出どころ(env / file / default)— キーの値は出さない。
    pub wire: String,
    /// 答えのある定義の数(cache か今回の問い)。
    pub judged: usize,
    /// 答えの無い定義の数(未判定 — 合格ではない)。
    pub unjudged: usize,
    /// 今回問うはずだったのに答えを得られなかった定義の数(Jev に届かない・鍵が無い・較正が撃てない — 「測れなかった」。緑と分けて出す・
    /// agora-redesign #1160 の決定 2)。
    pub unmeasured: usize,
    /// 今回 gateway へ撃った数(較正を含む)。
    pub asked: usize,
    /// 今回、代理が覚えていた答えを受け取って手元の cache に書いた数(覚えている時だけの問い — 本物の Jev は呼んでいない)。
    pub peeked: usize,
    /// 今回の費用(上流が答えに費用を載せた回の USD の和)。載せない回(TypeSafe 直)は cost_unreported に数え、ここに 0 を足さない
    /// — cost_unreported が 0 でなければ、この和は下限(agora-redesign #1892)。
    pub cost_usd: f64,
    /// 今回、上流を呼んだのに答えに費用が載っていなかった回の数(費用は不明 — 0 ではない)。
    pub cost_unreported: usize,
    /// 今回、proxy が覚えた答え・相乗りで答えた回の数(上流を呼んでいない — 費用と token を数えない)。
    pub remembered: usize,
    /// 今回、上流を呼んだ回の入力の token の和(答えに usage が在った回だけ)。
    pub input_tokens: u64,
    /// 今回、上流を呼んだ回の出力の token の和(答えに usage が在った回だけ)。
    pub output_tokens: u64,
    /// 今回答えた model の版つきの名(direct だけ・撃たない実行は null)。
    pub served_model: Option<String>,
    /// 較正の見張りの結果(not-run・ok・drifted・failed)。
    pub calibration: String,
    /// 誤判定の一覧に載っていて、違反から外した当たりの数(出さず・件数に入れない)。
    pub false_positives: usize,
    /// 人の判定(誤判定の一覧 = 反例・正例の一覧 = 正例)と Jev の答えの突き合わせ。
    pub labeled: LabeledSummary,
}

/// 人の判定と Jev の答えの突き合わせ — 今の閾値で当たりになるかと、判定ごとの確率(閾値を決める材料)。
#[derive(Debug, Clone, Default, Serialize)]
pub struct LabeledSummary {
    /// 正例(人が本当の違反と判定した物)。
    pub positives: LabelCount,
    /// 反例(誤判定の一覧)。
    pub negatives: LabelCount,
    /// 答えのある判定ごとの確率(鍵の順)。
    pub items: Vec<LabeledAnswer>,
}

/// 判定の種類ごとの数。
#[derive(Debug, Clone, Default, Serialize)]
pub struct LabelCount {
    /// 一覧に載った鍵の数。
    pub listed: usize,
    /// そのうち Jev の答えがある(今の定義を判じた)数。
    pub judged: usize,
    /// そのうち今の閾値で当たりになる数(正例なら当たり・反例なら誤判定)。
    pub flagged: usize,
}

/// 判定 1 つと Jev の答え。
#[derive(Debug, Clone, Serialize)]
pub struct LabeledAnswer {
    pub key: String,
    pub rule: String,
    /// 人の判定(true = 本当の違反・false = 誤判定)。
    pub expect: bool,
    /// 閾値と比べる確率(`target_probability` — Noul は答えの確率・DOEFF204 は external-world・DOEFF205 は mixed の確率)。
    pub probability: f64,
    /// 今の閾値で当たりになるか。
    pub flagged: bool,
}

/// 何を撃つか。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SemanticMode {
    /// cache を読むだけ(エディタの編集中の決定的な実行)。
    CacheOnly,
    /// cache を読み、無い定義は代理に「覚えている時だけ」問う(全体の実行・hook — 代理が無ければ CacheOnly と同じ)。
    Peek,
    /// 指定した file の定義を撃つ(repo の根からの path)。
    Ask(BTreeSet<String>),
    /// 指定した file の定義のうち、手元の cache に答えの無い定義(中身が変わった定義)だけを撃つ。
    AskChanged(BTreeSet<String>),
    /// 全部の定義を撃つ。
    AskAll,
    /// 旗の無い全体の実行の既定(agora-redesign #1160): 指定した file の定義のうち手元の cache に答えの無い物だけを撃ち(AskChanged と同じ)、
    /// 残りの答えの無い定義は代理に「覚えている時だけ」問う(Peek と同じ — 別の worker の答えを受け取る)。
    PeekThenAskChanged(BTreeSet<String>),
}

/// 意味の規則の結果(答えのある定義と、要約・理由)。
pub struct SemanticOutcome {
    pub answered: Vec<(SemanticItem, Answer)>,
    pub summary: SemanticSummary,
    pub errors: Vec<String>,
}

impl SemanticSummary {
    /// 1 回の問いの費用と token を要約に足すため — 上流を呼んでいない回(proxy の覚え)は数えず、費用の載らない回は不明として数える。
    pub fn tally(&mut self, asked: &Asked) {
        match asked.charge {
            Charge::Remembered => {
                self.remembered += 1;
                return;
            }
            Charge::Reported(cost) => self.cost_usd += cost,
            Charge::Unreported => self.cost_unreported += 1,
        }
        if let Some(tokens) = asked.answer.input_tokens {
            self.input_tokens += tokens;
        }
        if let Some(tokens) = asked.answer.output_tokens {
            self.output_tokens += tokens;
        }
    }
}

/// 定義の問いを cache から読み、mode が撃つ物は gateway へ撃つ(同時に workers 本)。撃つ実行は先に較正の見張りを撃つ。
pub fn evaluate(
    root: &Path,
    settings: &SemanticSettings,
    items: Vec<SemanticItem>,
    mode: &SemanticMode,
    gateway: Option<&dyn Gateway>,
    calibration: &[(SemanticItem, bool)],
) -> SemanticOutcome {
    let mut summary = SemanticSummary {
        model: gateway.map(|g| g.model()).unwrap_or_default(),
        calibration: "not-run".to_string(),
        ..SemanticSummary::default()
    };
    let mut errors = Vec::new();
    // 書きかけで読めない定義はどの実行でも問わない(未判定に数える)。
    let wants_ask = |item: &SemanticItem| {
        item.readable
            && match mode {
                SemanticMode::CacheOnly | SemanticMode::Peek => false,
                SemanticMode::AskAll => true,
                SemanticMode::Ask(targets) => targets.contains(&item.rel),
                SemanticMode::AskChanged(targets) | SemanticMode::PeekThenAskChanged(targets) => {
                    targets.contains(&item.rel) && read_cache(root, &item.key).is_none()
                }
            }
    };
    let asking = items.iter().any(wants_ask);
    // 問うと名指した Hy の file のうち、問いになる定義が 1 つも無い物を名乗る(問いの層の外・最上位の定義なし・書きかけ)— 名指しが
    // どの問いにもならずに「今回撃った 0」で終わる時、なぜ 0 かを黙らない(agora-redesign #2075)。
    if let SemanticMode::Ask(targets) | SemanticMode::AskChanged(targets) = mode {
        let silent: Vec<&str> = targets
            .iter()
            .filter(|rel| rel.ends_with(".hy") && !items.iter().any(|item| item.readable && &item.rel == *rel))
            .map(String::as_str)
            .collect();
        if !silent.is_empty() {
            errors.push(format!(
                "意味の規則: 名指しの file のうち {} 個には問いになる定義が無い(問いの層の外・最上位の定義なし・書きかけ)— 今回撃った数に入らない: {}",
                silent.len(),
                silent.join("・")
            ));
        }
    }
    let pool = rayon::ThreadPoolBuilder::new().num_threads(settings.workers).build();
    if asking {
        match (gateway, &pool) {
            (Some(gateway), Ok(pool)) => {
                let results: Vec<(bool, Result<Asked, String>)> = pool.install(|| {
                    calibration.par_iter().map(|(c, expect)| (*expect, gateway.ask(&c.state, &c.question_json, Freshness::Fresh))).collect()
                });
                summary.asked += results.len();
                let mut drifted = Vec::new();
                for ((expect, result), (example, _)) in results.into_iter().zip(calibration) {
                    match result {
                        Ok(asked) => {
                            summary.tally(&asked);
                            let answer = asked.answer;
                            if answer.served_model.is_some() {
                                summary.served_model = answer.served_model.clone();
                            }
                            let probability = target_probability(example.question, &answer);
                            let inside = if expect { probability >= CALIBRATION_POSITIVE_MIN } else { probability <= CALIBRATION_NEGATIVE_MAX };
                            if !inside {
                                drifted.push(format!("{} の {}(期待 {})が p={:.2}", example.question_id(), example.name, if expect { "真" } else { "偽" }, probability));
                            }
                        }
                        Err(reason) => {
                            summary.calibration = "failed".to_string();
                            errors.push(format!("意味の規則の較正の問いが撃てない: {}", reason));
                        }
                    }
                }
                if summary.calibration != "failed" {
                    if drifted.is_empty() {
                        summary.calibration = "ok".to_string();
                    } else {
                        summary.calibration = "drifted".to_string();
                        let removed = std::fs::remove_dir_all(cache_dir(root)).is_ok();
                        errors.push(format!(
                            "意味の規則の較正が既定の幅(正例 p ≥ {}・反例 p ≤ {})から外れた — model が変わった疑いで cache を捨てた{}: {}",
                            CALIBRATION_POSITIVE_MIN,
                            CALIBRATION_NEGATIVE_MAX,
                            if removed { "" } else { "(捨てる cache は無かった)" },
                            drifted.join("・")
                        ));
                    }
                }
            }
            (None, _) => errors.push("意味の規則: gateway の口を作れないので撃たない(cache だけを読む)".to_string()),
            (_, Err(error)) => errors.push(format!("意味の規則: 並列の pool を作れない: {}", error)),
        }
    }
    let can_ask = asking && gateway.is_some() && summary.calibration != "failed";
    let resolved: Vec<(SemanticItem, Option<Resolved>)> = match (&pool, gateway) {
        (Ok(pool), Some(gateway)) if can_ask => pool.install(|| {
            items
                .into_par_iter()
                .map(|item| {
                    if wants_ask(&item) {
                        let result = match gateway.ask(&item.state, &item.question_json, Freshness::Remembered) {
                            Ok(asked) => Resolved::Asked(asked),
                            Err(reason) => Resolved::Failed(reason),
                        };
                        (item, Some(result))
                    } else {
                        let cached = read_cache(root, &item.key).map(Resolved::Known);
                        (item, cached)
                    }
                })
                .collect()
        }),
        _ => items.into_iter().map(|item| {
            let cached = read_cache(root, &item.key).map(Resolved::Known);
            (item, cached)
        }).collect(),
    };
    // 全体の実行・hook: 手元の cache に無い読める定義を、代理の鍵の束で代理に「覚えている時だけ」問う(本物の Jev は呼ばない)。
    let resolved = match (mode, gateway, &pool, settings.proxy.as_ref()) {
        (SemanticMode::Peek | SemanticMode::PeekThenAskChanged(_), Some(gateway), Ok(pool), Some(proxy)) => {
            let wanted: Vec<Option<String>> = resolved
                .iter()
                .map(|(item, cached)| match cached {
                    None if item.readable => gateway.proxy_key(&item.state, &item.question_json),
                    _ => None,
                })
                .collect();
            let keys: Vec<String> = wanted.iter().flatten().cloned().collect::<BTreeSet<String>>().into_iter().collect();
            let peeked = if keys.is_empty() { Peeked::default() } else { peek_remembered(gateway, &keys, proxy.peek_timeout, pool) };
            if let Some(reason) = &peeked.missed_reason {
                errors.push(format!(
                    "意味の規則: proxy の覚えを読む束が返らなかった({})— 鍵 {} 個の定義を測れなかった(未判定ではない・待ち = semantic.proxy_peek_timeout_ms {} ms)",
                    reason,
                    peeked.missed.len(),
                    proxy.peek_timeout.as_millis()
                ));
            }
            resolved
                .into_iter()
                .zip(wanted)
                .map(|((item, cached), key)| match key.as_ref().and_then(|k| peeked.answers.get(k)) {
                    Some(answer) => {
                        summary.peeked += 1;
                        if let Err(reason) = write_cache(root, &item.key, answer) {
                            errors.push(reason);
                        }
                        ((item, Some(Resolved::Known(answer.clone()))), false)
                    }
                    None => {
                        let missed = key.as_ref().is_some_and(|k| peeked.missed.contains(k));
                        ((item, cached), missed)
                    }
                })
                .unzip()
        }
        _ => {
            let unmissed = vec![false; resolved.len()];
            (resolved, unmissed)
        }
    };
    let (resolved, peek_missed): (Vec<(SemanticItem, Option<Resolved>)>, Vec<bool>) = resolved;
    let mut answered = Vec::new();
    for ((item, result), missed) in resolved.into_iter().zip(peek_missed) {
        match result {
            Some(Resolved::Asked(asked)) => {
                summary.asked += 1;
                summary.tally(&asked);
                if let Err(reason) = write_cache(root, &item.key, &asked.answer) {
                    errors.push(reason);
                }
                summary.judged += 1;
                answered.push((item, asked.answer));
            }
            Some(Resolved::Known(answer)) => {
                summary.judged += 1;
                answered.push((item, answer));
            }
            Some(Resolved::Failed(reason)) => {
                summary.asked += 1;
                summary.unjudged += 1;
                summary.unmeasured += 1;
                errors.push(format!("{} の {}: Jev に問えない: {}", item.rel, item.name, reason));
            }
            // proxy の覚えを読む束が待ちの内に返らなかった(届かない・時間切れ)定義は、答えが在るかどうかを測れていない — 未判定に数えず
            // 「測れなかった」に数える(agora-redesign #1885: 束が 5 秒を超えた機体で、判定済み 6813 の repo が黙って未判定 7697 と出た)。
            None if missed => summary.unmeasured += 1,
            None => {
                summary.unjudged += 1;
                // 問うはずだった定義が、gateway・較正・pool のどれかで問えずに終わった(測れなかった)。
                if wants_ask(&item) {
                    summary.unmeasured += 1;
                }
            }
        }
    }
    SemanticOutcome { answered, summary, errors }
}

/// 定義 1 つの答えの出どころ — 今回問うた答え(費用を数えて手元の cache に書く)・手元の cache か proxy の覚えから読んだ答え(今回は
/// 上流を呼んでいない — 費用を数えない)・今回問えなかった理由。
enum Resolved {
    Asked(Asked),
    Known(Answer),
    Failed(String),
}

/// proxy の覚えを読んだ結果: answers = 覚えていた答え(proxy の鍵 → 答え)/ missed = 束が返らなかった(届かない・時間切れ・撃たずに
/// 止めた)鍵 — 答えが在るかどうかを測れていない / missed_reason = 最初に返らなかった束の理由。
#[derive(Default)]
struct Peeked {
    answers: BTreeMap<String, Answer>,
    missed: BTreeSet<String>,
    missed_reason: Option<String>,
}

/// 代理の鍵の束を PEEK_BATCH 個ずつに分けて並べて代理に「覚えている時だけ」問い、覚えていた答えを集める。全部の束を合わせた時間の
/// 上限つきで、どれかの束が届かなければ残りの束は撃たない。返らなかった束の鍵は missed に集める(未判定と分けて数える — #1885)。
fn peek_remembered(gateway: &dyn Gateway, keys: &[String], timeout: Duration, pool: &rayon::ThreadPool) -> Peeked {
    let deadline = Instant::now() + timeout;
    let unreachable = AtomicBool::new(false);
    let batches: Vec<&[String]> = keys.chunks(PEEK_BATCH).collect();
    let missed_batch = |batch: &[String], reason: String| Peeked { answers: BTreeMap::new(), missed: batch.iter().cloned().collect(), missed_reason: Some(reason) };
    pool.install(|| {
        batches
            .par_iter()
            .map(|batch| {
                let left = deadline.saturating_duration_since(Instant::now());
                if left.is_zero() {
                    return missed_batch(batch, format!("待ち {} ms を使い切った", timeout.as_millis()));
                }
                if unreachable.load(Ordering::Relaxed) {
                    return missed_batch(batch, "先の束が返らなかったので撃たなかった".to_string());
                }
                match gateway.peek_many(batch, left) {
                    PeekedMany::Remembered(answers) => Peeked { answers, ..Peeked::default() },
                    PeekedMany::Unreachable(reason) => {
                        unreachable.store(true, Ordering::Relaxed);
                        missed_batch(batch, reason)
                    }
                }
            })
            .reduce(Peeked::default, |mut all, part| {
                all.answers.extend(part.answers);
                all.missed.extend(part.missed);
                if all.missed_reason.is_none() {
                    all.missed_reason = part.missed_reason;
                }
                all
            })
    })
}

impl SemanticItem {
    /// 問いの ID(較正の知らせの文のため)。
    fn question_id(&self) -> &'static str {
        self.question.id()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_tags_truncates_and_keys_do_not_see_declared_roles() {
        let settings = SemanticSettings::validate(&SemanticSection::default(), &mut |_, _| None, &mut Vec::new());
        let description = LayerDescription { summary: Some("翻訳".into()), ..LayerDescription::default() };
        let range = { let p = doeff_indexer::hy_index::Position { line: 0, character: 0 }; doeff_indexer::hy_index::Range { start: p, end: p } };
        let one = item(&settings, "jev-latest", SemanticQuestion::BusinessDecision, "a.hy", Path::new("/r/a.hy"), "f", "defk", range, "(defk f [x] {:tags {:role \"protocol\"}} x)", LayerId(0), "protocol", &description);
        let other = item(&settings, "jev-latest", SemanticQuestion::BusinessDecision, "a.hy", Path::new("/r/a.hy"), "f", "defk", range, "(defk f [x] {:tags {:role \"judgment\"}} x)", LayerId(0), "protocol", &description);
        assert_eq!(one.key, other.key, "申告の役は鍵に入れない");
        assert_eq!(one.state["definition"]["source"], "(defk f [x] {} x)");
        let long = item(&settings, "jev-latest", SemanticQuestion::BusinessDecision, "a.hy", Path::new("/r/a.hy"), "f", "defk", range, &"あ".repeat(5000), LayerId(0), "protocol", &description);
        assert_eq!(long.state["definition"]["source"].as_str().unwrap().chars().count(), 1800);
        assert_eq!(
            parse_answer(r#"{"answers":{"q":{"type":"boolean","probability":0.93}},"providerMetadata":{"gateway":{"cost":"0.00006"}},"usage":{"inputTokens":10}}"#).unwrap(),
            Answer {
                probability: 0.93,
                reported_cost_usd: Some(0.00006),
                input_tokens: Some(10),
                output_tokens: None,
                served_model: None,
                choice: None,
                probabilities: None
            }
        );
        assert_eq!(parse_answer(r#"{"answers":{"q":{"noul":0.2}},"usage":{"input_tokens":7},"model":"jev-1"}"#).unwrap().probability, 0.2);
        assert!(parse_answer("{}").is_err());
        assert_eq!(calibration_examples().len(), 8);
    }

    // ---- 答えの費用(agora-redesign #1892): 上流が答えに載せた時だけ費用が在り、載せない答えは 0 ではなく「不明」----

    /// 費用の在る答え(Vercel の AI Gateway の形)— 費用と token を読み、上流を呼んだ回はその費用を数える。
    #[test]
    fn an_answer_with_a_cost_is_charged_that_cost() {
        let answer = parse_answer(
            r#"{"answers":{"q":{"type":"boolean","probability":0.9}},"providerMetadata":{"gateway":{"cost":"0.00004"}},"usage":{"inputTokens":900,"outputTokens":3}}"#,
        )
        .unwrap();
        assert_eq!((answer.reported_cost_usd, answer.input_tokens, answer.output_tokens), (Some(0.00004), Some(900), Some(3)));
        let asked = Asked { charge: charge_of(&answer, false), answer };
        assert_eq!(asked.charge, Charge::Reported(0.00004));
        let mut summary = SemanticSummary::default();
        summary.tally(&asked);
        assert_eq!((summary.cost_usd, summary.cost_unreported, summary.input_tokens, summary.output_tokens), (0.00004, 0, 900, 3));
    }

    /// 費用も usage も無い答え — 費用は 0 ではなく不明、token も 0 と書かない。
    #[test]
    fn an_answer_without_a_cost_is_unreported_not_zero() {
        let answer = parse_answer(r#"{"answers":{"q":{"noul":0.2}},"model":"jev-1.13.0"}"#).unwrap();
        assert_eq!((answer.reported_cost_usd, answer.input_tokens, answer.output_tokens), (None, None, None));
        let asked = Asked { charge: charge_of(&answer, false), answer };
        assert_eq!(asked.charge, Charge::Unreported);
        let mut summary = SemanticSummary::default();
        summary.tally(&asked);
        assert_eq!((summary.cost_usd, summary.cost_unreported, summary.input_tokens), (0.0, 1, 0));
        // cache に書く形でも「費用は無い」のまま(0 を書かない)。
        let written = serde_json::to_value(&asked.answer).unwrap();
        assert_eq!(written["reported_cost_usd"], Value::Null);
    }

    /// usage(token 数)だけの答え(TypeSafe 直の形)— token は数え、費用は不明として数える(単価を掛ける材料 — #1892 の案 B)。
    #[test]
    fn a_usage_only_answer_keeps_tokens_and_leaves_the_cost_unreported() {
        let answer = parse_answer(r#"{"answers":{"q":{"noul":0.7}},"usage":{"input_tokens":1234,"output_tokens":5},"model":"jev-1.13.0"}"#).unwrap();
        assert_eq!((answer.reported_cost_usd, answer.input_tokens, answer.output_tokens), (None, Some(1234), Some(5)));
        let asked = Asked { charge: charge_of(&answer, false), answer };
        let mut summary = SemanticSummary::default();
        summary.tally(&asked);
        assert_eq!((summary.cost_usd, summary.cost_unreported, summary.input_tokens, summary.output_tokens), (0.0, 1, 1234, 5));
    }

    /// proxy が覚えた答え・相乗りで答えた回は上流を呼んでいない — 本文に元の問いの費用が在っても、その回の費用と token は数えない。
    #[test]
    fn a_remembered_answer_is_not_charged_again() {
        assert!(answered_from_memory(Some("hit")) && answered_from_memory(Some("coalesced")));
        assert!(!answered_from_memory(Some("miss")) && !answered_from_memory(Some("refreshed")) && !answered_from_memory(None));
        let answer = parse_answer(
            r#"{"answers":{"q":{"type":"boolean","probability":0.9}},"providerMetadata":{"gateway":{"cost":"0.00004"}},"usage":{"inputTokens":900}}"#,
        )
        .unwrap();
        let asked = Asked { charge: charge_of(&answer, true), answer };
        assert_eq!(asked.charge, Charge::Remembered);
        let mut summary = SemanticSummary::default();
        summary.tally(&asked);
        assert_eq!((summary.cost_usd, summary.cost_unreported, summary.remembered, summary.input_tokens), (0.0, 0, 1, 0));
    }

    /// 以前の cache の答え(cost_usd を載らない時も 0 と書いていた)は、費用を「不明」として読む。token 数はそのまま読む。
    #[test]
    fn an_old_cache_answer_reads_its_cost_as_unknown() {
        let old: Answer = serde_json::from_str(r#"{"probability":0.1,"cost_usd":0.0,"input_tokens":1000,"served_model":"jev-1.13.0"}"#).unwrap();
        assert_eq!((old.reported_cost_usd, old.input_tokens, old.output_tokens), (None, Some(1000), None));
    }

    #[test]
    fn target_resolution_matches_doeff_jev() {
        let env = |pairs: &'static [(&'static str, &'static str)]| move |key: &str| pairs.iter().find(|(k, _)| *k == key).map(|(_, v)| v.to_string());
        let no_files = |_: &str| None;
        // 既定 = TypeSafe 直・キーは TYPESAFE_API_KEY。
        let direct = resolve_target(&env(&[("TYPESAFE_API_KEY", "k1")]), &no_files, None);
        assert_eq!((direct.base_url.as_str(), direct.model.as_str(), direct.wire, direct.source), (DIRECT_URL, DIRECT_MODEL, Wire::Direct, "default"));
        assert_eq!(direct.api_key.as_deref(), Some("k1"));
        assert!(!format!("{:?}", direct).contains("k1"), "Debug にキーの値を出さない");
        // gateway を名指した時の既定。
        let gateway = resolve_target(&env(&[("JEV_WIRE", "gateway"), ("AI_GATEWAY_API_KEY", "g")]), &no_files, None);
        assert_eq!((gateway.base_url.as_str(), gateway.model.as_str(), gateway.wire), (GATEWAY_URL, GATEWAY_MODEL, Wire::Gateway));
        // 環境変数が設定 file に勝ち、設定 file が既定に勝つ。
        let files = |path: &str| (path == "~/.config/jev/client.json").then(|| r#"{"base_url":"http://seimf:8000/v1/systemone","model":"file-model"}"#.to_string());
        let from_file = resolve_target(&env(&[]), &files, None);
        assert_eq!((from_file.base_url.as_str(), from_file.model.as_str(), from_file.source), ("http://seimf:8000/v1/systemone", "file-model", "file"));
        let from_env = resolve_target(&env(&[("JEV_BASE_URL", "http://x/v1"), ("JEV_MODEL", "m")]), &files, None);
        assert_eq!((from_env.base_url.as_str(), from_env.model.as_str(), from_env.source), ("http://x/v1", "m", "env"));
    }

    #[test]
    fn repo_proxy_gets_only_the_proxy_token_and_env_url_wins() {
        let env = |pairs: &'static [(&'static str, &'static str)]| move |key: &str| pairs.iter().find(|(k, _)| *k == key).map(|(_, v)| v.to_string());
        let proxy = ProxySettings { url: "http://proxy:8878/v1/systemone".into(), token_file: "~/.config/jev/proxy-token".into(), peek_timeout: Duration::from_millis(1500) };
        let token = |path: &str| (path == "~/.config/jev/proxy-token").then(|| "proxy-token\n".to_string());
        // 代理へは代理の token だけ(env の TypeSafe のキーは送らない)。
        let via = resolve_repo_target(&env(&[("TYPESAFE_API_KEY", "real-key")]), &token, Some(&proxy));
        assert_eq!((via.base_url.as_str(), via.model.as_str(), via.wire, via.source, via.proxy), ("http://proxy:8878/v1/systemone", DIRECT_MODEL, Wire::Direct, "repo", true));
        assert_eq!(via.api_key.as_deref(), Some("proxy-token"));
        // token の file が無ければキー無し(TypeSafe のキーへ倒れない)。
        let no_token = resolve_repo_target(&env(&[("TYPESAFE_API_KEY", "real-key")]), &|_| None, Some(&proxy));
        assert_eq!(no_token.api_key, None);
        assert!(HttpGateway::new(no_token, Duration::from_secs(1)).err().unwrap().contains("Jev の API キーが無い"));
        // env の JEV_BASE_URL が在れば repo の代理を使わない。
        let env_wins = resolve_repo_target(&env(&[("JEV_BASE_URL", "http://seimf/v1"), ("TYPESAFE_API_KEY", "k")]), &token, Some(&proxy));
        assert_eq!((env_wins.base_url.as_str(), env_wins.proxy), ("http://seimf/v1", false));
        // gateway を名指した環境でも、代理へは direct の形と direct の model で問う。
        let gateway_env = resolve_repo_target(&env(&[("JEV_WIRE", "gateway")]), &token, Some(&proxy));
        assert_eq!((gateway_env.wire, gateway_env.model.as_str()), (Wire::Direct, DIRECT_MODEL));
    }

    /// architecture.hy に :semantic-lines の無い repo では、問いは wire() のまま。DOEFF201 の cache のキーは線引きを足す前(agora-redesign
    /// #1909 の前)と同じ — キーの値は線引きを足す前の code で組んだ物(変われば、その repo の全部の定義が「答えなし」に戻る)。DOEFF205 のキーは
    /// #1995 で一般の注を線引き 4 の例外と揃えたので変わった(値は #1995 の code で組んだ物)。DOEFF202 のキーは #2059 で枠に外の data の
    /// 型の無い形を足したので変わった(値は #2059 の code で組んだ物)。
    #[test]
    fn without_lines_the_questions_and_keys_stay_as_before() {
        let settings = SemanticSettings::validate(&SemanticSection::default(), &mut |_, _| None, &mut Vec::new());
        let description = LayerDescription {
            summary: Some("翻訳".into()),
            knows: Some("相手の話し方".into()),
            does_not_know: Some("本物か模擬か".into()),
            question: Some("言い換えだけか?".into()),
        };
        let range = { let p = doeff_indexer::hy_index::Position { line: 0, character: 0 }; doeff_indexer::hy_index::Range { start: p, end: p } };
        let source = "(defk f [x] {:tags {:role \"protocol\"}} (when (in x allowed) x))";
        let layered = |question| item(&settings, "jev-latest", question, "a.hy", Path::new("/r/a.hy"), "f", "defk", range, source, LayerId(0), "protocol", &description);
        let decision = layered(SemanticQuestion::BusinessDecision);
        let transport = layered(SemanticQuestion::TransportKnowledge);
        let mixed = mixed_item(&settings, "jev-latest", "a.hy", Path::new("/r/a.hy"), "f", "defk", range, source, LayerId(0), "core", &description);
        for one in [&decision, &transport, &mixed] {
            assert_eq!(one.question_json, one.question.wire(), "線引きの無い問いは今の問いのまま");
        }
        assert_eq!(
            [decision.key.as_str(), transport.key.as_str(), mixed.key.as_str()],
            [
                "2801ee2d98aaee9974cea9bc225c176e997ab2a3743765c359bc9e5ef6a1b1d6",
                "c1ab2a63e708828c1272dc662d53b45b6fd17191cc2832b77f3b7252506684fb",
                "d87343a27410d6d05620233e1bb7ca2998b286adb57c69b148c9d903c16a9050"
            ]
        );
    }

    /// 線引きを入れた問いの instructions の部品(1 つの鍵の object の列)から、鍵 key の中身を引く。
    fn part<'a>(question: &'a Value, key: &str) -> &'a Value {
        question["instructions"].as_array().and_then(|parts| parts.iter().find_map(|p| p.get(key))).unwrap_or(&Value::Null)
    }

    /// 線引きを入れた問いの instructions の部品の鍵(送る順)。
    fn part_keys(question: &Value) -> Vec<String> {
        question["instructions"].as_array().map(|parts| parts.iter().filter_map(|p| p.as_object()?.keys().next().cloned()).collect()).unwrap_or_default()
    }

    /// 線引き 1 つ(鳴る例は why つき・鳴らない例は code だけ)。
    fn sample_line(name: &str, rules: Vec<SemanticQuestion>) -> SemanticLine {
        SemanticLine {
            name: name.into(),
            rules,
            text: format!("{} の文", name),
            fires: vec![LineExample { code: "(defk f [x] {:tags {:role \"protocol\"}} (g x))".into(), why: Some(format!("{} で鳴る理由", name)) }],
            silent: vec![LineExample { code: "(defk h [x] x)".into(), why: None }],
        }
    }

    /// agora-redesign #1909: 線引きはその規則の問いにだけ入る(名・文・鳴る例・鳴らない例・答えの向き)。例の code の :tags は消し、why を書いた
    /// 例は `{"code", "why"}`、書かない例は `{"code"}` で載る(#1995)。線引きを入れた問いは cache のキーも変わる(線引きを変えると答えなしに
    /// 戻る — 想定どおり)。
    #[test]
    fn lines_go_into_the_instructions_of_their_questions_only() {
        use SemanticQuestion::{BusinessDecision, MixedConcerns, PlainCallable, TransportKnowledge};
        let line = sample_line;
        let lines = vec![line("一", vec![BusinessDecision]), line("二", vec![TransportKnowledge]), line("五", vec![MixedConcerns, BusinessDecision])];
        let names = |question: &Value| -> Vec<String> {
            part(question, "lines").as_array().map(|all| all.iter().filter_map(|l| l["name"].as_str().map(str::to_string)).collect()).unwrap_or_default()
        };
        let decision = BusinessDecision.wire_with(&lines);
        assert_eq!(names(&decision), ["一", "五"]);
        assert_eq!(
            part(&decision, "lines")[0],
            json!({"name": "一", "text": "一 の文", "violating_examples": [{"code": "(defk f [x] {} (g x))", "why": "一 で鳴る理由"}], "complying_examples": [{"code": "(defk h [x] x)"}]})
        );
        assert_eq!(part(&decision, "question"), &BusinessDecision.wire()["instructions"]["question"]);
        assert_eq!(decision["criteria"], BusinessDecision.wire()["criteria"]);
        assert!(part(&decision, "lines_note").as_str().is_some_and(|note| note.contains("answer true") && note.contains("`why`")));
        let transport = TransportKnowledge.wire_with(&lines);
        assert_eq!(names(&transport), ["二"]);
        assert_eq!(part(&transport, "question"), &TransportKnowledge.wire()["instructions"]["question"]);
        let mixed = MixedConcerns.wire_with(&lines);
        assert_eq!(names(&mixed), ["五"]);
        assert!(part(&mixed, "lines_note").as_str().is_some_and(|note| note.contains("is `mixed`")));
        // 線引きを入れない問いと、当たる線引きの無い問いは今のまま。
        assert_eq!(PlainCallable.wire_with(&lines), PlainCallable.wire());
        assert_eq!(TransportKnowledge.wire_with(&lines[..1]), TransportKnowledge.wire());
        let bare = SemanticSettings::validate(&SemanticSection::default(), &mut |_, _| None, &mut Vec::new());
        let lined = SemanticSettings { lines, ..bare.clone() };
        let description = LayerDescription::default();
        let range = { let p = doeff_indexer::hy_index::Position { line: 0, character: 0 }; doeff_indexer::hy_index::Range { start: p, end: p } };
        let asked = |settings: &SemanticSettings| {
            item(settings, "m", BusinessDecision, "a.hy", Path::new("/r/a.hy"), "f", "defk", range, "(defk f [x] x)", LayerId(0), "protocol", &description)
        };
        let (before, after) = (asked(&bare), asked(&lined));
        assert_ne!(before.key, after.key);
        assert_eq!(after.question_json, BusinessDecision.wire_with(&lined.lines));
    }

    /// agora-redesign #1995: 線引きを入れた問いの instructions は、宣言の順(問いの文 → 一般の例・注 → 線引きの読み方 → 線引き)の列で送る —
    /// 鍵の object だと鍵の名の順に並び、DOEFF205 の一般の注(note)が線引き(lines)の後ろに来て、「上の注と違えば線引きに従え」が
    /// 逆さに読めた。送る本文の文字列でも、この順に出る(列は綴りでも順を保つ)。
    #[test]
    fn lined_instructions_go_in_the_declared_order_with_the_lines_last() {
        use SemanticQuestion::{BusinessDecision, MixedConcerns, TransportKnowledge};
        let lines = vec![sample_line("一", vec![BusinessDecision, TransportKnowledge, MixedConcerns])];
        for (question, keys) in [
            (BusinessDecision, vec!["question", "business_decision_examples", "not_business_decision_examples", "lines_note", "lines"]),
            (TransportKnowledge, vec!["question", "note", "transport_knowledge_examples", "not_transport_knowledge_examples", "lines_note", "lines"]),
            (MixedConcerns, vec!["question", "note", "lines_note", "lines"]),
        ] {
            let wired = question.wire_with(&lines);
            assert_eq!(part_keys(&wired), keys, "{} の部品の順", question.id());
            // 送る本文の綴り(proxy へ送る JSON の文字列)でも、部品の中身は宣言の順に出る。
            let sent = serde_json::to_string(&wired).unwrap();
            let at: Vec<usize> = keys.iter().map(|key| sent.find(&format!("\"{}\":", key)).unwrap_or_else(|| panic!("{} が無い: {}", key, sent))).collect();
            assert!(at.windows(2).all(|pair| pair[0] < pair[1]), "{} の送る順が宣言の順と違う: {:?}\n{}", question.id(), at, sent);
        }
    }

    /// agora-redesign #1995: DOEFF205 の一般の注は、線引き 4 の例外(型の union の枝分け・外から来ていない dict・形の確認を任せた関数の答えで
    /// 断るだけの枝)を同じ向きで言う — 前の注は isinstance と dict の読みを例外なしに形の確認と言い、線引きと食い違った。
    #[test]
    fn the_mixed_concerns_note_states_the_exceptions_of_the_lines() {
        let note = SemanticQuestion::MixedConcerns.wire()["instructions"]["note"].as_str().unwrap_or_default().to_string();
        for needle in ["untyped input that came from outside", "branching on the cases of a typed union", "did not come from outside", "the shape check was left to"] {
            assert!(note.contains(needle), "注に {:?} が無い: {}", needle, note);
        }
    }

    /// agora-redesign #2059: DOEFF202 の枠は、通信の手段(a)と外の data の型の無い形(b — dict・JSON の値を欄名で読む・組む)を同じ重さで問う。
    /// 前の枠は通信の手段だけで、json.loads を書かない欄名の読みは repo の線引きと例を足しても広がらなかった。true の例に json.loads を
    /// 含まない欄名の読みが在り、false の例に型の値だけで判じる core が在る。
    #[test]
    fn the_transport_knowledge_frame_weighs_untyped_field_reads_like_transport() {
        let wired = SemanticQuestion::TransportKnowledge.wire();
        let instructions = &wired["instructions"];
        let question = instructions["question"].as_str().unwrap_or_default();
        for needle in ["(a) how communication is carried out", "(b) the untyped shape of outside data", "by field name"] {
            assert!(question.contains(needle), "問いの文に {:?} が無い: {}", needle, question);
        }
        let note = instructions["note"].as_str().unwrap_or_default();
        for needle in ["(b) weighs the same as (a)", "whether or not the code calls json.loads", "the protocol side's job"] {
            assert!(note.contains(needle), "注に {:?} が無い: {}", needle, note);
        }
        let texts = |key: &str| -> Vec<String> {
            instructions[key].as_array().map(|all| all.iter().filter_map(|t| t.as_str().map(str::to_string)).collect()).unwrap_or_default()
        };
        assert!(texts("transport_knowledge_examples").iter().any(|t| t.contains("no json.loads in the code")), "true の例に json.loads を含まない欄名の読みが無い");
        assert!(texts("not_transport_knowledge_examples").iter().any(|t| t.contains("receives typed values")), "false の例に型の値だけで判じる core が無い");
        assert!(wired["criteria"]["true"].as_str().is_some_and(|t| t.contains("with or without json.loads")));
    }

    #[test]
    fn half_written_definitions_are_not_readable() {
        assert!(readable("(defk f [x] (+ x 1))"));
        assert!(!readable("(defk f [x] (+ x"));
        assert!(!readable("(defk f [x] \"open"));
        assert!(!readable("(defk f [x] x))"));
        assert!(!readable("   "));
    }
}
