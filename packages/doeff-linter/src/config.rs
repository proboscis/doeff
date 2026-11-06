//! Configuration loading for doeff-linter
//!
//! Loads configuration from pyproject.toml [tool.doeff-linter] section

use crate::project::layers::{LayersSection, RolesSection, TagsSection};
use crate::project::settings::{
    EnvironmentNamesSection, LawEntry, ProjectSections, ProjectSettings, RawSideEffectsSection, RegistrySection, ServicesSection, DefinitionsSection,
    ProgramSleepSection,
    TestSleepSection,
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

    /// Layer order, locations, and import rules for the layer rules (DOEFF101-105) -- `[tool.doeff-linter.layers]`
    #[serde(default)]
    pub layers: Option<LayersSection>,

    /// How tags are read -- `[tool.doeff-linter.tags]` (the default is fine)
    #[serde(default)]
    pub tags: Option<TagsSection>,

    /// The closed list of roles and the roles allowed per layer -- `[tool.doeff-linter.roles]`
    #[serde(default)]
    pub roles: Option<RolesSection>,

    /// Environment words that must not be attached to business names (DOEFF108) -- `[tool.doeff-linter.environment_names]`
    #[serde(default)]
    pub environment_names: Option<EnvironmentNamesSection>,

    /// Layers that may touch raw side effects directly (DOEFF106, 107) -- `[tool.doeff-linter.raw_side_effects]`
    #[serde(default)]
    pub raw_side_effects: Option<RawSideEffectsSection>,

    /// Mapping between rule IDs and ADR laws -- `[[tool.doeff-linter.laws]]`
    #[serde(default)]
    pub laws: Vec<LawEntry>,

    /// Registry of known breaches and the rules being reconciled -- `[tool.doeff-linter.registry]`
    #[serde(default)]
    pub registry: Option<RegistrySection>,

    /// Service boundaries (DOEFF109) and context checking (DOEFF113) -- `[tool.doeff-linter.services]`
    #[serde(default)]
    pub services: Option<ServicesSection>,

    /// Population of the rules on how definitions are written (DOEFF110 defn prohibition, 111 reasons for deff, 112 required tags) -- `[tool.doeff-linter.definitions]`
    #[serde(default)]
    pub definitions: Option<DefinitionsSection>,

    /// Population of "a Hy program does not sleep on the wall clock" (DOEFF210) = where programs live -- `[tool.doeff-linter.program_sleep]`
    #[serde(default)]
    pub program_sleep: Option<ProgramSleepSection>,

    /// Population of "a Hy test does not sleep on the wall clock unless it declares realtime" (DOEFF211) = where tests live -- `[tool.doeff-linter.test_sleep]`
    #[serde(default)]
    pub test_sleep: Option<TestSleepSection>,

    /// Path of architecture.hy, the declaration of services and layers (relative to the config file's dir). If not written, looks for architecture.hy at the repo root.
    #[serde(default)]
    pub architecture: Option<String>,

    /// Path of the repo root (the base for module names, layer locations, registries, and key paths) (relative to the config file's dir). If not written, the config file's dir.
    /// `--root` wins. This is so that, with the root set under a monorepo package's `src/`, module names match import names (`doeff_cluster.x`)
    /// (with the config file's dir as the root it would become `src.doeff_cluster.x`).
    #[serde(default)]
    pub root: Option<String>,

    /// Dirs to walk outside the root (relative to the config file's dir) -- the Hy files under them enter the population of the rules on how definitions are written and the judgment of "outside the target"
    /// (the layer rules judge only the layer locations under the root, so they are not included). Claimed names are relative to the root and include `..` (`../tests/x.hy` -- the same form as the key of a declaration file).
    /// This is to judge the `tests/` of a package with root = "src".
    #[serde(default)]
    pub include: Vec<String>,

    /// Layers and thresholds for the semantic rules (DOEFF201, 202 -- Jev) -- `[tool.doeff-linter.semantic]`
    #[serde(default)]
    pub semantic: Option<crate::project::semantic::SemanticSection>,

    /// Settings for the smell rules (DOEFF121-125) -- `[tool.doeff-linter.smells]`
    #[serde(default)]
    pub smells: Option<crate::project::settings::SmellsSection>,

    /// Settings for the rule on effects emitted by translation handlers (DOEFF130) -- `[tool.doeff-linter.translation_effects]`
    #[serde(default)]
    pub translation_effects: Option<crate::project::settings::TranslationEffectsSection>,

    /// Settings for the commit hook (`--commit-hook`) -- `[tool.doeff-linter.commit_hook]`
    #[serde(default)]
    pub commit_hook: Option<CommitHookSection>,

    /// The repo's catalogs of real I/O handlers, read on top of doeff's bundled catalog (paths relative to the config file's dir; the shape of
    /// doeff-linter `data/world_handlers.json`). For handlers of packages outside doeff: the package's repo keeps the catalog file and the using repo names it here,
    /// so :wraps of :world-handlers may name those handlers. `--world-catalog` wins (the commit hook and the merge gate pass the tip's catalogs as absolute paths
    /// when they measure a tree placed outside the checkout).
    #[serde(default)]
    pub world_catalogs: Vec<String>,
}

/// `[tool.doeff-linter.commit_hook]` -- the settings read by the commit hook's entry point (`--commit-hook`).
/// Each repo does not copy and keep the hook's logic; it writes only this one declaration.
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct CommitHookSection {
    /// A retired key -- formerly the hand-written list of rules applied to the whole repo. It is no longer read, and if left in place the hook announces it in one line
    /// (the split of rules is declared by `ProjectRule::needs_whole_repo`). Only the shape is kept so that old configs do not become unreadable.
    #[serde(default)]
    pub whole_repo_rules: Vec<String>,
    /// Upper limit per child linter run (seconds, default 20). If exceeded, it is treated as unmeasurable and does not block.
    #[serde(default)]
    pub timeout_s: Option<u64>,
}

impl Config {
    /// Checks the section for the layer rules and turns it into the form the rules read (name mismatches become a list of reason sentences).
    pub fn project_settings(&self) -> Result<ProjectSettings, Vec<String>> {
        self.project_settings_with(None)
    }

    /// Builds the settings for the layer rules. If architecture.hy exists, layers and roles are copied from it, and TOML layers, roles, and services cannot be placed
    /// (to keep the declaration in one place).
    pub fn project_settings_with(&self, architecture: Option<Architecture>) -> Result<ProjectSettings, Vec<String>> {
        let mut settings = match &architecture {
            None => self.project_settings_toml()?,
            Some(arch) => {
                let mut clashes = Vec::new();
                for (name, present) in [("layers", self.layers.is_some()), ("roles", self.roles.is_some()), ("services", self.services.is_some())] {
                    if present {
                        clashes.push(format!(
                            "[tool.doeff-linter.{}] is a duplicate declaration of {} -- write layers, roles, and services only in architecture.hy",
                            name,
                            arch.path.display()
                        ));
                    }
                }
                // Where raw I/O is allowed is decided only by architecture.hy's allowlist (:world-handlers) once it is written -- not combined with the layers allowed by
                // [tool.doeff-linter.raw_side_effects] allowed_layers.
                let allowed_layers = self.raw_side_effects.as_ref().is_some_and(|raw| !raw.allowed_layers.is_empty());
                if !arch.world_handlers.is_empty() && allowed_layers {
                    clashes.push(format!(
                        "[tool.doeff-linter.raw_side_effects] allowed_layers is a duplicate declaration of the :world-handlers of {} -- write where raw I/O is allowed only in the allowlist",
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
        // :wraps may contain only handlers that are in the catalog of real I/O handlers -- doeff's bundled catalog plus the repo's world_catalogs
        // (if a handler outside the catalog is wrapped, no rule knows that handler to be real I/O).
        if let Some(arch) = settings.architecture.as_ref() {
            let catalog = arch.world_catalog();
            let outside: Vec<String> = arch
                .world_handlers
                .iter()
                .flat_map(|h| h.wraps.iter().filter(|w| !catalog.handlers.contains_key(&w.target())).map(move |w| format!("{} :wraps {}", h.definition.spelling(), w.spelling())))
                .collect();
            if !outside.is_empty() {
                return Err(outside
                    .into_iter()
                    .map(|what| format!("{}: {} is not in the catalog of real I/O handlers (doeff-linter data/world_handlers.json and the files of [tool.doeff-linter] world_catalogs) -- add it to the catalog of the package that defines it and name that file in world_catalogs, or remove it from :wraps", arch.path.display(), what))
                    .collect());
            }
        }
        // In a repo that wrote an allowlist, where raw side effects are allowed = the modules of the allowlist's definitions (judged even without the TOML raw_side_effects section).
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
        // For modules outside the declared locations (such as a dir that precedes the layer), infer the layer from the :role tag and apply the layer rules.
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
                    None => unknown.push(format!("smells.shape_check_layers: layer {} is not among the declared layers", name)),
                }
            }
            if !unknown.is_empty() {
                return Err(unknown);
            }
            settings.smells = Some(crate::project::settings::SmellSettings { shape_check_layers: layers });
        }
        // DOEFF130 -- if the section is written, a mistake in a layer name is a config error. If not written, it applies only when both default layers (protocol, intent) exist.
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
                        None => unknown.push(format!("translation_effects.{}: layer {} is not among the declared layers", key, name)),
                    }
                }
                found
            };
            let handler_layers = resolve(&section.handler_layers, "handler_layers");
            let intent_layers = resolve(&section.intent_layers, "intent_layers");
            // Business layers of DOEFF174 -- if written, a mistake in a name is a config error. If not written, only those of the default names that exist in the declaration (do not stop DOEFF130).
            let domain_layers = match &section.domain_layers {
                Some(list) => resolve(list, "domain_layers"),
                None => crate::project::settings::DEFAULT_DOMAIN_LAYERS
                    .iter()
                    .filter_map(|name| names.iter().position(|n| n == name).map(crate::project::layers::LayerId))
                    .collect(),
            };
            if explicit && !unknown.is_empty() {
                return Err(unknown);
            }
            if unknown.is_empty() && !handler_layers.is_empty() && !intent_layers.is_empty() {
                settings.translation =
                    Some(crate::project::settings::TranslationSettings { handler_layers, intent_layers, max_depth: section.max_depth, domain_layers });
            }
        }
        if let Some(section) = &self.semantic {
            let names: Vec<String> = settings.layers.as_ref().map(|l| l.layers.iter().map(|s| s.name.clone()).collect()).unwrap_or_default();
            let mut unknown = Vec::new();
            let mut find = |name: &str, what: &str| {
                let found = names.iter().position(|n| n == name).map(crate::project::layers::LayerId);
                if found.is_none() {
                    unknown.push(format!("{}: layer {} is not among the declared layers", what, name));
                }
                found
            };
            let mut problems = Vec::new();
            let checked = crate::project::semantic::SemanticSettings::validate(section, &mut find, &mut problems);
            problems.extend(unknown);
            if !problems.is_empty() {
                return Err(problems);
            }
            // The lines drawn for the question are taken from :semantic-lines of architecture.hy (the source is the single place architecture.hy).
            let lines = settings.architecture.as_ref().map(|arch| arch.semantic_lines.clone()).unwrap_or_default();
            settings.semantic = Some(crate::project::semantic::SemanticSettings { lines, ..checked });
        }
        Ok(settings)
    }

    /// Builds the settings for the layer rules from the TOML sections (also reads the severity per rule).
    fn project_settings_toml(&self) -> Result<ProjectSettings, Vec<String>> {
        let mut problems = Vec::new();
        let mut unknown_rules = Vec::new();
        let mut registered_severity = std::collections::BTreeMap::new();
        let mut base_severity = std::collections::BTreeMap::new();
        let mut level = std::collections::BTreeMap::new();
        for (id, rule) in &self.rules {
            // Settings for rules not in this binary (the form DOEFF plus 3 digits) are not treated as errors; only that rule's settings are skipped and reported (DOEFF100).
            // The settings of the Python per-statement rules (DOEFF001-031) are also in this table, so only those that are neither layer rules nor Python rules.
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
                    (None, _) => problems.push(format!("rules.{}.severity: not the ID of a smell rule (DOEFF121-125)", id)),
                    (_, None) => problems.push(format!("rules.{}.severity: {:?} is neither warning nor info (smell rules are not made error)", id, text)),
                }
            }
            if let Some(text) = &rule.level {
                match crate::project::rule::RuleLevel::parse(text) {
                    Some(parsed) => {
                        level.insert(id.to_uppercase(), parsed);
                    }
                    None => problems.push(format!("rules.{}.level: {:?} is none of critical, major, minor, info", id, text)),
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
                (None, _) => problems.push(format!("rules.{}.registered_severity: not the ID of a layer rule (DOEFF101-113)", id)),
                (_, None) => problems.push(format!("rules.{}.registered_severity: {:?} is none of error, warning, info", id, text)),
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

    /// Checks the section for the layer rules (the part before the per-rule settings).
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
            program_sleep: self.program_sleep.as_ref(),
            test_sleep: self.test_sleep.as_ref(),
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

    /// Layer rules (DOEFF101-113): the severity of breaches listed in the registry (error, warning, info. Default warning)
    pub registered_severity: Option<String>,

    /// For any rule: the criticality (critical, major, minor, info). If absent, derived from the rule's own severity (error = major, warning = minor, info = info).
    /// Unlike severity, it is not lowered by the registry -- the axis on which the editor's violation pane counts "untouched critical"
    pub level: Option<String>,

    /// Smell rules (DOEFF121-125): severity (warning, info. Default warning -- raised from info because Absent / Raise entered the main line)
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

/// The config file found, and its `[tool.doeff-linter]` section.
#[derive(Debug, Clone)]
pub struct LoadedConfig {
    pub config: Config,
    /// The file the config was read from.
    pub path: PathBuf,
    /// The body of the config file (to find the position of unknown rule IDs later).
    pub text: String,
    /// Keys this binary does not know (the rest was read without them -- reported by DOEFF100).
    pub notices: Vec<crate::project::notice::ConfigNotice>,
}

/// Reads a config file. It may be a file with a `[tool.doeff-linter]` section (the pyproject.toml form) or a file that holds only the section's contents.
/// When it cannot be read, is not TOML, or a field has the wrong type, returns a reason sentence (does not silently fall back to defaults).
/// Keys this binary does not know (at any level) are not made errors; only that key is skipped and pushed into the list of notices (the config can
/// get ahead of the binary, and the whole lint must not stop in the meantime). The single source of truth for known keys is the definitions of Config and each section's struct (`serde_ignored`
/// collects keys not in the definitions with their paths -- no hand-maintained key table). Typos are visible through the same notices.
pub fn load_config_file(path: &Path) -> Result<LoadedConfig, String> {
    let content = std::fs::read_to_string(path).map_err(|e| format!("cannot read {}: {}", path.display(), e))?;
    let value: toml::Value = toml::from_str(&content).map_err(|e| format!("{} cannot be read as TOML: {}", path.display(), e))?;
    let section = match value.get("tool").and_then(|tool| tool.get("doeff-linter")) {
        Some(section) => section.clone(),
        None => value,
    };
    let mut unknown: Vec<String> = Vec::new();
    // serde_ignored's path writes an Option level as `?` (`semantic.?.proxy_url`) -- return it to the spelling written in the config (`semantic.proxy_url`).
    let spell = |path: &serde_ignored::Path| -> String {
        path.to_string().split('.').filter(|s| !s.is_empty() && *s != "?").collect::<Vec<_>>().join(".")
    };
    let config: Config = serde_ignored::deserialize(section, |key| unknown.push(spell(&key)))
        .map_err(|e: toml::de::Error| format!("cannot read [tool.doeff-linter] of {}: {}", path.display(), e))?;
    let notices = unknown.iter().map(|key| crate::project::notice::key_notice(path, &content, key)).collect();
    Ok(LoadedConfig { config, path: path.to_path_buf(), text: content, notices })
}

/// Finds and reads the config: uses explicit (`--config`) if given, otherwise searches upward from start for a pyproject.toml that has `[tool.doeff-linter]`.
/// Ok(None) if not found. Err if the found file cannot be read.
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

/// All rule IDs -- the Python per-statement rules (get_all_rules in rules) and the layer rules (ProjectRule). Used for the default of the config's enabled rules and for judging unknown rules
/// . They are placed here because rules/mod.rs reading project would form a dependency cycle (config already reads ProjectRule).
pub fn get_all_rule_ids() -> Vec<String> {
    crate::rules::get_all_rules()
        .iter()
        .map(|rule| rule.rule_id().to_string())
        .chain(ProjectRule::ALL.iter().map(|rule| rule.id().to_string()))
        .collect()
}

/// Whether a rule ID is a rule that needs the whole repo to judge its hits. The Python per-statement rules (DOEFF001-031) are judged from one file,
/// so false. Layer rules are as declared by ProjectRule::needs_whole_repo. Unknown IDs are false (they are not on the list of enabled rules -- DOEFF100 reports them).
pub fn needs_whole_repo(id: &str) -> bool {
    ProjectRule::parse(id).is_some_and(|rule| rule.needs_whole_repo())
}

/// Whether a rule ID is a semantic rule that asks Jev (as declared by ProjectRule::is_semantic -- not decided by the ID prefix). The Python per-statement
/// rules and unknown IDs are false.
pub fn is_semantic(id: &str) -> bool {
    ProjectRule::parse(id).is_some_and(|rule| rule.is_semantic())
}

/// Output of `--list-rules` -- the IDs of all rules, the declaration of whether the whole repo is needed (the one place where the gate and the hook choose the rules of whole-repo comparison), and the declaration of whether it is a rule that asks Jev
/// (the one place where the gate separates Jev's rules).
pub fn rule_list_json() -> serde_json::Value {
    serde_json::Value::Array(
        get_all_rule_ids()
            .into_iter()
            .map(|id| serde_json::json!({ "id": id, "whole_repo": needs_whole_repo(&id), "semantic": is_semantic(&id) }))
            .collect(),
    )
}

/// The repo's catalog files of real I/O handlers -- the `--world-catalog` arguments if given, otherwise the config's world_catalogs from the config file's dir
/// (an empty list = only doeff's bundled catalog).
pub fn repo_world_catalogs(cli: &[PathBuf], config: Option<&Config>, config_dir: &Path) -> Vec<PathBuf> {
    if !cli.is_empty() {
        return cli.to_vec();
    }
    config.map(|c| c.world_catalogs.iter().map(|path| config_dir.join(path)).collect()).unwrap_or_default()
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

    /// Once architecture.hy's allowlist (:world-handlers) is written, the allowed_layers that allow raw I/O by layer is a duplicate declaration.
    #[test]
    fn allowed_layers_clash_with_the_world_handler_list() {
        let arch_source = r#"(defarchitecture s :root "app" :layers [(layer core) (layer foundation)] :foundation foundation
  :world-handlers [(world-handler "app.foundation.host:with-host" :touches [http])])"#;
        let arch = Architecture::parse(arch_source, Path::new("architecture.hy")).unwrap();
        let with_layers: Config = toml::from_str("[raw_side_effects]\nallowed_layers = [\"foundation\"]\n").unwrap();
        let problems = with_layers.project_settings_with(Some(arch.clone())).err().expect("passed a duplicate declaration").join("\n");
        assert!(problems.contains("allowed_layers is a duplicate declaration of the :world-handlers of architecture.hy"), "{}", problems);
        let without: Config = toml::from_str("[raw_side_effects]\ncatalog_extra = \"extra.json\"\n").unwrap();
        assert!(without.project_settings_with(Some(arch)).is_ok(), "rejected raw_side_effects without allowed_layers");
    }

    /// When the config is newer than the binary (an unknown key at any level), it is not made an error; only that key is skipped and reported, and the rest is read.
    #[test]
    fn unknown_keys_at_every_depth_are_notices_and_the_rest_is_read() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("pyproject.toml");
        let content = "[tool.doeff-linter]\nenable = [\"ALL\"]\nfuture_top = 1\n\n[tool.doeff-linter.semantic]\nmystery_knob = 2\n\n[tool.doeff-linter.smells]\nshape_check_layers = []\nnew_smell_knob = true\n";
        fs::write(&path, content).unwrap();
        let loaded = load_config_file(&path).expect("stopped reading at an unknown key");
        assert_eq!(loaded.config.enable, vec!["ALL"]);
        assert!(loaded.config.smells.is_some(), "threw away a whole section that has an unknown key");
        let found: Vec<(String, u32)> = loaded.notices.iter().map(|n| (n.key.clone(), n.range.start.line)).collect();
        assert_eq!(
            found,
            vec![("future_top".to_string(), 2), ("semantic.mystery_knob".to_string(), 5), ("smells.new_smell_knob".to_string(), 9)]
        );
    }

    /// A value of the wrong type is an error as before (only unknown keys are sent to the notices).
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

    /// Reads and checks a config string.
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
        assert!(layers.layers[1].allowed.is_none(), "a layer with nothing written is not restricted");
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
        assert!(problems.contains("nested"), "{}", problems);
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
        for needle in ["twice", "ghost", "nowhere", "translation", "DOEFF1O1", "env-by-layer"] {
            assert!(text.contains(needle), "{} is missing: {}", needle, text);
        }
        // A well-formed unknown ID (a rule newer than this binary) is not made an error -- it goes to the DOEFF100 notice.
        assert!(!text.contains("DOEFF999"), "made the ID of a new rule an error: {}", text);
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
        .expect("made the config unreadable because of the ID of a new rule");
        let refs: Vec<(&str, &str)> = settings.unknown_rules.iter().map(|u| (u.key.as_str(), u.id.as_str())).collect();
        assert_eq!(refs, vec![("laws.future.rules", "DOEFF998"), ("registry.reconciling", "DOEFF999")]);
        assert_eq!(settings.laws[0].rules, vec![ProjectRuleOrExternal::Project(ProjectRule::DefnForbidden)], "threw away even the readable reference");
    }
}
