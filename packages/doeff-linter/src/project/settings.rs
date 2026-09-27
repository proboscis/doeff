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
#[serde(deny_unknown_fields)]
pub struct LayersSection {
    /// 層の名前(外の世界からの遠さの順)。
    #[serde(default)]
    pub order: Vec<String>,
    /// 層の名前 → repo の根からの dir。
    #[serde(default)]
    pub paths: BTreeMap<String, String>,
    /// 層の規則の外に置く path の区切り(dir の名か file の名の完全一致 — 例 `tests`・`conftest.py`)。
    #[serde(default)]
    pub exclude: Vec<String>,
    /// 層の module として読む拡張子(既定 hy・hyk・hyp・py)。
    #[serde(default)]
    pub extensions: Option<Vec<String>>,
    /// 層の名前 → import してよい層の名前(同じ層も書く)。書かない層は制限しない。
    #[serde(default)]
    pub allow_imports: BTreeMap<String, Vec<String>>,
    /// 層の名前 → 直に import してはいけない module の一番上の綴り(例 httpx・subprocess)。
    #[serde(default)]
    pub forbid_modules: BTreeMap<String, Vec<String>>,
    /// 型だけを置く層(関数と handler を定めない)。
    #[serde(default)]
    pub types_only: Vec<String>,
    /// 型だけの層で数える関数の定義の形(Hy の頭の綴り。既定 defk・deff・defp・defpp・defhandler・defn)。
    #[serde(default)]
    pub function_definers: Option<Vec<String>>,
}

/// `[tool.doeff-linter.tags]` — タグの読み方(doeff-hy の綴り。既定のままでよい)。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
#[serde(deny_unknown_fields)]
pub struct TagsSection {
    /// Hy の module の頭のタグの名(`(val MODULE-TAGS {…})`)。
    pub module_variable_hy: Option<String>,
    /// Python の module の頭のタグの名(`MODULE_TAGS = {…}`)。
    pub module_variable_py: Option<String>,
    /// 契約の辞書に `:tags` を書ける定義の形。
    pub contract_definers: Option<Vec<String>>,
    /// タグを書く場所の無い定義の形(module の頭のタグに頼る)。
    pub plain_definers: Option<Vec<String>>,
    /// 鍵と値の並びに `:tags` を持つ effect の型の定義の形。
    pub effect_definers: Option<Vec<String>>,
}

/// `[tool.doeff-linter.roles]` — role の閉じた一覧と、層ごとに許す role。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
#[serde(deny_unknown_fields)]
pub struct RolesSection {
    #[serde(default)]
    pub names: Vec<String>,
    #[serde(default)]
    pub by_layer: BTreeMap<String, Vec<String>>,
}

/// `[tool.doeff-linter.environment_names]` — 業務の名に付けてはいけない環境の語。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
#[serde(deny_unknown_fields)]
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
#[serde(deny_unknown_fields)]
pub struct RawSideEffectsSection {
    #[serde(default)]
    pub allowed_layers: Vec<String>,
    /// 目録への追加(hy-index の `--raw-catalog-extra` と同じ形の JSON file・repo の根から)。
    pub catalog_extra: Option<String>,
}

/// `[[tool.doeff-linter.laws]]` の 1 件 — ADR の law と、それを判じる規則の対応。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
#[serde(deny_unknown_fields)]
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
#[serde(deny_unknown_fields)]
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
}

/// 設定の中の層の番号(`order` の添字)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct LayerId(pub usize);

/// 検めた後の層 1 つ。
#[derive(Debug, Clone)]
pub struct LayerSpec {
    pub name: String,
    /// repo の根からの dir(区切りは `/`・末尾の `/` なし)。
    pub dir: String,
    /// import してよい層(None = 制限しない)。
    pub allowed: Option<BTreeSet<LayerId>>,
    pub forbid_modules: BTreeSet<String>,
    pub types_only: bool,
    /// 許す role(None = role の規則を当てない)。
    pub roles: Option<BTreeSet<String>>,
}

/// タグの読み方(検めた後)。
#[derive(Debug, Clone)]
pub struct TagReading {
    pub module_variable_hy: String,
    pub module_variable_py: String,
    pub contract_definers: BTreeSet<String>,
    pub plain_definers: BTreeSet<String>,
    pub effect_definers: BTreeSet<String>,
    pub function_definers: BTreeSet<String>,
}

/// 層の設定(検めた後)。
#[derive(Debug, Clone)]
pub struct LayerSettings {
    pub layers: Vec<LayerSpec>,
    pub exclude: BTreeSet<String>,
    pub extensions: BTreeSet<String>,
    pub tags: TagReading,
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

/// 登録簿の設定(検めた後)。
#[derive(Debug, Clone, Default)]
pub struct RegistrySpec {
    pub dirs: Vec<String>,
    pub files: Vec<String>,
    pub reconciling: BTreeSet<ProjectRule>,
}

/// 層の規則の設定の全部(検めた後)。節が無い規則は None で、その規則は何も出さない。
#[derive(Debug, Clone, Default)]
pub struct ProjectSettings {
    pub layers: Option<LayerSettings>,
    pub environment: Option<EnvironmentSettings>,
    pub raw: Option<RawSettingsSpec>,
    pub laws: Vec<LawSpec>,
    pub registry: RegistrySpec,
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
fn normalize_dir(dir: &str) -> String {
    let unified = dir.replace('\\', "/");
    let trimmed = unified.trim_start_matches("./").trim_end_matches('/');
    trimmed.to_string()
}

impl ProjectSettings {
    /// 読んだ節を検めて規則が読む形にする。名前の食い違い(順に無い層・一覧に無い role・知らない規則の ID)は理由の文の列で返す。
    pub fn validate(sections: &ProjectSections) -> Result<ProjectSettings, Vec<String>> {
        let mut problems = Vec::new();
        let layers = sections.layers.map(|layers| validate_layers(layers, sections.tags, sections.roles, &mut problems));
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
                    .map(|id| match ProjectRule::parse(id) {
                        Some(rule) => ProjectRuleOrExternal::Project(rule),
                        None => ProjectRuleOrExternal::External(id.to_uppercase()),
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
                reconciling: registry
                    .reconciling
                    .iter()
                    .filter_map(|id| {
                        let rule = ProjectRule::parse(id);
                        if rule.is_none() {
                            problems.push(format!("registry.reconciling: 規則 {} は層の規則(DOEFF101〜108)に無い", id));
                        }
                        rule
                    })
                    .collect(),
            })
            .unwrap_or_default();
        if problems.is_empty() {
            Ok(ProjectSettings { layers, environment, raw, laws, registry })
        } else {
            Err(problems)
        }
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
            let dir = match section.paths.get(name) {
                Some(dir) => normalize_dir(dir),
                None => {
                    problems.push(format!("layers.paths: 層 {} の置き場が無い", name));
                    String::new()
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
                dir,
                allowed,
                forbid_modules: section.forbid_modules.get(name).map(|m| m.iter().cloned().collect()).unwrap_or_default(),
                types_only: section.types_only.contains(name),
                roles: layer_roles,
            }
        })
        .collect();
    let tags = tags.cloned().unwrap_or_default();
    LayerSettings {
        layers,
        exclude: section.exclude.iter().cloned().collect(),
        extensions: section.extensions.as_ref().map(|e| e.iter().cloned().collect()).unwrap_or_else(default_extensions),
        tags: TagReading {
            module_variable_hy: tags.module_variable_hy.unwrap_or_else(|| "MODULE-TAGS".to_string()),
            module_variable_py: tags.module_variable_py.unwrap_or_else(|| "MODULE_TAGS".to_string()),
            contract_definers: tags
                .contract_definers
                .map(|v| v.into_iter().collect())
                .unwrap_or_else(|| set_of(&["defk", "deff", "defp", "defpp", "defhandler"])),
            plain_definers: tags
                .plain_definers
                .map(|v| v.into_iter().collect())
                .unwrap_or_else(|| set_of(&["defn", "defclass", "defrecord", "defenum"])),
            effect_definers: tags.effect_definers.map(|v| v.into_iter().collect()).unwrap_or_else(|| set_of(&["defeffect"])),
            function_definers: section
                .function_definers
                .as_ref()
                .map(|v| v.iter().cloned().collect())
                .unwrap_or_else(|| set_of(&["defk", "deff", "defp", "defpp", "defhandler", "defn"])),
        },
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
        assert_eq!(layers.layers[0].dir, "app/core");
        assert_eq!(layers.layers[0].allowed.as_ref().unwrap().len(), 1);
        assert!(layers.layers[1].allowed.is_none(), "書かない層は制限しない");
        assert_eq!(settings.law_for(ProjectRule::LayerImportDirection, Some(LayerId(0))).map(|l| l.name.as_str()), Some("core-law"));
        assert!(settings.law_for(ProjectRule::LayerImportDirection, Some(LayerId(1))).is_none());
        assert_eq!(settings.law_for_external("doeff016").map(|l| l.name.as_str()), Some("core-law"));
        assert!(settings.registry.reconciling.contains(&ProjectRule::ModuleDeclaresTags));
        assert!(settings.raw.unwrap().allowed.contains(&LayerId(1)));
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
"#,
        )
        .unwrap_err();
        let text = problems.join("\n");
        for needle in ["2 度", "ghost", "nowhere", "translation", "DOEFF999"] {
            assert!(text.contains(needle), "{} が無い: {}", needle, text);
        }
    }
}
