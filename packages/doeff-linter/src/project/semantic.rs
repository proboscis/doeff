//! 意味の規則(DOEFF201・202)— 決定的な規則では読めない「コードが何をしているか」を Jev(TypeSafe の System One の model)に問う。
//! 宛先(URL・model・通信の形・API キー)の決め方は doeff の packages/doeff-jev/src/doeff_jev/target.py の写し(下の resolve_target)、
//! 通信の形(direct = TypeSafe の /v1/systemone・gateway = Vercel AI Gateway の evaluation-model v4)は同じ package の wire.py の写し。
//!
//! - 問いの文(instructions・criteria)はこの file の 1 か所の宣言(英語のまま — jev-lint の questions.py の J2・J3 と同じ内容)。
//! - gateway を呼ぶのは `--semantic` / `--semantic-all` の時だけ。決定的な規則の実行(エディタの保存ごと・hook)は cache を読むだけ。
//! - cache の答えが無い定義は違反にせず「未判定」の数に出す(合格に倒さない)。
//! - 重さは warning か info だけ(当たり外れを測り終えるまで error にしない — 設定でも選べない)。
//! - 較正の見張り: 問いを撃つ実行ごとに、既知の正例と反例(data/semantic_calibration.json)を 1 回ずつ問い、確率が幅の外なら cache を捨てて警告する。
//! API キーの値は、設定・出力・log・cache の鍵に書かない。
//!
//! Jev の呼び出しを覚える代理(doeff の packages/doeff-jev-proxy・agora-redesign #843):
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
                "instructions": {
                    "question": "Does the code in `definition.source` make a business decision, beyond rephrasing a request into the other party's way of talking?",
                    "business_decision_examples": [
                        "deciding who is allowed to do something",
                        "enforcing a domain rule such as which participants a chat may have or where a reply may be posted",
                        "choosing recipients or a business outcome"
                    ],
                    "not_business_decision_examples": [
                        "building URLs, headers or request bodies",
                        "parsing JSON, HTTP status codes or process output into typed values",
                        "turning transport failures into error values",
                        "retry and timeout policy",
                        "checking the shape of a payload or reading configuration"
                    ]
                },
                "criteria": {
                    "true": "The code decides a business matter (permission, domain rule, recipient, business outcome).",
                    "false": "The code only translates: it builds or reads the wire form, maps failures, or checks shapes."
                }
            }),
            SemanticQuestion::PlainCallable => Self::plain_callable_wire(&[], &[]),
            SemanticQuestion::MixedConcerns => json!({
                "type": "choice",
                "instructions": {
                    "question": "What does the definition in `definition.source` do? `layer` describes what code in its layer should know and not know.",
                    "note": "Shape checking means validating untyped input: reading keys out of dicts or JSON payloads, isinstance checks, and empty or missing checks on those raw values before they can be used. Business judgment means deciding by business rules (who may do what, which outcome, which write). Comparing already-typed values by a business rule is judgment, not shape checking."
                },
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
                "instructions": "Does the code in `definition.source` know how communication is carried out: URLs or URL paths and query strings, HTTP methods, status codes or headers, JSON wire field names or JSON encoding and decoding, SQL, or network endpoint addresses?",
                "criteria": {
                    "true": "The code builds, parses or holds such transport details, for example a URL, a query string, an HTTP request or status code, json.loads or json.dumps of a wire body, or an endpoint URL field.",
                    "false": "The code only works with typed business values and business-level request effects; naming an outside system without its transport details does not count."
                }
            }),
        }
    }

    /// 問いの意味(説明の文に差し込む日本語)。
    pub fn meaning(self) -> &'static str {
        match self {
            SemanticQuestion::BusinessDecision => "要求の言い換えを越えて、業務の判断(誰に許すか・業務の決まり・宛先・業務の結果)をしている",
            SemanticQuestion::TransportKnowledge => "通信の手段(URL や query・HTTP の method や status・JSON の wire・SQL・宛先の address)を知っている",
            SemanticQuestion::PlainCallable => "名乗った理由の種類では、素の関数でなければならない理由にならない見込み",
            SemanticQuestion::ClassRole => "処理を持つ method のある class が、外の世界の窓口か状態を持つ物の見込み",
            SemanticQuestion::MixedConcerns => "判断の定義が、入力の形の検めと業務の判断を混ぜている見込み",
        }
    }
}

/// `[tool.doeff-linter.semantic]` の問い 1 つの設定(読んだ形)。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
#[serde(deny_unknown_fields)]
pub struct QuestionSection {
    /// 問いを当てる層の名。
    #[serde(default)]
    pub layers: Vec<String>,
    /// この確率以上で warning。
    pub warning: Option<f64>,
    /// この確率以上で info。
    pub info: Option<f64>,
}

/// `[tool.doeff-linter.semantic]`(読んだ形)。重さは warning と info だけで、error の欄は無い。宛先・model・キーはここに書かない
/// (doeff-jev と同じ決め方 — 環境変数 JEV_* と ~/.config/jev/client.json)。例外は Jev の呼び出しを覚える代理の宛先 proxy_url(repo ごとに向ける)。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
#[serde(deny_unknown_fields)]
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
}

/// `[tool.doeff-linter.semantic] mixed_concerns`(読んだ形)。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
#[serde(deny_unknown_fields)]
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
/// 代理の鍵の決まりの版(doeff の packages/doeff-jev-proxy/src/doeff_jev_proxy/key.hy の KEY-VERSION と同じ)。
pub const PROXY_KEY_VERSION: &str = "jev-proxy-key-1";

/// `[tool.doeff-linter.semantic] plain_callable`(読んだ形)。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
#[serde(deny_unknown_fields)]
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

/// 問いの本文の代理の鍵(64 桁の小文字の 16 進)= sha256(PROXY_KEY_VERSION + "\n" + 決まった綴りの本文)。代理の決まり(doeff の
/// packages/doeff-jev-proxy/src/doeff_jev_proxy/key.hy の normalize-request — object の鍵を符号位置の順に並べ・区切りの空白なし・
/// 文字は UTF-8 のまま)と同じ鍵になる。本文は model を持つこと(代理は model の無い本文に既定の名を足してから綴る)。小数は綴りが
/// 言語で違うので同じ鍵にならない(外れるだけで、別の問いの答えには当たらない — linter の本文は小数を持たない)。
/// 同じ鍵になることは、代理の見本(packages/doeff-jev-proxy/tests/key_contract.json)を両方の検が読んで確かめる。
pub fn proxy_key(body: &Value) -> String {
    let mut hasher = Sha256::new();
    hasher.update(PROXY_KEY_VERSION.as_bytes());
    hasher.update(b"\n");
    hasher.update(canonical(body).as_bytes());
    hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect()
}

/// 定義 1 つの state と cache の鍵を作る。鍵 = sha256(model・問いの JSON・層の説明・タグを消した source)。申告の役は鍵に入れない。
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
    let mut hasher = Sha256::new();
    for part in [model.to_string(), canonical(&question.wire()), canonical(&layer_json), stripped] {
        hasher.update(part.as_bytes());
        hasher.update(b"\n");
    }
    let key = hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect();
    SemanticItem { question, question_json: question.wire(), declared: None, rel: rel.to_string(), path: path.to_path_buf(), name: name.to_string(), kind, range, layer, state, key, readable: readable(source) }
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

/// DOEFF205 の定義 1 つの state と cache の鍵を作る(state = 定義の source(タグを消して切る)と物差しの層の説明)。
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
    let question_json = question.wire();
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

/// 較正の見張りで比べる確率 — Noul は答えの確率、DOEFF204 は external-world の確率(正例 = 窓口・反例 = 値の class)。
fn calibration_probability(question: SemanticQuestion, answer: &Answer) -> f64 {
    match question {
        SemanticQuestion::ClassRole => answer.probabilities.as_ref().and_then(|p| p.get("external-world").copied()).unwrap_or(0.0),
        SemanticQuestion::MixedConcerns => answer.probabilities.as_ref().and_then(|p| p.get("mixed").copied()).unwrap_or(0.0),
        SemanticQuestion::BusinessDecision | SemanticQuestion::TransportKnowledge | SemanticQuestion::PlainCallable => answer.probability,
    }
}

/// Jev の答え(確率・gateway が返した費用 USD・入力のトークン)。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Answer {
    pub probability: f64,
    #[serde(default)]
    pub cost_usd: f64,
    #[serde(default)]
    pub input_tokens: u64,
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
    /// 1 つの定義に 1 つの問いを撃つ。
    fn ask(&self, state: &Value, question: &Value, freshness: Freshness) -> Result<Answer, String>;
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
    /// 1 回問う(429・5xx は 3 回まで間を空けて撃ち直す)。代理には Fresh の時だけ Cache-Control: no-cache を付ける。
    fn ask(&self, state: &Value, question: &Value, freshness: Freshness) -> Result<Answer, String> {
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
                    let text = ok.into_string().map_err(|e| format!("答えを読めない: {}", e))?;
                    return parse_answer(&text);
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
    let cost_usd = value
        .pointer("/providerMetadata/gateway/cost")
        .and_then(|c| c.as_str().and_then(|s| s.parse::<f64>().ok()).or_else(|| c.as_f64()))
        .unwrap_or(0.0);
    let usage = value.get("usage");
    let input_tokens = usage.and_then(|u| u.get("input_tokens").or_else(|| u.get("inputTokens"))).and_then(Value::as_u64).unwrap_or(0);
    let served_model = value.get("model").and_then(Value::as_str).map(str::to_string);
    Ok(Answer { probability, cost_usd, input_tokens, served_model, choice, probabilities })
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
    /// 今回 gateway へ撃った数(較正を含む)。
    pub asked: usize,
    /// 今回、代理が覚えていた答えを受け取って手元の cache に書いた数(覚えている時だけの問い — 本物の Jev は呼んでいない)。
    pub peeked: usize,
    /// 今回の費用(gateway が返した USD の和 — direct は費用を返さないので 0)。
    pub cost_usd: f64,
    /// 今回の入力のトークンの和。
    pub input_tokens: u64,
    /// 今回答えた model の版つきの名(direct だけ・撃たない実行は null)。
    pub served_model: Option<String>,
    /// 較正の見張りの結果(not-run・ok・drifted・failed)。
    pub calibration: String,
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
}

/// 意味の規則の結果(答えのある定義と、要約・理由)。
pub struct SemanticOutcome {
    pub answered: Vec<(SemanticItem, Answer)>,
    pub summary: SemanticSummary,
    pub errors: Vec<String>,
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
                SemanticMode::AskChanged(targets) => targets.contains(&item.rel) && read_cache(root, &item.key).is_none(),
            }
    };
    let asking = items.iter().any(wants_ask);
    let pool = rayon::ThreadPoolBuilder::new().num_threads(settings.workers).build();
    if asking {
        match (gateway, &pool) {
            (Some(gateway), Ok(pool)) => {
                let results: Vec<(bool, Result<Answer, String>)> = pool.install(|| {
                    calibration.par_iter().map(|(c, expect)| (*expect, gateway.ask(&c.state, &c.question_json, Freshness::Fresh))).collect()
                });
                summary.asked += results.len();
                let mut drifted = Vec::new();
                for ((expect, result), (example, _)) in results.into_iter().zip(calibration) {
                    match result {
                        Ok(answer) => {
                            summary.cost_usd += answer.cost_usd;
                            summary.input_tokens += answer.input_tokens;
                            if answer.served_model.is_some() {
                                summary.served_model = answer.served_model.clone();
                            }
                            let probability = calibration_probability(example.question, &answer);
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
    let resolved: Vec<(SemanticItem, Option<Result<Answer, String>>)> = match (&pool, gateway) {
        (Ok(pool), Some(gateway)) if can_ask => pool.install(|| {
            items
                .into_par_iter()
                .map(|item| {
                    if wants_ask(&item) {
                        let result = gateway.ask(&item.state, &item.question_json, Freshness::Remembered);
                        (item, Some(result))
                    } else {
                        let cached = read_cache(root, &item.key).map(Ok);
                        (item, cached)
                    }
                })
                .collect()
        }),
        _ => items.into_iter().map(|item| {
            let cached = read_cache(root, &item.key).map(Ok);
            (item, cached)
        }).collect(),
    };
    // 全体の実行・hook: 手元の cache に無い読める定義を、代理の鍵の束で代理に「覚えている時だけ」問う(本物の Jev は呼ばない)。
    let resolved = match (mode, gateway, &pool, settings.proxy.as_ref()) {
        (SemanticMode::Peek, Some(gateway), Ok(pool), Some(proxy)) => {
            let wanted: Vec<Option<String>> = resolved
                .iter()
                .map(|(item, cached)| match cached {
                    None if item.readable => gateway.proxy_key(&item.state, &item.question_json),
                    _ => None,
                })
                .collect();
            let keys: Vec<String> = wanted.iter().flatten().cloned().collect::<BTreeSet<String>>().into_iter().collect();
            let remembered = if keys.is_empty() { BTreeMap::new() } else { peek_remembered(gateway, &keys, proxy.peek_timeout, pool) };
            resolved
                .into_iter()
                .zip(wanted)
                .map(|((item, cached), key)| match key.and_then(|k| remembered.get(&k)) {
                    Some(answer) => {
                        summary.peeked += 1;
                        if let Err(reason) = write_cache(root, &item.key, answer) {
                            errors.push(reason);
                        }
                        (item, Some(Ok(answer.clone())))
                    }
                    None => (item, cached),
                })
                .collect()
        }
        _ => resolved,
    };
    let mut answered = Vec::new();
    for (item, result) in resolved {
        let asked_now = can_ask && wants_ask(&item);
        match result {
            Some(Ok(answer)) => {
                if asked_now {
                    summary.asked += 1;
                    summary.cost_usd += answer.cost_usd;
                    summary.input_tokens += answer.input_tokens;
                    if let Err(reason) = write_cache(root, &item.key, &answer) {
                        errors.push(reason);
                    }
                }
                summary.judged += 1;
                answered.push((item, answer));
            }
            Some(Err(reason)) => {
                summary.asked += 1;
                summary.unjudged += 1;
                errors.push(format!("{} の {}: Jev に問えない: {}", item.rel, item.name, reason));
            }
            None => summary.unjudged += 1,
        }
    }
    SemanticOutcome { answered, summary, errors }
}

/// 代理の鍵の束を PEEK_BATCH 個ずつに分けて並べて代理に「覚えている時だけ」問い、覚えていた答えを集める。全部の束を合わせた時間の
/// 上限つきで、どれかの束が届かなければ残りの束は撃たない(集まらなかった鍵は手元の cache だけで動く)。
fn peek_remembered(gateway: &dyn Gateway, keys: &[String], timeout: Duration, pool: &rayon::ThreadPool) -> BTreeMap<String, Answer> {
    let deadline = Instant::now() + timeout;
    let unreachable = AtomicBool::new(false);
    let batches: Vec<&[String]> = keys.chunks(PEEK_BATCH).collect();
    pool.install(|| {
        batches
            .par_iter()
            .map(|batch| {
                let left = deadline.saturating_duration_since(Instant::now());
                if left.is_zero() || unreachable.load(Ordering::Relaxed) {
                    return BTreeMap::new();
                }
                match gateway.peek_many(batch, left) {
                    PeekedMany::Remembered(answers) => answers,
                    PeekedMany::Unreachable(_) => {
                        unreachable.store(true, Ordering::Relaxed);
                        BTreeMap::new()
                    }
                }
            })
            .reduce(BTreeMap::new, |mut all, part| {
                all.extend(part);
                all
            })
    })
}

impl SemanticItem {
    /// 問いの ID(較正の知らせの文のため)。
    fn question_id(&self) -> &'static str {
        match self.question {
            SemanticQuestion::BusinessDecision => "DOEFF201",
            SemanticQuestion::TransportKnowledge => "DOEFF202",
            SemanticQuestion::PlainCallable => "DOEFF203",
            SemanticQuestion::ClassRole => "DOEFF204",
            SemanticQuestion::MixedConcerns => "DOEFF205",
        }
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
            Answer { probability: 0.93, cost_usd: 0.00006, input_tokens: 10, served_model: None, choice: None, probabilities: None }
        );
        assert_eq!(parse_answer(r#"{"answers":{"q":{"noul":0.2}},"usage":{"input_tokens":7},"model":"jev-1"}"#).unwrap().probability, 0.2);
        assert!(parse_answer("{}").is_err());
        assert_eq!(calibration_examples().len(), 8);
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

    #[test]
    fn half_written_definitions_are_not_readable() {
        assert!(readable("(defk f [x] (+ x 1))"));
        assert!(!readable("(defk f [x] (+ x"));
        assert!(!readable("(defk f [x] \"open"));
        assert!(!readable("(defk f [x] x))"));
        assert!(!readable("   "));
    }
}
