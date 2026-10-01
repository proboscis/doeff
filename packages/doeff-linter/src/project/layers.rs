//! 層の設定の型 — `[tool.doeff-linter.layers]` と `roles` の節の読んだ形と、層の番号・置き場・タグの読み方の検めた形。
//! 設定の節を組む `settings`・宣言を読む `architecture`・Jev の問い `semantic`・事実の読み `facts` が下から読む型で、
//! この file は project の他の module を読まない(agora-redesign #2123 で settings.rs から分けた)。

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

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
    /// 値を型だけで渡す層(defwire と写像・型の無い組を置かない — DOEFF170・171・agora-redesign #2143)。
    #[serde(default)]
    pub wire_free: Vec<String>,
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
    /// 値を型だけで渡す層か(DOEFF170・171)。
    pub wire_free: bool,
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

/// dir の綴りを揃える(`./` と末尾の `/` を外し、区切りを `/` にする)。
pub fn normalize_dir(dir: &str) -> String {
    let unified = dir.replace('\\', "/");
    let trimmed = unified.trim_start_matches("./").trim_end_matches('/');
    trimmed.to_string()
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

/// 文字列の列を集合にする(設定の省略時の既定値と合わせるため)。
fn set_of(values: &[&str]) -> BTreeSet<String> {
    values.iter().map(|s| s.to_string()).collect()
}

/// タグの読み方を設定から作る(省略した欄は doeff-hy の既定の綴り)。
pub fn tag_reading(tags: Option<&TagsSection>, function_definers: Option<&Vec<String>>) -> TagReading {
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
