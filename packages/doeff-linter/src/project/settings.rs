//! 層の規則(DOEFF101〜108)の設定 — `[tool.doeff-linter.*]` の節の形(TOML から読む形)と、読んだ後に検めた形。
//!
//! 層の名前・role・環境の語・登録簿の置き場は repo ごとに違うので、Rust には書き込まず、すべてこの設定から受け取る。
//! 読んだ形(`*Section`)は serde の写しで、`ProjectSettings::validate` が名前の食い違いを理由つきの誤りにして
//! 検めた形(`ProjectSettings`)へ直す。規則はこの検めた形だけを読む。

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

use super::rule::ProjectRule;

/// `[tool.doeff-linter.layers]` — 層の順と置き場と、層ごとの import の決まり。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct LayersSection {
    /// 層の名前(外の世界からの遠さの順)。
    #[serde(default)]
    pub order: Vec<String>,
    /// 層の名前 → repo の根からの置き場(1 つの綴りか、綴りの列)。段 `*` は service の名に当たる(例 `controllers/*/core`)。
    #[serde(default)]
    pub paths: BTreeMap<String, PathPatterns>,
    /// 層の規則の外に置く path の区切り(dir の名か file の名の完全一致 — 例 `tests`・`conftest.py`)。
    #[serde(default)]
    pub exclude: Vec<String>,
    /// 層の module として読む拡張子(既定 hy・hyk・hyp・py)。
    #[serde(default)]
    pub extensions: Option<Vec<String>>,
    /// 層の名前 → import してよい層の名前(同じ層も書く)。書かない層は制限しない。
    #[serde(default)]
    pub allow_imports: BTreeMap<String, Vec<String>>,
    /// 層の名前 → 直に import してはいけない module の綴り(前方一致 — 例 httpx・urllib.request)。
    #[serde(default)]
    pub forbid_modules: BTreeMap<String, Vec<String>>,
    /// 型だけを置く層(関数と handler を定めない)。
    #[serde(default)]
    pub types_only: Vec<String>,
    /// 型だけの層で数える関数の定義の形(Hy の頭の綴り。既定 defk・deff・defp・defpp・defhandler・defn)。
    #[serde(default)]
    pub function_definers: Option<Vec<String>>,
    /// 層の名前 → 層の説明(出力の layers と、違反の理由の文に差し込む)。
    #[serde(default)]
    pub describe: BTreeMap<String, LayerDescription>,
}

/// 層の置き場の書き方 — 1 つの綴りか、綴りの列(移行の途中は、層が先の形と service が先の形を並べて書く)。
#[derive(Debug, Deserialize, Serialize, Clone, PartialEq, Eq)]
#[serde(untagged)]
pub enum PathPatterns {
    One(String),
    Many(Vec<String>),
}

impl PathPatterns {
    /// 綴りの列。
    pub fn list(&self) -> Vec<&str> {
        match self {
            PathPatterns::One(one) => vec![one.as_str()],
            PathPatterns::Many(many) => many.iter().map(String::as_str).collect(),
        }
    }
}

/// 置き場の綴りの 1 段(そのままの名か、service の名に当たる `*`)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PlaceSegment {
    Literal(String),
    Service,
}

/// 置き場の綴り 1 つ(検めた後)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlacePattern {
    /// 設定に書かれた綴り(揃えた後)。
    pub text: String,
    pub segments: Vec<PlaceSegment>,
}

/// file の path が置き場に当たった結果(実際の dir と、`*` に当たった service の名)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlaceMatch {
    pub dir: String,
    pub service: Option<String>,
}

impl PlacePattern {
    /// 綴りを読む。空・`.`・絶対 path・`..`・段の中の `*`(`foo*`)・`*` の 2 つ以上は理由を返す。
    pub fn parse(text: &str) -> Result<PlacePattern, String> {
        let normalized = normalize_dir(text);
        if normalized.is_empty() || normalized == "." || normalized.starts_with('/') {
            return Err(format!("置き場 {:?} は repo の根の下の dir でない(空・`.`・絶対 path は使えない)", text));
        }
        let mut segments = Vec::new();
        for part in normalized.split('/') {
            match part {
                "*" => segments.push(PlaceSegment::Service),
                ".." | "." | "" => return Err(format!("置き場 {:?} に `..`・`.`・空の段は使えない", text)),
                other if other.contains('*') => return Err(format!("置き場 {:?} の `*` は段まるごとだけに書ける", text)),
                other => segments.push(PlaceSegment::Literal(other.to_string())),
            }
        }
        if segments.iter().filter(|s| **s == PlaceSegment::Service).count() > 1 {
            return Err(format!("置き場 {:?} の `*`(service の段)は 1 つまで", text));
        }
        Ok(PlacePattern { text: normalized, segments })
    }

    /// `*` より前のそのままの段(探索を始める dir)。
    pub fn base(&self) -> String {
        self.segments
            .iter()
            .take_while(|s| matches!(s, PlaceSegment::Literal(_)))
            .map(|s| match s {
                PlaceSegment::Literal(text) => text.as_str(),
                PlaceSegment::Service => "",
            })
            .collect::<Vec<_>>()
            .join("/")
    }

    /// file の repo の根からの path がこの置き場の下なら、実際の dir と service の名を返す。
    pub fn matches(&self, rel: &str) -> Option<PlaceMatch> {
        let parts: Vec<&str> = rel.split('/').collect();
        if parts.len() <= self.segments.len() {
            return None;
        }
        let mut service = None;
        for (segment, part) in self.segments.iter().zip(parts.iter()) {
            match segment {
                PlaceSegment::Literal(text) if text == part => {}
                PlaceSegment::Literal(_) => return None,
                PlaceSegment::Service => service = Some(part.to_string()),
            }
        }
        Some(PlaceMatch { dir: parts[..self.segments.len()].join("/"), service })
    }

    /// 段の数(当たった置き場が 2 つある時は、段の多い方を採る)。
    pub fn depth(&self) -> usize {
        self.segments.len()
    }
}

/// `[tool.doeff-linter.services]` — service の境界(DOEFF109)と、タグの文脈と service の照らし(DOEFF113)。
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct ServicesSection {
    /// 入り切り(既定 true)。
    #[serde(default = "default_true")]
    pub enabled: bool,
    /// 共有の置き場の名(どの service からも読める — 例 shared)。
    #[serde(default)]
    pub shared: Vec<String>,
    /// 境界を守る層(この層の module が別の service を読むと破れ — 例 core・protocol)。
    #[serde(default)]
    pub guarded_layers: Vec<String>,
    /// 別の service から読んでよい層(例 intent)。
    #[serde(default)]
    pub open_layers: Vec<String>,
    /// 例外(from の service が to の service を読んでよい)。
    #[serde(default)]
    pub exceptions: Vec<ServiceException>,
    /// タグの :context と dir の service を照らすか(既定 true)。
    #[serde(default = "default_true")]
    pub check_context: bool,
}

/// service の境界の例外 1 件。
#[derive(Debug, Deserialize, Serialize, Clone, PartialEq, Eq)]
pub struct ServiceException {
    pub from: String,
    pub to: String,
}

/// 省略時に true の欄の既定値。
fn default_true() -> bool {
    true
}

/// service の境界の設定(検めた後)。
#[derive(Debug, Clone)]
pub struct ServiceSettings {
    pub shared: BTreeSet<String>,
    pub guarded: BTreeSet<LayerId>,
    pub open: BTreeSet<LayerId>,
    pub exceptions: BTreeSet<(String, String)>,
    pub check_context: bool,
}

/// `[tool.doeff-linter.layers.describe.<層>]` — 層が何か(Rust には書かず、repo ごとの設定が持つ)。
#[derive(Debug, Deserialize, Serialize, Default, Clone, PartialEq, Eq)]
pub struct LayerDescription {
    /// 層の要約(例 業務の判断と Program)。
    pub summary: Option<String>,
    /// この層が知る物。
    pub knows: Option<String>,
    /// この層が知らない物。
    pub does_not_know: Option<String>,
    /// 迷った時の問い。
    pub question: Option<String>,
}

/// `[tool.doeff-linter.tags]` — タグの読み方(doeff-hy の綴り。既定のままでよい)。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct TagsSection {
    /// Hy の module の頭のタグの名(`(val MODULE-TAGS {…})`)。
    pub module_variable_hy: Option<String>,
    /// Python の module の頭のタグの名(`MODULE_TAGS = {…}`)。
    pub module_variable_py: Option<String>,
    /// 契約の辞書に `:tags` を書ける定義の形。
    pub contract_definers: Option<Vec<String>>,
    /// タグを書く場所の無い定義の形(module の頭のタグに頼る)。
    pub plain_definers: Option<Vec<String>>,
    /// `(名 "doc"? {… :tags {…}})` の形の effect の型の定義の形(既定 defeffect)。
    pub effect_definers: Option<Vec<String>>,
    /// `(名 "doc"? {:tags {…} :check […]}? 欄 …)` の形の record の型の定義の形(既定 defrecord — doeff-hy の頭の辞書・
    /// agora-redesign #798)。頭の辞書が在ればその :tags を読み、無ければタグを書いていない定義。
    pub record_definers: Option<Vec<String>>,
    /// DOEFF112: 定義の :tags に必須の鍵(既定 context・role)。
    pub required: Option<Vec<String>>,
    /// DOEFF112: module の頭のタグで定義のタグを補えるか(既定 true)。
    pub module_default: Option<bool>,
    /// DOEFF112: タグを必須にする定義の頭(既定 defk・deff・defp・defhandler・defeffect。defrecord は頭の辞書で :tags を
    /// 書けるので、ここに足せば必須にできる — 既定では足さない: 頭の辞書は省ける形なので)。
    pub require_on: Option<Vec<String>>,
}

/// `[tool.doeff-linter.definitions]` — 定義の書き方の規則(DOEFF110 defn の禁止・111 deff の理由・112 タグ必須)の母集団。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct DefinitionsSection {
    /// 判じる置き場(repo の根からの dir・末尾 `*` は前方一致)。空なら repo の Hy の全部。
    #[serde(default)]
    pub paths: Vec<String>,
    /// 除く置き場(書き方は paths と同じ — 例 doeff-hy の macro の持ち主)。
    #[serde(default)]
    pub exclude: Vec<String>,
    /// 除く path の区切り(dir の名の完全一致)。
    #[serde(default)]
    pub exclude_parts: Vec<String>,
    /// deff の理由の註の目印(既定 `defk にできない:`)。
    pub deff_reason_marker: Option<String>,
    /// 検の置き場(DOEFF118)— glob(`**` は 0 個以上の段・`*` は段の中の任意の綴り・`/` を含まない綴りは file の名に当てる)。
    #[serde(default)]
    pub test_paths: Vec<String>,
}

/// 定義の書き方の規則の設定(検めた後)。
#[derive(Debug, Clone)]
pub struct DefinitionSettings {
    pub paths: Vec<String>,
    pub exclude: Vec<String>,
    pub exclude_parts: BTreeSet<String>,
    pub deff_reason_marker: String,
    pub tags: TagReading,
    pub test_paths: Vec<String>,
}

/// `[tool.doeff-linter.smells]` — 臭いの規則の設定(DOEFF121 を当てる層)。DOEFF122〜125 は定義の規則の母集団に当たる。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct SmellsSection {
    /// DOEFF121(文字列の鍵で読んだ欄への isinstance)を当てる層の名(判断の層 — 例 core)。
    #[serde(default)]
    pub shape_check_layers: Vec<String>,
}

/// 臭いの規則の設定(検めた後)。
#[derive(Debug, Clone, Default)]
pub struct SmellSettings {
    pub shape_check_layers: BTreeSet<LayerId>,
}

/// `[tool.doeff-linter.translation_effects]` — DOEFF130(翻訳の handler が業務の intent を出す)の設定。節が無ければ既定
/// (層 protocol の handler・層 intent の型・8 段)で、その名の層が在る時だけ当たる。
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct TranslationEffectsSection {
    /// handler を判じる層(翻訳の層 — 例 protocol)。
    #[serde(default = "default_translation_handler_layers")]
    pub handler_layers: Vec<String>,
    /// 業務の intent を置く層(この層の module の型を撃てば違反 — 例 intent)。
    #[serde(default = "default_translation_intent_layers")]
    pub intent_layers: Vec<String>,
    /// handler の撃った呼びから辿る defk の段の上限(0 なら handler の本体で直に撃つ intent だけ)。
    #[serde(default = "default_translation_max_depth")]
    pub max_depth: usize,
}

impl Default for TranslationEffectsSection {
    fn default() -> Self {
        TranslationEffectsSection {
            handler_layers: default_translation_handler_layers(),
            intent_layers: default_translation_intent_layers(),
            max_depth: default_translation_max_depth(),
        }
    }
}

fn default_translation_handler_layers() -> Vec<String> {
    vec!["protocol".to_string()]
}

fn default_translation_intent_layers() -> Vec<String> {
    vec!["intent".to_string()]
}

fn default_translation_max_depth() -> usize {
    8
}

/// DOEFF130 の設定(検めた後)。
#[derive(Debug, Clone)]
pub struct TranslationSettings {
    pub handler_layers: BTreeSet<LayerId>,
    pub intent_layers: BTreeSet<LayerId>,
    pub max_depth: usize,
}

/// `[tool.doeff-linter.roles]` — role の閉じた一覧と、層ごとに許す role。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct RolesSection {
    #[serde(default)]
    pub names: Vec<String>,
    #[serde(default)]
    pub by_layer: BTreeMap<String, Vec<String>>,
    /// role → その役の説明(違反の理由の文に差し込む。一覧から外した古い役の説明も書ける)。
    #[serde(default)]
    pub describe: BTreeMap<String, String>,
}

/// `[tool.doeff-linter.environment_names]` — 業務の名に付けてはいけない環境の語。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct EnvironmentNamesSection {
    /// 環境の語(名を `-`・`_`・`.` で切った語のどれかが当たれば違反)。
    #[serde(default)]
    pub words: Vec<String>,
    /// 業務の file の置き場(repo の根からの dir。末尾 `*` は path の前方一致)。
    #[serde(default)]
    pub paths: Vec<String>,
    /// 業務の file から外す置き場(書き方は paths と同じ)。
    #[serde(default)]
    pub exclude: Vec<String>,
    /// 業務の file から外す path の区切り(dir の名の完全一致 — 例 tests・adr)。
    #[serde(default)]
    pub exclude_parts: Vec<String>,
    /// 読む拡張子(既定 hy・hyk・hyp・py)。
    #[serde(default)]
    pub extensions: Option<Vec<String>>,
    /// 組み立ての file の名(この file の最上位の定義の名を全部見る — 例 handler_sets.hy)。
    #[serde(default)]
    pub assembly_files: Vec<String>,
}

/// `[tool.doeff-linter.raw_side_effects]` — 生の副作用に直に触ってよい層。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct RawSideEffectsSection {
    #[serde(default)]
    pub allowed_layers: Vec<String>,
    /// 目録への追加(hy-index の `--raw-catalog-extra` と同じ形の JSON file・repo の根から)。
    pub catalog_extra: Option<String>,
}

/// `[[tool.doeff-linter.laws]]` の 1 件 — ADR の law と、それを判じる規則の対応。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct LawEntry {
    /// law の名(ADR の綴りのまま)。登録簿の鍵の `<規則>` の欄にも使う。
    pub name: String,
    pub adr: Option<String>,
    /// law の :statement の逐語(エディタの「何を見ているか」)。
    #[serde(default)]
    pub statement: String,
    /// この law を判じる規則の ID(空なら未配線 — 違反は出さず、一覧に wired = false で載る)。
    #[serde(default)]
    pub rules: Vec<String>,
    /// この law が当たる層(空なら全部の層)。規則が層ごとに別の law に結びつく時に書く。
    #[serde(default)]
    pub layers: Vec<String>,
}

/// `[tool.doeff-linter.registry]` — 既知の破れの登録簿と、照合中の規則。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct RegistrySection {
    /// 1 鍵 1 file の dir(中の `*.txt` の 1 行目が鍵)。
    #[serde(default)]
    pub dirs: Vec<String>,
    /// 1 行 1 鍵の file(空行と `#` で始まる行は飛ばす)。
    #[serde(default)]
    pub files: Vec<String>,
    /// 照合中の規則の ID(違反は info に下げる。registered は登録簿どおり)。
    #[serde(default)]
    pub reconciling: Vec<String>,
    /// 1 行 1 鍵の file で、設定 file の dir からの相対で読む物(設定と登録簿を一緒に置いて持ち運ぶ)。
    #[serde(default)]
    pub config_files: Vec<String>,
}

/// 設定の中の層の番号(`order` の添字)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct LayerId(pub usize);

/// 検めた後の層 1 つ。
#[derive(Debug, Clone)]
pub struct LayerSpec {
    pub name: String,
    /// repo の根からの置き場の綴り(`*` の段は service)。
    pub places: Vec<PlacePattern>,
    /// import してよい層(None = 制限しない)。
    pub allowed: Option<BTreeSet<LayerId>>,
    pub forbid_modules: BTreeSet<String>,
    pub types_only: bool,
    /// 許す role(None = role の規則を当てない)。
    pub roles: Option<BTreeSet<String>>,
    /// 層の説明(設定に無ければ欄は全部 None)。
    pub description: LayerDescription,
}

/// タグの読み方(検めた後)。
#[derive(Debug, Clone)]
pub struct TagReading {
    pub module_variable_hy: String,
    pub module_variable_py: String,
    pub contract_definers: BTreeSet<String>,
    pub plain_definers: BTreeSet<String>,
    pub effect_definers: BTreeSet<String>,
    pub record_definers: BTreeSet<String>,
    pub function_definers: BTreeSet<String>,
    /// 定義の :tags に必須の鍵。
    pub required: Vec<String>,
    /// module の頭のタグで補えるか。
    pub module_default: bool,
    /// タグを必須にする定義の頭。
    pub require_on: BTreeSet<String>,
}

/// 層の設定(検めた後)。
#[derive(Debug, Clone)]
pub struct LayerSettings {
    pub layers: Vec<LayerSpec>,
    /// role → 説明。
    pub role_descriptions: BTreeMap<String, String>,
    pub exclude: BTreeSet<String>,
    pub extensions: BTreeSet<String>,
    pub tags: TagReading,
    /// architecture.hy の root(在れば、宣言した置き場所の外の module の層を :role のタグから推す)。
    pub infer_root: Option<String>,
}

/// 環境の語の設定(検めた後)。
#[derive(Debug, Clone)]
pub struct EnvironmentSettings {
    pub words: BTreeSet<String>,
    pub paths: Vec<String>,
    pub exclude: Vec<String>,
    pub exclude_parts: BTreeSet<String>,
    pub extensions: BTreeSet<String>,
    pub assembly_files: BTreeSet<String>,
}

/// 生の副作用の設定(検めた後)。
#[derive(Debug, Clone)]
pub struct RawSettingsSpec {
    pub allowed: BTreeSet<LayerId>,
    pub catalog_extra: Option<String>,
    /// architecture.hy の許可名簿(:world-handlers)を書いた時、生の副作用を許す module(mangle した dotted の綴り)。
    /// Some なら層の `allowed` は使わず、この module の file だけに許す(agora-redesign #1140)。
    pub world_modules: Option<BTreeSet<String>>,
    /// 境目の部品の module(mangle した dotted の綴り)→ 許す触れる先(architecture.hy の :boundary-parts・agora-redesign #1797)。
    /// ここに在る module の中では、触れる先が一覧に入る生の副作用の証拠を DOEFF106 で当たりにしない。
    pub boundary: BTreeMap<String, Vec<super::architecture::WorldTouch>>,
}

/// law の対応 1 件(検めた後)。
#[derive(Debug, Clone)]
pub struct LawSpec {
    pub name: String,
    pub adr: Option<String>,
    pub statement: String,
    pub rules: Vec<ProjectRuleOrExternal>,
    /// 当たる層(空なら全部)。
    pub layers: BTreeSet<LayerId>,
}

/// law に書かれた規則の ID — 層の規則(閉じた一覧)か、既存の Python の規則の ID。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProjectRuleOrExternal {
    Project(ProjectRule),
    External(String),
}

impl ProjectRuleOrExternal {
    /// 規則の ID の綴りを返す(出力と照合のため)。
    pub fn id(&self) -> &str {
        match self {
            ProjectRuleOrExternal::Project(rule) => rule.id(),
            ProjectRuleOrExternal::External(id) => id,
        }
    }
}

/// 規則の重大さ(repo が `[tool.doeff-linter.rules.<ID>] level` で宣言する方針)。重さ(severity)とは別の軸で、
/// 登録簿で重さを下げても重大さは下げない — エディタが「手つかずの critical が何件残るか」を数えるため。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RuleLevel {
    Critical,
    Major,
    Minor,
    Info,
}

impl RuleLevel {
    /// 設定の綴りを読む(閉じた集合の外は None)。
    pub fn parse(text: &str) -> Option<RuleLevel> {
        match text {
            "critical" => Some(RuleLevel::Critical),
            "major" => Some(RuleLevel::Major),
            "minor" => Some(RuleLevel::Minor),
            "info" => Some(RuleLevel::Info),
            _ => None,
        }
    }

    /// 宣言の無い規則の重大さ — 規則そのものの重さ(登録簿で下げる前)から決める。critical は宣言だけが付ける。
    pub fn default_for(base: crate::models::Severity) -> RuleLevel {
        match base {
            crate::models::Severity::Error => RuleLevel::Major,
            crate::models::Severity::Warning => RuleLevel::Minor,
            crate::models::Severity::Info => RuleLevel::Info,
        }
    }
}

/// 登録簿の設定(検めた後)。
#[derive(Debug, Clone, Default)]
pub struct RegistrySpec {
    pub dirs: Vec<String>,
    pub files: Vec<String>,
    pub reconciling: BTreeSet<ProjectRule>,
    pub config_files: Vec<String>,
}

/// 層の規則の設定の全部(検めた後)。節が無い規則は None で、その規則は何も出さない。
#[derive(Debug, Clone, Default)]
pub struct ProjectSettings {
    pub layers: Option<LayerSettings>,
    pub environment: Option<EnvironmentSettings>,
    pub raw: Option<RawSettingsSpec>,
    pub services: Option<ServiceSettings>,
    pub definitions: Option<DefinitionSettings>,
    /// 規則ごとの、登録簿に載った破れの重さ(無ければ warning)。
    pub registered_severity: BTreeMap<ProjectRule, crate::models::Severity>,
    /// 規則ごとの重さの上書き(臭いの規則 DOEFF121〜125 だけ — 既定の info を warning に上げる時)。
    pub severity: BTreeMap<ProjectRule, crate::models::Severity>,
    /// 規則ごとの重大さの宣言(規則の ID の大文字 → 重大さ。層の規則も Python の規則も)。無い規則は重さから決める。
    pub level: BTreeMap<String, RuleLevel>,
    /// 臭いの規則の設定(`[tool.doeff-linter.smells]`・無ければ None)。
    pub smells: Option<SmellSettings>,
    /// DOEFF130 の設定(handler の層と intent の層が両方とも宣言した層に在る時だけ Some)。
    pub translation: Option<TranslationSettings>,
    /// 設定を読んだ file の dir(registry.config_files の基準)。
    pub config_dir: Option<std::path::PathBuf>,
    /// repo の一番上の architecture.hy(service と層の唯一の宣言)。在れば層・role は ここから写す。
    pub architecture: Option<super::architecture::Architecture>,
    /// 意味の規則(DOEFF201・202 — Jev)の設定。
    pub semantic: Option<super::semantic::SemanticSettings>,
    pub laws: Vec<LawSpec>,
    pub registry: RegistrySpec,
    /// 設定が参照したが、この binary に無い規則の ID(`DOEFF` と 3 桁の形)— 誤りにせず、その参照だけを読まずに DOEFF100 で知らせる。
    pub unknown_rules: Vec<super::notice::UnknownRuleRef>,
}

/// 読んだ節の全部(config.rs の Config が持つ欄の写し)。
pub struct ProjectSections<'a> {
    pub layers: Option<&'a LayersSection>,
    pub tags: Option<&'a TagsSection>,
    pub roles: Option<&'a RolesSection>,
    pub environment_names: Option<&'a EnvironmentNamesSection>,
    pub raw_side_effects: Option<&'a RawSideEffectsSection>,
    pub laws: &'a [LawEntry],
    pub registry: Option<&'a RegistrySection>,
    pub services: Option<&'a ServicesSection>,
    pub definitions: Option<&'a DefinitionsSection>,
}

/// 既定の拡張子(Hy の 3 つと Python)。
fn default_extensions() -> BTreeSet<String> {
    ["hy", "hyk", "hyp", "py"].iter().map(|s| s.to_string()).collect()
}

/// 文字列の列を集合にする(設定の省略時の既定値と合わせるため)。
fn set_of(values: &[&str]) -> BTreeSet<String> {
    values.iter().map(|s| s.to_string()).collect()
}

/// dir の綴りを揃える(`./` と末尾の `/` を外し、区切りを `/` にする)。
pub fn normalize_dir(dir: &str) -> String {
    let unified = dir.replace('\\', "/");
    let trimmed = unified.trim_start_matches("./").trim_end_matches('/');
    trimmed.to_string()
}

impl ProjectSettings {
    /// 読んだ節を検めて規則が読む形にする。名前の食い違い(順に無い層・一覧に無い role・知らない規則の ID)は理由の文の列で返す。
    pub fn validate(sections: &ProjectSections) -> Result<ProjectSettings, Vec<String>> {
        let mut problems = Vec::new();
        let layers = sections.layers.map(|layers| validate_layers(layers, sections.tags, sections.roles, &mut problems));
        if sections.layers.is_none() {
            if sections.roles.is_some() {
                problems.push("roles: 層(layers)が無いと role の規則を当てられない".to_string());
            }
            if sections.tags.is_some() && sections.definitions.is_none() {
                problems.push("tags: 層(layers)か定義の規則(definitions)が無いとタグを読む module が無い".to_string());
            }
        }
        let definitions = sections.definitions.map(|section| DefinitionSettings {
            paths: section.paths.iter().map(|p| normalize_dir(p)).collect(),
            exclude: section.exclude.iter().map(|p| normalize_dir(p)).collect(),
            exclude_parts: section.exclude_parts.iter().cloned().collect(),
            deff_reason_marker: section.deff_reason_marker.clone().unwrap_or_else(|| "defk にできない:".to_string()),
            tags: tag_reading(sections.tags, sections.layers.and_then(|l| l.function_definers.as_ref())),
            test_paths: section.test_paths.clone(),
        });
        let mut unknown_rules: Vec<super::notice::UnknownRuleRef> = Vec::new();
        let python_ids: BTreeSet<String> = crate::rules::get_all_rules().iter().map(|r| r.rule_id().to_string()).collect();
        let layer_names: Vec<String> = layers.as_ref().map(|l| l.layers.iter().map(|s| s.name.clone()).collect()).unwrap_or_default();
        let find_layer = |name: &str, what: &str, problems: &mut Vec<String>| -> Option<LayerId> {
            let found = layer_names.iter().position(|n| n == name).map(LayerId);
            if found.is_none() {
                problems.push(format!("{}: 層 {} は layers.order に無い", what, name));
            }
            found
        };
        let environment = sections.environment_names.map(|env| EnvironmentSettings {
            words: env.words.iter().map(|w| w.to_lowercase()).collect(),
            paths: env.paths.iter().map(|p| normalize_dir(p)).collect(),
            exclude: env.exclude.iter().map(|p| normalize_dir(p)).collect(),
            exclude_parts: env.exclude_parts.iter().cloned().collect(),
            extensions: env.extensions.as_ref().map(|e| e.iter().cloned().collect()).unwrap_or_else(default_extensions),
            assembly_files: env.assembly_files.iter().cloned().collect(),
        });
        let raw = sections.raw_side_effects.map(|raw| {
            if layers.is_none() {
                problems.push("raw_side_effects: 層の置き場(layers)が無いと生の副作用の置き場を判じられない".to_string());
            }
            RawSettingsSpec {
                allowed: raw
                    .allowed_layers
                    .iter()
                    .filter_map(|name| find_layer(name, "raw_side_effects.allowed_layers", &mut problems))
                    .collect(),
                catalog_extra: raw.catalog_extra.clone(),
                world_modules: None,
                boundary: BTreeMap::new(),
            }
        });
        let laws = sections
            .laws
            .iter()
            .map(|law| LawSpec {
                name: law.name.clone(),
                adr: law.adr.clone(),
                statement: law.statement.clone(),
                rules: law
                    .rules
                    .iter()
                    .filter_map(|id| match ProjectRule::parse(id) {
                        Some(rule) => Some(ProjectRuleOrExternal::Project(rule)),
                        None if python_ids.contains(&id.to_uppercase()) => Some(ProjectRuleOrExternal::External(id.to_uppercase())),
                        None if super::notice::is_rule_id_shape(id) => {
                            unknown_rules.push(super::notice::UnknownRuleRef { key: format!("laws.{}.rules", law.name), id: id.clone() });
                            None
                        }
                        None => {
                            problems.push(format!("laws.{}.rules: 規則 {} は doeff-linter に無い", law.name, id));
                            None
                        }
                    })
                    .collect(),
                layers: law
                    .layers
                    .iter()
                    .filter_map(|name| find_layer(name, &format!("laws.{}.layers", law.name), &mut problems))
                    .collect(),
            })
            .collect();
        let registry = sections
            .registry
            .map(|registry| RegistrySpec {
                dirs: registry.dirs.clone(),
                files: registry.files.clone(),
                config_files: registry.config_files.clone(),
                reconciling: registry
                    .reconciling
                    .iter()
                    .filter_map(|id| {
                        let rule = ProjectRule::parse(id);
                        if rule.is_none() && super::notice::is_rule_id_shape(id) {
                            unknown_rules.push(super::notice::UnknownRuleRef { key: "registry.reconciling".to_string(), id: id.clone() });
                        } else if rule.is_none() {
                            problems.push(format!("registry.reconciling: 規則 {} は層の規則(DOEFF101〜113)に無い", id));
                        }
                        rule
                    })
                    .collect(),
            })
            .unwrap_or_default();
        let services = sections.services.filter(|s| s.enabled).map(|section| {
            if layers.is_none() {
                problems.push("services: 層(layers)が無いと service の境界を判じられない".to_string());
            }
            let ids = |names: &[String], what: &str, problems: &mut Vec<String>| -> BTreeSet<LayerId> {
                names.iter().filter_map(|name| find_layer(name, what, problems)).collect()
            };
            ServiceSettings {
                shared: section.shared.iter().cloned().collect(),
                guarded: ids(&section.guarded_layers, "services.guarded_layers", &mut problems),
                open: ids(&section.open_layers, "services.open_layers", &mut problems),
                exceptions: section.exceptions.iter().map(|e| (e.from.clone(), e.to.clone())).collect(),
                check_context: section.check_context,
            }
        });
        for law in sections.laws.iter().filter(|law| !law.layers.is_empty()) {
            // この binary の知らない規則(DOEFF100 で知らせる)は層を問う規則かを判じられないので、層を問う側に数える。
            let layered = law.rules.iter().any(|id| match ProjectRule::parse(id) {
                Some(rule) => rule.is_layered(),
                None => super::notice::is_rule_id_shape(id) && !python_ids.contains(&id.to_uppercase()),
            });
            if !layered {
                problems.push(format!("laws.{}.layers: この law の規則は層を問わないので、layers を書くと一度も当たらない", law.name));
            }
        }
        if problems.is_empty() {
            Ok(ProjectSettings {
                layers,
                environment,
                raw,
                services,
                definitions,
                registered_severity: BTreeMap::new(),
                severity: BTreeMap::new(),
                level: BTreeMap::new(),
                smells: None,
                translation: None,
                config_dir: None,
                architecture: None,
                semantic: None,
                laws,
                registry,
                unknown_rules,
            })
        } else {
            Err(problems)
        }
    }

    /// 違反の重大さ — repo の宣言があればそれ、無ければ規則の既定(`ProjectRule::default_level`)、それも無ければ規則そのものの重さ base から。
    pub fn level_of(&self, rule_id: &str, base: crate::models::Severity) -> RuleLevel {
        let id = rule_id.to_uppercase();
        self.level
            .get(&id)
            .copied()
            .or_else(|| ProjectRule::parse(&id).and_then(ProjectRule::default_level))
            .unwrap_or_else(|| RuleLevel::default_for(base))
    }

    /// 規則 rule が層 layer の file で出す違反の law(無ければ None)。層を問わない規則は layer = None。
    pub fn law_for(&self, rule: ProjectRule, layer: Option<LayerId>) -> Option<&LawSpec> {
        self.laws.iter().find(|law| {
            law.rules.contains(&ProjectRuleOrExternal::Project(rule))
                && (law.layers.is_empty() || layer.is_some_and(|l| law.layers.contains(&l)))
        })
    }

    /// 既存の Python の規則の ID に結びつけた law(無ければ None)。
    pub fn law_for_external(&self, id: &str) -> Option<&LawSpec> {
        let wanted = ProjectRuleOrExternal::External(id.to_uppercase());
        self.laws.iter().find(|law| law.rules.contains(&wanted))
    }
}

/// タグの読み方を設定から作る(省略した欄は doeff-hy の既定の綴り)。
fn tag_reading(tags: Option<&TagsSection>, function_definers: Option<&Vec<String>>) -> TagReading {
    let tags = tags.cloned().unwrap_or_default();
    TagReading {
        module_variable_hy: tags.module_variable_hy.unwrap_or_else(|| "MODULE-TAGS".to_string()),
        module_variable_py: tags.module_variable_py.unwrap_or_else(|| "MODULE_TAGS".to_string()),
        contract_definers: tags
            .contract_definers
            .map(|v| v.into_iter().collect())
            .unwrap_or_else(|| set_of(&["defk", "deff", "defp", "defpp", "defhandler"])),
        plain_definers: tags.plain_definers.map(|v| v.into_iter().collect()).unwrap_or_else(|| set_of(&["defn", "defclass", "defenum"])),
        effect_definers: tags.effect_definers.map(|v| v.into_iter().collect()).unwrap_or_else(|| set_of(&["defeffect"])),
        record_definers: tags.record_definers.map(|v| v.into_iter().collect()).unwrap_or_else(|| set_of(&["defrecord", "defwire"])),
        function_definers: function_definers
            .map(|v| v.iter().cloned().collect())
            .unwrap_or_else(|| set_of(&["defk", "deff", "defp", "defpp", "defhandler", "defn"])),
        required: tags.required.unwrap_or_else(|| vec!["context".to_string(), "role".to_string()]),
        module_default: tags.module_default.unwrap_or(true),
        require_on: tags
            .require_on
            .map(|v| v.into_iter().collect())
            .unwrap_or_else(|| set_of(&["defk", "deff", "defp", "defhandler", "defeffect"])),
    }
}

/// 層の節を検める(順・置き場・import の決まり・role・タグの読み方)。
fn validate_layers(
    section: &LayersSection,
    tags: Option<&TagsSection>,
    roles: Option<&RolesSection>,
    problems: &mut Vec<String>,
) -> LayerSettings {
    let order = &section.order;
    let mut seen = BTreeSet::new();
    for name in order {
        if !seen.insert(name.clone()) {
            problems.push(format!("layers.order: 層 {} が 2 度ある", name));
        }
    }
    let id_of = |name: &str| order.iter().position(|n| n == name).map(LayerId);
    for (table, names) in [
        ("layers.paths", section.paths.keys().cloned().collect::<Vec<_>>()),
        ("layers.allow_imports", section.allow_imports.keys().cloned().collect()),
        ("layers.forbid_modules", section.forbid_modules.keys().cloned().collect()),
        ("layers.types_only", section.types_only.clone()),
        ("layers.describe", section.describe.keys().cloned().collect()),
        ("roles.by_layer", roles.map(|r| r.by_layer.keys().cloned().collect()).unwrap_or_default()),
    ] {
        for name in names {
            if id_of(&name).is_none() {
                problems.push(format!("{}: 層 {} は layers.order に無い", table, name));
            }
        }
    }
    let role_names: BTreeSet<String> = roles.map(|r| r.names.iter().cloned().collect()).unwrap_or_default();
    let layers = order
        .iter()
        .map(|name| {
            let places: Vec<PlacePattern> = match section.paths.get(name) {
                Some(patterns) => patterns
                    .list()
                    .into_iter()
                    .filter_map(|text| match PlacePattern::parse(text) {
                        Ok(place) => Some(place),
                        Err(reason) => {
                            problems.push(format!("layers.paths.{}: {}", name, reason));
                            None
                        }
                    })
                    .collect(),
                None => {
                    problems.push(format!("layers.paths: 層 {} の置き場が無い", name));
                    Vec::new()
                }
            };
            let allowed = section.allow_imports.get(name).map(|names| {
                names
                    .iter()
                    .filter_map(|target| {
                        let id = id_of(target);
                        if id.is_none() {
                            problems.push(format!("layers.allow_imports.{}: 層 {} は layers.order に無い", name, target));
                        }
                        id
                    })
                    .collect()
            });
            let layer_roles = roles.and_then(|r| r.by_layer.get(name)).map(|allowed_roles| {
                for role in allowed_roles {
                    if !role_names.is_empty() && !role_names.contains(role) {
                        problems.push(format!("roles.by_layer.{}: role {} は roles.names に無い", name, role));
                    }
                }
                allowed_roles.iter().cloned().collect()
            });
            LayerSpec {
                name: name.clone(),
                places,
                allowed,
                forbid_modules: section.forbid_modules.get(name).map(|m| m.iter().cloned().collect()).unwrap_or_default(),
                types_only: section.types_only.contains(name),
                roles: layer_roles,
                description: section.describe.get(name).cloned().unwrap_or_default(),
            }
        })
        .collect();
    // `*` の無い置き場どうしの入れ子は、どちらの層か決まらない(`*` の置き場は段の多い方を採るので入れ子にならない)。
    let dirs: Vec<(&String, String)> = section
        .paths
        .iter()
        .flat_map(|(name, patterns)| patterns.list().into_iter().map(move |text| (name, normalize_dir(text))))
        .filter(|(_, dir)| !dir.contains('*'))
        .collect();
    for (a, dir_a) in &dirs {
        for (b, dir_b) in &dirs {
            if a != b && !dir_a.is_empty() && dir_b.starts_with(&format!("{}/", dir_a)) {
                problems.push(format!("layers.paths: 層 {} の置き場が層 {} の置き場の中にある(入れ子の置き場はどちらの層か決まらない)", b, a));
            }
        }
    }
    LayerSettings {
        layers,
        role_descriptions: roles.map(|r| r.describe.clone()).unwrap_or_default(),
        exclude: section.exclude.iter().cloned().collect(),
        extensions: section.extensions.as_ref().map(|e| e.iter().cloned().collect()).unwrap_or_else(default_extensions),
        tags: tag_reading(tags, section.function_definers.as_ref()),
        infer_root: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 設定の文字列を読んで検める。
    fn validate(text: &str) -> Result<ProjectSettings, Vec<String>> {
        let config: crate::config::Config = toml::from_str(text).unwrap();
        config.project_settings()
    }

    #[test]
    fn reads_layers_roles_laws_and_registry() {
        let settings = validate(
            r#"
[layers]
order = ["core", "foundation"]
paths = { core = "./app/core/", foundation = "app/foundation" }
types_only = []
[layers.allow_imports]
core = ["core"]
[roles.by_layer]
core = ["judgment"]
[raw_side_effects]
allowed_layers = ["foundation"]
[[laws]]
name = "core-law"
rules = ["doeff101", "DOEFF016"]
layers = ["core"]
[registry]
reconciling = ["DOEFF104"]
"#,
        )
        .unwrap();
        let layers = settings.layers.as_ref().unwrap();
        assert_eq!(layers.layers[0].places[0].text, "app/core");
        assert_eq!(layers.layers[0].allowed.as_ref().unwrap().len(), 1);
        assert!(layers.layers[1].allowed.is_none(), "書かない層は制限しない");
        assert_eq!(settings.law_for(ProjectRule::LayerImportDirection, Some(LayerId(0))).map(|l| l.name.as_str()), Some("core-law"));
        assert!(settings.law_for(ProjectRule::LayerImportDirection, Some(LayerId(1))).is_none());
        assert_eq!(settings.law_for_external("doeff016").map(|l| l.name.as_str()), Some("core-law"));
        assert!(settings.registry.reconciling.contains(&ProjectRule::ModuleDeclaresTags));
        assert!(settings.raw.unwrap().allowed.contains(&LayerId(1)));
    }

    #[test]
    fn rejects_empty_absolute_and_nested_layer_dirs() {
        let problems = validate(
            "[layers]\norder = [\"a\", \"b\", \"c\", \"d\"]\npaths = { a = \"./\", b = \"/abs\", c = \"app\", d = \"app/inner\" }\n[roles]\n",
        )
        .unwrap_err()
        .join("\n");
        assert!(problems.contains("layers.paths.a"), "{}", problems);
        assert!(problems.contains("layers.paths.b"), "{}", problems);
        assert!(problems.contains("入れ子"), "{}", problems);
        let without_layers = validate("[roles]\nnames = []\n").unwrap_err().join("\n");
        assert!(without_layers.contains("roles"), "{}", without_layers);
    }

    #[test]
    fn reports_every_name_mismatch() {
        let problems = validate(
            r#"
[layers]
order = ["core", "core"]
paths = { core = "c", ghost = "g" }
[layers.allow_imports]
core = ["nowhere"]
[roles]
names = ["judgment"]
[roles.by_layer]
core = ["translation"]
[registry]
reconciling = ["DOEFF999"]
[[laws]]
name = "typo"
rules = ["DOEFF1O1"]
[[laws]]
name = "env-by-layer"
rules = ["DOEFF108"]
layers = ["core"]
"#,
        )
        .unwrap_err();
        let text = problems.join("\n");
        for needle in ["2 度", "ghost", "nowhere", "translation", "DOEFF1O1", "env-by-layer"] {
            assert!(text.contains(needle), "{} が無い: {}", needle, text);
        }
        // 形の正しい知らない ID(この binary より新しい規則)は誤りにしない — DOEFF100 の知らせに回す(agora-redesign #848)。
        assert!(!text.contains("DOEFF999"), "新しい規則の ID を誤りにした: {}", text);
    }

    #[test]
    fn newer_rule_ids_are_kept_as_unknown_references() {
        let settings = validate(
            r#"
[registry]
reconciling = ["DOEFF999"]
[[laws]]
name = "future"
rules = ["DOEFF998", "DOEFF110"]
"#,
        )
        .expect("新しい規則の ID で設定を読めなくした");
        let refs: Vec<(&str, &str)> = settings.unknown_rules.iter().map(|u| (u.key.as_str(), u.id.as_str())).collect();
        assert_eq!(refs, vec![("laws.future.rules", "DOEFF998"), ("registry.reconciling", "DOEFF999")]);
        assert_eq!(settings.laws[0].rules, vec![ProjectRuleOrExternal::Project(ProjectRule::DefnForbidden)], "読める参照まで捨てた");
    }
}

#[cfg(test)]
mod place_tests {
    use super::*;

    #[test]
    fn place_patterns_capture_the_service_segment() {
        let pattern = PlacePattern::parse("./controllers/*/core/").unwrap();
        assert_eq!(pattern.base(), "controllers");
        assert_eq!(
            pattern.matches("controllers/land_notice/core/goal.hy"),
            Some(PlaceMatch { dir: "controllers/land_notice/core".into(), service: Some("land_notice".into()) })
        );
        assert_eq!(pattern.matches("controllers/core/goal.hy"), None);
        assert_eq!(pattern.matches("controllers/land_notice/core"), None, "dir そのものは file でない");
        let plain = PlacePattern::parse("controllers/core").unwrap();
        assert_eq!(plain.matches("controllers/core/a/b.hy"), Some(PlaceMatch { dir: "controllers/core".into(), service: None }));
        for bad in ["", ".", "/abs", "a/../b", "a/b*", "*/x/*"] {
            assert!(PlacePattern::parse(bad).is_err(), "{:?}", bad);
        }
    }
}
