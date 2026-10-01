//! 層の規則の結果の型(違反 1 件・立場・出どころ・module の要約・結果の全部)。読み手(editor.rs・notice.rs・unreadable.rs)は
//! ここを直に読む — 組み立ての project/mod.rs を読み戻すと、mod.rs が宣言する子の module と依存の輪になる(agora-redesign #2121)。

use std::path::PathBuf;

use crate::models::Severity;
use crate::position::Range;

use super::explain::Explanation;
use super::rule::ProjectRule;
use super::{semantic, signatures};

/// 違反 1 件(law と登録簿を当てた後)。
#[derive(Debug, Clone)]
pub struct Finding {
    pub rule: ProjectRule,
    pub law: Option<String>,
    pub adr: Option<String>,
    pub severity: Severity,
    /// file の絶対の path。
    pub path: PathBuf,
    /// repo の根からの path(区切り `/`)。
    pub rel: String,
    pub range: Range,
    pub message: String,
    pub hint: String,
    /// 登録簿の鍵 `<path>::<law か規則の ID>[::<細目>]`。
    pub key: String,
    /// 登録簿に鍵が載っているか(照合中の規則でも載っていれば真)。
    pub registered: bool,
    /// 登録簿と照合中を当てる前の、規則そのものの重さ(`severity` はこれを下げた後の重さ)。
    pub base_severity: Severity,
    /// 新しい破れか・登録簿に載った既知の破れか・照合中で下げたか。
    pub standing: Standing,
    /// これは何か・なぜ違反か・law の文(explain.rs が作る)。
    pub explanation: Explanation,
    /// 判定の出どころ(決定的な規則か Jev か)。
    pub origin: FindingOrigin,
    /// Jev の判定の確率(Jev の違反だけ)。
    pub probability: Option<f64>,
}

/// 違反の立場 — 重さを下げた理由の閉じた集合(エディタが「手つかずの重い破れ」を数えるため)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Standing {
    /// 登録簿に無い新しい破れ(重さは規則そのもの)。
    New,
    /// 登録簿に載った既知の破れ(重さは下げてある)。
    Registered,
    /// 照合中の規則の破れ(`registry.reconciling` — 登録簿の有無によらず info に下げてある)。
    Reconciling,
}

impl Standing {
    /// 登録簿の有無と照合中かから立場を決める(照合中が先 — 重さを info に下げた理由はそちら)。
    pub fn of(registered: bool, reconciling: bool) -> Standing {
        match (reconciling, registered) {
            (true, _) => Standing::Reconciling,
            (false, true) => Standing::Registered,
            (false, false) => Standing::New,
        }
    }
}

/// 違反の判定の出どころ。
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum FindingOrigin {
    /// 決定的な規則。
    Linter,
    /// Jev の意味の判定。
    Jev,
}

/// 地図の材料 — 層の規則が読んだ module 1 つ。
#[derive(Debug, Clone)]
pub struct ModuleSummary {
    pub path: PathBuf,
    pub rel: String,
    pub layer: Option<String>,
    pub context: Option<String>,
    pub role: Option<String>,
    /// 層を何で決めたか(path の置き場所・タグ・両方の食い違い)。
    pub layer_reason: Option<String>,
    /// 置き場の `*` の段に当たった service の名(無ければ None)。
    pub service: Option<String>,
}

/// 層の規則の結果の全部。
#[derive(Debug, Clone, Default)]
pub struct ProjectReport {
    pub findings: Vec<Finding>,
    pub modules: Vec<ModuleSummary>,
    /// 読めなかった file・登録簿・目録の理由。
    pub errors: Vec<String>,
    /// 誤りではない知らせ(無い登録簿の dir を空として読んだ — agora-redesign #1732)。
    pub notes: Vec<String>,
    /// 意味の規則の要約(設定が無ければ None)。
    pub semantic: Option<semantic::SemanticSummary>,
    /// 1 file の実行で組んだ effect の推論の表(組んだ時だけ・書いた file の中身を overlay にした物)。
    pub world: Option<signatures::World>,
}
