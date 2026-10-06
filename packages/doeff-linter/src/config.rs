//! Configuration loading for doeff-linter
//!
//! Loads configuration from pyproject.toml [tool.doeff-linter] section

use crate::project::layers::{LayersSection, RolesSection, TagsSection};
use crate::project::settings::{
    EnvironmentNamesSection, LawEntry, ProjectSections, ProjectSettings, RawSideEffectsSection, RegistrySection, ServicesSection, DefinitionsSection,
};
use crate::models::Severity;
use crate::project::architecture::Architecture;
use crate::project::rule::ProjectRule;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

/// Main configuration structure
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct Config {
    /// Rules to enable (empty means all rules, or use ["ALL"])
    #[serde(default)]
    pub enable: Vec<String>,

    /// Rules to disable
    #[serde(default)]
    pub disable: Vec<String>,

    /// Paths to exclude from linting
    #[serde(default)]
    pub exclude: Vec<String>,

    /// Rule-specific configuration
    #[serde(default)]
    pub rules: HashMap<String, RuleConfig>,

    /// Git integration settings
    #[serde(default)]
    pub git: GitConfig,

    /// Path to log file for recording lint results (JSON Lines format)
    /// Defaults to ".doeff-lint.jsonl"
    #[serde(default = "default_log_file")]
    pub log_file: Option<String>,

    /// 層の規則(DOEFF101〜105)の層の順・置き場・import の決まり — `[tool.doeff-linter.layers]`
    #[serde(default)]
    pub layers: Option<LayersSection>,

    /// タグの読み方 — `[tool.doeff-linter.tags]`(既定のままでよい)
    #[serde(default)]
    pub tags: Option<TagsSection>,

    /// role の閉じた一覧と層ごとに許す role — `[tool.doeff-linter.roles]`
    #[serde(default)]
    pub roles: Option<RolesSection>,

    /// 業務の名に付けてはいけない環境の語(DOEFF108)— `[tool.doeff-linter.environment_names]`
    #[serde(default)]
    pub environment_names: Option<EnvironmentNamesSection>,

    /// 生の副作用に直に触ってよい層(DOEFF106・107)— `[tool.doeff-linter.raw_side_effects]`
    #[serde(default)]
    pub raw_side_effects: Option<RawSideEffectsSection>,

    /// 規則 ID と ADR の law の対応 — `[[tool.doeff-linter.laws]]`
    #[serde(default)]
    pub laws: Vec<LawEntry>,

    /// 既知の破れの登録簿と照合中の規則 — `[tool.doeff-linter.registry]`
    #[serde(default)]
    pub registry: Option<RegistrySection>,

    /// service の境界(DOEFF109)と文脈の照らし(DOEFF113)— `[tool.doeff-linter.services]`
    #[serde(default)]
    pub services: Option<ServicesSection>,

    /// 定義の書き方の規則(DOEFF110 defn の禁止・111 deff の理由・112 タグ必須)の母集団 — `[tool.doeff-linter.definitions]`
    #[serde(default)]
    pub definitions: Option<DefinitionsSection>,

    /// service と層の宣言 architecture.hy の path(設定 file の dir からの相対)。書かなければ repo の根の architecture.hy を探す。
    #[serde(default)]
    pub architecture: Option<String>,

    /// repo の根(module の名・層の置き場・登録簿・鍵の path の基準)の path(設定 file の dir からの相対)。書かなければ設定 file の dir。
    /// `--root` が勝つ。monorepo の package の `src/` の下を根にして、module の名を import の名(`doeff_cluster.x`)に揃えるため
    /// (agora-redesign #1977 — 設定 file の dir を根にすると `src.doeff_cluster.x` になる)。
    #[serde(default)]
    pub root: Option<String>,

    /// 根の外で歩く dir(設定 file の dir からの相対)— その下の Hy の file は定義の書き方の規則の母集団と「対象の外」の判定に入る
    /// (層の規則は根の下の層の置き場だけを判じるので入らない)。名乗りは根から `..` を含む相対(`../tests/x.hy` — 宣言の file の鍵と
    /// 同じ形)。root = "src" の package の `tests/` を判じるため(agora-redesign #2821 の案 A-1)。
    #[serde(default)]
    pub include: Vec<String>,

    /// 意味の規則(DOEFF201・202 — Jev)の層と閾値 — `[tool.doeff-linter.semantic]`
    #[serde(default)]
    pub semantic: Option<crate::project::semantic::SemanticSection>,

    /// 臭いの規則(DOEFF121〜125)の設定 — `[tool.doeff-linter.smells]`
    #[serde(default)]
    pub smells: Option<crate::project::settings::SmellsSection>,

    /// 翻訳の handler が出す effect の規則(DOEFF130)の設定 — `[tool.doeff-linter.translation_effects]`
    #[serde(default)]
    pub translation_effects: Option<crate::project::settings::TranslationEffectsSection>,

    /// commit の hook(`--commit-hook`)の設定 — `[tool.doeff-linter.commit_hook]`(agora-redesign #1989)
    #[serde(default)]
    pub commit_hook: Option<CommitHookSection>,
}

/// `[tool.doeff-linter.commit_hook]` — commit の hook の入口(`--commit-hook`)が読む設定(agora-redesign #1989)。
/// 各 repo が hook の論理を写して持たず、この 1 か所の宣言だけを書く。
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct CommitHookSection {
    /// 退役した鍵(agora-redesign #2090)— 以前は repo 全体に当てる規則の手の一覧。今は読まず、残っていれば hook が 1 行で名乗る
    /// (規則の分けは `ProjectRule::needs_whole_repo` の名乗り)。古い設定を読めなくしないために形だけ残す。
    #[serde(default)]
    pub whole_repo_rules: Vec<String>,
    /// 子の linter 1 回ごとの上限(秒・既定 20)。越えたら測れなかったとして止めない。
    #[serde(default)]
    pub timeout_s: Option<u64>,
}

impl Config {
    /// 層の規則の節を検めて、規則が読む形にする(名前の食い違いは理由の文の列)。
    pub fn project_settings(&self) -> Result<ProjectSettings, Vec<String>> {
        self.project_settings_with(None)
    }

    /// 層の規則の設定を作る。architecture.hy が在れば、層・role は そこから写し、TOML の layers・roles・services は置けない
    /// (宣言を 1 か所にするため)。
    pub fn project_settings_with(&self, architecture: Option<Architecture>) -> Result<ProjectSettings, Vec<String>> {
        let mut settings = match &architecture {
            None => self.project_settings_toml()?,
            Some(arch) => {
                let mut clashes = Vec::new();
                for (name, present) in [("layers", self.layers.is_some()), ("roles", self.roles.is_some()), ("services", self.services.is_some())] {
                    if present {
                        clashes.push(format!(
                            "[tool.doeff-linter.{}] は {} と二重の宣言 — 層・role・service は architecture.hy だけに書く",
                            name,
                            arch.path.display()
                        ));
                    }
                }
                // 生の I/O を許す所は、architecture.hy の許可名簿(:world-handlers)を書いたらそこだけで決める — 層で許す
                // [tool.doeff-linter.raw_side_effects] allowed_layers と並べない(agora-redesign #1106)。
                let allowed_layers = self.raw_side_effects.as_ref().is_some_and(|raw| !raw.allowed_layers.is_empty());
                if !arch.world_handlers.is_empty() && allowed_layers {
                    clashes.push(format!(
                        "[tool.doeff-linter.raw_side_effects] allowed_layers は {} の :world-handlers と二重の宣言 — 生の I/O を許す所は許可名簿だけに書く",
                        arch.path.display()
                    ));
                }
                if !clashes.is_empty() {
                    return Err(clashes);
                }
                let mut with_arch = self.clone();
                with_arch.layers = Some(arch.layers_section());
                with_arch.roles = Some(arch.roles_section());
                with_arch.project_settings_toml()?
            }
        };
        settings.architecture = architecture;
        // :wraps は doeff の実 I/O の handler の目録に在る物だけ(目録の外の handler を包むと、どの規則もその handler を実 I/O と知らない)。
        if let Some(arch) = settings.architecture.as_ref() {
            let catalog = crate::project::world_catalog::WorldCatalog::bundled();
            let outside: Vec<String> = arch
                .world_handlers
                .iter()
                .flat_map(|h| h.wraps.iter().filter(|w| !catalog.handlers.contains_key(&w.target())).map(move |w| format!("{} の :wraps の {}", h.definition.spelling(), w.spelling())))
                .collect();
            if !outside.is_empty() {
                return Err(outside
                    .into_iter()
                    .map(|what| format!("{}: {} は doeff の実 I/O の handler の目録(doeff-linter data/world_handlers.json)に無い — 目録に足すか :wraps から外す", arch.path.display(), what))
                    .collect());
            }
        }
        // 許可名簿を書いた repo では、生の副作用を許す所 = 名簿の定義の module(TOML の raw_side_effects の節が無くても判じる)。
        if let Some(arch) = settings.architecture.as_ref().filter(|a| !a.world_handlers.is_empty()) {
            let modules = arch.world_modules();
            let raw = settings.raw.get_or_insert_with(|| crate::project::raw_settings::RawSettingsSpec {
                allowed: std::collections::BTreeSet::new(),
                catalog_extra: None,
                world_modules: None,
                boundary: std::collections::BTreeMap::new(),
            });
            raw.world_modules = Some(modules);
            raw.boundary = arch.boundary_touches();
        }
        // 宣言した置き場所の外の module(層が先の dir など)は、:role のタグから層を推して層の規則をかける。
        if let (Some(arch), Some(layers)) = (&settings.architecture, settings.layers.as_mut()) {
            layers.infer_root = Some(crate::project::layers::normalize_dir(&arch.root));
        }
        if let Some(section) = &self.smells {
            let names: Vec<String> = settings.layers.as_ref().map(|l| l.layers.iter().map(|s| s.name.clone()).collect()).unwrap_or_default();
            let mut layers = std::collections::BTreeSet::new();
            let mut unknown = Vec::new();
            for name in &section.shape_check_layers {
                match names.iter().position(|n| n == name) {
                    Some(index) => {
                        layers.insert(crate::project::layers::LayerId(index));
                    }
                    None => unknown.push(format!("smells.shape_check_layers: 層 {} は宣言した層に無い", name)),
                }
            }
            if !unknown.is_empty() {
                return Err(unknown);
            }
            settings.smells = Some(crate::project::settings::SmellSettings { shape_check_layers: layers });
        }
        // DOEFF130 — 節を書いたなら層の名の誤りは設定の誤り。書かなければ既定の層(protocol・intent)が両方在る時だけ当たる。
        {
            let names: Vec<String> = settings.layers.as_ref().map(|l| l.layers.iter().map(|s| s.name.clone()).collect()).unwrap_or_default();
            let explicit = self.translation_effects.is_some();
            let section = self.translation_effects.clone().unwrap_or_default();
            let mut unknown = Vec::new();
            let mut resolve = |list: &[String], key: &str| -> std::collections::BTreeSet<crate::project::layers::LayerId> {
                let mut found = std::collections::BTreeSet::new();
                for name in list {
                    match names.iter().position(|n| n == name) {
                        Some(index) => {
                            found.insert(crate::project::layers::LayerId(index));
                        }
                        None => unknown.push(format!("translation_effects.{}: 層 {} は宣言した層に無い", key, name)),
                    }
                }
                found
            };
            let handler_layers = resolve(&section.handler_layers, "handler_layers");
            let intent_layers = resolve(&section.intent_layers, "intent_layers");
            if explicit && !unknown.is_empty() {
                return Err(unknown);
            }
            if unknown.is_empty() && !handler_layers.is_empty() && !intent_layers.is_empty() {
                settings.translation = Some(crate::project::settings::TranslationSettings { handler_layers, intent_layers, max_depth: section.max_depth });
            }
        }
        if let Some(section) = &self.semantic {
            let names: Vec<String> = settings.layers.as_ref().map(|l| l.layers.iter().map(|s| s.name.clone()).collect()).unwrap_or_default();
            let mut unknown = Vec::new();
            let mut find = |name: &str, what: &str| {
                let found = names.iter().position(|n| n == name).map(crate::project::layers::LayerId);
                if found.is_none() {
                    unknown.push(format!("{}: 層 {} は宣言した層に無い", what, name));
                }
                found
            };
            let mut problems = Vec::new();
            let checked = crate::project::semantic::SemanticSettings::validate(section, &mut find, &mut problems);
            problems.extend(unknown);
            if !problems.is_empty() {
                return Err(problems);
            }
            // 問いに入れる線引きは architecture.hy の :semantic-lines から取り込む(定義元は architecture.hy の 1 か所・agora-redesign #1909)。
            let lines = settings.architecture.as_ref().map(|arch| arch.semantic_lines.clone()).unwrap_or_default();
            settings.semantic = Some(crate::project::semantic::SemanticSettings { lines, ..checked });
        }
        Ok(settings)
    }

    /// TOML の節から層の規則の設定を作る(規則ごとの重さも読む)。
    fn project_settings_toml(&self) -> Result<ProjectSettings, Vec<String>> {
        let mut problems = Vec::new();
        let mut unknown_rules = Vec::new();
        let mut registered_severity = std::collections::BTreeMap::new();
        let mut base_severity = std::collections::BTreeMap::new();
        let mut level = std::collections::BTreeMap::new();
        for (id, rule) in &self.rules {
            // この binary に無い規則(DOEFF と 3 桁の形)の設定は、誤りにせずその規則の設定だけを読まずに知らせる(DOEFF100)。
            // Python の文ごとの規則(DOEFF001〜031)の設定もこの表に在るので、層の規則でも Python の規則でもない物だけ。
            if crate::project::notice::is_rule_id_shape(id)
                && ProjectRule::parse(id).is_none()
                && !get_all_rule_ids().contains(&id.to_uppercase())
            {
                unknown_rules.push(crate::project::notice::UnknownRuleRef { key: format!("rules.{}", id), id: id.clone() });
                continue;
            }
            if let Some(text) = &rule.severity {
                let parsed = match text.as_str() {
                    "warning" => Some(Severity::Warning),
                    "info" => Some(Severity::Info),
                    _ => None,
                };
                match (ProjectRule::parse(id).filter(|r| r.is_smell()), parsed) {
                    (Some(rule), Some(severity)) => {
                        base_severity.insert(rule, severity);
                    }
                    (None, _) => problems.push(format!("rules.{}.severity: 臭いの規則(DOEFF121〜125)の ID ではない", id)),
                    (_, None) => problems.push(format!("rules.{}.severity: {:?} は warning・info のどちらでもない(臭いの規則は error にしない)", id, text)),
                }
            }
            if let Some(text) = &rule.level {
                match crate::project::rule::RuleLevel::parse(text) {
                    Some(parsed) => {
                        level.insert(id.to_uppercase(), parsed);
                    }
                    None => problems.push(format!("rules.{}.level: {:?} は critical・major・minor・info のどれでもない", id, text)),
                }
            }
            let Some(text) = &rule.registered_severity else { continue };
            let severity = match text.as_str() {
                "error" => Some(Severity::Error),
                "warning" => Some(Severity::Warning),
                "info" => Some(Severity::Info),
                _ => None,
            };
            match (ProjectRule::parse(id), severity) {
                (Some(rule), Some(severity)) => {
                    registered_severity.insert(rule, severity);
                }
                (None, _) => problems.push(format!("rules.{}.registered_severity: 層の規則(DOEFF101〜113)の ID ではない", id)),
                (_, None) => problems.push(format!("rules.{}.registered_severity: {:?} は error・warning・info のどれでもない", id, text)),
            }
        }
        let mut settings = match self.validate_sections() {
            Ok(settings) if problems.is_empty() => settings,
            Ok(_) => return Err(problems),
            Err(more) => return Err(problems.into_iter().chain(more).collect()),
        };
        settings.registered_severity = registered_severity;
        settings.severity = base_severity;
        settings.level = level;
        settings.unknown_rules.extend(unknown_rules);
        Ok(settings)
    }

    /// 層の規則の節を検める(規則ごとの設定の前の部分)。
    fn validate_sections(&self) -> Result<ProjectSettings, Vec<String>> {
        ProjectSettings::validate(&ProjectSections {
            layers: self.layers.as_ref(),
            tags: self.tags.as_ref(),
            roles: self.roles.as_ref(),
            environment_names: self.environment_names.as_ref(),
            raw_side_effects: self.raw_side_effects.as_ref(),
            laws: &self.laws,
            registry: self.registry.as_ref(),
            services: self.services.as_ref(),
            definitions: self.definitions.as_ref(),
        })
    }
}

fn default_log_file() -> Option<String> {
    Some(".doeff-lint.jsonl".to_string())
}

/// Git integration configuration
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct GitConfig {
    /// Include untracked files when using --modified
    #[serde(default = "default_include_untracked")]
    pub include_untracked: bool,
}

impl Default for GitConfig {
    fn default() -> Self {
        Self {
            include_untracked: true,
        }
    }
}

fn default_include_untracked() -> bool {
    true
}

/// Rule-specific configuration
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct RuleConfig {
    /// DOEFF003: Maximum number of mutable attributes
    pub max_mutable_attributes: Option<usize>,

    /// DOEFF009: Skip private functions (starting with _)
    pub skip_private_functions: Option<bool>,

    /// DOEFF009: Skip test functions (starting with test_)
    pub skip_test_functions: Option<bool>,

    /// 層の規則(DOEFF101〜113): 登録簿に載った破れの重さ(error・warning・info。既定 warning)
    pub registered_severity: Option<String>,

    /// どの規則でも: 重大さ(critical・major・minor・info)。無ければ規則そのものの重さから(error = major・warning = minor・info = info)。
    /// 重さと違い登録簿で下げない — エディタの違反の欄が「手つかずの critical」を数える軸
    pub level: Option<String>,

    /// 臭いの規則(DOEFF121〜125): 重さ(warning・info。既定 warning — Absent / Raise が本線に入ったので info から上げた)
    pub severity: Option<String>,
}

/// Find pyproject.toml file starting from a path and walking up
pub fn find_pyproject_toml(start_path: &Path) -> Option<PathBuf> {
    let mut current = if start_path.is_file() {
        start_path.parent()?
    } else {
        start_path
    };

    loop {
        let pyproject = current.join("pyproject.toml");
        if pyproject.exists() {
            return Some(pyproject);
        }

        current = current.parent()?;
    }
}

/// Find pyproject.toml with [tool.doeff-linter] section
pub fn find_config_pyproject_toml(start_path: &Path) -> Option<PathBuf> {
    let mut current = if start_path.is_file() {
        start_path.parent()?
    } else {
        start_path
    };

    loop {
        let pyproject = current.join("pyproject.toml");
        if pyproject.exists() {
            if let Ok(content) = std::fs::read_to_string(&pyproject) {
                if let Ok(value) = toml::from_str::<toml::Value>(&content) {
                    if let Some(tool) = value.get("tool") {
                        if tool.get("doeff-linter").is_some() {
                            return Some(pyproject);
                        }
                    }
                }
            }
        }

        current = current.parent()?;
    }
}

/// Load configuration from pyproject.toml
pub fn load_config(path: Option<&Path>) -> Option<Config> {
    let config_path = if let Some(p) = path {
        if p.exists() {
            p.to_path_buf()
        } else {
            return None;
        }
    } else {
        find_config_pyproject_toml(&std::env::current_dir().ok()?)?
    };

    let content = std::fs::read_to_string(&config_path).ok()?;
    let value: toml::Value = toml::from_str(&content).ok()?;

    let tool = value.get("tool")?;
    let doeff_linter = tool.get("doeff-linter")?;

    let config: Config = doeff_linter.clone().try_into().ok()?;

    Some(config)
}

/// 見つけた設定 file と、その中の `[tool.doeff-linter]` の節。
#[derive(Debug, Clone)]
pub struct LoadedConfig {
    pub config: Config,
    /// 設定を読んだ file。
    pub path: PathBuf,
    /// 設定の file の本文(知らない規則の ID の位置を後で探すため)。
    pub text: String,
    /// この binary の知らない鍵(読まずに残りを読んだ — DOEFF100 で知らせる)。
    pub notices: Vec<crate::project::notice::ConfigNotice>,
}

/// 設定 file を読む。`[tool.doeff-linter]` の節を持つ file(pyproject.toml の形)でも、節の中身だけを書いた file でもよい。
/// 読めない・TOML でない・欄の型が違う時は理由の文を返す(黙って既定値にしない)。
/// この binary の知らない鍵(どの段でも)は誤りにせず、その鍵だけを読まずに知らせの列へ積む(agora-redesign #848 — 設定は binary より
/// 先に進むことがあり、その間に lint 全体を止めない)。知っている鍵の唯一の正本は Config と各節の struct の定義(`serde_ignored` が
/// 定義に無い鍵を path つきで集める — 鍵の表を手で持たない)。書き違いも同じ知らせで見える。
pub fn load_config_file(path: &Path) -> Result<LoadedConfig, String> {
    let content = std::fs::read_to_string(path).map_err(|e| format!("{} を読めない: {}", path.display(), e))?;
    let value: toml::Value = toml::from_str(&content).map_err(|e| format!("{} は TOML として読めない: {}", path.display(), e))?;
    let section = match value.get("tool").and_then(|tool| tool.get("doeff-linter")) {
        Some(section) => section.clone(),
        None => value,
    };
    let mut unknown: Vec<String> = Vec::new();
    // serde_ignored の path は Option の段を `?` と書く(`semantic.?.proxy_url`)— 設定に書く綴り(`semantic.proxy_url`)へ戻す。
    let spell = |path: &serde_ignored::Path| -> String {
        path.to_string().split('.').filter(|s| !s.is_empty() && *s != "?").collect::<Vec<_>>().join(".")
    };
    let config: Config = serde_ignored::deserialize(section, |key| unknown.push(spell(&key)))
        .map_err(|e: toml::de::Error| format!("{} の [tool.doeff-linter] を読めない: {}", path.display(), e))?;
    let notices = unknown.iter().map(|key| crate::project::notice::key_notice(path, &content, key)).collect();
    Ok(LoadedConfig { config, path: path.to_path_buf(), text: content, notices })
}

/// 設定を探して読む: explicit(`--config`)があればそれを、無ければ start から上へ `[tool.doeff-linter]` を持つ pyproject.toml を探す。
/// 見つからなければ Ok(None)。見つけた file が読めなければ Err。
pub fn load_config_checked(explicit: Option<&Path>, start: &Path) -> Result<Option<LoadedConfig>, String> {
    let path = match explicit {
        Some(path) => path.to_path_buf(),
        None => match find_config_pyproject_toml(start) {
            Some(path) => path,
            None => return Ok(None),
        },
    };
    load_config_file(&path).map(Some)
}

/// 規則の ID の全部 — Python の文ごとの規則(rules の get_all_rules)と層の規則(ProjectRule)。設定の有効な規則の既定と知らない規則の
/// 判じに使う。ここに置くのは、rules/mod.rs が project を読むと依存の輪になるため(config は既に ProjectRule を読む・agora-redesign #2122)。
pub fn get_all_rule_ids() -> Vec<String> {
    crate::rules::get_all_rules()
        .iter()
        .map(|rule| rule.rule_id().to_string())
        .chain(ProjectRule::ALL.iter().map(|rule| rule.id().to_string()))
        .collect()
}

/// 規則の ID が、当たりを判じるのに repo 全体が要る規則か(agora-redesign #2090)。Python の文ごとの規則(DOEFF001〜031)は file 1 つで
/// 判じるので偽。層の規則は ProjectRule::needs_whole_repo の名乗り。知らない ID は偽(有効な規則の一覧には載らない — DOEFF100 が知らせる)。
pub fn needs_whole_repo(id: &str) -> bool {
    ProjectRule::parse(id).is_some_and(|rule| rule.needs_whole_repo())
}

/// 規則の ID が Jev に問う意味の規則か(ProjectRule::is_semantic の名乗り — ID の頭では決めない・agora-redesign #3834)。Python の文ごとの
/// 規則と知らない ID は偽。
pub fn is_semantic(id: &str) -> bool {
    ProjectRule::parse(id).is_some_and(|rule| rule.is_semantic())
}

/// `--list-rules` の出力 — 全部の規則の ID と、repo 全体が要るかの名乗り(門と hook が repo 全体の比べの規則を選ぶ 1 か所)と、Jev に問う
/// 規則かの名乗り(門が Jev の規則を分ける 1 か所)。
pub fn rule_list_json() -> serde_json::Value {
    serde_json::Value::Array(
        get_all_rule_ids()
            .into_iter()
            .map(|id| serde_json::json!({ "id": id, "whole_repo": needs_whole_repo(&id), "semantic": is_semantic(&id) }))
            .collect(),
    )
}

/// Merge command line arguments with config file settings
/// CLI arguments take precedence
pub fn merge_config(
    config: Option<&Config>,
    cli_enable: &[String],
    cli_disable: &[String],
    cli_exclude: &[String],
) -> (Option<Vec<String>>, Vec<String>) {
    let mut enable = None;
    let mut exclude = vec![];

    // Start with config file settings
    if let Some(cfg) = config {
        if !cfg.enable.is_empty() && cli_enable.is_empty() && cli_disable.is_empty() {
            if cfg.enable.contains(&"ALL".to_string()) {
                let all_rules = get_all_rule_ids();
                let enabled: Vec<String> = if !cfg.disable.is_empty() {
                    all_rules
                        .into_iter()
                        .filter(|r| !cfg.disable.contains(r))
                        .collect()
                } else {
                    all_rules
                };
                enable = Some(enabled);
            } else {
                enable = Some(cfg.enable.clone());
            }
        } else if !cfg.disable.is_empty() && cli_enable.is_empty() && cli_disable.is_empty() {
            let all_rules = get_all_rule_ids();
            let enabled: Vec<String> = all_rules
                .into_iter()
                .filter(|r| !cfg.disable.contains(r))
                .collect();
            enable = Some(enabled);
        }

        exclude.extend(cfg.exclude.iter().cloned());
    }

    // Apply CLI overrides
    if !cli_enable.is_empty() {
        if cli_enable.contains(&"ALL".to_string()) {
            let all_rules = get_all_rule_ids();
            let enabled: Vec<String> = if !cli_disable.is_empty() {
                all_rules
                    .into_iter()
                    .filter(|r| !cli_disable.contains(r))
                    .collect()
            } else {
                all_rules
            };
            enable = Some(enabled);
        } else {
            enable = Some(cli_enable.to_vec());
        }
    } else if !cli_disable.is_empty() {
        let all_rules = get_all_rule_ids();
        let enabled: Vec<String> = all_rules
            .into_iter()
            .filter(|r| !cli_disable.contains(r))
            .collect();
        enable = Some(enabled);
    }

    // Add CLI exclude patterns
    exclude.extend(cli_exclude.iter().cloned());

    // Add default excludes
    let defaults = vec![
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        ".tox",
        "build",
        "dist",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        ".mypy_cache",
    ];
    for default in defaults {
        if !exclude.contains(&default.to_string()) {
            exclude.push(default.to_string());
        }
    }

    (enable, exclude)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn test_find_pyproject_toml() {
        let dir = TempDir::new().unwrap();
        let pyproject_path = dir.path().join("pyproject.toml");
        fs::write(
            &pyproject_path,
            "[tool.doeff-linter]\nexclude = [\"test\"]",
        )
        .unwrap();

        assert_eq!(
            find_pyproject_toml(dir.path()),
            Some(pyproject_path.clone())
        );

        let subdir = dir.path().join("subdir");
        fs::create_dir(&subdir).unwrap();
        assert_eq!(find_pyproject_toml(&subdir), Some(pyproject_path));
    }

    #[test]
    fn test_load_config() {
        let dir = TempDir::new().unwrap();
        let pyproject_path = dir.path().join("pyproject.toml");

        let content = r#"
[tool.doeff-linter]
enable = ["DOEFF001", "DOEFF002"]
exclude = ["venv", "build"]

[tool.doeff-linter.rules.DOEFF003]
max_mutable_attributes = 5
"#;
        fs::write(&pyproject_path, content).unwrap();

        let config = load_config(Some(&pyproject_path)).unwrap();
        assert_eq!(config.enable, vec!["DOEFF001", "DOEFF002"]);
        assert_eq!(config.exclude, vec!["venv", "build"]);
        assert_eq!(config.rules["DOEFF003"].max_mutable_attributes, Some(5));
    }

    /// architecture.hy の許可名簿(:world-handlers)を書いたら、層で生の I/O を許す allowed_layers は二重の宣言(agora-redesign #1106)。
    #[test]
    fn allowed_layers_clash_with_the_world_handler_list() {
        let arch_source = r#"(defarchitecture s :root "app" :layers [(layer core) (layer foundation)] :foundation foundation
  :world-handlers [(world-handler "app.foundation.host:with-host" :touches [http])])"#;
        let arch = Architecture::parse(arch_source, Path::new("architecture.hy")).unwrap();
        let with_layers: Config = toml::from_str("[raw_side_effects]\nallowed_layers = [\"foundation\"]\n").unwrap();
        let problems = with_layers.project_settings_with(Some(arch.clone())).err().expect("二重の宣言を通した").join("\n");
        assert!(problems.contains("allowed_layers は architecture.hy の :world-handlers と二重の宣言"), "{}", problems);
        let without: Config = toml::from_str("[raw_side_effects]\ncatalog_extra = \"extra.json\"\n").unwrap();
        assert!(without.project_settings_with(Some(arch)).is_ok(), "allowed_layers の無い raw_side_effects を断った");
    }

    /// 設定が binary より新しい時(知らない鍵がどの段に在っても)、誤りにせずその鍵だけを読まずに知らせ、残りは読む(agora-redesign #848)。
    #[test]
    fn unknown_keys_at_every_depth_are_notices_and_the_rest_is_read() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("pyproject.toml");
        let content = "[tool.doeff-linter]\nenable = [\"ALL\"]\nfuture_top = 1\n\n[tool.doeff-linter.semantic]\nmystery_knob = 2\n\n[tool.doeff-linter.smells]\nshape_check_layers = []\nnew_smell_knob = true\n";
        fs::write(&path, content).unwrap();
        let loaded = load_config_file(&path).expect("知らない鍵で読みを止めた");
        assert_eq!(loaded.config.enable, vec!["ALL"]);
        assert!(loaded.config.smells.is_some(), "知らない鍵の在る節を丸ごと捨てた");
        let found: Vec<(String, u32)> = loaded.notices.iter().map(|n| (n.key.clone(), n.range.start.line)).collect();
        assert_eq!(
            found,
            vec![("future_top".to_string(), 2), ("semantic.mystery_knob".to_string(), 5), ("smells.new_smell_knob".to_string(), 9)]
        );
    }

    /// 型の違う値は今までどおり誤り(知らない鍵だけを知らせに回す)。
    #[test]
    fn wrong_types_are_still_errors() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("pyproject.toml");
        fs::write(&path, "[tool.doeff-linter]\nenable = \"ALL\"\n").unwrap();
        assert!(load_config_file(&path).is_err());
    }

    #[test]
    fn test_merge_config() {
        let config = Config {
            enable: vec!["DOEFF001".to_string()],
            disable: vec![],
            exclude: vec!["custom_dir".to_string()],
            ..Default::default()
        };

        let (enable, exclude) = merge_config(
            Some(&config),
            &["DOEFF002".to_string()],
            &[],
            &["skip_me".to_string()],
        );

        assert_eq!(enable, Some(vec!["DOEFF002".to_string()]));
        assert!(exclude.contains(&"custom_dir".to_string()));
        assert!(exclude.contains(&"skip_me".to_string()));
        assert!(exclude.contains(&".venv".to_string()));
    }
}

#[cfg(test)]
mod project_settings_tests {
    use crate::project::law::ProjectRuleOrExternal;
    use crate::project::layers::LayerId;
    use crate::project::rule::ProjectRule;
    use crate::project::settings::*;
    use super::Config;

    /// 設定の文字列を読んで検める。
    fn validate(text: &str) -> Result<ProjectSettings, Vec<String>> {
        let config: Config = toml::from_str(text).unwrap();
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
