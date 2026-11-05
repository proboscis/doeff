//! Settings for the layer rules (DOEFF101-108) — the shape of the `[tool.doeff-linter.*]` sections (the form read from TOML) and the validated form after reading.
//!
//! Layer names, roles, environment words, and registry locations differ per repo, so they are not written into Rust and are all received from these settings.
//! The read form (`*Section`) is a serde mirror, and `ProjectSettings::validate` turns name mismatches into errors with reasons
//! and converts it into the validated form (`ProjectSettings`). Rules read only this validated form.

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

use super::layers::{
    normalize_dir, tag_reading, LayerId, LayerSettings, LayerSpec, LayersSection, PlacePattern, RolesSection, TagReading, TagsSection,
};
use super::law::{LawSpec, ProjectRuleOrExternal};
use super::raw_settings::RawSettingsSpec;
use super::rule::{ProjectRule, RuleLevel};

/// `[tool.doeff-linter.services]` — service boundaries (DOEFF109) and the check of tag contexts against services (DOEFF113).
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct ServicesSection {
    /// On/off (default true).
    #[serde(default = "default_true")]
    pub enabled: bool,
    /// Name of the shared location (readable from any service — e.g. shared).
    #[serde(default)]
    pub shared: Vec<String>,
    /// Layers that guard the boundary (if a module in these layers reads another service, it is a breach — e.g. core, protocol).
    #[serde(default)]
    pub guarded_layers: Vec<String>,
    /// Layers that may be read from another service (e.g. intent).
    #[serde(default)]
    pub open_layers: Vec<String>,
    /// Exceptions (the from service may read the to service).
    #[serde(default)]
    pub exceptions: Vec<ServiceException>,
    /// Whether to check the tag's :context against the dir's service (default true).
    #[serde(default = "default_true")]
    pub check_context: bool,
}

/// One exception to the service boundary.
#[derive(Debug, Deserialize, Serialize, Clone, PartialEq, Eq)]
pub struct ServiceException {
    pub from: String,
    pub to: String,
}

/// Default value for a field that is true when omitted.
fn default_true() -> bool {
    true
}

/// Settings of the service boundary (after validation).
#[derive(Debug, Clone)]
pub struct ServiceSettings {
    pub shared: BTreeSet<String>,
    pub guarded: BTreeSet<LayerId>,
    pub open: BTreeSet<LayerId>,
    pub exceptions: BTreeSet<(String, String)>,
    pub check_context: bool,
}

/// `[tool.doeff-linter.definitions]` — the population for the definition-style rules (DOEFF110 ban on defn, 111 reason for deff, 112 required tags).
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct DefinitionsSection {
    /// Locations to judge (dirs from the repo root; a trailing `*` is a prefix match). If empty, all of the repo's Hy.
    #[serde(default)]
    pub paths: Vec<String>,
    /// Locations to exclude (written the same as paths — e.g. the owner of doeff-hy's macros).
    #[serde(default)]
    pub exclude: Vec<String>,
    /// Path segments to exclude (exact match of a dir name).
    #[serde(default)]
    pub exclude_parts: Vec<String>,
    /// The marker of the reason comment for deff (default `defk にできない:`, meaning "cannot be a defk:").
    pub deff_reason_marker: Option<String>,
    /// Test locations (DOEFF118) — globs (`**` is zero or more levels, `*` is any spelling within a level, a spelling without `/` is matched against the file name).
    #[serde(default)]
    pub test_paths: Vec<String>,
}

/// Settings of the definition-style rules (after validation).
#[derive(Debug, Clone)]
pub struct DefinitionSettings {
    pub paths: Vec<String>,
    pub exclude: Vec<String>,
    pub exclude_parts: BTreeSet<String>,
    pub deff_reason_marker: String,
    pub tags: TagReading,
    pub test_paths: Vec<String>,
}

/// `[tool.doeff-linter.program_sleep]` — the population of DOEFF210 (a Hy program does not sleep on the wall clock) = the locations of programs.
/// If the section is absent the rule judges nothing (separate from `definitions`, the population of the definition-style rules — this rule alone can be applied to programs
/// of a package that does not apply that group of rules).
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct ProgramSleepSection {
    /// Locations to judge (dirs from the repo root; a trailing `*` is a prefix match). If empty, all of the repo's Hy.
    #[serde(default)]
    pub paths: Vec<String>,
    /// Locations to exclude (written the same as paths).
    #[serde(default)]
    pub exclude: Vec<String>,
    /// Path segments to exclude (exact match of a dir name — e.g. tests, examples).
    #[serde(default)]
    pub exclude_parts: Vec<String>,
}

/// Population of DOEFF210 (after validation).
#[derive(Debug, Clone)]
pub struct ProgramSleepSettings {
    pub paths: Vec<String>,
    pub exclude: Vec<String>,
    pub exclude_parts: BTreeSet<String>,
}

/// `[tool.doeff-linter.test_sleep]` — the population of DOEFF211 (a Hy test does not sleep on the wall clock unless it declares realtime) = the locations of tests.
/// If the section is absent the rule judges nothing. Test files = Hy files that have a dir named in `parts` among their path segments, plus Hy files whose name matches a `names`
/// glob wherever they are (so tests placed in a dir with another name are covered), minus those under a `exclude` dir.
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct TestSleepSection {
    /// Names of dirs where tests live (exact match of a path segment — e.g. tests).
    #[serde(default)]
    pub parts: Vec<String>,
    /// Globs for test file names (matched against the file name only — e.g. test_*.hy).
    #[serde(default)]
    pub names: Vec<String>,
    /// Locations to exclude (dirs or files from the repo root; a trailing `*` is a prefix match) — dirs of rule fixtures, dirs the registry lists as having real time as the subject for the whole group, and
    /// files of independent scripts that run as separate processes (which cannot import the test tooling and so cannot declare the marker).
    #[serde(default)]
    pub exclude: Vec<String>,
}

/// Population of DOEFF211 (after validation).
#[derive(Debug, Clone)]
pub struct TestSleepSettings {
    pub parts: BTreeSet<String>,
    pub names: Vec<String>,
    pub exclude: Vec<String>,
}

/// `[tool.doeff-linter.smells]` — settings of the smell rules (the layer to which DOEFF121 applies). DOEFF122-125 apply to the population of the definition rules.
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct SmellsSection {
    /// Names of the layers to which DOEFF121 (isinstance on a field read by string key) applies (judgment layers — e.g. core).
    #[serde(default)]
    pub shape_check_layers: Vec<String>,
}

/// Settings of the smell rules (after validation).
#[derive(Debug, Clone, Default)]
pub struct SmellSettings {
    pub shape_check_layers: BTreeSet<LayerId>,
}

/// `[tool.doeff-linter.translation_effects]` — settings of DOEFF130 (a translation handler emits business intents). If the section is absent, the defaults
/// (handlers of layer protocol, types of layer intent, 8 levels) apply, and it hits only when layers with those names exist.
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct TranslationEffectsSection {
    /// Layers to judge handlers in (translation layers — e.g. protocol).
    #[serde(default = "default_translation_handler_layers")]
    pub handler_layers: Vec<String>,
    /// Layers where business intents live (firing a type of a module in this layer is a violation — e.g. intent).
    #[serde(default = "default_translation_intent_layers")]
    pub intent_layers: Vec<String>,
    /// Upper bound on the depth of defk levels followed from the calls a handler fires (if 0, only intents the handler body fires directly).
    #[serde(default = "default_translation_max_depth")]
    pub max_depth: usize,
    /// Business layers (outside translation handlers — layers that use only the domain API). DOEFF174 turns red where a definition in these layers emits an effect of `:lower-layer-modules` of
    /// `:business-fakes` in architecture.hy, or applies a handler of that package. If not written, only the layers present in the declaration
    /// among the default names (core, intent) — if written, a wrong name is a settings error. The depth limit to follow uses max_depth.
    #[serde(default)]
    pub domain_layers: Option<Vec<String>>,
}

impl Default for TranslationEffectsSection {
    fn default() -> Self {
        TranslationEffectsSection {
            handler_layers: default_translation_handler_layers(),
            intent_layers: default_translation_intent_layers(),
            max_depth: default_translation_max_depth(),
            domain_layers: None,
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

/// Default names of the business layers of DOEFF174 (when `domain_layers` is not written — only layers present in the declaration are counted).
pub const DEFAULT_DOMAIN_LAYERS: &[&str] = &["core", "intent"];

/// Settings of DOEFF130 (after validation).
#[derive(Debug, Clone)]
pub struct TranslationSettings {
    pub handler_layers: BTreeSet<LayerId>,
    pub intent_layers: BTreeSet<LayerId>,
    pub max_depth: usize,
    /// Business layers of DOEFF174 (if no layer with a default name exists, that name is not counted — if empty, DOEFF174 judges nothing).
    pub domain_layers: BTreeSet<LayerId>,
}

/// `[tool.doeff-linter.environment_names]` — environment words that must not be put in business names.
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct EnvironmentNamesSection {
    /// Environment words (a violation if any word from splitting a name on `-`, `_`, `.` matches).
    #[serde(default)]
    pub words: Vec<String>,
    /// Locations of business files (dirs from the repo root. A trailing `*` is a prefix match on the path).
    #[serde(default)]
    pub paths: Vec<String>,
    /// Locations to exclude from business files (written the same as paths).
    #[serde(default)]
    pub exclude: Vec<String>,
    /// Path segments to exclude from business files (exact match of a dir name — e.g. tests, adr).
    #[serde(default)]
    pub exclude_parts: Vec<String>,
    /// Extensions to read (default hy, hyk, hyp, py).
    #[serde(default)]
    pub extensions: Option<Vec<String>>,
    /// Names of assembly files (all top-level definition names in these files are examined — e.g. handler_sets.hy).
    #[serde(default)]
    pub assembly_files: Vec<String>,
}

/// `[tool.doeff-linter.raw_side_effects]` — layers that may touch raw side effects directly.
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct RawSideEffectsSection {
    #[serde(default)]
    pub allowed_layers: Vec<String>,
    /// Additions to the catalog (a JSON file in the same form as hy-index's `--raw-catalog-extra`, relative to the repo root).
    pub catalog_extra: Option<String>,
}

/// One `[[tool.doeff-linter.laws]]` — the mapping between an ADR law and the rules that judge it.
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct LawEntry {
    /// The law's name (as spelled in the ADR). Also used for the `<rule>` field of the registry key.
    pub name: String,
    pub adr: Option<String>,
    /// The law's :statement verbatim (the editor's "what is being looked at").
    #[serde(default)]
    pub statement: String,
    /// IDs of the rules that judge this law (if empty it is unwired — no violations are emitted, and it is listed with wired = false).
    #[serde(default)]
    pub rules: Vec<String>,
    /// The layers this law applies to (all layers if empty). Written when a rule is tied to a different law per layer.
    #[serde(default)]
    pub layers: Vec<String>,
}

/// `[tool.doeff-linter.registry]` — the registry of known breaches, and the rules being reconciled.
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct RegistrySection {
    /// A dir with 1 key per file (line 1 of each `*.txt` inside is the key).
    #[serde(default)]
    pub dirs: Vec<String>,
    /// A file with 1 key per line (blank lines and lines starting with `#` are skipped).
    #[serde(default)]
    pub files: Vec<String>,
    /// IDs of the rules being reconciled (violations are lowered to info. registered follows the registry).
    #[serde(default)]
    pub reconciling: Vec<String>,
    /// Files with 1 key per line, read relative to the settings file's dir (so the settings and the registry can be placed and carried together).
    #[serde(default)]
    pub config_files: Vec<String>,
}

/// Settings of environment words (after validation).
#[derive(Debug, Clone)]
pub struct EnvironmentSettings {
    pub words: BTreeSet<String>,
    pub paths: Vec<String>,
    pub exclude: Vec<String>,
    pub exclude_parts: BTreeSet<String>,
    pub extensions: BTreeSet<String>,
    pub assembly_files: BTreeSet<String>,
}

/// Settings of the registry (after validation).
#[derive(Debug, Clone, Default)]
pub struct RegistrySpec {
    pub dirs: Vec<String>,
    pub files: Vec<String>,
    pub reconciling: BTreeSet<ProjectRule>,
    pub config_files: Vec<String>,
}

/// All of the layer-rule settings (after validation). A rule with no section is None, and that rule emits nothing.
#[derive(Debug, Clone, Default)]
pub struct ProjectSettings {
    pub layers: Option<LayerSettings>,
    pub environment: Option<EnvironmentSettings>,
    pub raw: Option<RawSettingsSpec>,
    pub services: Option<ServiceSettings>,
    pub definitions: Option<DefinitionSettings>,
    /// Population of DOEFF210 (`[tool.doeff-linter.program_sleep]`; None if absent — the rule judges nothing).
    pub program_sleep: Option<ProgramSleepSettings>,
    /// Population of DOEFF211 (`[tool.doeff-linter.test_sleep]`; None if absent — the rule judges nothing).
    pub test_sleep: Option<TestSleepSettings>,
    /// Per rule, the weight of breaches listed in the registry (warning if absent).
    pub registered_severity: BTreeMap<ProjectRule, crate::models::Severity>,
    /// Per-rule overrides of weight (only for the smell rules DOEFF121-125 — when raising the default info to warning).
    pub severity: BTreeMap<ProjectRule, crate::models::Severity>,
    /// Per-rule severity declarations (rule ID uppercased -> severity. Both layer rules and Python rules). A rule without one is decided from its weight.
    pub level: BTreeMap<String, RuleLevel>,
    /// Settings of the smell rules (`[tool.doeff-linter.smells]`; None if absent).
    pub smells: Option<SmellSettings>,
    /// Settings of DOEFF130 (Some only when both the handler layer and the intent layer are among the declared layers).
    pub translation: Option<TranslationSettings>,
    /// The dir of the file the settings were read from (the base for registry.config_files).
    pub config_dir: Option<std::path::PathBuf>,
    /// Dirs walked outside the root (absolute paths from resolving the settings' `include` against the settings file's dir). The walked range = root + these
    /// (`hy_files::walked`)。
    pub include: Vec<std::path::PathBuf>,
    /// The topmost architecture.hy of the repo (the only declaration of services and layers). If present, layers and roles are copied from here.
    pub architecture: Option<super::architecture::Architecture>,
    /// Settings of the semantic rules (DOEFF201, 202 — Jev).
    pub semantic: Option<super::semantic::SemanticSettings>,
    pub laws: Vec<LawSpec>,
    pub registry: RegistrySpec,
    /// IDs of rules that the settings referenced but this binary lacks (the form `DOEFF` plus 3 digits) — not made an error; only that reference is left unread and reported with DOEFF100.
    pub unknown_rules: Vec<super::notice::UnknownRuleRef>,
}

/// All the sections read (a mirror of the fields held by Config in config.rs).
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
    pub program_sleep: Option<&'a ProgramSleepSection>,
    pub test_sleep: Option<&'a TestSleepSection>,
}

/// Default extensions (the 3 Hy ones and Python).
fn default_extensions() -> BTreeSet<String> {
    ["hy", "hyk", "hyp", "py"].iter().map(|s| s.to_string()).collect()
}

impl ProjectSettings {
    /// Validates the sections read and turns them into the form the rules read. Name mismatches (a layer not in the order, a role not in the list, an unknown rule ID) are returned as a list of reason sentences.
    pub fn validate(sections: &ProjectSections) -> Result<ProjectSettings, Vec<String>> {
        let mut problems = Vec::new();
        let layers = sections.layers.map(|layers| validate_layers(layers, sections.tags, sections.roles, &mut problems));
        if sections.layers.is_none() {
            if sections.roles.is_some() {
                problems.push("roles: the role rules cannot be applied without layers".to_string());
            }
            if sections.tags.is_some() && sections.definitions.is_none() {
                problems.push("tags: there is no module to read tags from without layers or the definition rules (definitions)".to_string());
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
        let program_sleep = sections.program_sleep.map(|section| ProgramSleepSettings {
            paths: section.paths.iter().map(|p| normalize_dir(p)).collect(),
            exclude: section.exclude.iter().map(|p| normalize_dir(p)).collect(),
            exclude_parts: section.exclude_parts.iter().cloned().collect(),
        });
        let test_sleep = sections.test_sleep.map(|section| TestSleepSettings {
            parts: section.parts.iter().cloned().collect(),
            names: section.names.clone(),
            exclude: section.exclude.iter().map(|p| normalize_dir(p)).collect(),
        });
        let mut unknown_rules: Vec<super::notice::UnknownRuleRef> = Vec::new();
        let python_ids: BTreeSet<String> = crate::rules::get_all_rules().iter().map(|r| r.rule_id().to_string()).collect();
        let layer_names: Vec<String> = layers.as_ref().map(|l| l.layers.iter().map(|s| s.name.clone()).collect()).unwrap_or_default();
        let find_layer = |name: &str, what: &str, problems: &mut Vec<String>| -> Option<LayerId> {
            let found = layer_names.iter().position(|n| n == name).map(LayerId);
            if found.is_none() {
                problems.push(format!("{}: layer {} is not in layers.order", what, name));
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
                problems.push("raw_side_effects: the locations of raw side effects cannot be judged without the layer locations (layers)".to_string());
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
                            problems.push(format!("laws.{}.rules: rule {} is not in doeff-linter", law.name, id));
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
                            problems.push(format!("registry.reconciling: rule {} is not a layer rule (DOEFF101-113)", id));
                        }
                        rule
                    })
                    .collect(),
            })
            .unwrap_or_default();
        let services = sections.services.filter(|s| s.enabled).map(|section| {
            if layers.is_none() {
                problems.push("services: the service boundary cannot be judged without layers".to_string());
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
            // A rule this binary does not know (reported with DOEFF100) cannot be judged as to whether it asks about layers, so it is counted on the side that asks about layers.
            let layered = law.rules.iter().any(|id| match ProjectRule::parse(id) {
                Some(rule) => rule.is_layered(),
                None => super::notice::is_rule_id_shape(id) && !python_ids.contains(&id.to_uppercase()),
            });
            if !layered {
                problems.push(format!("laws.{}.layers: the rules of this law do not ask about layers, so writing layers would never match", law.name));
            }
        }
        if problems.is_empty() {
            Ok(ProjectSettings {
                layers,
                environment,
                raw,
                services,
                definitions,
                program_sleep,
                test_sleep,
                registered_severity: BTreeMap::new(),
                severity: BTreeMap::new(),
                level: BTreeMap::new(),
                smells: None,
                translation: None,
                config_dir: None,
                include: Vec::new(),
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

    /// Severity of a violation — the repo's declaration if any, otherwise the rule's default (`ProjectRule::default_level`), and if neither, from the rule's own weight base.
    pub fn level_of(&self, rule_id: &str, base: crate::models::Severity) -> RuleLevel {
        let id = rule_id.to_uppercase();
        self.level
            .get(&id)
            .copied()
            .or_else(|| ProjectRule::parse(&id).and_then(ProjectRule::default_level))
            .unwrap_or_else(|| RuleLevel::default_for(base))
    }

    /// The law for the violation that rule `rule` emits in a file of layer `layer` (None if none). A rule that does not ask about layers has layer = None.
    pub fn law_for(&self, rule: ProjectRule, layer: Option<LayerId>) -> Option<&LawSpec> {
        self.laws.iter().find(|law| {
            law.rules.contains(&ProjectRuleOrExternal::Project(rule))
                && (law.layers.is_empty() || layer.is_some_and(|l| law.layers.contains(&l)))
        })
    }

    /// The law tied to the ID of an existing Python rule (None if none).
    pub fn law_for_external(&self, id: &str) -> Option<&LawSpec> {
        let wanted = ProjectRuleOrExternal::External(id.to_uppercase());
        self.laws.iter().find(|law| law.rules.contains(&wanted))
    }
}

/// Validates the layers section (order, locations, import rules, roles, how tags are read).
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
            problems.push(format!("layers.order: layer {} appears twice", name));
        }
    }
    let id_of = |name: &str| order.iter().position(|n| n == name).map(LayerId);
    for (table, names) in [
        ("layers.paths", section.paths.keys().cloned().collect::<Vec<_>>()),
        ("layers.allow_imports", section.allow_imports.keys().cloned().collect()),
        ("layers.forbid_modules", section.forbid_modules.keys().cloned().collect()),
        ("layers.types_only", section.types_only.clone()),
        ("layers.wire_free", section.wire_free.clone()),
        ("layers.describe", section.describe.keys().cloned().collect()),
        ("roles.by_layer", roles.map(|r| r.by_layer.keys().cloned().collect()).unwrap_or_default()),
    ] {
        for name in names {
            if id_of(&name).is_none() {
                problems.push(format!("{}: layer {} is not in layers.order", table, name));
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
                    problems.push(format!("layers.paths: layer {} has no location", name));
                    Vec::new()
                }
            };
            let allowed = section.allow_imports.get(name).map(|names| {
                names
                    .iter()
                    .filter_map(|target| {
                        let id = id_of(target);
                        if id.is_none() {
                            problems.push(format!("layers.allow_imports.{}: layer {} is not in layers.order", name, target));
                        }
                        id
                    })
                    .collect()
            });
            let layer_roles = roles.and_then(|r| r.by_layer.get(name)).map(|allowed_roles| {
                for role in allowed_roles {
                    if !role_names.is_empty() && !role_names.contains(role) {
                        problems.push(format!("roles.by_layer.{}: role {} is not in roles.names", name, role));
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
                wire_free: section.wire_free.contains(name),
                roles: layer_roles,
                description: section.describe.get(name).cloned().unwrap_or_default(),
            }
        })
        .collect();
    // Locations without `*` nested in each other do not decide which layer applies (a `*` location takes the one with more levels, so it does not nest).
    let dirs: Vec<(&String, String)> = section
        .paths
        .iter()
        .flat_map(|(name, patterns)| patterns.list().into_iter().map(move |text| (name, normalize_dir(text))))
        .filter(|(_, dir)| !dir.contains('*'))
        .collect();
    for (a, dir_a) in &dirs {
        for (b, dir_b) in &dirs {
            if a != b && !dir_a.is_empty() && dir_b.starts_with(&format!("{}/", dir_a)) {
                problems.push(format!("layers.paths: the location of layer {} is inside the location of layer {} (nested locations do not decide which layer applies)", b, a));
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
