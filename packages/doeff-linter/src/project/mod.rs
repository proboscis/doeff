//! Layer rules (DOEFF101-108) — rules judged from the repo's module list and the settings. They read both Hy and Python files.
//!
//! The substance of the rules comes from the settings (`[tool.doeff-linter.layers]` and others, `settings.rs`); layer names, roles and environment words are
//! not written into Rust. Hy facts are taken from doeff-indexer's hy-index analysis (called as a function) and the reader.
//!
//! Flow: collect the population files -> read facts per file (`facts.rs`) -> make draft violations per rule ->
//! apply laws and registry keys to decide severity (`finish`).

pub mod affected_tests;
pub mod architecture;
pub mod blob_chunks;
pub mod blob_spell;
pub mod bulk_read;
pub mod dir_stamp;
pub mod explain;
pub mod facts_cache;
pub mod fsmonitor;
pub mod git_index;
pub mod facts;
pub mod deferred;
pub mod hy_files;
pub mod macro_reach;
mod module_index;
pub mod module_probe;
pub mod named_modules;
pub mod names;
pub mod param_calls;
pub mod spelled_callers;
pub mod spelled_failures;
pub mod spelling;
pub mod registry;
pub mod notice;
pub mod notice_findings;
pub mod rule;
pub mod bare_calls;
pub mod defn_to_defk;
pub mod semantic;
pub mod smells;
pub mod unreadable;
pub mod settings;
pub mod layers;
pub mod law;
pub mod raw_settings;
pub mod signatures;
pub mod file_view;
pub mod call_view;
pub mod body_view;
pub mod world_catalog;
pub mod test_forms;
pub mod single_point_vocabulary;
pub mod spelling_scope;
pub mod bound_names;
pub mod confined_spellings;
pub mod counted_spellings;
pub mod effect_census;
pub mod field_holders;
pub mod env_branch;
pub mod match_field_names;
pub mod stale_outcome_branches;
pub mod retired;
pub mod blind;
pub mod allowed_heads;
pub mod call_sites;
pub mod broad_catches;
pub mod top_level;
pub mod handler_arguments;
pub mod typed_values;
pub mod record_stubs;
pub mod business_fakes;
pub mod clause_ops;
pub mod intent_coverage;
pub mod python_env;
pub mod paths;
pub mod report;
pub mod spans;
pub mod contract_breach;
pub mod assembly_shape;
pub mod invariants;
pub mod system_access;
pub mod record_waits;
pub mod timed_refetch;
pub mod program_sleep;
pub mod test_sleep;
pub mod system_decls;
pub mod clause_coverage;
pub mod python_reach;

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use doeff_indexer::hy_index::{self, Definition, DefinitionKind, HyFileIndex, RawCatalog, RawCategory, RawSettings, RawStrength};
use rayon::prelude::*;
use walkdir::WalkDir;

use crate::models::Severity;
use crate::position::{first_line_range, LineIndex, Position, Range};
use explain::{Explain, NameSubject, Narrator, Placement};
use facts::{read_facts, ByteSpan, Language, ModuleFacts};
use names::{environment_words_of, hy_mangle, is_upper_name, module_of};
use registry::Registry;
use rule::ProjectRule;
// The definition of the path helpers is in paths.rs. The names read by outside crates (tests) are kept, and modules inside project read paths directly (#2119).
pub use paths::{declared_rel, glob_matches, relative_path};
// The definition of the diagnostic types is in report.rs, and the kinds of DOEFF137 hits are in contract_breach.rs. The names read by outside crates (main.rs, tests) are kept, and modules
// inside project and editor.rs read the defining modules directly (re-reading through mod.rs makes a dependency cycle — #2121).
pub use contract_breach::ContractTestBreach;
pub use report::{Finding, FindingOrigin, ModuleSummary, ProjectReport, Standing};
use paths::segments_match;
use spans::innermost_definition;
use law::LawSpec;
use layers::{LayerId, LayerSettings};
use module_index::{ModuleIndex, Roads};
use settings::{EnvironmentSettings, ProgramSleepSettings, ProjectSettings, TestSleepSettings};

/// Index of module spelling -> location (to resolve import targets to layers and services).
type SiteIndex<'a> = ModuleIndex<'a, ModuleSite>;

/// What to judge — the whole repo, or one file with its pre-save contents.
pub enum Target<'a> {
    /// The whole repo. focus is the path named on the command line (None = everything) — for rules that can be judged from one file (DOEFF150, 151), if something is named,
    /// only the files under it are read and the whole repo is not read (preventing a recurrence of DOEFF133, where a rule that reads everything blocked a commit).
    /// Other rules read everything as before, and the output is narrowed by the named path.
    Whole { focus: Option<&'a [PathBuf]> },
    Single { path: PathBuf, source: &'a str },
}

impl Target<'_> {
    /// Whether this run judges the rule — a whole run judges all enabled rules (except DOEFF166 when a path is named —
    /// its hits may lie outside the named path), and a single-file run judges only those the rule declares judgeable from one file (`ProjectRule::judged_on_one_file`).
    /// A rule that is not judged is not run even if enabled, and is not listed in the report's `judged` (the editor keeps that rule's violations as the whole run's result).
    pub fn judges(&self, rule: ProjectRule) -> bool {
        match self {
            Target::Whole { focus: None } => true,
            Target::Whole { focus: Some(_) } => rule != ProjectRule::RegistryEntryStale,
            Target::Single { .. } => rule.judged_on_one_file(),
        }
    }
}

/// A draft of a violation (before laws and the registry are applied).
struct Draft {
    rule: ProjectRule,
    layer: Option<LayerId>,
    rel: String,
    path: PathBuf,
    range: Range,
    message: String,
    /// Detail after the `<rule>` of the key (None if none).
    detail: Option<String>,
    base: Severity,
    /// Material for the explanation.
    explain: Explain,
}

/// One file of the population.
#[derive(Debug, Clone)]
struct SourceFile {
    rel: String,
    path: PathBuf,
    language: Language,
}

/// One file of a layer's population.
#[derive(Debug, Clone)]
struct LayerFile {
    file: SourceFile,
    module: String,
    site: ModuleSite,
}

/// A module's location — the layer, the actual dir of the matched location, and the name of the service matched by the `*` segment.
#[derive(Debug, Clone, PartialEq, Eq)]
struct ModuleSite {
    layer: LayerId,
    dir: String,
    service: Option<String>,
    /// Whether the layer was inferred from the tag's role rather than the path (a module outside the locations declared in architecture.hy).
    by_tags: bool,
}

/// Runs the layer rules. root is the normalized repo root, enabled is the enabled rules.
pub fn run(root: &Path, settings: &ProjectSettings, enabled: &BTreeSet<ProjectRule>, target: Target) -> ProjectReport {
    run_with(root, settings, enabled, target, &semantic::SemanticMode::CacheOnly)
}

/// Runs the layer rules (how to treat the semantic rules can be chosen — the default `run` only reads the cache).
pub fn run_with(root: &Path, settings: &ProjectSettings, enabled: &BTreeSet<ProjectRule>, target: Target, semantic_mode: &semantic::SemanticMode) -> ProjectReport {
    let semantic_files: Vec<(LayerFile, Option<String>)>;
    let plain_files: Vec<(SourceFile, Option<String>)>;
    // hy-index files (with evidence of raw side effects) — DOEFF119, and DOEFF204, which asks only about classes DOEFF119 emits nothing for, read the same thing.
    let mut indexes: HashMap<String, HyFileIndex> = HashMap::new();
    let mut report = ProjectReport::default();
    // In a single-file run, if the written file has a definition that calls its own argument bare, start in another thread, from the beginning, the repo read that collects the callers' facts
    // (overlapped with the wait on disk for the registry, the effect world, and the table of the named module on a cold worktree).
    let program_params = match &target {
        Target::Single { path, source } if enabled.contains(&ProjectRule::DefkCalledBare) && target.judges(ProjectRule::DefkCalledBare) => {
            match bare_call_population_rel(root, settings, enabled, path) {
                Some(rel) => spelled_callers::SpelledCallers::started(root, enabled, &rel, source),
                None => spelled_callers::SpelledCallers::new(root, enabled),
            }
        }
        _ => spelled_callers::SpelledCallers::new(root, enabled),
    };
    let mut registry = crate::timing::timed("registry", || Registry::load(root, &settings.registry.dirs, &settings.registry.files));
    if !settings.registry.config_files.is_empty() {
        let base = settings.config_dir.clone().unwrap_or_else(|| root.to_path_buf());
        let extra = Registry::load(&base, &[], &settings.registry.config_files);
        registry.keys.extend(extra.keys);
        registry.origins.extend(extra.origins);
        registry.problems.extend(extra.problems);
    }
    report.errors.extend(registry.problems.iter().cloned());
    report.notes.extend(registry.notes.iter().cloned());
    // The rules this run judges (`Target::judges`). A rule that is not judged is not run even if enabled — the unmatched rows of the registry (DOEFF166) are judged only
    // when applied to the whole repo (in a named-files-only or single-file run, its hits may lie outside the range), and a single-file run runs only the rules that declare
    // they are judged from one file.
    let judged_rules: BTreeSet<ProjectRule> = enabled.iter().copied().filter(|rule| target.judges(*rule)).collect();
    // In a whole run that judges DOEFF166, the rules that the registry keys name are applied in the same run even if not in enable (lent) — the hits are used only
    // for the judgment of 166 and removed before reporting. If only a partial list containing 166 was passed to enable, the named rules did not run and 166 silently found 0
    // (the miss of #1992).
    let lent: BTreeSet<ProjectRule> = if judged_rules.contains(&ProjectRule::RegistryEntryStale) {
        rules_named_by_registry(&registry, settings).difference(&judged_rules).copied().collect()
    } else {
        BTreeSet::new()
    };
    let widened: BTreeSet<ProjectRule> = judged_rules.union(&lent).copied().collect();
    let enabled = &widened;
    let raw = raw_settings(root, settings, &mut report.errors);
    let mut semantic_probes: Vec<SemanticProbe> = Vec::new();
    let wants_raw = enabled.contains(&ProjectRule::RawSideEffectDirect)
        || enabled.contains(&ProjectRule::RawSideEffectVia)
        || enabled.contains(&ProjectRule::WorldHandlerNamedOutsideList)
        || enabled.contains(&ProjectRule::WorldHandlerMisplaced);
    let mut drafts = Vec::new();

    // Material for the notice of unreadable Hy files (DOEFF128) — for a whole run, all of the repo's Hy files; for a single file, its pre-save contents.
    let unreadable_target: Option<(PathBuf, String)> = match &target {
        Target::Whole { .. } => None,
        Target::Single { path, source } => Some((path.clone(), source.to_string())),
    };
    match target {
        Target::Whole { focus } => {
            if let Some(architecture) = &settings.architecture {
                let (found, errors) = crate::timing::timed("retired", || judge_retired_files(root, architecture, enabled, &raw.catalog, focus));
                drafts.extend(found);
                report.errors.extend(errors);
                // DOEFF141 reads only the files of the declared definitions' modules and of the modules reached (small regardless of what is named — it does not read the whole repo).
                if enabled.contains(&ProjectRule::BlindDefinitionReads) && !architecture.blind_definitions.is_empty() {
                    let architecture_rel = declared_rel(root, &architecture.path);
                    let (found, errors) =
                        crate::timing::timed("blind", || blind::find(root, &architecture.blind_definitions, &raw, &architecture_rel));
                    drafts.extend(found.into_iter().map(|found| Draft {
                        rule: ProjectRule::BlindDefinitionReads,
                        layer: None,
                        path: root.join(&found.rel),
                        message: format!(
                            "{} — {}",
                            found.rel,
                            match &found.problem {
                                blind::BlindProblem::ReadsWord { reached, word } => {
                                    format!("The definition {1} reached from {0} reads the word {2}, which is outside the decided material", found.declared, reached, word)
                                }
                                blind::BlindProblem::Imports { module } => format!("The module of {} imports {}", found.declared, module),
                                blind::BlindProblem::Missing { reason } => format!("The declared definition {} is missing — {}", found.declared, reason),
                            }
                        ),
                        detail: Some(found.detail),
                        base: Severity::Error,
                        explain: Explain::BlindDefinition { declared: found.declared, why: found.why, problem: found.problem },
                        range: found.range,
                        rel: found.rel,
                    }));
                    report.errors.extend(errors);
                }
                // DOEFF147 reads only the file of the declared definition's module (small regardless of what is named — it does not read the whole repo).
                if enabled.contains(&ProjectRule::DefinitionCallsUnlistedHead) && !architecture.allowed_heads.is_empty() {
                    let architecture_rel = declared_rel(root, &architecture.path);
                    let (found, errors) =
                        crate::timing::timed("allowed-heads", || allowed_heads::find(root, &architecture.allowed_heads, &architecture_rel));
                    drafts.extend(found.into_iter().map(|found| Draft {
                        rule: ProjectRule::DefinitionCallsUnlistedHead,
                        layer: None,
                        path: root.join(&found.rel),
                        message: format!(
                            "{} — {}",
                            found.rel,
                            match &found.problem {
                                allowed_heads::HeadProblem::Unlisted { head } => {
                                    format!("{} calls ({} …), which is outside the list of heads it may call", found.declared, head)
                                }
                                allowed_heads::HeadProblem::Missing { reason } => format!("The declared definition {} is missing — {}", found.declared, reason),
                            }
                        ),
                        detail: Some(found.detail),
                        base: Severity::Error,
                        explain: Explain::AllowedHeads { declared: found.declared, why: found.why, problem: found.problem },
                        range: found.range,
                        rel: found.rel,
                    }));
                    report.errors.extend(errors);
                }
                // DOEFF148 walks only the head dirs of the groups' :files globs (it does not read the whole repo).
                if enabled.contains(&ProjectRule::SpellingOutsideItsFiles) && !architecture.confined_spellings.is_empty() {
                    let (found, errors) = crate::timing::timed("confined-spellings", || {
                        confined_spellings::find(root, &architecture.confined_spellings, |file| declared_rel(root, file), focus)
                    });
                    drafts.extend(found.into_iter().map(|found| {
                        let (message, detail) = match &found.problem {
                            confined_spellings::ConfinedProblem::Outside { count } => (
                                match &found.first {
                                    Some(first) => format!("{} — spelling group {} appears in {} occurrence(s) outside the files allowed to write it (first match {})", found.rel, found.group, count, first),
                                    None => format!("{} — spelling group {} appears in {} occurrence(s) outside the files allowed to write it", found.rel, found.group, count),
                                },
                                found.group.clone(),
                            ),
                            confined_spellings::ConfinedProblem::Missing => (
                                format!("{} — no file matches :files of spelling group {}", found.rel, found.group),
                                format!("{}:missing", found.group),
                            ),
                        };
                        Draft {
                            rule: ProjectRule::SpellingOutsideItsFiles,
                            layer: None,
                            path: root.join(&found.rel),
                            message,
                            detail: Some(detail),
                            base: Severity::Error,
                            explain: Explain::ConfinedSpelling { group: found.group, why: found.why, problem: found.problem, bound: found.bound },
                            range: found.range,
                            rel: found.rel,
                        }
                    }));
                    report.errors.extend(errors);
                }
                // DOEFF161 also walks only the head dirs of the declaration's :files globs (it does not read the whole repo).
                if enabled.contains(&ProjectRule::SpellingCountDiffers) && !architecture.counted_spellings.is_empty() {
                    let (found, errors) = crate::timing::timed("counted-spellings", || {
                        counted_spellings::find(root, &architecture.counted_spellings, |file| declared_rel(root, file))
                    });
                    drafts.extend(found.into_iter().map(|found| {
                        let message = match &found.problem {
                            counted_spellings::CountProblem::Mismatch { found: count, wanted, within: Some(name) } => format!(
                                "{} — counted spelling {} appears {} occurrence(s) inside definition {} ({} expected)",
                                found.rel,
                                found.group,
                                name,
                                count,
                                wanted.spelling()
                            ),
                            counted_spellings::CountProblem::Mismatch { found: count, wanted, within: None } => {
                                format!("{} — counted spelling {} appears {} occurrence(s) ({} expected)", found.rel, found.group, count, wanted.spelling())
                            }
                            counted_spellings::CountProblem::Missing { reason } => {
                                format!("{} — counted spelling {} has nothing to count — {}", found.rel, found.group, reason)
                            }
                        };
                        Draft {
                            rule: ProjectRule::SpellingCountDiffers,
                            layer: None,
                            path: root.join(&found.rel),
                            message,
                            detail: Some(found.detail),
                            base: Severity::Error,
                            explain: Explain::CountedSpelling { group: found.group, why: found.why, problem: found.problem },
                            range: found.range,
                            rel: found.rel,
                        }
                    }));
                    report.errors.extend(errors);
                }
                // DOEFF162 reads only the files of the declaration's :files (it does not read the whole repo).
                if enabled.contains(&ProjectRule::EffectOutsideCensus) && !architecture.effect_census.is_empty() {
                    let architecture_rel = declared_rel(root, &architecture.path);
                    let (found, errors) =
                        crate::timing::timed("effect-census", || effect_census::find(root, &architecture.effect_census, &architecture_rel));
                    drafts.extend(found.into_iter().map(|found| {
                        let what = match &found.problem {
                            effect_census::CensusProblem::Unlisted { effect } => format!("effect {} is declared outside list {}", effect, found.group),
                            effect_census::CensusProblem::Twice { effect } => format!("effect {} is declared twice (list {})", effect, found.group),
                            effect_census::CensusProblem::Undeclared { effect } => format!("effect {} of list {} has no declaration", effect, found.group),
                            effect_census::CensusProblem::NoFiles => format!("no file matches :files of list {}", found.group),
                        };
                        Draft {
                            rule: ProjectRule::EffectOutsideCensus,
                            layer: None,
                            path: root.join(&found.rel),
                            message: format!("{} — {}", found.rel, what),
                            detail: Some(found.detail),
                            base: Severity::Error,
                            explain: Explain::EffectCensus { group: found.group, why: found.why, problem: found.problem },
                            range: found.range,
                            rel: found.rel,
                        }
                    }));
                    report.errors.extend(errors);
                }
                // DOEFF149 also reads only the files of the declaration's :files (it does not read the whole repo).
                if enabled.contains(&ProjectRule::FieldHoldersDiffer) && !architecture.field_holders.is_empty() {
                    let architecture_rel = declared_rel(root, &architecture.path);
                    let (found, errors) =
                        crate::timing::timed("field-holders", || field_holders::find(root, &architecture.field_holders, &architecture_rel));
                    drafts.extend(found.into_iter().map(|found| {
                        let what = match &found.problem {
                            field_holders::HolderProblem::Unlisted { class } => {
                                format!("class {} has fields of {} (outside holder list {})", class, found.type_name, found.group)
                            }
                            field_holders::HolderProblem::Absent { class } => {
                                format!("class {} of holder list {} does not have fields of {}", found.group, class, found.type_name)
                            }
                            field_holders::HolderProblem::NoClass { class } => format!("class {} named by holder list {} is missing", class, found.group),
                            field_holders::HolderProblem::NoFiles => format!("no Python file matches :files of holder list {}", found.group),
                        };
                        Draft {
                            rule: ProjectRule::FieldHoldersDiffer,
                            layer: None,
                            path: root.join(&found.rel),
                            message: format!("{} — {}", found.rel, what),
                            detail: Some(found.detail),
                            base: Severity::Error,
                            explain: Explain::FieldHolders { group: found.group, type_name: found.type_name, why: found.why, problem: found.problem },
                            range: found.range,
                            rel: found.rel,
                        }
                    }));
                    report.errors.extend(errors);
                }
                // DOEFF159 walks only the head dirs of the :files globs taken literally (small regardless of what is named — it does not walk the whole repo).
                if enabled.contains(&ProjectRule::CallOutsideDeclaredSites) && !architecture.call_sites.is_empty() {
                    let architecture_rel = declared_rel(root, &architecture.path);
                    let found = crate::timing::timed("call-sites", || call_sites::find(root, &architecture.call_sites, &architecture_rel));
                    drafts.extend(found.into_iter().map(|found| Draft {
                        rule: ProjectRule::CallOutsideDeclaredSites,
                        layer: None,
                        path: root.join(&found.rel),
                        message: format!("{} — {}", found.rel, call_sites::describe(&found.head, &found.problem)),
                        detail: Some(found.detail),
                        base: Severity::Error,
                        explain: Explain::CallSites { head: found.head, why: found.why, problem: found.problem },
                        range: found.range,
                        rel: found.rel,
                    }));
                }
                // DOEFF160 also walks only the head dirs of the :files globs (it does not walk the whole repo).
                if enabled.contains(&ProjectRule::BroadCatchOutsideCarrier) && !architecture.broad_catches.is_empty() {
                    let architecture_rel = declared_rel(root, &architecture.path);
                    let (found, errors) =
                        crate::timing::timed("broad-catches", || broad_catches::find(root, &architecture.broad_catches, &architecture_rel));
                    drafts.extend(found.into_iter().map(|found| Draft {
                        rule: ProjectRule::BroadCatchOutsideCarrier,
                        layer: None,
                        path: root.join(&found.rel),
                        message: format!("{} — {}", found.rel, broad_catches::describe(&found.group, &found.problem)),
                        detail: Some(found.detail),
                        base: Severity::Error,
                        explain: Explain::BroadCatches { group: found.group, why: found.why, problem: found.problem },
                        range: found.range,
                        rel: found.rel,
                    }));
                    report.errors.extend(errors);
                }
                // DOEFF144 and 145 are also judged from one file each (if something is named, only files under it are read — the index of the whole repo is not built).
                if let Some(selection) = architecture.typed_values.as_ref().filter(|_| enabled.contains(&ProjectRule::UntypedStructuredValue)) {
                    let (found, errors) = crate::timing::timed("typed-values", || typed_values::find(root, selection, focus));
                    drafts.extend(found.into_iter().flat_map(|file| typed_value_drafts(&file.rel, &file.path, &file.source, file.hits)));
                    report.errors.extend(errors);
                }
                if let Some(selection) = architecture.record_stubs.as_ref().filter(|_| enabled.contains(&ProjectRule::RecordStubNotKwOnly)) {
                    let (found, errors) = crate::timing::timed("record-stubs", || record_stubs::find(root, selection, focus));
                    drafts.extend(found.into_iter().flat_map(|file| record_stub_drafts(&file.rel, &file.path, &file.source, file.mismatches)));
                    report.errors.extend(errors);
                }
            }
            let layer_files = settings.layers.as_ref().map(|layers| collect_layer_files(root, layers)).unwrap_or_default();
            semantic_files = layer_files.iter().map(|f| (f.clone(), None)).collect();
            let wants_plain = settings.semantic.as_ref().is_some_and(|s| s.plain_callable.is_some() || s.class_role.is_some() || s.mixed_concerns.is_some());
            plain_files = match (&settings.definitions, wants_plain) {
                (Some(definitions), true) => hy_files::collect(root)
                    .into_iter()
                    .filter_map(|path| {
                        let rel = relative_path(root, &path)?;
                        is_definition_file(&rel, definitions).then_some((SourceFile { rel, path, language: Language::Hy }, None))
                    })
                    .collect(),
                _ => Vec::new(),
            };
            // In a named run, when every enabled rule is decided by one file, read only the named files (file_local_scope).
            let scope = file_local_scope(focus, enabled);
            let env_files: Vec<SourceFile> = settings
                .environment
                .as_ref()
                .map(|env| collect_environment_files(root, env))
                .unwrap_or_default()
                .into_iter()
                .filter(|file| in_scope(&file.path, scope))
                .collect();
            indexes = crate::timing::timed("hy-index", || whole_hy_index(root, settings, enabled, &raw, &layer_files, &env_files, wants_raw, scope));
            let hy = &indexes;
            let effect_world = crate::timing::timed("effect-world", || effect_world_for(root, settings, enabled, None));
            if let Some(layers) = &settings.layers {
                let index = SiteIndex::whole(layer_files.iter().map(|f| (f.module.clone(), f.site.clone())));
                let judged: Vec<LayerJudgement> = crate::timing::timed("layer-judge", || layer_files
                    .par_iter()
                    .filter(|file| in_scope(&file.file.path, scope))
                    .map(|file| match std::fs::read_to_string(&file.file.path) {
                        Ok(source) => judge_layer_file(file, &source, layers, settings, enabled, &index, hy.get(&file.file.rel)),
                        Err(error) => LayerJudgement { errors: vec![format!("{}: cannot read: {}", file.file.rel, error)], ..LayerJudgement::default() },
                    })
                    .collect());
                let mut crossings: BTreeSet<(String, String)> = BTreeSet::new();
                for judged in judged {
                    drafts.extend(judged.drafts);
                    report.modules.extend(judged.summary);
                    report.errors.extend(judged.errors);
                    crossings.extend(judged.crossings);
                }
                if let (Some(translation), Some(world)) = (&settings.translation, &effect_world) {
                    if let Some(decl) = lower_layer_decl(settings).filter(|_| enabled.contains(&ProjectRule::LowerLayerInDomain)) {
                        let judged: Vec<Result<Vec<Draft>, String>> = layer_files
                            .par_iter()
                            .filter(|file| is_domain_file(file, translation))
                            .map(|file| {
                                std::fs::read_to_string(&file.file.path)
                                    .map(|source| judge_lower_layer_uses(file, &source, layers, translation, decl, world))
                                    .map_err(|error| format!("{}: cannot read: {}", file.file.rel, error))
                            })
                            .collect();
                        for result in judged {
                            match result {
                                Ok(found) => drafts.extend(found),
                                Err(error) => report.errors.push(error),
                            }
                        }
                    }
                    if enabled.contains(&ProjectRule::TranslationEmitsIntent) {
                        let judged: Vec<Result<Vec<Draft>, String>> = layer_files
                            .par_iter()
                            .filter(|file| is_translation_file(file, translation))
                            .map(|file| {
                                std::fs::read_to_string(&file.file.path)
                                    .map(|source| judge_translation_intents(file, &source, layers, translation, &index, world))
                                    .map_err(|error| format!("{}: cannot read: {}", file.file.rel, error))
                            })
                            .collect();
                        for result in judged {
                            match result {
                                Ok(found) => drafts.extend(found),
                                Err(error) => report.errors.push(error),
                            }
                        }
                    }
                }
                if let Some(architecture) = &settings.architecture {
                    if enabled.contains(&ProjectRule::UnusedDependency) {
                        drafts.extend(judge_unused_dependencies(root, architecture, &crossings));
                    }
                    if enabled.contains(&ProjectRule::WorldHandlerMisplaced) && !architecture.world_handlers.is_empty() {
                        drafts.extend(crate::timing::timed("world-handler-places", || judge_world_handler_places(root, architecture, layers, &layer_files, hy)));
                    }
                    if enabled.contains(&ProjectRule::TestKindMismatch) && !architecture.world_handlers.is_empty() {
                        let (found, errors) = judge_test_kinds(root, architecture, hy, &raw.catalog);
                        drafts.extend(found);
                        report.errors.extend(errors);
                    }
                    if enabled.contains(&ProjectRule::WorldHandlerWithoutContractTest)
                        && (!architecture.world_handlers.is_empty() || !architecture.stand_ins.is_empty())
                    {
                        drafts.extend(crate::timing::timed("contract-tests", || judge_contract_tests(root, architecture, hy)));
                    }
                    let fake_rules = [
                        ProjectRule::BusinessEffectFake,
                        ProjectRule::StaleCounterexampleRow,
                        ProjectRule::TestOnlyFake,
                        ProjectRule::IntentAnswererNotTranslation,
                        ProjectRule::ServiceWithoutCounterexample,
                        ProjectRule::ClauseWithoutCounterexample,
                        ProjectRule::IntentEffectUncovered,
                        ProjectRule::IntentFakedInVerification,
                    ];
                    if let Some(decl) = architecture.business_fakes.as_ref().filter(|_| fake_rules.iter().any(|r| enabled.contains(r))) {
                        let judged = crate::timing::timed("business-fakes", || {
                            judge_business_fakes(root, architecture, Some(layers), decl, architecture.assembly_shape.as_ref(), hy, enabled)
                        });
                        drafts.extend(judged.drafts);
                        report.errors.extend(judged.problems);
                        report.notes.extend(judged.notes);
                    }
                    if let (Some(decl), Some(shape)) = (architecture.business_fakes.as_ref(), architecture.assembly_shape.as_ref()) {
                        if enabled.contains(&ProjectRule::AssemblyShapeBroken) || enabled.contains(&ProjectRule::AssemblyAnswerMisplaced) {
                            let (found, problems) =
                                crate::timing::timed("assembly-shape", || judge_assembly_shape(root, architecture, Some(layers), decl, shape, hy, enabled));
                            drafts.extend(found);
                            report.errors.extend(problems);
                        }
                    }
                    if enabled.contains(&ProjectRule::ServiceUntestedOnSim) {
                        drafts.extend(judge_untested_services(root, architecture, hy).into_iter().map(|d| Draft { path: root.join(&d.rel), ..d }));
                    }
                    if enabled.contains(&ProjectRule::ServiceInvariantsMissing) {
                        drafts.extend(judge_service_invariants(root, architecture, hy));
                    }
                    if enabled.contains(&ProjectRule::ServiceSystemMissing) {
                        drafts.extend(judge_service_systems(root, architecture, hy));
                    }
                    if enabled.contains(&ProjectRule::SystemAccessUnwritten) {
                        drafts.extend(judge_system_access(root, architecture, hy));
                    }
                    if let Some(decl) = architecture.business_fakes.as_ref().filter(|_| enabled.contains(&ProjectRule::RecordChangeWakesJob)) {
                        let (found, problems) = crate::timing::timed("record-waits", || judge_record_waits(root, decl));
                        drafts.extend(found);
                        report.errors.extend(problems);
                    }
                    if let Some(decl) = architecture.business_fakes.as_ref().filter(|_| enabled.contains(&ProjectRule::PollingOnTimer)) {
                        let (found, problems) = crate::timing::timed("timed-refetch", || judge_timed_refetch(root, decl));
                        drafts.extend(found);
                        report.errors.extend(problems);
                    }
                    if let Some(raw) = settings.raw.as_ref().filter(|r| r.world_modules.is_some()) {
                        let placed: BTreeSet<&str> = layer_files.iter().map(|f| f.file.rel.as_str()).collect();
                        drafts.extend(crate::timing::timed("unplaced-world", || judge_unplaced_world(root, architecture, raw, &placed, hy, enabled)));
                    }
                    if let Some(forms) = architecture.test_forms.as_ref().filter(|_| enabled.contains(&ProjectRule::TestFormNotDeftest)) {
                        drafts.extend(test_forms::find(root, forms).into_iter().map(|found| {
                            let start = Position { line: found.line, character: 0 };
                            Draft {
                                rule: ProjectRule::TestFormNotDeftest,
                                layer: None,
                                path: root.join(&found.rel),
                                rel: found.rel.clone(),
                                range: Range { start, end: start },
                                message: format!("{} — test form {} ({}) — tests must be deftest only", found.rel, found.form, found.detail),
                                detail: Some(found.form.to_string()),
                                base: Severity::Error,
                                explain: Explain::TestFormNotDeftest { form: found.form, detail: found.detail },
                            }
                        }));
                    }
                    if !architecture.single_point_vocabulary.is_empty() && enabled.contains(&ProjectRule::VocabularyOutsideSinglePoint) {
                        drafts.extend(single_point_vocabulary::find(root, &architecture.single_point_vocabulary, focus).into_iter().map(|hit| {
                            let start = Position { line: hit.line, character: 0 };
                            Draft {
                                rule: ProjectRule::VocabularyOutsideSinglePoint,
                                layer: None,
                                path: root.join(&hit.rel),
                                rel: hit.rel.clone(),
                                range: Range { start, end: start },
                                message: format!("{} — vocabulary {} appears on {} lines outside the single point of judgment (#146) — {}", hit.rel, hit.group, hit.count, hit.instead),
                                detail: Some(hit.group.clone()),
                                base: Severity::Error,
                                explain: Explain::VocabularyOutsideSinglePoint { group: hit.group, count: hit.count, instead: hit.instead },
                            }
                        }));
                    }
                }
            }
            if let (Some(architecture), Some(layers)) = (&settings.architecture, &settings.layers) {
                if enabled.contains(&ProjectRule::UndeclaredPlace) || enabled.contains(&ProjectRule::UndeclaredDirectory) {
                    let files = collect_architecture_files(root, architecture, layers);
                    drafts.extend(crate::timing::timed("places", || judge_places(root, architecture, layers, &files, enabled, PlaceScope::Whole, None)));
                }
            }
            if let Some(definitions) = &settings.definitions {
                if wants_definitions(enabled) {
                    let failure = crate::timing::timed("failure-types", || failure_types_for(root, enabled, &definitions.tags));
                    let defks = crate::timing::timed("defk-names", || defk_names_for(root, enabled));
                    let program_params = crate::timing::timed("program-params", || program_params_for(root, enabled, &defks));
                    let program_fields = crate::timing::timed("program-fields", || program_fields_for(root, enabled));
                    // The range walked (under the root plus the settings' include — also test dirs outside the root).
                    let files: Vec<SourceFile> = hy_files::walked(root, &settings.include)
                        .into_iter()
                        .filter_map(|(rel, path)| {
                            (is_definition_file(&rel, definitions) || is_test_file(&rel, definitions))
                                .then_some(SourceFile { rel, path, language: Language::Hy })
                        })
                        // If something is named, judge only the files under it — the judgment is decided by one file (and the whole-repo tables), and the output is narrowed by the named path
                        // (the same predicate as main's project_results). Judging everything used 2.4 seconds of CPU in a one-file commit hook.
                        .filter(|file| focus.is_none_or(|only| only.iter().any(|p| file.path.starts_with(p))))
                        .collect();
                    let judged: Vec<Result<Vec<Draft>, String>> = crate::timing::timed("definitions-judge", || files
                        .par_iter()
                        .map(|file| {
                            std::fs::read_to_string(&file.path)
                                .map(|source| {
                                    let mut found = judge_definitions(file, &source, definitions, enabled, plain_callable_reasons(settings), hy.get(&file.rel));
                                    found.extend(judge_smells(file, &source, settings, definitions, enabled, &failure));
                                    found.extend(judge_stale_branches(file, &source, definitions, enabled, &failure));
                                    // A repo with no defk matches neither bare calls nor argument calls (the set is empty — a single-file run has a set of only the named modules,
                                    // so even if empty, other modules may have defks. Only a whole run is excluded here).
                                    if !defks.is_empty() {
                                        found.extend(judge_bare_calls(file, &source, definitions, enabled, &defks, &program_params, &program_fields));
                                    }
                                    if enabled.contains(&ProjectRule::EffectsDisagreeWithInference) {
                                        found.extend(judge_effect_mismatches(file, &source, definitions, effect_world.as_ref()));
                                    }
                                    without_exempted(found, &layer_exemption(&file.path))
                                })
                                .map_err(|error| format!("{}: cannot read: {}", file.rel, error))
                        })
                        .collect());
                    for result in judged {
                        match result {
                            Ok(found) => drafts.extend(found),
                            Err(error) => report.errors.push(error),
                        }
                    }
                }
            }
            if let Some(architecture) = &settings.architecture {
                if enabled.contains(&ProjectRule::JsonValueOutsideWire) {
                    let judged: Vec<Result<Option<Draft>, String>> = collect_json_value_files(root)
                        .par_iter()
                        .map(|file| {
                            std::fs::read_to_string(&file.path)
                                .map(|source| judge_json_value(file, &source, architecture, settings.layers.as_ref()))
                                .map_err(|error| format!("{}: cannot read: {}", file.rel, error))
                        })
                        .collect();
                    for result in judged {
                        match result {
                            Ok(found) => drafts.extend(found),
                            Err(error) => report.errors.push(error),
                        }
                    }
                }
            }
            if let Some(decl) = settings.architecture.as_ref().and_then(|a| a.handler_arguments.as_ref()).filter(|_| enabled.contains(&ProjectRule::HandlerArgumentHoldsState)) {
                let classes = crate::timing::timed("handler-argument-classes", || handler_arguments::class_index(root, None));
                let judged: Vec<Result<Vec<Draft>, String>> = handler_arguments::population(root, decl)
                    .par_iter()
                    .map(|(rel, path)| {
                        std::fs::read_to_string(path)
                            .map(|source| judge_handler_arguments(root, rel, &source, decl, &classes))
                            .map_err(|error| format!("{}: cannot read: {}", rel, error))
                    })
                    .collect();
                for result in judged {
                    match result {
                        Ok(found) => drafts.extend(found),
                        Err(error) => report.errors.push(error),
                    }
                }
            }
            if let Some(env) = &settings.environment {
                if enabled.contains(&ProjectRule::EnvironmentName) {
                    for file in &env_files {
                        match std::fs::read_to_string(&file.path) {
                            Ok(source) => drafts.extend(judge_environment_names(file, &source, env, hy.get(&file.rel))),
                            Err(error) => report.errors.push(format!("{}: cannot read: {}", file.rel, error)),
                        }
                    }
                }
            }
            if let Some(places) = settings.program_sleep.as_ref().filter(|_| enabled.contains(&ProjectRule::ProgramSleepsOnWallClock)) {
                let walk = SleepWalk { root, include: &settings.include, focus, label: "program-sleep" };
                for result in walk.judge(|rel| is_program_sleep_file(rel, places), judge_program_sleep) {
                    match result {
                        Ok(found) => drafts.extend(found),
                        Err(error) => report.errors.push(error),
                    }
                }
            }
            if let Some(places) = settings.test_sleep.as_ref().filter(|_| enabled.contains(&ProjectRule::TestSleepsOnWallClock)) {
                let walk = SleepWalk { root, include: &settings.include, focus, label: "test-sleep" };
                for result in walk.judge(|rel| is_test_sleep_file(rel, places), judge_test_sleep) {
                    match result {
                        Ok(found) => drafts.extend(found),
                        Err(error) => report.errors.push(error),
                    }
                }
            }
        }
        Target::Single { path, source } => {
            let rel = relative_path(root, &path);
            plain_files = match (&settings.definitions, &rel) {
                (Some(definitions), Some(rel)) if language_of(&path) == Some(Language::Hy) && is_definition_file(rel, definitions) => {
                    vec![(SourceFile { rel: rel.clone(), path: root.join(rel), language: Language::Hy }, Some(source.to_string()))]
                }
                _ => Vec::new(),
            };
            semantic_files = match (&settings.layers, &rel) {
                (Some(layers), Some(rel)) => classify_layer_file(rel, layers)
                    .or_else(|| infer_layer_site(rel, source, layers))
                    .map(|(site, language)| {
                        let file = LayerFile { file: SourceFile { rel: rel.clone(), path: root.join(rel), language }, module: module_of(rel), site };
                        vec![(file, Some(source.to_string()))]
                    })
                    .unwrap_or_default(),
                _ => Vec::new(),
            };
            // Files inside the root give "root + path from the root", as in a whole run (to align the keys by which the editor replaces results).
            let path = rel.as_ref().map(|r| root.join(r)).unwrap_or(path);
            let wants_index = wants_raw
                || enabled.contains(&ProjectRule::EnvironmentName)
                || enabled.contains(&ProjectRule::ClassWithBehaviour)
                || enabled.contains(&ProjectRule::SemanticClassRole);
            let hy_file = match (language_of(&path), wants_index) {
                (Some(Language::Hy), true) => crate::timing::timed("single.index", || hy_index::index_stdin_source(root, &path, source, &raw).files.into_iter().next()),
                _ => None,
            };
            if let (Some(index), Some(rel)) = (&hy_file, &rel) {
                indexes.insert(rel.clone(), index.clone());
            }
            let effect_world = crate::timing::timed("effect-world", || rel.as_ref().and_then(|rel| effect_world_for(root, settings, enabled, Some((rel.as_str(), source)))));
            if let (Some(layers), Some(rel)) = (&settings.layers, &rel) {
                if let Some((site, language)) = classify_layer_file(rel, layers).or_else(|| infer_layer_site(rel, source, layers)) {
                    let file = LayerFile { file: SourceFile { rel: rel.clone(), path: path.clone(), language }, module: module_of(rel), site };
            // For import targets, judge only the modules the written file looks up (do not build a table that lines up all layer modules and infers the layer of files outside the locations from their contents).
                    let index = named_site_index(root, layers, &file);
                    let judged = crate::timing::timed("single.layer-judge", || judge_layer_file(&file, source, layers, settings, enabled, &index, hy_file.as_ref()));
                    drafts.extend(judged.drafts);
                    if let (Some(translation), Some(world)) = (&settings.translation, &effect_world) {
                        if enabled.contains(&ProjectRule::TranslationEmitsIntent) && is_translation_file(&file, translation) {
                            drafts.extend(judge_translation_intents(&file, source, layers, translation, &index, world));
                        }
                        if let Some(decl) = lower_layer_decl(settings).filter(|_| enabled.contains(&ProjectRule::LowerLayerInDomain) && is_domain_file(&file, translation)) {
                            drafts.extend(judge_lower_layer_uses(&file, source, layers, translation, decl, world));
                        }
                    }
                    report.modules.extend(judged.summary);
                    report.errors.extend(judged.errors);
                }
            }
            // Also apply the allowlist rules (DOEFF106, 131) to Hy files outside the layer locations — the same judgment as the whole run's judge_unplaced_world,
            // for this one file (because a single-file run declares it judges these two). The index is only this file's, built above.
            if let (Some(architecture), Some(layers), Some(raw_spec), Some(rel)) =
                (&settings.architecture, &settings.layers, settings.raw.as_ref().filter(|r| r.world_modules.is_some()), &rel)
            {
                if classify_layer_file(rel, layers).or_else(|| infer_layer_site(rel, source, layers)).is_none() {
                    drafts.extend(judge_unplaced_world(root, architecture, raw_spec, &BTreeSet::new(), &indexes, enabled));
                }
            }
            if let (Some(architecture), Some(layers), Some(rel)) = (&settings.architecture, &settings.layers, &rel) {
                if is_architecture_file(rel, architecture, layers) {
                    let file = SourceFile { rel: rel.clone(), path: path.clone(), language: language_of(&path).unwrap_or(Language::Hy) };
                    drafts.extend(judge_places(root, architecture, layers, &[file], enabled, PlaceScope::Single, Some(source)));
                }
            }
            // The names of the definition-style rules are looked up by the walked range — even outside the root, a file under the settings' include dir has a relative path containing `..` (the same name as the whole run's
            // hy_files::walked).
            let definition_rel = rel.clone().or_else(|| hy_files::walked_rel(root, &settings.include, &path));
            if let (Some(definitions), Some(rel)) = (&settings.definitions, &definition_rel) {
                if wants_definitions(enabled)
                    && language_of(&path) == Some(Language::Hy)
                    && (is_definition_file(rel, definitions) || is_test_file(rel, definitions))
                {
                    let file = SourceFile { rel: rel.clone(), path: path.clone(), language: Language::Hy };
                    let mut found = crate::timing::timed("single.definitions", || judge_definitions(&file, source, definitions, enabled, plain_callable_reasons(settings), hy_file.as_ref()));
                    // The repo's facts are built only from the files of modules the written file names (defk names, record fields — looked up by the name including the module, and
                    // the looked-up name is the written file's symbol resolved). Reverse dependencies — failure types (declared by a defeffect in another file) and Programs passed as arguments
                    // (passed by callers in other files) — are built only when looked up, and only from files that contain the looked-up name literally (`SpelledFailures`, `SpelledCallers` —
                    // both the declaration and the caller must contain the spelling of the looked-up name). A class's frozen-ness is decided by whether a class of the same name exists anywhere in the repo, so it is built from the whole repo when looked up (`Deferred`).
                    let (named_files, named_py_files) = if enabled.contains(&ProjectRule::DefkCalledBare) {
                        let known = hy_files::by_module(root);
                        let hy = named_modules::files_of(&known, &named_modules::of_file(&known, &file.rel, source), None);
                        (hy, named_python_files(root, &file.rel, source))
                    } else {
                        (Vec::new(), Vec::new())
                    };
                    let failure = spelled_failures::SpelledFailures::new(root, enabled, &definitions.tags);
                    found.extend(crate::timing::timed("single.smells", || judge_smells(&file, source, settings, definitions, enabled, &failure)));
                    found.extend(crate::timing::timed("single.stale-branches", || judge_stale_branches(&file, source, definitions, enabled, &failure)));
                    let defks = crate::timing::timed("defk-names", || bare_calls::defk_names_over(root, enabled, facts_cache::Reach::Named, &named_files));
                    let program_fields = crate::timing::timed("program-fields", || {
                        let files: Vec<(String, PathBuf)> = named_files.iter().chain(&named_py_files).cloned().collect();
                        program_fields_over(root, enabled, facts_cache::Reach::Named, &files)
                    });
                    found.extend(crate::timing::timed("single.bare-calls", || judge_bare_calls(&file, source, definitions, enabled, &defks, &program_params, &program_fields)));
                    if enabled.contains(&ProjectRule::EffectsDisagreeWithInference) {
                        found.extend(crate::timing::timed("single.effect-mismatches", || judge_effect_mismatches(&file, source, definitions, effect_world.as_ref())));
                    }
                    // At the same single point as a whole run, drop the definition-style rules for definitions that the layer declaration excluded from the named module (#2913).
                    drafts.extend(without_exempted(found, &layer_exemption(&file.path)));
                }
            }
            if let (Some(architecture), Some(rel), Some(language)) = (&settings.architecture, &rel, language_of(&path)) {
                if enabled.contains(&ProjectRule::JsonValueOutsideWire) && is_json_value_file(rel) {
                    let file = SourceFile { rel: rel.clone(), path: path.clone(), language };
                    drafts.extend(judge_json_value(&file, source, architecture, settings.layers.as_ref()));
                }
            }
            if let (Some(architecture), Some(rel)) = (&settings.architecture, &rel) {
                let (words, calls) = retired_groups(architecture, enabled);
                let parts = retired::PartCalls::new(calls, &architecture.boundary_touches(), &raw.catalog);
                let (word_hits, call_hits) = crate::timing::timed("single.retired", || retired::judge(rel, source, words, calls, &parts));
                drafts.extend(retired_drafts(&path, source, word_hits, call_hits));
                if enabled.contains(&ProjectRule::UntypedStructuredValue) && architecture.typed_values.as_ref().is_some_and(|s| typed_values::wants(rel, s)) {
                    match typed_values::judge(rel, source) {
                        Ok(hits) => drafts.extend(typed_value_drafts(rel, &path, source, hits)),
                        Err(reason) => report.errors.push(format!("{}: cannot read the DOEFF144 judgment ({})", rel, reason)),
                    }
                }
                if let Some(selection) = architecture.record_stubs.as_ref().filter(|_| enabled.contains(&ProjectRule::RecordStubNotKwOnly)) {
                    if record_stubs::wants(rel, &path, selection) {
                        match record_stubs::judge_file(rel, &path, source) {
                            Ok(found) => drafts.extend(record_stub_drafts(rel, &path, source, found)),
                            Err(reason) => report.errors.push(format!("{}: cannot read the DOEFF145 judgment ({})", rel, reason)),
                        }
                    } else if let Some((stub_rel, stub_path)) = record_stubs::stub_of_hy(rel, &path, selection) {
                        // The pre-save .hy is the stdin contents, the adjacent type declaration (.pyi) is read from disk, and the hit is reported at the .pyi path.
                        let judged = std::fs::read_to_string(&stub_path)
                            .map_err(|error| error.to_string())
                            .and_then(|stub| record_stubs::judge(source, &stub, &stub_rel).map(|found| (stub, found)));
                        match judged {
                            Ok((stub, found)) => drafts.extend(record_stub_drafts(&stub_rel, &stub_path, &stub, found)),
                            Err(reason) => report.errors.push(format!("{}: cannot read the DOEFF145 judgment ({})", stub_rel, reason)),
                        }
                    }
                }
            }
            if let (Some(env), Some(rel)) = (&settings.environment, &rel) {
                if enabled.contains(&ProjectRule::EnvironmentName) && is_environment_file(rel, env) {
                    if let Some(language) = language_of(&path) {
                        let file = SourceFile { rel: rel.clone(), path: path.clone(), language };
                        drafts.extend(crate::timing::timed("single.environment", || judge_environment_names(&file, source, env, hy_file.as_ref())));
                    }
                }
            }
            // The names are looked up by the walked range (the same as the definition-style rules — even outside the root, a file under the settings' include dir has a relative path containing `..`).
            let program_rel = rel.clone().or_else(|| hy_files::walked_rel(root, &settings.include, &path));
            if let (Some(places), Some(rel)) = (&settings.program_sleep, &program_rel) {
                if enabled.contains(&ProjectRule::ProgramSleepsOnWallClock) && is_program_sleep_file(rel, places) {
                    let file = SourceFile { rel: rel.clone(), path: path.clone(), language: Language::Hy };
                    drafts.extend(crate::timing::timed("single.program-sleep", || judge_program_sleep(&file, source)));
                }
            }
            if let (Some(places), Some(rel)) = (&settings.test_sleep, &program_rel) {
                if enabled.contains(&ProjectRule::TestSleepsOnWallClock) && is_test_sleep_file(rel, places) {
                    let file = SourceFile { rel: rel.clone(), path: path.clone(), language: Language::Hy };
                    drafts.extend(crate::timing::timed("single.test-sleep", || judge_test_sleep(&file, source)));
                }
            }
            if let (Some(decl), Some(rel)) = (settings.architecture.as_ref().and_then(|a| a.handler_arguments.as_ref()), &rel) {
                if enabled.contains(&ProjectRule::HandlerArgumentHoldsState) && handler_arguments::in_population(rel, decl) {
                    // A class's frozen-ness is looked up by name, and if a class of the same name that is not frozen exists anywhere in the repo, it is not frozen (a declaration in an unnamed file changes the answer), so
                    // it is built from the whole repo only when there are arguments judged by type and it is looked up (the index covers all Hy and Python files — 3000 facts). The classes of unchanged files are looked up by git's
                    // blob id and their contents are not read (`class_index_by_blobs`).
                    let classes = deferred::Deferred::new(|| crate::timing::timed("handler-argument-classes", || handler_arguments::class_index_by_blobs(root, Some((rel.as_str(), source)))));
                    drafts.extend(crate::timing::timed("single.handler-arguments", || judge_handler_arguments(root, rel, source, decl, &classes)));
                }
            }
            // Return the table built with the same root and the same overlay — the editor's headings and bindings (version 2) use it without rebuilding (it used to be built twice in one run, #1033).
            report.world = effect_world;
        }
    }
    if let (Some(semantic), Some(layers)) = (&settings.semantic, &settings.layers) {
        let wanted = [
            ProjectRule::SemanticBusinessDecision,
            ProjectRule::SemanticTransportKnowledge,
            ProjectRule::SemanticPlainCallable,
            ProjectRule::SemanticClassRole,
            ProjectRule::SemanticMixedConcerns,
        ]
            .iter()
            .any(|r| enabled.contains(r));
        if wanted {
            let plain = PlainCallableInput {
                files: plain_files,
                accepted: plain_callable_reasons(settings).to_vec(),
                rejected: settings.architecture.as_ref().map(|a| a.rejected_plain_callable_reasons.clone()).unwrap_or_default(),
                marker: settings.definitions.as_ref().map(|d| d.deff_reason_marker.clone()).unwrap_or_else(|| DEFAULT_REASON_MARKER.to_string()),
                tags: settings.definitions.as_ref().map(|d| d.tags.clone()),
                indexes: &indexes,
            };
            let (found, summary, errors, probes) =
                crate::timing::timed("semantic", || judge_semantic(root, semantic, layers, enabled, &semantic_files, semantic_mode, &plain));
            drafts.extend(found);
            report.errors.extend(errors);
            report.semantic = Some(summary);
            semantic_probes = probes;
        }
    }
    // Cleanup of the index (the whole repo's Hy, hundreds of MB) is done in another thread — it does not make the end of the run wait (0.07 seconds in a one-file commit hook).
    // If the process ends first, the OS takes care of the cleanup.
    crate::timing::timed("drop-index", || {
        std::thread::spawn(move || drop(indexes));
    });
    if judged_rules.contains(&ProjectRule::RegistryEntryStale) {
        let stale = crate::timing::timed("stale-registry", || stale_registry_drafts(root, settings, enabled, &registry, &drafts));
        drafts.extend(stale);
    }
    // The hits of the rules that were named and applied (lent) were used only for the judgment of 166 — findings of rules not in enable are not reported.
    drafts.retain(|draft| !lent.contains(&draft.rule));
    let labels = crate::timing::timed("labels", || judged_labels(root, settings, &mut report.errors));
    let (findings, dropped) = crate::timing::timed("finish", || finish(drafts, settings, &registry, &labels.false_positives));
    report.findings = findings;
    if let Some(summary) = report.semantic.as_mut() {
        summary.false_positives = dropped;
        summary.labeled = labeled_summary(settings, &labels, &semantic_probes);
    }
    // Unreadable Hy files are reported regardless of the list of enabled rules (a missing violation is not silently hidden — DOEFF128).
    report.findings.extend(unreadable_findings(root, settings, &unreadable_target));
    report.judged = judged_rules;
    report.judged.insert(ProjectRule::UnreadableFile);
    report
}

/// The groups for the enabled rules (if DOEFF150 is disabled, the word groups are empty; if DOEFF151 is disabled, the call groups are empty).
fn retired_groups<'a>(architecture: &'a architecture::Architecture, enabled: &BTreeSet<ProjectRule>) -> (&'a [architecture::RetiredWords], &'a [architecture::RetiredCalls]) {
    let words: &[architecture::RetiredWords] = if enabled.contains(&ProjectRule::RetiredWord) { &architecture.retired_words } else { &[] };
    let calls: &[architecture::RetiredCalls] = if enabled.contains(&ProjectRule::RetiredCall) { &architecture.retired_calls } else { &[] };
    (words, calls)
}

/// Judges DOEFF150 and 151 in a whole run (if focus is given, reads only the files under it). The calls inside boundary parts are classified by the catalog (the same catalog of raw side effects
/// as DOEFF106) and checked against the part's :touches.
fn judge_retired_files(
    root: &Path,
    architecture: &architecture::Architecture,
    enabled: &BTreeSet<ProjectRule>,
    catalog: &RawCatalog,
    focus: Option<&[PathBuf]>,
) -> (Vec<Draft>, Vec<String>) {
    let (words, calls) = retired_groups(architecture, enabled);
    if words.is_empty() && calls.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let parts = retired::PartCalls::new(calls, &architecture.boundary_touches(), catalog);
    let (found, errors) = retired::find(root, words, calls, &parts, focus);
    let drafts = found.into_iter().flat_map(|file| retired_drafts(&file.path, &file.source, file.words, file.calls)).collect();
    (drafts, errors)
}

/// Turns the hits of DOEFF144 into draft violations (for one file — the key detail is `<kind>:<name>`).
fn typed_value_drafts(rel: &str, path: &Path, source: &str, hits: Vec<typed_values::TypedHit>) -> Vec<Draft> {
    let lines = LineIndex::new(source);
    hits.into_iter()
        .map(|hit| Draft {
            rule: ProjectRule::UntypedStructuredValue,
            layer: None,
            path: path.to_path_buf(),
            range: lines.range(hit.start, hit.end),
            message: format!("{} — {} {} has type {} (express it with a type that has field names and types)", rel, hit.what.label(), hit.name, hit.problem),
            detail: Some(hit.detail()),
            base: Severity::Error,
            explain: Explain::UntypedStructuredValue { what: hit.what.label().to_string(), name: hit.name, problem: hit.problem },
            rel: rel.to_string(),
        })
        .collect()
}

/// Turns the mismatches of DOEFF145 into draft violations (for one .pyi — the key detail is the class name).
fn record_stub_drafts(rel: &str, path: &Path, source: &str, mismatches: Vec<record_stubs::Mismatch>) -> Vec<Draft> {
    let lines = LineIndex::new(source);
    mismatches
        .into_iter()
        .map(|m| Draft {
            rule: ProjectRule::RecordStubNotKwOnly,
            layer: None,
            path: path.to_path_buf(),
            range: lines.range(m.start, m.end),
            message: format!(
                "{} — class {} has a .hy of the same name with {} (at runtime fields are accepted only by name), but the type declaration's @dataclass lacks kw_only=True",
                rel, m.name, m.form
            ),
            detail: Some(m.name.clone()),
            base: Severity::Error,
            explain: Explain::RecordStubNotKwOnly { class: m.name, form: m.form.to_string() },
            rel: rel.to_string(),
        })
        .collect()
}

/// Turns hits into draft violations (for one file).
fn retired_drafts(path: &Path, source: &str, words: Vec<retired::WordHit>, calls: Vec<retired::CallHit>) -> Vec<Draft> {
    let lines = LineIndex::new(source);
    let mut out: Vec<Draft> = words
        .into_iter()
        .map(|hit| {
            let what = match (&hit.place, &hit.name) {
                (architecture::WordPlace::Paths, _) => format!("retired spelling {} in a file name", hit.spelling),
                (_, Some(name)) => format!("definition name {} uses the retired spelling {}", name, hit.spelling),
                (_, None) => format!("retired spelling {}", hit.spelling),
            };
            Draft {
                rule: ProjectRule::RetiredWord,
                layer: None,
                path: path.to_path_buf(),
                range: lines.range(hit.start, hit.end),
                message: format!("{} — {} (group {}, instead: {})", hit.rel, what, hit.group, hit.instead),
                detail: Some(hit.detail),
                base: Severity::Error,
                explain: Explain::RetiredWord { group: hit.group, spelling: hit.spelling, instead: hit.instead, name: hit.name, place: hit.place },
                rel: hit.rel,
            }
        })
        .collect();
    out.extend(calls.into_iter().map(|hit| Draft {
        rule: ProjectRule::RetiredCall,
        layer: None,
        path: path.to_path_buf(),
        range: lines.range(hit.start, hit.end),
        message: format!("{} — retired call ({} …) (group {}, instead: {})", hit.rel, hit.call, hit.group, hit.instead),
        detail: Some(hit.call.clone()),
        base: Severity::Error,
        explain: Explain::RetiredCall { group: hit.group, call: hit.call, instead: hit.instead },
        rel: hit.rel,
    }));
    out
}

/// Reads the catalog (the bundled one plus the settings' additions). If an addition cannot be read, the reason is accumulated and only the bundled one is used for judging.
fn raw_settings(root: &Path, settings: &ProjectSettings, errors: &mut Vec<String>) -> RawSettings {
    let bundled = match RawCatalog::bundled() {
        Ok(catalog) => catalog,
        Err(reason) => {
            errors.push(format!("cannot read the catalog of raw side effects: {}", reason));
            let empty = RawCatalog { categories: Vec::new(), ignored: Vec::new(), exception_suffixes: Vec::new() };
            return RawSettings { catalog: empty, problems: Vec::new() };
        }
    };
    let extra = settings.raw.as_ref().and_then(|raw| raw.catalog_extra.as_ref());
    match extra {
        None => RawSettings { catalog: bundled, problems: Vec::new() },
        Some(extra) => {
            let parsed = std::fs::read_to_string(root.join(extra))
                .map_err(|e| e.to_string())
                .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).map_err(|e| e.to_string()));
            match parsed {
                Ok(value) => {
                    let (catalog, problems) = bundled.with_extra(&value);
                    errors.extend(problems.iter().map(|p| format!("raw_side_effects.catalog_extra: {}", p)));
                    RawSettings { catalog, problems }
                }
                Err(reason) => {
                    errors.push(format!("cannot read raw_side_effects.catalog_extra {}: {}", extra, reason));
                    RawSettings { catalog: bundled, problems: Vec::new() }
                }
            }
        }
    }
}

/// For a named run (focus), when every enabled rule is declared to be decided by the contents of one file and the settings alone (`ProjectRule::needs_whole_repo` is false, and it is not a rule that
/// asks Jev), the range to read (the named paths) — the commit hook's shape. The hits are decided by the contents of the named files alone,
/// and the output is narrowed by the named path, so the answer is the same without reading outside the named ones. None (read everything) if even one rule needs the whole repo.
/// Previously DOEFF106, 131 and 108 built the Hy index of the whole repo even in a named run, using 20-60 seconds of CPU and 0.8-1 GB for one file of agora-controllers.
fn file_local_scope<'a>(focus: Option<&'a [PathBuf]>, enabled: &BTreeSet<ProjectRule>) -> Option<&'a [PathBuf]> {
    focus.filter(|_| enabled.iter().all(|rule| !rule.needs_whole_repo() && !rule.is_semantic()))
}

/// Whether the path is inside the range to read (file_local_scope — None = everything).
fn in_scope(path: &Path, scope: Option<&[PathBuf]>) -> bool {
    scope.is_none_or(|only| only.iter().any(|p| path.starts_with(p)))
}

/// The Hy index of a whole run (path from the repo root -> file index). When evidence through calls (DOEFF107) is needed, the whole root is indexed;
/// otherwise only the files to be judged are indexed. If a read range (scope) exists, only the files in it are read, without going through the cache — following calls
/// crosses files so it is not done (only a run of rules decided by one file has a range). The cache is not used because rewriting the cache would treat files not in the passed
/// set of files as vanished and delete the index placed by a whole run.
#[allow(clippy::too_many_arguments)]
fn whole_hy_index(
    root: &Path,
    settings: &ProjectSettings,
    enabled: &BTreeSet<ProjectRule>,
    raw: &RawSettings,
    layer_files: &[LayerFile],
    env_files: &[SourceFile],
    wants_raw: bool,
    scope: Option<&[PathBuf]>,
) -> HashMap<String, HyFileIndex> {
    let wants_env = enabled.contains(&ProjectRule::EnvironmentName) && settings.environment.is_some();
    // DOEFF119 looks at the raw side effects (also through calls) of classes in business files, so it builds the whole index.
    let wants_classes = (enabled.contains(&ProjectRule::ClassWithBehaviour) || enabled.contains(&ProjectRule::SemanticClassRole)) && settings.definitions.is_some();
    // DOEFF133 uses the whole index including test files, and follows definitions from tests.
    // A repo that has an allowlist applies DOEFF106, 131 also to files outside the layer locations (#1147), so it builds the whole index.
    // DOEFF136 checks whether a deftest of a simulated environment reaches a definition in a service's entry layer.
    let wants_tests = ((enabled.contains(&ProjectRule::BusinessEffectFake)
        || enabled.contains(&ProjectRule::StaleCounterexampleRow)
        || enabled.contains(&ProjectRule::TestOnlyFake)
        || enabled.contains(&ProjectRule::IntentAnswererNotTranslation)
        || enabled.contains(&ProjectRule::ServiceWithoutCounterexample)
        || enabled.contains(&ProjectRule::ClauseWithoutCounterexample)
        || enabled.contains(&ProjectRule::IntentEffectUncovered)
        || enabled.contains(&ProjectRule::IntentFakedInVerification)
        || enabled.contains(&ProjectRule::AssemblyShapeBroken)
        || enabled.contains(&ProjectRule::AssemblyAnswerMisplaced))
        && settings.architecture.as_ref().is_some_and(|a| a.business_fakes.is_some()))
        || ((enabled.contains(&ProjectRule::TestKindMismatch) || enabled.contains(&ProjectRule::WorldHandlerWithoutContractTest))
            && settings.architecture.as_ref().is_some_and(|a| a.edge_mark.is_some()))
        || (enabled.contains(&ProjectRule::ServiceUntestedOnSim) && settings.architecture.as_ref().is_some_and(|a| a.verification_environment.is_some()))
        || (enabled.contains(&ProjectRule::ServiceInvariantsMissing) && settings.architecture.is_some())
        || (enabled.contains(&ProjectRule::ServiceSystemMissing) && settings.architecture.is_some())
        || (enabled.contains(&ProjectRule::SystemAccessUnwritten) && settings.architecture.as_ref().is_some_and(|a| a.outside_writers.is_some()))
        || (settings.raw.as_ref().is_some_and(|r| r.world_modules.is_some())
            && (enabled.contains(&ProjectRule::RawSideEffectDirect) || enabled.contains(&ProjectRule::WorldHandlerNamedOutsideList)));
    if !(wants_raw && settings.raw.is_some()) && !wants_env && !wants_classes && !wants_tests {
        return HashMap::new();
    }
    // The same construction as hy_index::index_root / index_paths (read each file in the collected order and annotate raw side effects) — except that the per-file read is
    // drawn from the cache (cached_hy_files). The walk through calls is built every time because its answer changes with the contents of other files and the catalog.
    let (paths, via) = if (enabled.contains(&ProjectRule::RawSideEffectVia) && settings.raw.is_some()) || wants_classes || wants_tests {
        (crate::timing::timed("hy-index.collect", || hy_files::collect(root)), true)
    } else {
        let paths: BTreeSet<PathBuf> = layer_files
            .iter()
            .map(|f| &f.file)
            .chain(env_files.iter())
            .filter(|f| f.language == Language::Hy)
            .map(|f| f.path.clone())
            .collect();
        (paths.into_iter().collect::<Vec<_>>(), false)
    };
    let mut files = match scope {
        Some(_) => crate::timing::timed("hy-index.read-scoped", || {
            paths.iter().filter(|path| in_scope(path, scope)).map(|path| hy_index::index_file(root, path)).collect()
        }),
        None => crate::timing::timed("hy-index.read", || cached_hy_files(root, &paths)),
    };
    // The walk through calls does not enter definitions declared to bear real I/O (:world-handlers) — the raw side effects beyond them are that definition's responsibility, and
    // there is no point counting that callers reach them (all 39 cases of DOEFF107 were this shape).
    let stops: BTreeSet<String> = settings.architecture.as_ref().map(|a| a.world_definition_targets().into_keys().collect()).unwrap_or_default();
    let trace = if via && scope.is_none() { hy_index::ViaTrace::Through { stops: &stops } } else { hy_index::ViaTrace::Skip };
    crate::timing::timed("hy-index.annotate", || hy_index::annotate_raw(&mut files, &raw.catalog, trace));
    files.into_iter().filter_map(|file| relative_path(root, Path::new(&file.path)).map(|rel| (rel, file))).collect()
}

/// To read the Hy file index (before annotating raw side effects) through a per-file cache (facts_cache kind "hy-index", binary form)
/// (a single-file run also builds the index of the whole repo, so only changed files are re-read). The answer is the same as
/// hy_index::index_file without a cache — the cache shape also holds fields that the contract's JSON shape cannot carry (hy_index::CachedHyFile). A broken cache whose number of fields does not match
/// causes that file to be re-read. The key is the same as facts_cache (the file's size and modification time, the content hash, and the linter binary's mark and version),
/// with the file's path from the root as the key's name (an absolute path for a file not under the root). The index holds the file's absolute path, so the stored value has an empty path,
/// and after reading, the current root's file path is attached — so that worktrees share the index of a file with the same contents.
fn cached_hy_files(root: &Path, paths: &[PathBuf]) -> Vec<HyFileIndex> {
    let keyed: Vec<(String, PathBuf)> =
        paths.iter().map(|path| (relative_path(root, path).unwrap_or_else(|| path.to_string_lossy().into_owned()), path.clone())).collect();
    facts_cache::per_file_compact_bound(
        root,
        "hy-index",
        &keyed,
        |_key, path| {
            let mut cached = hy_index::CachedHyFile::of(hy_index::index_file(root, path));
            cached.set_path("");
            Some(cached)
        },
        |cached, _key, path| cached.set_path(&path.to_string_lossy()),
    )
    .into_iter()
    .map(|cached| cached.into_file().unwrap_or_else(|broken| hy_index::index_file(root, Path::new(&broken.path))))
    .collect()
}

/// Decides the language from the path's extension (None if neither Hy nor Python).
fn language_of(path: &Path) -> Option<Language> {
    match path.extension().and_then(|e| e.to_str()) {
        Some("py") => Some(Language::Python),
        Some("hy" | "hyk" | "hyp") => Some(Language::Hy),
        _ => None,
    }
}

/// Whether rel is under dir (or is dir itself).
fn under(rel: &str, dir: &str) -> bool {
    dir.is_empty() || rel == dir || rel.starts_with(&format!("{}/", dir))
}

/// Whether rel matches the way a location is written (a dir, or a prefix match before a trailing `*`).
fn matches_place(rel: &str, place: &str) -> bool {
    match place.strip_suffix('*') {
        Some(prefix) => rel.starts_with(prefix),
        None => under(rel, place),
    }
}

/// Whether it is a file of a layer's population — under some layer's location, with a matching extension, and not containing an excluded segment. If two or more locations match,
/// the one with more segments (the more specific location) is taken, and if equal, the earlier layer in the layer order.
fn classify_layer_file(rel: &str, layers: &LayerSettings) -> Option<(ModuleSite, Language)> {
    let language = language_of(Path::new(rel))?;
    let extension = Path::new(rel).extension()?.to_str()?;
    if !layers.extensions.contains(extension) || rel.split('/').any(|part| layers.exclude.contains(part)) {
        return None;
    }
    let mut best: Option<(usize, ModuleSite)> = None;
    for (index, layer) in layers.layers.iter().enumerate() {
        for place in &layer.places {
            if let Some(found) = place.matches(rel) {
                if best.as_ref().is_none_or(|(depth, _)| place.depth() > *depth) {
                    best = Some((place.depth(), ModuleSite { layer: LayerId(index), dir: found.dir, service: found.service, by_tags: false }));
                }
            }
        }
    }
    best.map(|(_, site)| (site, language))
}

/// Infers the layer of a module outside the declared locations (under architecture.hy's root) from the :role tag. If one layer allows the role, that layer;
/// if two or more, the layer with the same name in a path segment (a layer-first dir), if any. None if it cannot be inferred (it is not in the population of the layer rules).
fn infer_layer_site(rel: &str, source: &str, layers: &LayerSettings) -> Option<(ModuleSite, Language)> {
    let infer_root = layers.infer_root.as_ref()?;
    let language = language_of(Path::new(rel))?;
    let extension = Path::new(rel).extension()?.to_str()?;
    if !rel.starts_with(&format!("{}/", infer_root)) || !layers.extensions.contains(extension) || rel.split('/').any(|part| layers.exclude.contains(part)) {
        return None;
    }
    // Only modules of layer-first dirs (a layer name appears in a segment under root — controllers/core/… etc.) are inferred. Dirs of old features are not put into the population of the layer rules
    // (what was not in it before is not added).
    let parts: Vec<&str> = rel.split('/').collect();
    let dirs = &parts[..parts.len().saturating_sub(1)];
    let by_path = (0..layers.layers.len()).map(LayerId).find(|id| dirs.contains(&layers.layers[id.0].name.as_str()))?;
    let facts = read_facts(language, source, &module_of(rel), &layers.tags);
    let roles: Vec<String> = facts.tag_sets().iter().filter_map(|t| t.role.clone()).filter(|r| !r.is_empty()).collect();
    let candidates: Vec<LayerId> = (0..layers.layers.len())
        .map(LayerId)
        .filter(|id| !roles.is_empty() && roles.iter().all(|role| layers.layers[id.0].roles.as_ref().is_some_and(|allowed| allowed.contains(role))))
        .collect();
    let dir = rel.rsplit_once('/').map(|(d, _)| d.to_string()).unwrap_or_default();
    // If one layer allows the role, that layer; if two or more, choose the layer of the path segment. What cannot be inferred by tag (no role, or a role of no layer) is placed in the layer of the path segment, and
    // the tag rules (DOEFF104, 105) give the reason.
    let (layer, by_tags) = match candidates.as_slice() {
        [only] => (*only, true),
        [] => (by_path, false),
        many => (many.iter().copied().find(|id| *id == by_path).unwrap_or(many[0]), true),
    };
    Some((ModuleSite { layer, dir, service: None, by_tags }, language))
}

/// Collects all modules under the layer locations (in layer order, and in path order within a layer).
fn collect_layer_files(root: &Path, layers: &LayerSettings) -> Vec<LayerFile> {
    let mut found: BTreeMap<String, LayerFile> = BTreeMap::new();
    let bases: BTreeSet<String> = layers.layers.iter().flat_map(|layer| layer.places.iter().map(|place| place.base())).collect();
    for base in bases {
        for path in walk_files(&root.join(&base)) {
            let Some(rel) = relative_path(root, &path) else { continue };
            if found.contains_key(&rel) {
                continue;
            }
            if let Some((site, language)) = classify_layer_file(&rel, layers) {
                found.insert(rel.clone(), LayerFile { module: module_of(&rel), site, file: SourceFile { rel, path, language } });
            }
        }
    }
    // Modules outside the declared locations are put into the population with their layer inferred from the tag's role (location violations are emitted separately by DOEFF114, 115).
    if let Some(infer_root) = &layers.infer_root {
        for path in walk_files(&root.join(infer_root)) {
            let Some(rel) = relative_path(root, &path) else { continue };
            if found.contains_key(&rel) || language_of(&path).is_none() {
                continue;
            }
            let Ok(source) = std::fs::read_to_string(&path) else { continue };
            if let Some((site, language)) = infer_layer_site(&rel, &source, layers) {
                found.insert(rel.clone(), LayerFile { module: module_of(&rel), site, file: SourceFile { rel, path, language } });
            }
        }
    }
    let mut out: Vec<LayerFile> = found.into_values().collect();
    out.sort_by(|a, b| (a.site.layer, &a.file.rel).cmp(&(b.site.layer, &b.file.rel)));
    out
}

/// The module location index of a single-file run — applies the same procedure as `collect_layer_files` (under the declared locations it is decided by path; outside the declaration and under infer_root it is inferred from contents)
/// only to the files of the modules looked up. The written file (`own`) has the location judged from its pre-save contents, and overrides other files for its own module.
fn named_site_index<'a>(root: &'a Path, layers: &'a LayerSettings, own: &LayerFile) -> SiteIndex<'a> {
    let bases: BTreeSet<String> = layers.layers.iter().flat_map(|layer| layer.places.iter().map(|place| place.base())).collect();
    SiteIndex::named(
        root,
        bases.into_iter().collect(),
        layers.infer_root.clone(),
        (own.module.clone(), own.site.clone()),
        Box::new(move |rel: &str, path: &Path, roads: Roads| {
            let by_path = roads.in_bases.then(|| classify_layer_file(rel, layers)).flatten().map(|(site, _)| site);
            let site = match by_path {
                Some(site) => Some(site),
                None if roads.in_infer_root => {
                    std::fs::read_to_string(path).ok().and_then(|source| infer_layer_site(rel, &source, layers)).map(|(site, _)| site)
                }
                None => None,
            };
            site.map(|site| (site.layer, site))
        }),
    )
}

/// Collects all files under a dir (skipping unreadable branches; symlinks are not followed).
fn walk_files(dir: &Path) -> Vec<PathBuf> {
    WalkDir::new(dir).follow_links(false).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()).map(|e| e.into_path()).collect()
}

/// The result of judging one layer module (draft violations, one row of the map, the reason it could not be read, and the pairs that read another service).
#[derive(Default)]
struct LayerJudgement {
    drafts: Vec<Draft>,
    summary: Option<ModuleSummary>,
    errors: Vec<String>,
    /// (this file's service, the service of the import target) — to find declared but unused dependencies (DOEFF117).
    crossings: Vec<(String, String)>,
}

/// Judges one layer module (draft violations, one row of the map, the reason it could not be read).
fn judge_layer_file(
    file: &LayerFile,
    source: &str,
    layers: &LayerSettings,
    settings: &ProjectSettings,
    enabled: &BTreeSet<ProjectRule>,
    index: &SiteIndex<'_>,
    hy_file: Option<&HyFileIndex>,
) -> LayerJudgement {
    let facts = read_facts(file.file.language, source, &file.module, &layers.tags);
    let errors = facts.errors.iter().map(|e| format!("{}: {}", file.file.rel, e)).collect();
    let lines = LineIndex::new(source);
    let spec = &layers.layers[file.site.layer.0];
    let mut roles: Vec<String> = Vec::new();
    for tags in facts.tag_sets() {
        if let Some(role) = tags.role.clone().filter(|r| !r.is_empty() && !roles.contains(r)) {
            roles.push(role);
        }
    }
    let placement = Placement { layer: file.site.layer, dir: file.site.dir.clone(), service: file.site.service.clone(), roles, by_tags: file.site.by_tags };
    let contexts: Vec<String> = facts.tag_sets().into_iter().filter_map(|t| t.context.clone()).filter(|c| !c.is_empty()).collect();
    let narrator = Narrator { layers: Some(layers), raw: settings.raw.as_ref() };
    let layer_reason = narrator.layer_reason(&placement);
    let judge = LayerJudge { file, source, lines: &lines, layers, layer: file.site.layer, placement };
    let mut drafts = Vec::new();
    if enabled.contains(&ProjectRule::ModuleDeclaresTags) {
        drafts.extend(judge.declares_tags(&facts));
    }
    if enabled.contains(&ProjectRule::RoleMatchesLayer) {
        drafts.extend(judge.role_matches_layer(&facts));
    }
    if enabled.contains(&ProjectRule::LayerTypesOnly) && spec.types_only {
        drafts.extend(judge.types_only(&facts));
    }
    if enabled.contains(&ProjectRule::LayerForbiddenModule) {
        drafts.extend(judge.forbidden_modules(&facts));
    }
    if enabled.contains(&ProjectRule::LayerImportDirection) {
        drafts.extend(judge.import_direction(&facts, index));
    }
    // DOEFF169: all Hy files in the layer locations (no settings needed — a match clause that never matches).
    if enabled.contains(&ProjectRule::MatchFieldHyphen) && file.file.language == Language::Hy {
        drafts.extend(judge.match_field_hyphens());
    }
    // DOEFF170, 171: Hy and Python files of layers that pass values by type only (architecture.hy's layer :wire-free True — agora's core)
    // (the Python fields are #2203 — a mapping in a .py dataclass field was silently missed).
    let wire_free_rules = [ProjectRule::WireInWireFreeLayer, ProjectRule::BareMapInWireFreeLayer];
    if spec.wire_free && wire_free_rules.iter().any(|r| enabled.contains(r)) {
        drafts.extend(judge.wire_free(enabled));
    }
    // DOEFF172: smell of where mappings are placed — all Hy and Python files in the layer locations (fields only outside the layers that pass values by type only — those are DOEFF171, (2) of #2143).
    if enabled.contains(&ProjectRule::DictOutsideItsPlace) {
        drafts.extend(judge.dict_smells(!spec.wire_free));
    }
    // DOEFF168: only Hy files of layers in the declaration's :layers (business layers — locations whose layer is unknown are outside the layer rules).
    if let Some(decl) = settings.architecture.as_ref().and_then(|a| a.environment_branches.as_ref()) {
        if enabled.contains(&ProjectRule::EnvironmentBranch) && file.file.language == Language::Hy && decl.layers.contains(&spec.name) {
            drafts.extend(judge.environment_branches(decl));
        }
    }
    let mut crossings = Vec::new();
    if let Some(architecture) = &settings.architecture {
        let (found, crossed) = judge.service_dependencies(
            &facts,
            index,
            architecture,
            enabled.contains(&ProjectRule::ServiceDependency),
            enabled.contains(&ProjectRule::LayerImportDirection),
        );
        drafts.extend(found);
        crossings = crossed;
        if enabled.contains(&ProjectRule::PlacedDependency) {
            drafts.extend(judge.placed_dependencies(&facts, index, architecture));
        }
        if enabled.contains(&ProjectRule::ContextMatchesService) {
            // For a service whose name differs from its dir via :dir (compartments hidden in a dir starting with _ — doeff's ADR-DOE-API-001 R15), :context is checked against
            // the service's name, not the dir.
            let declared = judge.placement.service.as_deref().and_then(|s| architecture.service_by_dir(s));
            let shared = settings::ServiceSettings {
                shared: architecture.shared.iter().cloned().collect(),
                guarded: BTreeSet::new(),
                open: BTreeSet::new(),
                exceptions: BTreeSet::new(),
                check_context: true,
            };
            if let Some(mut draft) = judge.context_matches_service(&facts, &contexts, &shared, declared.map(|s| s.name.as_str())) {
                if declared.is_some() {
                    draft.base = Severity::Warning;
                }
                drafts.push(draft);
            }
        }
    }
    if let Some(services) = &settings.services {
        if enabled.contains(&ProjectRule::ServiceBoundary) {
            drafts.extend(judge.service_boundary(&facts, index, services));
        }
        if enabled.contains(&ProjectRule::ContextMatchesService) && services.check_context {
            drafts.extend(judge.context_matches_service(&facts, &contexts, services, None));
        }
    }
    if let (Some(raw), Some(hy_file)) = (&settings.raw, hy_file) {
        // In a repo that has an allowlist, only the modules of the allowlist's definitions are allowed (not allowed by layer).
        let allowed_here = match &raw.world_modules {
            Some(modules) => modules.contains(&architecture::mangle_dotted(&file.module)),
            None => raw.allowed.contains(&file.site.layer),
        };
        if !allowed_here {
            if enabled.contains(&ProjectRule::RawSideEffectDirect) {
                drafts.extend(judge.raw_direct(hy_file, raw, &file.module));
            }
            if enabled.contains(&ProjectRule::RawSideEffectVia) {
                drafts.extend(judge.raw_via(hy_file));
            }
        }
    }
    // Reading environment variables in Python files — for `.py` outside the Hy index too, the same single allowance judgment as the Hy branch is used.
    if let (Some(raw), Language::Python, true) = (&settings.raw, file.file.language, enabled.contains(&ProjectRule::RawSideEffectDirect)) {
        let allowed_here = match &raw.world_modules {
            Some(modules) => modules.contains(&architecture::mangle_dotted(&file.module)),
            None => raw.allowed.contains(&file.site.layer),
        };
        if !allowed_here && !boundary_allows(raw, &file.module, RawCategory::Env) {
            drafts.extend(judge.python_env_direct(raw));
        }
    }
    if let (Some(architecture), Some(hy_file)) = (&settings.architecture, hy_file) {
        if enabled.contains(&ProjectRule::WorldHandlerNamedOutsideList) && !architecture.world_handlers.is_empty() {
            drafts.extend(judge.world_handler_named(hy_file, architecture));
        }
    }
    let tags = facts.summary_tags();
    let summary = ModuleSummary {
        path: file.file.path.clone(),
        rel: file.file.rel.clone(),
        layer: Some(spec.name.clone()),
        context: tags.and_then(|t| t.context.clone()),
        role: tags.and_then(|t| t.role.clone()),
        service: file.site.service.clone(),
        layer_reason: Some(layer_reason),
    };
    LayerJudgement { drafts, summary: Some(summary), errors, crossings }
}

/// A tool bundling what is needed for judging one layer module.
struct LayerJudge<'a> {
    file: &'a LayerFile,
    source: &'a str,
    lines: &'a LineIndex<'a>,
    layers: &'a LayerSettings,
    layer: LayerId,
    /// This file's layer, and the role declared by tag (the subject of the explanation).
    placement: Placement,
}

impl<'a> LayerJudge<'a> {
    /// The layer's name.
    fn layer_name(&self, id: LayerId) -> &'a str {
        &self.layers.layers[id.0].name
    }

    /// Converts a byte range to an editor range.
    fn range(&self, span: ByteSpan) -> Range {
        self.lines.range(span.start, span.end)
    }

    /// Makes one draft.
    fn draft(&self, rule: ProjectRule, range: Range, message: String, detail: Option<String>, explain: Explain) -> Draft {
        Draft {
            explain,
            rule,
            layer: Some(self.layer),
            rel: self.file.file.rel.clone(),
            path: self.file.file.path.clone(),
            range,
            message,
            detail,
            base: Severity::Error,
        }
    }

    /// DOEFF104: definitions with no tags (and no module-head tags either), and modules that claim nothing.
    fn declares_tags(&self, facts: &ModuleFacts) -> Option<Draft> {
        let rel = &self.file.file.rel;
        match (facts.untagged.first(), facts.module_tags.is_some(), facts.tagged.is_empty()) {
            (Some(first), false, _) => {
                let names: Vec<&str> = facts.untagged.iter().map(|n| n.name.as_str()).collect();
                Some(self.draft(
                    ProjectRule::ModuleDeclaresTags,
                    self.range(first.span),
                    format!("{} definition {} has no tags — declare context and role with the contract dict's :tags {{:context … :role …}} or with the module-head tags", rel, names.join(", ")),
                    None,
                    Explain::UntaggedDefinitions { placement: self.placement.clone(), names: names.iter().map(|n| n.to_string()).collect() },
                ))
            }
            (None, false, true) => Some(self.draft(
                ProjectRule::ModuleDeclaresTags,
                first_line_range(self.source),
                format!("{} has no tags (:context and :role) — a module under a layer dir declares its context and role with tags", rel),
                None,
                Explain::NoTags { placement: self.placement.clone() },
            )),
            _ => None,
        }
    }

    /// DOEFF105: for each effective tag, whether it has a role and a context, and the role is allowed in this layer.
    fn role_matches_layer(&self, facts: &ModuleFacts) -> Vec<Draft> {
        let spec = &self.layers.layers[self.layer.0];
        let Some(allowed) = &spec.roles else { return Vec::new() };
        let allowed_text = allowed.iter().cloned().collect::<Vec<_>>().join(" / ");
        facts
            .tag_sets()
            .into_iter()
            .filter(|t| {
                let role = t.role.as_deref().unwrap_or("");
                role.is_empty() || t.context.as_deref().unwrap_or("").is_empty() || !allowed.contains(role)
            })
            .map(|t| {
                let role = t.role.clone().unwrap_or_else(|| "None".to_string());
                self.draft(
                    ProjectRule::RoleMatchesLayer,
                    self.range(t.span),
                    format!(
                        "{} role {} cannot be placed in layer {} (:context and :role are required; allowed roles = {})",
                        self.file.file.rel, role, spec.name, allowed_text
                    ),
                    Some(role),
                    Explain::RoleMismatch { placement: self.placement.clone(), role: t.role.clone(), context: t.context.clone() },
                )
            })
            .collect()
    }

    /// DOEFF103: do not place functions or handlers in a types-only layer.
    fn types_only(&self, facts: &ModuleFacts) -> Option<Draft> {
        let first = facts.functions.first()?;
        let names: Vec<&str> = facts.functions.iter().map(|n| n.name.as_str()).collect();
        Some(self.draft(
            ProjectRule::LayerTypesOnly,
            self.range(first.span),
            format!(
                "{} ({}) defines function / handler {} — {} is a types-only layer",
                self.file.file.rel,
                self.layer_name(self.layer),
                names.join(", "),
                self.layer_name(self.layer)
            ),
            Some("definitions".to_string()),
            Explain::TypesOnly { placement: self.placement.clone(), functions: names.iter().map(|n| n.to_string()).collect() },
        ))
    }

    /// DOEFF170, 171: defwire in a layer that passes values by type only, and mappings / untyped tuples in :pre / :post / field types (the key detail is `<kind>:<name>`). An unreadable
    /// file is reported by DOEFF128, so nothing is emitted here.
    fn wire_free(&self, enabled: &BTreeSet<ProjectRule>) -> Vec<Draft> {
        let read = match self.file.file.language {
            Language::Hy => typed_values::wire_free_hits(self.source),
            Language::Python => typed_values::py_wire_free_hits(self.source, &self.file.file.rel),
        };
        let Ok(hits) = read else { return Vec::new() };
        let layer = self.layer_name(self.layer);
        hits.into_iter()
            .filter_map(|hit| {
                let wire = hit.what == typed_values::WireFreeWhat::Wire;
                let rule = if wire { ProjectRule::WireInWireFreeLayer } else { ProjectRule::BareMapInWireFreeLayer };
                if !enabled.contains(&rule) {
                    return None;
                }
                let message = if wire {
                    format!("{} defwire {} — layer {} passes values by type only (put wire types in the translation layer)", self.file.file.rel, hit.name, layer)
                } else {
                    format!("{} {} ({}) has type {} — layer {} passes neither mappings nor tuples / sequences without element types", self.file.file.rel, hit.name, hit.what.kind(), hit.problem, layer)
                };
                let detail = hit.detail();
                Some(self.draft(
                    rule,
                    self.range(ByteSpan { start: hit.start, end: hit.end }),
                    message,
                    Some(detail),
                    Explain::WireFree { placement: self.placement.clone(), wire, problem: hit.problem },
                ))
            })
            .collect()
    }

    /// DOEFF172: definitions that build a mapping outside a handler, and mapping fields (count only — severity warning). An unreadable file is reported by DOEFF128.
    fn dict_smells(&self, fields: bool) -> Vec<Draft> {
        // Python has no way to count places that build a mapping outside a handler (Hy forms only) — for Python only fields are counted.
        let read = match self.file.file.language {
            Language::Hy => typed_values::dict_smell_hits(self.source, fields),
            Language::Python if fields => typed_values::py_dict_smell_hits(self.source, &self.file.file.rel),
            Language::Python => Ok(Vec::new()),
        };
        let Ok(hits) = read else { return Vec::new() };
        hits.into_iter()
            .map(|hit| {
                let field = hit.what == typed_values::DictSmellWhat::Field;
                let message = if field {
                    format!("{} field {} has type {} — do not hold a mapping in a field (use a type with named, typed fields)", self.file.file.rel, hit.name, hit.problem)
                } else {
                    format!("{} {} {} — a handler holds a mapping only for a cache", self.file.file.rel, hit.name, hit.problem)
                };
                let detail = hit.detail();
                let mut draft = self.draft(
                    ProjectRule::DictOutsideItsPlace,
                    self.range(ByteSpan { start: hit.start, end: hit.end }),
                    message,
                    Some(detail),
                    Explain::DictSmell { placement: self.placement.clone(), field, problem: hit.problem },
                );
                draft.base = Severity::Warning;
                draft
            })
            .collect()
    }

    /// DOEFF169: a keyword field name in a match class pattern contains `-` (one finding per matching keyword — the key detail is `<class>:<field name>`).
    fn match_field_hyphens(&self) -> Vec<Draft> {
        match_field_names::judge(self.source)
            .into_iter()
            .map(|found| {
                self.draft(
                    ProjectRule::MatchFieldHyphen,
                    self.range(ByteSpan { start: found.start, end: found.end }),
                    format!(
                        "{} match class pattern ({} :{} …) never matches — Hy does not convert keywords to attribute names, so write :{}",
                        self.file.file.rel,
                        found.class,
                        found.field,
                        found.field.replace('-', "_")
                    ),
                    Some(format!("{}:{}", found.class, found.field)),
                    Explain::MatchFieldHyphen { placement: self.placement.clone(), class: found.class, field: found.field },
                )
            })
            .collect()
    }

    /// DOEFF102: do not directly import modules forbidden to this layer. Compared by prefix of the import spelling (the same spelling, or a module / name below it —
    /// `urllib.request` matches `urllib.request.urlopen` and does not match `urllib.parse`). One finding per matched module, positioned at the first import.
    /// DOEFF168: comparison with an environment-name value and branching on a dry-run flag (one finding per matched literal or symbol — the key detail is `value:<spelling>` or `flag:<spelling>`).
    fn environment_branches(&self, decl: &architecture::EnvironmentBranches) -> Vec<Draft> {
        let spec = &self.layers.layers[self.layer.0];
        env_branch::judge(self.source, decl)
            .into_iter()
            .map(|found| {
                let what = match &found.hit {
                    env_branch::BranchHit::Value(value) => format!("branches by comparing with the environment-name value \"{}\"", value),
                    env_branch::BranchHit::Flag(flag) => format!("branches on the dry-run flag {}", flag),
                };
                self.draft(
                    ProjectRule::EnvironmentBranch,
                    self.range(ByteSpan { start: found.start, end: found.end }),
                    format!("{} ({}) {} — business layers do not know the environment (express environment differences by swapping handler sets)", self.file.file.rel, spec.name, what),
                    Some(found.hit.detail()),
                    Explain::EnvironmentBranch { placement: self.placement.clone(), hit: found.hit },
                )
            })
            .collect()
    }

    fn forbidden_modules(&self, facts: &ModuleFacts) -> Vec<Draft> {
        let spec = &self.layers.layers[self.layer.0];
        let mut first: BTreeMap<&str, ByteSpan> = BTreeMap::new();
        for import in &facts.imports {
            let target = import.target.as_str();
            let hit = spec.forbid_modules.iter().find(|module| target == module.as_str() || target.starts_with(&format!("{}.", module)));
            if let Some(module) = hit {
                first.entry(module.as_str()).or_insert(import.span);
            }
        }
        first
            .into_iter()
            .map(|(top, span)| {
                self.draft(
                    ProjectRule::LayerForbiddenModule,
                    self.range(span),
                    format!("{} ({}) imports module {} — this layer does not import it directly (I/O belongs to the handlers of allowed layers)", self.file.file.rel, spec.name, top),
                    Some(top.to_string()),
                    Explain::ForbiddenModule { placement: self.placement.clone(), module: top.to_string() },
                )
            })
            .collect()
    }

    /// Whether this is an import target that DOEFF101 judges a breach — a module of the population, not this file itself, and outside the layers the layer allows.
    /// DOEFF116 does not count the same import twice, so it does not judge imports for which this is true (fixing layer direction comes first — #1799).
    fn breaks_direction(&self, target: &str, index: &SiteIndex<'_>) -> bool {
        let Some(allowed) = &self.layers.layers[self.layer.0].allowed else { return false };
        resolve_target(target, index).is_some_and(|(owner, site)| owner != self.file.module && !allowed.contains(&site.layer))
    }

    /// DOEFF101: a breach if the import target is a module of the population (or a name inside it) outside the allowed layers.
    fn import_direction(&self, facts: &ModuleFacts, index: &SiteIndex<'_>) -> Vec<Draft> {
        let spec = &self.layers.layers[self.layer.0];
        let Some(allowed) = &spec.allowed else { return Vec::new() };
        let mut first: BTreeMap<&str, ByteSpan> = BTreeMap::new();
        for import in &facts.imports {
            first.entry(import.target.as_str()).or_insert(import.span);
        }
        let allowed_text = allowed.iter().map(|id| self.layer_name(*id)).collect::<BTreeSet<_>>().into_iter().collect::<Vec<_>>().join(", ");
        first
            .into_iter()
            .filter_map(|(target, span)| {
                let (owner, owner_site) = resolve_target(target, index)?;
                let owner_layer = owner_site.layer;
                if owner == self.file.module || allowed.contains(&owner_layer) {
                    return None;
                }
                Some(self.draft(
                    ProjectRule::LayerImportDirection,
                    self.range(span),
                    format!(
                        "{} ({}) imports {} of {} — the layers that layer {} may import are {}",
                        self.file.file.rel,
                        spec.name,
                        self.layer_name(owner_layer),
                        target,
                        spec.name,
                        allowed_text
                    ),
                    Some(target.to_string()),
                    Explain::ImportDirection {
                        placement: self.placement.clone(),
                        target: target.to_string(),
                        target_layer: owner_layer,
                        target_dir: owner_site.dir.clone(),
                    },
                ))
            })
            .collect()
    }

    /// DOEFF109: service boundary — a breach if this file (a guarded layer of service A) reads a module in a guarded layer of another service B.
    /// B's open layers (such as intent), the shared location and exceptions may be read. Locations without a service (layer-first form) are not judged.
    fn service_boundary(&self, facts: &ModuleFacts, index: &SiteIndex<'_>, services: &settings::ServiceSettings) -> Vec<Draft> {
        let Some(own) = self.placement.service.as_deref() else { return Vec::new() };
        if !services.guarded.contains(&self.layer) || services.shared.contains(own) {
            return Vec::new();
        }
        let mut first: BTreeMap<&str, ByteSpan> = BTreeMap::new();
        for import in &facts.imports {
            first.entry(import.target.as_str()).or_insert(import.span);
        }
        first
            .into_iter()
            .filter_map(|(target, span)| {
                let (_, site) = resolve_target(target, index)?;
                let other = site.service.as_deref()?;
                let crosses = other != own
                    && !services.shared.contains(other)
                    && !services.open.contains(&site.layer)
                    && services.guarded.contains(&site.layer)
                    && !services.exceptions.contains(&(own.to_string(), other.to_string()));
                crosses.then(|| {
                    self.draft(
                        ProjectRule::ServiceBoundary,
                        self.range(span),
                        format!(
                            "{} (service {}, layer {}) imports from service {} layer {}: {} — ask another service through intent",
                            self.file.file.rel,
                            own,
                            self.layer_name(self.layer),
                            other,
                            self.layer_name(site.layer),
                            target
                        ),
                        Some(target.to_string()),
                        Explain::ServiceBoundary {
                            placement: self.placement.clone(),
                            target: target.to_string(),
                            target_service: other.to_string(),
                            target_layer: site.layer,
                            target_dir: site.dir.clone(),
                            open_layers: services.open.iter().copied().collect(),
                            shared: services.shared.iter().cloned().collect(),
                        },
                    )
                })
            })
            .collect()
    }

    /// DOEFF116: service dependencies — when service A of this file reads a module of another service B, B must be in A's :depends-on,
    /// and what is read must be in a layer this file's layer may read in the dependency (the layer's :dependency-layers — the assembly entry gets intent and protocol —,
    /// or, if absent, the intent of :open-layers). The shared location and foundation are not services, so they are not looked at.
    /// The pairs of other services read are returned for dependencies that were declared but not used (DOEFF117).
    fn service_dependencies(
        &self,
        facts: &ModuleFacts,
        index: &SiteIndex<'_>,
        architecture: &architecture::Architecture,
        judge: bool,
        direction_judged: bool,
    ) -> (Vec<Draft>, Vec<(String, String)>) {
        let Some(own_dir) = self.placement.service.as_deref() else { return (Vec::new(), Vec::new()) };
        if architecture.shared.as_deref() == Some(own_dir) {
            return (Vec::new(), Vec::new());
        }
        let own = architecture.service_by_dir(own_dir);
        let own_name = own.map(|s| s.name.clone()).unwrap_or_else(|| own_dir.to_string());
        let mut first: BTreeMap<&str, ByteSpan> = BTreeMap::new();
        for import in &facts.imports {
            first.entry(import.target.as_str()).or_insert(import.span);
        }
        let mut drafts = Vec::new();
        let mut crossings = Vec::new();
        for (target, span) in first {
            let Some((_, site)) = resolve_target(target, index) else { continue };
            let Some(other_dir) = site.service.as_deref() else { continue };
            if other_dir == own_dir || architecture.shared.as_deref() == Some(other_dir) {
                continue;
            }
            let other_name = architecture.service_by_dir(other_dir).map(|s| s.name.clone()).unwrap_or_else(|| other_dir.to_string());
            crossings.push((own_name.clone(), other_name.clone()));
            if !judge || (direction_judged && self.breaks_direction(target, index)) {
                continue;
            }
            let declared = own.is_some_and(|s| s.depends_on.contains(&other_name));
            // If the dependency already depends on this service, adding it to :depends-on makes a cycle of dependencies between services.
            let reverse = architecture.services.iter().any(|s| s.name == other_name && s.depends_on.contains(&own_name));
            let readable = architecture.dependency_layers_for(self.layer_name(self.placement.layer));
            let open = readable.iter().any(|l| *l == self.layer_name(site.layer));
            if declared && open {
                continue;
            }
            let depends_on = own.map(|s| s.depends_on.clone()).unwrap_or_default();
            drafts.push(self.draft(
                ProjectRule::ServiceDependency,
                self.range(span),
                if declared {
                    format!(
                        "{} (service {}, layer {}) reads from dependency {} layer {}: {} — the only layer-{} reads allowed in a dependency are {}",
                        self.file.file.rel,
                        own_name,
                        self.layer_name(self.placement.layer),
                        other_name,
                        self.layer_name(site.layer),
                        target,
                        self.layer_name(self.placement.layer),
                        readable.join(", ")
                    )
                } else {
                    format!("{} (service {}) reads {} of service {}, which is not in :depends-on", self.file.file.rel, own_name, target, other_name)
                },
                Some(target.to_string()),
                Explain::ServiceDependency {
                    placement: self.placement.clone(),
                    own: own_name.clone(),
                    target: target.to_string(),
                    target_service: other_name,
                    target_layer: site.layer,
                    target_dir: site.dir.clone(),
                    declared,
                    reverse,
                    depends_on,
                    open_layers: readable.to_vec(),
                    widened: readable != architecture.open_layers.as_slice(),
                },
            ));
        }
        (drafts, crossings)
    }

    /// DOEFF140: a module in a layer of architecture.hy's :placed-dependencies (service and shared — old layer-first dirs and foundation are excluded)
    /// imports a module outside the layer locations under root. The target is decided by looking up a file from the import spelling (that file or its parent module is
    /// under root, is not in the layer index, and is not a package marker) — judged only from the given file's imports and locations; the whole-repo index is not read.
    /// Reading the same module any number of times is one finding (at the first import).
    fn placed_dependencies(&self, facts: &ModuleFacts, index: &SiteIndex<'_>, architecture: &architecture::Architecture) -> Vec<Draft> {
        if self.placement.service.is_none() || !architecture.placed_dependencies.iter().any(|l| l == self.layer_name(self.placement.layer)) {
            return Vec::new();
        }
        let mut repo = self.file.file.path.clone();
        for _ in self.file.file.rel.split('/') {
            repo.pop();
        }
        let root_module = layers::normalize_dir(&architecture.root).replace('/', ".");
        let mut first: BTreeMap<String, (ByteSpan, String)> = BTreeMap::new();
        for import in &facts.imports {
            if resolve_target(&import.target, index).is_some() {
                continue;
            }
            let Some((owner, owner_rel)) = unplaced_owner(&import.target, &root_module, &repo) else { continue };
            if owner != self.file.module {
                first.entry(owner).or_insert((import.span, owner_rel));
            }
        }
        first
            .into_iter()
            .map(|(owner, (span, owner_rel))| {
                self.draft(
                    ProjectRule::PlacedDependency,
                    self.range(span),
                    format!(
                        "{} (layer {}) imports module {} ({}) outside the layer locations — depend only on modules moved into the layer locations",
                        self.file.file.rel,
                        self.layer_name(self.placement.layer),
                        owner,
                        owner_rel
                    ),
                    Some(owner.clone()),
                    Explain::PlacedDependency { placement: self.placement.clone(), owner, owner_rel },
                )
            })
            .collect()
    }

    /// DOEFF113: a tag's :context and the dir's service disagree (info). `-` and `_` in names are treated as the same. The shared location is not looked at.
    /// `named` = the names of services that declared a dir (when architecture.hy's :dir differs from the name, compare with the name, not the dir).
    fn context_matches_service(
        &self,
        facts: &ModuleFacts,
        contexts: &[String],
        services: &settings::ServiceSettings,
        named: Option<&str>,
    ) -> Option<Draft> {
        let dir = self.placement.service.as_deref()?;
        if services.shared.contains(dir) {
            return None;
        }
        let own = named.unwrap_or(dir);
        let norm = |text: &str| text.replace('-', "_");
        let mut foreign: Vec<String> = contexts.iter().filter(|c| norm(c) != norm(own)).cloned().collect();
        foreign.dedup();
        let first = foreign.first()?;
        let span = facts.tag_sets().into_iter().find(|t| t.context.as_deref() == Some(first.as_str())).map(|t| t.span);
        let range = span.map(|s| self.range(s)).unwrap_or_else(|| first_line_range(self.source));
        let mut draft = self.draft(
            ProjectRule::ContextMatchesService,
            range,
            format!("{} tag :context {} disagrees with the location's service {}", self.file.file.rel, foreign.join(", "), own),
            Some(foreign.join("+")),
            Explain::ContextMismatch { placement: self.placement.clone(), service: own.to_string(), contexts: foreign.clone() },
        );
        draft.base = Severity::Info;
        Some(draft)
    }

    /// DOEFF106: direct evidence of raw side effects inside a definition (strong evidence is an error, weak evidence a warning). When the same evidence overlaps between a nested definition and
    /// an outer one, it is counted only once, for the innermost definition.
    /// To turn environment-variable reads in `.py` files into DOEFF106 findings (env_reads in python_env.rs — the same statement shapes as Hy's raw_direct).
    fn python_env_direct(&self, raw: &raw_settings::RawSettingsSpec) -> Vec<Draft> {
        let allowed = match &raw.world_modules {
            Some(_) => "only modules of definitions in architecture.hy's :world-handlers (the allow-list of definitions that may touch the outside world) may touch raw side effects".to_string(),
            None => format!("the layers that may touch raw side effects are {}", raw.allowed.iter().map(|id| self.layer_name(*id)).collect::<Vec<_>>().join(", ")),
        };
        python_env::env_reads(self.source)
            .into_iter()
            .map(|read| {
                let mut draft = self.draft(
                    ProjectRule::RawSideEffectDirect,
                    self.lines.range(read.start, read.end),
                    format!(
                        "definition {} ({}) directly touches raw side effect {} ({}) — {}",
                        read.definition,
                        self.layer_name(self.layer),
                        read.name,
                        RawCategory::Env.as_str(),
                        allowed
                    ),
                    Some(format!("{}::{}", read.definition, read.name)),
                    Explain::RawDirect {
                        placement: self.placement.clone(),
                        definition: read.definition.clone(),
                        kind: "def",
                        evidence: read.name.clone(),
                        category: RawCategory::Env.as_str(),
                        weak: false,
                    },
                );
                draft.base = Severity::Error;
                draft
            })
            .collect()
    }

    fn raw_direct(&self, hy_file: &HyFileIndex, raw: &raw_settings::RawSettingsSpec, module: &str) -> Vec<Draft> {
        let definitions = &hy_file.definitions;
        let allowed = match &raw.world_modules {
            Some(_) => "only modules of definitions in architecture.hy's :world-handlers (the allow-list of definitions that may touch the outside world) may touch raw side effects".to_string(),
            None => format!("the layers that may touch raw side effects are {}", raw.allowed.iter().map(|id| self.layer_name(*id)).collect::<Vec<_>>().join(", ")),
        };
        innermost_raw_evidence(definitions)
            .into_iter()
            .filter(|found| !boundary_allows(raw, module, definitions[found.definition].raw.direct[found.evidence].category))
            .map(|found| {
                let definition = &definitions[found.definition];
                let evidence = &definition.raw.direct[found.evidence];
                let mut draft = self.draft(
                    ProjectRule::RawSideEffectDirect,
                    evidence.range,
                    format!(
                        "definition {} ({}) directly touches raw side effect {} ({}) — {}",
                        definition.name,
                        self.layer_name(self.layer),
                        evidence.name,
                        evidence.category.as_str(),
                        allowed
                    ),
                    Some(format!("{}::{}", definition_label(definition), evidence.name)),
                    Explain::RawDirect {
                        placement: self.placement.clone(),
                        definition: definition_label(definition),
                        kind: definition.kind.as_str(),
                        evidence: evidence.name.clone(),
                        category: evidence.category.as_str(),
                        weak: evidence.strength == RawStrength::Weak,
                    },
                );
                draft.base = match evidence.strength {
                    RawStrength::Strong => Severity::Error,
                    RawStrength::Weak => Severity::Warning,
                };
                draft
            })
            .collect()
    }

    /// DOEFF131: places that name a doeff real-I/O handler listed in :wraps outside the allow-list definitions (and definitions nested inside them).
    /// Counts both references passed as values (the list of with-handlers) and calls, and merges the same handler in the same definition into one finding.
    fn world_handler_named(&self, hy_file: &HyFileIndex, architecture: &architecture::Architecture) -> Vec<Draft> {
        let definitions = &hy_file.definitions;
        world_handler_spots(hy_file, architecture)
            .into_iter()
            .map(|spot| {
                let WorldSpot { owner, spelling, allowed, range } = spot;
                let definition = owner.map(|i| definition_label(&definitions[i])).unwrap_or_else(|| "module の top level".to_string());
                let shown = owner.map(|i| definitions[i].name.clone()).unwrap_or_else(|| "module top level".to_string());
                self.draft(
                    ProjectRule::WorldHandlerNamedOutsideList,
                    range,
                    format!(
                        "{} names doeff real-I/O handler {} — only the definitions in architecture.hy's :world-handlers ({}) may name it",
                        shown,
                        spelling,
                        allowed
                    ),
                    Some(format!("{}::world::{}", definition, spelling)),
                    Explain::WorldHandlerNamed { placement: self.placement.clone(), definition: shown.clone(), wrapped: spelling.clone(), listed_by: allowed.clone() },
                )
            })
            .collect()
    }

    /// DOEFF107: raw side effects through the definitions called (info, with the path). Only a whole run has the evidence through calls.
    /// When both an outer definition and a nested definition have the same path and the same evidence, it is counted only for the inner one, and within one definition the same path and evidence name
    /// (such as two `.stat` in the same function) are merged into one finding.
    fn raw_via(&self, hy_file: &HyFileIndex) -> Vec<Draft> {
        let definitions = &hy_file.definitions;
        let mut groups: BTreeMap<ViaSpot, Vec<EvidenceRef>> = BTreeMap::new();
        for (definition_index, definition) in definitions.iter().enumerate() {
            for (via_index, via) in definition.raw.via.iter().enumerate() {
                let spot = ViaSpot {
                    through: via.through.iter().map(|s| s.name.clone()).collect(),
                    evidence: EvidenceSpot {
                        path: via.evidence.path.clone(),
                        start: via.evidence.range.start,
                        end: via.evidence.range.end,
                        name: via.evidence.name.clone(),
                    },
                };
                groups.entry(spot).or_default().push(EvidenceRef { definition: definition_index, evidence: via_index });
            }
        }
        let contains = |outer: &Definition, inner: &Definition| {
            outer.full_range.start <= inner.full_range.start
                && inner.full_range.end <= outer.full_range.end
                && outer.full_range != inner.full_range
        };
        let mut chosen: BTreeMap<ViaReport, EvidenceRef> = BTreeMap::new();
        for (spot, members) in groups {
            for member in &members {
                let has_inner = members
                    .iter()
                    .any(|other| other.definition != member.definition && contains(&definitions[member.definition], &definitions[other.definition]));
                if !has_inner {
                    let report = ViaReport { definition: member.definition, through: spot.through.clone(), name: spot.evidence.name.clone() };
                    chosen.entry(report).or_insert(*member);
                }
            }
        }
        chosen
            .into_values()
            .map(|found| {
                let definition = &definitions[found.definition];
                let via = &definition.raw.via[found.evidence];
                let path: Vec<&str> = via.through.iter().map(|s| s.name.as_str()).collect();
                let mut draft = self.draft(
                    ProjectRule::RawSideEffectVia,
                    definition.range,
                    format!(
                        "definition {} reaches, through {}, raw side effect {} ({}) ({})",
                        definition.name,
                        path.join(" → "),
                        via.evidence.name,
                        via.evidence.category.as_str(),
                        via.evidence.path
                    ),
                    Some(format!("{}::via::{}::{}", definition_label(definition), path.join(">"), via.evidence.name)),
                    Explain::RawVia {
                        placement: self.placement.clone(),
                        definition: definition_label(definition),
                        through: path.iter().map(|p| p.to_string()).collect(),
                        evidence: via.evidence.name.clone(),
                        category: via.evidence.category.as_str(),
                    },
                );
                draft.base = Severity::Info;
                draft
            })
            .collect()
    }
}

/// Position of evidence (file, range, name) — to distinguish evidence that overlaps between a nested definition and an outer one.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct EvidenceSpot {
    path: String,
    start: Position,
    end: Position,
    name: String,
}

/// Position of evidence through calls (the list of definition names on the path, and the evidence reached).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct ViaSpot {
    through: Vec<String>,
    evidence: EvidenceSpot,
}

/// The unit of one through-call notice (definition, path, evidence name — evidence of the same name in the same function is one finding).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct ViaReport {
    definition: usize,
    through: Vec<String>,
    name: String,
}

/// Where evidence is in the file's index (the definition's index, and the index in that definition's evidence list).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct EvidenceRef {
    definition: usize,
    evidence: usize,
}

/// The definition spelling used in keys (a nested one is `outer.inner`, mangled).
fn definition_label(definition: &Definition) -> String {
    match &definition.container {
        Some(container) => format!("{}.{}", hy_mangle(container), hy_mangle(&definition.name)),
        None => hy_mangle(&definition.name),
    }
}

/// Resolves an import target to a module of the population (the module itself, or `module.name`). None if outside the population.
fn resolve_target<'i>(target: &'i str, index: &SiteIndex<'_>) -> Option<(&'i str, ModuleSite)> {
    if let Some(site) = index.get(target) {
        return Some((target, site));
    }
    let (owner, _) = target.rsplit_once('.')?;
    index.get(owner).map(|site| (owner, site))
}

/// The spelling and file path of the module under root that an import spelling points to (that module or its parent) — None if absent, outside root, or a package marker.
/// `-` in a Hy spelling is read as `_` in a file name.
fn unplaced_owner(target: &str, root_module: &str, repo: &Path) -> Option<(String, String)> {
    let target = target.replace('-', "_");
    let parent = target.rsplit_once('.').map(|(owner, _)| owner.to_string());
    for candidate in std::iter::once(target.clone()).chain(parent) {
        if !candidate.starts_with(&format!("{}.", root_module)) {
            continue;
        }
        let base = candidate.replace('.', "/");
        if ["__init__.py", "__init__.hy"].iter().any(|init| repo.join(&base).join(init).is_file()) {
            return None;
        }
        if let Some(rel) = ["hy", "py"].iter().map(|ext| format!("{}.{}", base, ext)).find(|rel| repo.join(rel).is_file()) {
            return Some((candidate, rel));
        }
    }
    None
}

// --- Locations in architecture.hy (DOEFF114, 115) and unused dependencies (DOEFF117) ---------------

/// Whole run or single file (DOEFF115's dir notice is emitted once per dir in a whole run, and on that file in a single-file run).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PlaceScope {
    Whole,
    Single,
}

/// Whether this is a module under root (its extension is in the layer settings and it contains no excluded segment).
fn is_architecture_file(rel: &str, architecture: &architecture::Architecture, layers: &LayerSettings) -> bool {
    let root = layers::normalize_dir(&architecture.root);
    let extension_ok = Path::new(rel).extension().and_then(|e| e.to_str()).is_some_and(|e| layers.extensions.contains(e));
    extension_ok && rel.starts_with(&format!("{}/", root)) && !rel.split('/').any(|part| layers.exclude.contains(part))
}

/// Collects all modules under root (in path order).
fn collect_architecture_files(root: &Path, architecture: &architecture::Architecture, layers: &LayerSettings) -> Vec<SourceFile> {
    let mut files: Vec<SourceFile> = walk_files(&root.join(layers::normalize_dir(&architecture.root)))
        .into_iter()
        .filter_map(|path| {
            let rel = relative_path(root, &path)?;
            let language = language_of(&path)?;
            is_architecture_file(&rel, architecture, layers).then_some(SourceFile { rel, path, language })
        })
        .collect();
    files.sort_by(|a, b| a.rel.cmp(&b.rel));
    files
}

/// Comparison of a file against the location declarations (as declared, a file in an undeclared location, a dir not in the declaration).
enum PlaceVerdict {
    Declared,
    UndeclaredPlace(explain::PlaceProblem),
    UndeclaredDirectory { dir: String, problem: explain::DirectoryProblem },
}

/// Checks one file against the declaration.
fn place_verdict(rel: &str, architecture: &architecture::Architecture) -> PlaceVerdict {
    let root = layers::normalize_dir(&architecture.root);
    let rest = rel.strip_prefix(&format!("{}/", root)).unwrap_or(rel);
    // Package markers (`__init__.py`, `__init__.hy`) are not modules of a location.
    if Path::new(rel).file_stem().is_some_and(|stem| stem == "__init__") {
        return PlaceVerdict::Declared;
    }
    let parts: Vec<&str> = rest.split('/').collect();
    // Under the simulated environment's location (:verification-environment) counts as declared — a location that is not a service.
    if let (Some(place), [first, _, ..]) = (architecture.verification_environment.as_deref(), parts.as_slice()) {
        if *first == place {
            return PlaceVerdict::Declared;
        }
    }
    let service_layers: BTreeSet<&str> =
        architecture.layers.iter().map(|l| l.name.as_str()).filter(|l| architecture.foundation.as_deref() != Some(*l)).collect();
    match parts.as_slice() {
        [file] if Path::new(file).file_stem().and_then(|stem| stem.to_str()).is_some_and(|stem| architecture.root_modules.iter().any(|m| m == stem)) => {
            PlaceVerdict::Declared
        }
        [_file] => PlaceVerdict::UndeclaredPlace(explain::PlaceProblem::DirectlyUnderRoot),
        [first, ..] if architecture.foundation.as_deref() == Some(*first) => PlaceVerdict::Declared,
        [first, _file] if architecture.shared.as_deref() == Some(*first) || architecture.service_by_dir(first).is_some() => {
            PlaceVerdict::UndeclaredPlace(explain::PlaceProblem::DirectlyUnderService { service: first.to_string() })
        }
        [first, second, ..] if architecture.shared.as_deref() == Some(*first) => {
            if service_layers.contains(second) {
                PlaceVerdict::Declared
            } else {
                PlaceVerdict::UndeclaredDirectory {
                    dir: format!("{}/{}/{}", root, first, second),
                    problem: explain::DirectoryProblem::LayerNotDeclared { service: first.to_string(), layer: second.to_string(), declared: service_layers.iter().map(|l| l.to_string()).collect() },
                }
            }
        }
        [first, second, ..] => match architecture.service_by_dir(first) {
            Some(service) if service.layers.iter().any(|l| l == second) => PlaceVerdict::Declared,
            Some(service) => PlaceVerdict::UndeclaredDirectory {
                dir: format!("{}/{}/{}", root, first, second),
                problem: explain::DirectoryProblem::LayerNotDeclared { service: service.name.clone(), layer: second.to_string(), declared: service.layers.clone() },
            },
            None => PlaceVerdict::UndeclaredDirectory {
                dir: format!("{}/{}", root, first),
                problem: explain::DirectoryProblem::ServiceNotDeclared { dir: first.to_string(), services: architecture.services.iter().map(|s| s.name.clone()).collect() },
            },
        },
        [] => PlaceVerdict::Declared,
    }
}

/// DOEFF114, 115: checks modules under root against architecture.hy's declarations. In a whole run, 115 is emitted only once per dir, at the first file.
fn judge_places(
    root: &Path,
    architecture: &architecture::Architecture,
    layers: &LayerSettings,
    files: &[SourceFile],
    enabled: &BTreeSet<ProjectRule>,
    scope: PlaceScope,
    single_source: Option<&str>,
) -> Vec<Draft> {
    let _ = root;
    let mut drafts = Vec::new();
    // Proposed destination: the service from the :context tag (`-` becomes `_`), the layer from the layer inferred from the :role tag, or, if it cannot be inferred, the layer name of the current path segment.
    let destination = |file: &SourceFile| -> String {
        let source = match single_source {
            Some(text) => Some(text.to_string()),
            None => std::fs::read_to_string(&file.path).ok(),
        }
        .unwrap_or_default();
        let facts = read_facts(file.language, &source, &module_of(&file.rel), &layers.tags);
        let service = facts
            .tag_sets()
            .iter()
            .find_map(|t| t.context.clone().filter(|c| !c.is_empty()))
            .map(|c| c.replace('-', "_"))
            .unwrap_or_else(|| "<service>".to_string());
        let parts: Vec<&str> = file.rel.split('/').collect();
        let by_path = layers.layers.iter().find(|l| parts[..parts.len().saturating_sub(1)].contains(&l.name.as_str())).map(|l| l.name.clone());
        // The layer, if exactly one layer allows all the :role tags.
        let roles: Vec<String> = facts.tag_sets().iter().filter_map(|t| t.role.clone()).filter(|r| !r.is_empty()).collect();
        let by_roles = || {
            let fitting: Vec<&layers::LayerSpec> = layers
                .layers
                .iter()
                .filter(|l| !roles.is_empty() && roles.iter().all(|role| l.roles.as_ref().is_some_and(|allowed| allowed.contains(role))))
                .collect();
            match fitting.as_slice() {
                [only] => Some(only.name.clone()),
                _ => None,
            }
        };
        // The layer is inferred from the definition's :role (DOEFF114 is emitted because the location is wrong — the current path segment cannot be the proposal), and only when it cannot be inferred, the layer name of the path segment.
        let layer = by_roles().or(by_path).unwrap_or_else(|| "<layer>".to_string());
        let name = parts.last().copied().unwrap_or("");
        let root_dir = layers::normalize_dir(&architecture.root);
        match architecture.foundation.as_deref() == Some(layer.as_str()) {
            true => format!("{}/{}/{}", root_dir, layer, name),
            false => format!("{}/{}/{}/{}", root_dir, service, layer, name),
        }
    };
    let mut reported_dirs = BTreeSet::new();
    for file in files {
        let draft = |rule: ProjectRule, range: Range, message: String, detail: Option<String>, explain: Explain, rel: String| Draft {
            rule,
            layer: None,
            rel,
            path: file.path.clone(),
            range,
            message,
            detail,
            base: Severity::Error,
            explain,
        };
        match place_verdict(&file.rel, architecture) {
            PlaceVerdict::Declared => {}
            PlaceVerdict::UndeclaredPlace(problem) => {
                if enabled.contains(&ProjectRule::UndeclaredPlace) {
                    drafts.push(draft(
                        ProjectRule::UndeclaredPlace,
                        zero_range(),
                        format!("{} is in an undeclared location", file.rel),
                        None,
                        Explain::UndeclaredPlace { rel: file.rel.clone(), problem, root: architecture.root.clone(), destination: destination(file) },
                        file.rel.clone(),
                    ));
                }
            }
            PlaceVerdict::UndeclaredDirectory { dir, problem } => {
                // A module in a dir outside the declaration is also emitted per file (visible per file in the editor, with a destination proposal attached per file).
                if enabled.contains(&ProjectRule::UndeclaredPlace) {
                    drafts.push(draft(
                        ProjectRule::UndeclaredPlace,
                        zero_range(),
                        format!("{} is in an undeclared location", file.rel),
                        None,
                        Explain::UndeclaredPlace {
                            rel: file.rel.clone(),
                            problem: explain::PlaceProblem::InUndeclaredDirectory { dir: dir.clone() },
                            root: architecture.root.clone(),
                            destination: destination(file),
                        },
                        file.rel.clone(),
                    ));
                }
                let first_in_dir = scope == PlaceScope::Single || reported_dirs.insert(dir.clone());
                if enabled.contains(&ProjectRule::UndeclaredDirectory) && first_in_dir {
                    drafts.push(draft(
                        ProjectRule::UndeclaredDirectory,
                        zero_range(),
                        format!("dir {} is not declared in architecture.hy", dir),
                        None,
                        Explain::UndeclaredDirectory { dir: dir.clone(), problem },
                        dir,
                    ));
                }
            }
        }
    }
    drafts
}

/// A zero-length range at the head of the file (a location violation applies to the whole file).
fn zero_range() -> Range {
    let start = Position { line: 0, character: 0 };
    Range { start, end: start }
}

/// DOEFF117 (info): a declared dependency that no module of that service reads. Positioned at the defservice name in architecture.hy.
fn judge_unused_dependencies(root: &Path, architecture: &architecture::Architecture, crossings: &BTreeSet<(String, String)>) -> Vec<Draft> {
    let mut drafts = Vec::new();
    for service in &architecture.services {
        for dependency in &service.depends_on {
            if crossings.contains(&(service.name.clone(), dependency.clone())) {
                continue;
            }
            drafts.push(Draft {
                rule: ProjectRule::UnusedDependency,
                layer: None,
                rel: declared_rel(root, &service.file),
                path: service.file.clone(),
                range: service.range,
                message: format!("{}'s :depends-on entry {} is not read by any module of {}", service.name, dependency, service.name),
                detail: Some(format!("{}>{}", service.name, dependency)),
                base: Severity::Info,
                explain: Explain::UnusedDependency { service: service.name.clone(), dependency: dependency.clone() },
            });
        }
    }
    drafts
}

/// DOEFF132: each definition of the allow-list one by one — whether the module is in a layer location, whether that layer is foundation, and, for a Hy module, whether the definition exists.
/// Positioned at the element of the list in architecture.hy (whole run only).
fn judge_world_handler_places(
    root: &Path,
    architecture: &architecture::Architecture,
    layers: &layers::LayerSettings,
    layer_files: &[LayerFile],
    hy: &HashMap<String, HyFileIndex>,
) -> Vec<Draft> {
    let rel = declared_rel(root, &architecture.path);
    let foundation = architecture.foundation.as_deref();
    let mut drafts = Vec::new();
    for handler in &architecture.world_handlers {
        let module = handler.definition.mangled_module();
        let file = layer_files.iter().find(|f| architecture::mangle_dotted(&f.module) == module);
        let problem = match file {
            None => Some(format!("module {} is not in a layer location (no such module, or a location outside the layers)", handler.definition.module)),
            Some(file) => {
                let layer = &layers.layers[file.site.layer.0].name;
                if Some(layer.as_str()) != foundation {
                    Some(format!("{} is in layer {} — place it only in the foundation layer", file.file.rel, layer))
                } else {
                    let target = handler.definition.target();
                    match hy.get(&file.file.rel) {
                        Some(index) if !index.definitions.iter().any(|d| d.qualified_name == target) => {
                            Some(format!("{} has no definition {}", file.file.rel, handler.definition.name))
                        }
                        _ => None,
                    }
                }
            }
        };
        if let Some(problem) = problem {
            let spelling = handler.definition.spelling();
            drafts.push(Draft {
                rule: ProjectRule::WorldHandlerMisplaced,
                layer: None,
                rel: rel.clone(),
                path: architecture.path.clone(),
                range: handler.range,
                message: format!("allow-list definition {} — {}", spelling, problem),
                detail: Some(spelling.clone()),
                base: Severity::Error,
                explain: Explain::WorldHandlerMisplaced { definition: spelling, problem },
            });
        }
    }
    drafts
}

/// Whether definition range inner is inside outer (the same range is not nested).
fn range_inside(inner: &Range, outer: &Range) -> bool {
    outer.start <= inner.start && inner.end <= outer.end && inner != outer
}


/// Reading of the marks of one test file (read once per file with the Hy reader — not re-read per deftest).
/// Module marks = the spelling of the value of a top-level `(val pytestmark value)` / `(setv pytestmark value)`. Deftest marks = start line of the deftest -> the sequence of spellings in :marks
/// (read the same way as doeff-hy's deftest — remove one fixture list `[..]` after the name, skip the leading string, and take :marks of the first dict).
/// Spellings in comments or in strings inside a test body are not marks. Deftests nested inside defadr and the like are also read.
struct FileMarks {
    module: Vec<String>,
    deftests: HashMap<usize, String>,
    /// The start lines of deftests that have a non-empty `:interpreters` (DOEFF137).
    interpreters: HashSet<usize>,
    /// Start line of deftest -> spellings of the elements of `:interpreters` that are symbols (DOEFF133 follows from the definition of the name constant and the assembly of the name -> assembly table).
    interpreter_symbols: HashMap<usize, Vec<String>>,
}

impl FileMarks {
    fn read(source: &str) -> FileMarks {
        use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
        let text = |form: &Form| source.get(form.span.start..form.span.end).unwrap_or("").to_string();
        let forms = Reader::new(source, 0, source.len()).read_all();
        let module = forms
            .iter()
            .filter_map(|form| match form.paren_items() {
                Some([head, name, value, ..])
                    if matches!(head.node, Node::Symbol) && matches!(text(head).as_str(), "val" | "setv") && text(name) == "pytestmark" =>
                {
                    Some(text(value))
                }
                _ => None,
            })
            .collect();
        // Byte position of the head of each line (look up the form's start line by binary search).
        let starts: Vec<usize> = std::iter::once(0).chain(source.match_indices('\n').map(|(at, _)| at + 1)).collect();
        let line_of = |form: &Form| starts.partition_point(|start| *start <= form.span.start).saturating_sub(1);
        let mut deftests = HashMap::new();
        let mut interpreters = HashSet::new();
        let mut interpreter_symbols = HashMap::new();
        let mut stack: Vec<&Form> = forms.iter().collect();
        while let Some(form) = stack.pop() {
            // A nested one is inside any parentheses (e.g. in the :tests [(deftest …) …] list of defadr).
            let Node::Seq { delim, items } = &form.node else { continue };
            stack.extend(items.iter());
            if *delim != Delim::Paren || !items.first().is_some_and(|head| text(head) == "deftest") {
                continue;
            }
            let mut body = items.get(2..).unwrap_or_default();
            if body.first().is_some_and(|form| form.bracket_items().is_some()) {
                body = &body[1..];
            }
            let options = body.iter().find(|form| !matches!(form.node, Node::Str { .. }));
            let option = |name: &str| match options.map(|options| &options.node) {
                Some(Node::Seq { delim: Delim::Brace, items }) => {
                    items.chunks(2).find(|pair| pair.first().is_some_and(|key| text(key) == name)).and_then(|pair| pair.get(1))
                }
                _ => None,
            };
            if let Some(marks) = option(":marks") {
                deftests.insert(line_of(form), text(marks));
            }
            // DOEFF137 only looks at whether the list is non-empty. DOEFF133 resolves the element symbols to the definitions of name constants and follows them (string elements are names that the conftest table looks up,
            // and cannot be resolved by the Hy index).
            if let Some(Node::Seq { items, .. }) = option(":interpreters").map(|value| &value.node) {
                if !items.is_empty() {
                    interpreters.insert(line_of(form));
                    let symbols: Vec<String> = items.iter().filter(|item| matches!(item.node, Node::Symbol)).map(text).collect();
                    if !symbols.is_empty() {
                        interpreter_symbols.insert(line_of(form), symbols);
                    }
                }
            }
        }
        FileMarks { module, deftests, interpreters, interpreter_symbols }
    }

    /// Whether a test definition has a non-empty `:interpreters` (an edge test — passing the same test through real and simulated interpreters, DOEFF137).
    fn runs_interpreters(&self, test: &Definition) -> bool {
        self.interpreters.contains(&(test.full_range.start.line as usize))
    }

    /// Spellings of the elements of a test definition's `:interpreters` that are symbols (DOEFF133).
    fn interpreter_symbols(&self, test: &Definition) -> &[String] {
        self.interpreter_symbols.get(&(test.full_range.start.line as usize)).map_or(&[], Vec::as_slice)
    }

    /// Whether a test definition or the module head has the edge mark `mark`. The mark's name is read in both the Python spelling (`real_world`) and the Hy spelling (`real-world`).
    fn carries(&self, test: &Definition, mark: &str) -> bool {
        let spellings = [mark.to_string(), mark.replace('_', "-")];
        let named = |text: &str| spellings.iter().any(|s| text.contains(&format!("\"{}\"", s)) || text.contains(&format!("mark.{}", s)));
        self.deftests.get(&(test.full_range.start.line as usize)).is_some_and(|marks| named(marks)) || self.module.iter().any(|value| named(value))
    }
}

/// The graph of edges between definitions (calls, references, nesting) — built once from the whole index (used by DOEFF133 and 136).
struct DefinitionGraph<'h> {
    rels: Vec<&'h String>,
    /// One definition = one node (in file order, then index order within a file).
    nodes: Vec<(&'h str, usize)>,
    base: HashMap<&'h str, usize>,
    /// world[n] = the reason that definition itself touches the outside world (the spelling of an allow-list definition, the spelling of a :wraps handler, the name of raw I/O evidence).
    world: Vec<Option<String>>,
    /// callers[n] = definitions that reach n (the reverse direction of edges).
    callers: Vec<Vec<usize>>,
    /// world_carried[n] = the reason it touches the outside world inside a system value (the body of defsystem, the arguments of :carriers in :systems).
    world_carried: Vec<Option<String>>,
    /// carried[n] = definitions that reach n by an edge inside a system value (only carried as a system value; edges that do not run here).
    carried: Vec<Vec<usize>>,
    /// runs[n] = that definition names an entry that runs systems (:runners in :systems).
    runs: Vec<bool>,
    /// References outside the index (node, the fully qualified name named, whether inside a system value) — only names that the `foreign` passed at build time accepted (DOEFF133's
    /// Python functions).
    foreign: Vec<(usize, String, bool)>,
}

fn definition_graph<'h>(architecture: &architecture::Architecture, hy: &'h HashMap<String, HyFileIndex>) -> DefinitionGraph<'h> {
    definition_graph_with(architecture, hy, &|_| false)
}

/// Builds the graph of edges between definitions, and among names outside the index, leaves the references that foreign accepts in the `foreign` field (not as edges — the reading side decides).
fn definition_graph_with<'h>(
    architecture: &architecture::Architecture,
    hy: &'h HashMap<String, HyFileIndex>,
    foreign: &dyn Fn(&str) -> bool,
) -> DefinitionGraph<'h> {
    let listed: HashMap<String, (String, Vec<architecture::WorldTouch>)> =
        architecture.world_handlers.iter().map(|h| (h.definition.target(), (h.definition.spelling(), h.touches.clone()))).collect();
    let catalog = architecture.world_catalog();
    let wrapped = catalog.world_targets(architecture);
    let static_readers: BTreeSet<String> = architecture.static_readers.iter().map(|r| r.target()).collect();
    // What builds a system value = :carriers and the index's defsystem definitions (the foundation of the arguments of their calls is carried as a system value).
    let carriers: BTreeSet<String> = architecture
        .systems
        .iter()
        .flat_map(|s| s.carriers.iter().map(|r| r.target()))
        .chain(architecture.systems.iter().flat_map(|_| {
            hy.values().flat_map(|f| f.definitions.iter().filter(|d| d.kind == DefinitionKind::Defsystem).map(|d| d.qualified_name.clone()))
        }))
        .collect();
    let runners: BTreeSet<String> = architecture.systems.iter().flat_map(|s| s.runners.iter().map(|r| r.target())).collect();
    let mut rels: Vec<&String> = hy.keys().collect();
    rels.sort();
    // One definition = one node (in file order, then index order within a file).
    let mut nodes: Vec<(&str, usize)> = Vec::new();
    let mut base: HashMap<&str, usize> = HashMap::new();
    let mut by_name: HashMap<&str, usize> = HashMap::new();
    for rel in &rels {
        base.insert(rel.as_str(), nodes.len());
        for (index, definition) in hy[*rel].definitions.iter().enumerate() {
            by_name.entry(definition.qualified_name.as_str()).or_insert(nodes.len());
            nodes.push((rel.as_str(), index));
        }
    }
    // world[n] = the reason that definition itself touches the outside world (the spelling of an allow-list definition, the spelling of a :wraps handler, the name of raw I/O evidence).
    let mut world: Vec<Option<String>> = vec![None; nodes.len()];
    // callers[n] = definitions that reach n (the reverse direction of edges).
    let mut callers: Vec<Vec<usize>> = vec![Vec::new(); nodes.len()];
    let mut world_carried: Vec<Option<String>> = vec![None; nodes.len()];
    let mut carried: Vec<Vec<usize>> = vec![Vec::new(); nodes.len()];
    let mut runs: Vec<bool> = vec![false; nodes.len()];
    let mut foreign_spots: Vec<(usize, String, bool)> = Vec::new();
    for rel in &rels {
        let file = &hy[*rel];
        let first = base[rel.as_str()];
        let definitions = &file.definitions;
        for (index, definition) in definitions.iter().enumerate() {
            // Only the :edge-touches targets count as an edge (an allow-list definition by its :touches, raw I/O by the classification of the evidence).
            if let Some((spelling, _)) = listed.get(&definition.qualified_name).filter(|(_, t)| architecture.counts_as_edge(t)) {
                world[first + index] = Some(spelling.clone());
            } else if let Some(evidence) = definition
                .raw
                .direct
                .iter()
                .find(|e| e.strength == RawStrength::Strong && architecture.counts_as_edge(&[world_catalog::touch_of_raw(e.category)]))
            {
                world[first + index] = Some(evidence.name.clone());
            }
        }
        // Nesting: an outer definition reaches what the inner definition reaches (arrange in start order and look up the parent with a stack of open definitions).
        let mut order: Vec<usize> = (0..definitions.len()).collect();
        order.sort_by(|a, b| definitions[*a].full_range.start.cmp(&definitions[*b].full_range.start).then(definitions[*b].full_range.end.cmp(&definitions[*a].full_range.end)));
        let mut open: Vec<usize> = Vec::new();
        for index in order {
            while open.last().is_some_and(|top| !range_inside(&definitions[index].full_range, &definitions[*top].full_range)) {
                open.pop();
            }
            if let Some(parent) = open.last() {
                callers[first + index].push(first + *parent);
            }
            open.push(index);
        }
        let in_import = |range: &Range| file.imports.iter().any(|imp| imp.range.start <= range.start && range.end <= imp.range.end);
        // References inside the arguments of calls to read-only receivers (:static-readers) are only read as values and not executed — they are not edges.
        let read_only: Vec<&Range> =
            file.calls.iter().filter(|c| c.target.as_deref().is_some_and(|t| static_readers.contains(t))).map(|c| &c.form_range).collect();
        let only_read = |range: &Range| read_only.iter().any(|form| range_inside(range, form));
        // Names inside a system value (the arguments of :carriers calls, the body of defsystem) are edges that are only carried as a system value (#1390).
        let carrier_forms: Vec<&Range> =
            file.calls.iter().filter(|c| c.target.as_deref().is_some_and(|t| carriers.contains(t))).map(|c| &c.form_range).collect();
        let in_carrier = |range: &Range| carrier_forms.iter().any(|form| range_inside(range, form));
        // Look only at names that become edges (index definitions or :wraps handlers) — do not look up the owner definitions of other names.
        let known = |t: &str| by_name.contains_key(t) || wrapped.contains_key(t) || runners.contains(t);
        let interesting = |t: &str| known(t) || foreign(t);
        // A reference that only inspects a value (a comparison form or an operand of assert — the index's `Reference::inspected`) does not call, wrap or pass the named value, so
        // it is not an edge. References that call, put in a with-handlers list, pass as an argument of another definition, or read an attribute outside a comparison are edges as before.
        let spots = file
            .references
            .iter()
            .filter(|r| r.target.as_deref().is_some_and(interesting) && !in_import(&r.range) && !only_read(&r.range) && !r.inspected)
            .filter_map(|r| r.target.as_deref().map(|t| (t, innermost_definition(definitions, &r.range), in_carrier(&r.range))))
            .chain(file.calls.iter().filter_map(|c| {
                c.target.as_deref().filter(|t| interesting(t)).map(|t| (t, c.caller, in_carrier(&c.range)))
            }));
        for (target, owner, inside_carrier) in spots {
            let Some(owner_index) = owner else { continue };
            let owner = first + owner_index;
            // A repo that does not declare :systems works as before (edges inside defsystem are ordinary edges — the entry is unknown, so the inside of a system is not counted separately).
            let is_carried = architecture.systems.is_some() && (inside_carrier || definitions[owner_index].kind == DefinitionKind::Defsystem);
            if !known(target) {
                foreign_spots.push((owner, target.to_string(), is_carried));
                continue;
            }
            if runners.contains(target) {
                runs[owner] = true;
                continue;
            }
            if let Some((spelling, _, touches)) = wrapped.get(target) {
                // Rows the catalog does not count (rows that can be written in :wraps only during migration) do not touch the outside world.
                let counted = catalog.handlers.get(target).is_some_and(|h| h.counts());
                if counted && architecture.counts_as_edge(touches) {
                    let slot = if is_carried { &mut world_carried[owner] } else { &mut world[owner] };
                    if slot.is_none() {
                        *slot = Some(spelling.clone());
                    }
                }
            } else if let Some(&callee) = by_name.get(target) {
                if callee != owner {
                    if is_carried { carried[callee].push(owner) } else { callers[callee].push(owner) }
                }
            }
        }
    }
    DefinitionGraph { rels, nodes, base, world, callers, world_carried, carried, runs, foreign: foreign_spots }
}

/// Nodes that reach a seed node on the graph of edges between definitions (used by DOEFF133 and 137). reaches[n] = n reaches a seed; toward[n] = the next node from n toward a seed
/// (None if n itself is a seed).
struct Reach {
    reaches: Vec<bool>,
    toward: Vec<Option<usize>>,
}

/// Definitions that reach an entry that runs systems (:runners in :systems) (follow ordinary edges in reverse from the definitions that name the entry).
fn running_nodes(graph: &DefinitionGraph) -> Vec<bool> {
    let mut running = graph.runs.clone();
    let mut queue: std::collections::VecDeque<usize> = (0..graph.nodes.len()).filter(|n| running[*n]).collect();
    while let Some(node) = queue.pop_front() {
        for &caller in &graph.callers[node] {
            if !running[caller] {
                running[caller] = true;
                queue.push_back(caller);
            }
        }
    }
    running
}

/// Follows in reverse from the seed nodes (the same way as DOEFF133's edge counting). seeds[n] = n itself is a seed; carried_seeds[n] = n touches a seed inside a system value.
/// Two stages: direct = reaches without passing an edge inside a system value; via_system = reaches by passing an edge inside a system value at least once (#1390). Reaches = reaches directly, or reaches from inside a system value
/// and also reaches an entry that runs systems (running).
fn reach_seeds(graph: &DefinitionGraph, seeds: &[bool], carried_seeds: &[bool], running: &[bool]) -> Reach {
    let (callers, carried, count) = (&graph.callers, &graph.carried, graph.nodes.len());
    let mut direct: Vec<bool> = seeds.to_vec();
    let mut toward: Vec<Option<usize>> = vec![None; count];
    let mut queue: std::collections::VecDeque<usize> = (0..count).filter(|n| direct[*n]).collect();
    while let Some(node) = queue.pop_front() {
        for &caller in &callers[node] {
            if !direct[caller] {
                direct[caller] = true;
                toward[caller] = Some(node);
                queue.push_back(caller);
            }
        }
    }
    let mut via_system: Vec<bool> = vec![false; count];
    let mut queue: std::collections::VecDeque<usize> = std::collections::VecDeque::new();
    let mark_via = |node: usize, from: Option<usize>, via_system: &mut Vec<bool>, toward: &mut Vec<Option<usize>>, queue: &mut std::collections::VecDeque<usize>| {
        if !direct[node] && !via_system[node] {
            via_system[node] = true;
            toward[node] = from;
            queue.push_back(node);
        }
    };
    for node in 0..count {
        if carried_seeds[node] {
            mark_via(node, None, &mut via_system, &mut toward, &mut queue);
        }
        if direct[node] {
            for &caller in &carried[node] {
                mark_via(caller, Some(node), &mut via_system, &mut toward, &mut queue);
            }
        }
    }
    while let Some(node) = queue.pop_front() {
        for &caller in callers[node].iter().chain(carried[node].iter()) {
            mark_via(caller, Some(node), &mut via_system, &mut toward, &mut queue);
        }
    }
    let reaches: Vec<bool> = (0..count).map(|n| direct[n] || (via_system[n] && running[n])).collect();
    Reach { reaches, toward }
}

/// DOEFF133: derives a test's kind from what it reaches. Builds the graph of edges between definitions (calls, references, nesting) once from the whole index, and follows in reverse from the outside-world side
/// (allow-list definitions, definitions that name a :wraps handler, definitions with strong raw I/O evidence) to find the set of "definitions that reach the outside world". If a deftest is in that set it is an edge (the :edge-mark is required); otherwise it is local (no mark).
/// Python tests are not counted (moved to deftest in R6).
/// For Python functions in the repo that a Hy definition names, follows their strong raw I/O evidence and the callees decided by name (Python functions, Hy definitions)
/// (python_reach). An unreadable Python module is named in the second answer (the report's errors).
fn judge_test_kinds(
    root: &Path,
    architecture: &architecture::Architecture,
    hy: &HashMap<String, HyFileIndex>,
    catalog: &RawCatalog,
) -> (Vec<Draft>, Vec<String>) {
    let Some(mark) = architecture.edge_mark.as_deref() else { return (Vec::new(), Vec::new()) };
    let python = python_reach::PythonReach::new(root, catalog);
    let mut graph = definition_graph_with(architecture, hy, &|target| python.names_function(target));
    let by_name: HashMap<&str, usize> = graph
        .nodes
        .iter()
        .enumerate()
        .rev()
        .map(|(node, (rel, index))| (hy[*rel].definitions[*index].qualified_name.as_str(), node))
        .collect();
    // Symbols of a deftest's :interpreters -> the definitions of name constants, and the assembly that the module's name -> assembly table assigned to that name (the real side's assembly
    // reaches the network and cluster — the contract test is an edge on the real side). Python functions outside the index are followed by foreign, like other references.
    let marks_of: HashMap<String, FileMarks> = graph
        .rels
        .iter()
        .filter(|rel| hy[**rel].definitions.iter().any(|d| d.kind == DefinitionKind::Deftest))
        .filter_map(|rel| std::fs::read_to_string(root.join(rel.as_str())).ok().map(|source| (rel.to_string(), FileMarks::read(&source))))
        .collect();
    let mut tables: HashMap<String, HashMap<String, Vec<String>>> = HashMap::new();
    for rel in &graph.rels {
        let Some(marks) = marks_of.get(rel.as_str()) else { continue };
        let file = &hy[*rel];
        let first = graph.base[rel.as_str()];
        for (index, test) in file.definitions.iter().enumerate().filter(|(_, d)| d.kind == DefinitionKind::Deftest) {
            for symbol in marks.interpreter_symbols(test) {
                let Some(name) = file
                    .references
                    .iter()
                    .filter(|r| !r.member && (r.name == *symbol || r.mangled == *symbol) && range_inside(&r.range, &test.full_range))
                    .find_map(|r| r.target.clone())
                else {
                    continue;
                };
                let Some(&constant) = by_name.get(name.as_str()) else { continue };
                let (owner_rel, _) = graph.nodes[constant];
                let table = tables.entry(owner_rel.to_string()).or_insert_with(|| interpreter_table(root, owner_rel, &hy[owner_rel]));
                let assemblies = table.get(&name).cloned().unwrap_or_default();
                for target in std::iter::once(name).chain(assemblies) {
                    if let Some(&callee) = by_name.get(target.as_str()) {
                        graph.callers[callee].push(first + index);
                    } else if python.names_function(&target) {
                        graph.foreign.push((first + index, target, false));
                    }
                }
            }
        }
    }
    let edge = |category: RawCategory| architecture.counts_as_edge(&[world_catalog::touch_of_raw(category)]);
    let mut answers: HashMap<String, python_reach::PythonReached> = HashMap::new();
    for (owner, target, is_carried) in std::mem::take(&mut graph.foreign) {
        let reached = answers.entry(target.clone()).or_insert_with(|| python.reach(&target, &edge, &|name| by_name.contains_key(name)));
        if let Some(trail) = &reached.world {
            let slot = if is_carried { &mut graph.world_carried[owner] } else { &mut graph.world[owner] };
            if slot.is_none() {
                *slot = Some(trail.join(" → "));
            }
        }
        for callee in reached.hy.iter().filter_map(|name| by_name.get(name.as_str())) {
            if *callee != owner {
                if is_carried { graph.carried[*callee].push(owner) } else { graph.callers[*callee].push(owner) }
            }
        }
    }
    let python_errors = python.errors();
    let running = running_nodes(&graph);
    let seeds: Vec<bool> = graph.world.iter().map(Option::is_some).collect();
    let carried_seeds: Vec<bool> = graph.world_carried.iter().map(Option::is_some).collect();
    let Reach { reaches, toward } = reach_seeds(&graph, &seeds, &carried_seeds, &running);
    let DefinitionGraph { rels, nodes, base, world, world_carried, .. } = graph;
    let world: Vec<Option<String>> = world.into_iter().zip(world_carried).map(|(w, c)| w.or(c)).collect();
    let name_of = |node: usize| -> String {
        let (rel, index) = nodes[node];
        hy[rel].definitions[index].name.clone()
    };
    let mut drafts = Vec::new();
    for rel in &rels {
        let file = &hy[*rel];
        let first = base[rel.as_str()];
        let tests: Vec<usize> = file.definitions.iter().enumerate().filter(|(_, d)| d.kind == DefinitionKind::Deftest).map(|(i, _)| i).collect();
        if tests.is_empty() {
            continue;
        }
        let Some(marks) = marks_of.get(rel.as_str()) else { continue };
        for index in tests {
            let test = &file.definitions[index];
            let edge = reaches[first + index];
            let marked = marks.carries(test, mark);
            if edge == marked {
                continue;
            }
            let mut reached = Vec::new();
            let mut at = first + index;
            while let Some(next) = toward[at] {
                reached.push(name_of(next));
                at = next;
            }
            if let Some(reason) = &world[at] {
                reached.push(reason.clone());
            }
            let message = if edge {
                format!("test {} reaches the outside world (edge — {}) but has no mark {}", test.name, reached.join(" → "), mark)
            } else {
                format!("test {} does not reach the outside world (local) but has the mark {}", test.name, mark)
            };
            drafts.push(Draft {
                rule: ProjectRule::TestKindMismatch,
                layer: None,
                rel: (*rel).clone(),
                path: root.join(rel.as_str()),
                range: test.range,
                message,
                detail: Some(format!("{}::{}", definition_label(test), if edge { "edge" } else { "local" })),
                base: Severity::Error,
                explain: Explain::TestKindMismatch { test: test.name.clone(), edge, mark: mark.to_string(), reached },
            });
        }
    }
    (drafts, python_errors)
}

/// DOEFF137: judges for each handler of the allow-list whether an edge test exists. Edge test = among deftests with a non-empty `:interpreters`, one that follows the same graph of edges between definitions as DOEFF133
/// and reaches that handler's definition (the elements of `:interpreters` are symbols of constants outside the file, so they are not resolved).
/// A handler with `:contract-test (none …)` with a reason is not judged; a `none` without a reason fires even if an edge test exists. The finding is positioned at the allow-list element in architecture.hy,
/// and the detail is the allow-list spelling (#1796).
/// For each pair in the stand-in list (:stand-ins), it also counts edge tests that reach both the stand-in and the real one (verified fake, Pact shape — run the same test through both
/// and compare the answers). The real one may be outside the index (a package such as doeff), in which case follow from the definitions that name the real one directly. The detail is "stand-in->real".
fn judge_contract_tests(root: &Path, architecture: &architecture::Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<Draft> {
    let judged: Vec<&architecture::WorldHandler> =
        architecture.world_handlers.iter().filter(|h| !matches!(h.contract_test, architecture::ContractTest::Waived(_))).collect();
    let judged_stand_ins: Vec<&architecture::StandIn> =
        architecture.stand_ins.iter().filter(|s| !matches!(s.contract_test, architecture::ContractTest::Waived(_))).collect();
    if judged.is_empty() && judged_stand_ins.is_empty() {
        return Vec::new();
    }
    let graph = definition_graph(architecture, hy);
    let running = running_nodes(&graph);
    // Nodes of edge tests (deftests with a non-empty :interpreters) — read once per file.
    let mut contract_tests: Vec<usize> = Vec::new();
    for rel in &graph.rels {
        let file = &hy[*rel];
        let first = graph.base[rel.as_str()];
        let tests: Vec<usize> = file.definitions.iter().enumerate().filter(|(_, d)| d.kind == DefinitionKind::Deftest).map(|(i, _)| i).collect();
        if tests.is_empty() {
            continue;
        }
        let Ok(source) = std::fs::read_to_string(root.join(rel.as_str())) else { continue };
        let marks = FileMarks::read(&source);
        contract_tests.extend(tests.into_iter().filter(|index| marks.runs_interpreters(&file.definitions[*index])).map(|index| first + index));
    }
    let node_of: HashMap<&str, usize> = graph
        .nodes
        .iter()
        .enumerate()
        .rev()
        .map(|(node, (rel, index))| (hy[*rel].definitions[*index].qualified_name.as_str(), node))
        .collect();
    let rel = declared_rel(root, &architecture.path);
    let no_carried = vec![false; graph.nodes.len()];
    let mut drafts = Vec::new();
    for handler in judged {
        let target = handler.definition.target();
        let covered = || {
            node_of.get(target.as_str()).is_some_and(|&node| {
                let mut seeds = vec![false; graph.nodes.len()];
                seeds[node] = true;
                let reach = reach_seeds(&graph, &seeds, &no_carried, &running);
                contract_tests.iter().any(|test| reach.reaches[*test])
            })
        };
        let Some(breach) = contract_test_breach(&handler.contract_test, covered) else { continue };
        let spelling = handler.definition.spelling();
        let message = match breach {
            ContractTestBreach::NoEdgeTest => format!("allow-list handler {} has no edge test (a deftest with a non-empty :interpreters that reaches this handler)", spelling),
            ContractTestBreach::NoneWithoutReason => {
                format!("allow-list handler {} has no reason in :contract-test none (write the name of a test that explains why no edge test is required)", spelling)
            }
        };
        drafts.push(Draft {
            rule: ProjectRule::WorldHandlerWithoutContractTest,
            layer: None,
            rel: rel.clone(),
            path: architecture.path.clone(),
            range: handler.range,
            message,
            detail: Some(spelling.clone()),
            base: Severity::Error,
            explain: Explain::WorldHandlerWithoutContractTest { handler: spelling, breach },
        });
    }
    // Seeds are the definition of the name (if in the index), the definitions that name it directly (call, pass as a value, read an attribute — excluding inspection-only references and imports), and
    // the definitions that spell it as an interpreter name string (`"module:name"` / `"module:name@<variant>"`), followed in reverse. A real one outside the index (such as doeff's
    // handler) has no definition node, so only the definitions that name it become seeds.
    let spelled = if judged_stand_ins.is_empty() { HashMap::new() } else { spelled_handlers(root, &graph, hy) };
    let reach_of = |target: &str| -> Vec<bool> {
        let mut seeds = vec![false; graph.nodes.len()];
        if let Some(&node) = node_of.get(target) {
            seeds[node] = true;
        }
        for &node in spelled.get(target).into_iter().flatten() {
            seeds[node] = true;
        }
        for rel in &graph.rels {
            let file = &hy[*rel];
            let first = graph.base[rel.as_str()];
            let in_import = |range: &Range| file.imports.iter().any(|imp| imp.range.start <= range.start && range.end <= imp.range.end);
            let named = file
                .references
                .iter()
                .filter(|r| r.target.as_deref() == Some(target) && !r.inspected && !in_import(&r.range))
                .filter_map(|r| innermost_definition(&file.definitions, &r.range))
                .chain(file.calls.iter().filter(|c| c.target.as_deref() == Some(target)).filter_map(|c| c.caller));
            for owner in named {
                seeds[first + owner] = true;
            }
        }
        reach_seeds(&graph, &seeds, &no_carried, &running).reaches
    };
    for stand_in in judged_stand_ins {
        let target = stand_in.definition.target();
        let covered = || {
            let (by_stand_in, by_real) = (reach_of(&target), reach_of(&stand_in.real.target()));
            contract_tests.iter().any(|test| by_stand_in[*test] && by_real[*test])
        };
        let Some(breach) = contract_test_breach(&stand_in.contract_test, covered) else { continue };
        let (spelling, real) = (stand_in.definition.spelling(), stand_in.real.spelling());
        let message = match breach {
            ContractTestBreach::NoEdgeTest => format!(
                "stand-in {} has no contract test of the same contract as real {} (a deftest with a non-empty :interpreters that reaches both the stand-in and the real one){}",
                spelling,
                real,
                if node_of.contains_key(target.as_str()) { "" } else { " (the stand-in's definition is not in the index)" }
            ),
            ContractTestBreach::NoneWithoutReason => {
                format!("stand-in {} has no reason in :contract-test none (write the name of a test that explains why no contract test is required)", spelling)
            }
        };
        drafts.push(Draft {
            rule: ProjectRule::WorldHandlerWithoutContractTest,
            layer: None,
            rel: rel.clone(),
            path: architecture.path.clone(),
            range: stand_in.range,
            message,
            detail: Some(format!("{}->{}", spelling, real)),
            base: Severity::Error,
            explain: Explain::StandInWithoutContractTest { stand_in: spelling, real, breach },
        });
    }
    drafts
}

/// Module name of the assembly for contract tests -> assembly table (`(val INTERPRETERS {name-constant assembly …})`) — among the pairs of dict literals, those whose key and value are
/// both symbols with resolvable names: the fully qualified name of the key -> the list of fully qualified names of the value (DOEFF133). Interpreter name constants are strings and do not name an assembly —
/// the assembly that runs is looked up by conftest from this table, so the table's pairs are read as edges from a name constant to an assembly.
fn interpreter_table(root: &Path, rel: &str, file: &HyFileIndex) -> HashMap<String, Vec<String>> {
    use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
    let mut out: HashMap<String, Vec<String>> = HashMap::new();
    let Ok(source) = std::fs::read_to_string(root.join(rel)) else { return out };
    let text = |form: &Form| source.get(form.span.start..form.span.end).unwrap_or("").to_string();
    let target_of = |symbol: &str| {
        file.references.iter().filter(|r| !r.member && (r.name == symbol || r.mangled == symbol)).find_map(|r| r.target.clone())
    };
    let forms = Reader::new(&source, 0, source.len()).read_all();
    let mut stack: Vec<&Form> = forms.iter().collect();
    while let Some(form) = stack.pop() {
        let Node::Seq { delim, items } = &form.node else { continue };
        stack.extend(items.iter());
        if *delim != Delim::Brace {
            continue;
        }
        for pair in items.chunks(2) {
            let [key, value] = pair else { continue };
            if !matches!(key.node, Node::Symbol) || !matches!(value.node, Node::Symbol) {
                continue;
            }
            if let (Some(key), Some(value)) = (target_of(&text(key)), target_of(&text(value))) {
                out.entry(key).or_default().push(value);
            }
        }
    }
    out
}

/// String literals inside a definition that are interpreter-name spellings `"module:name"` / `"module:name@<variant>"` — fully qualified name -> the clauses holding that string
/// (the counterpart pairs of DOEFF137). A contract test's assembly names the interpreter with a constant of the name string and connects it to the handler through a name -> assembly table,
/// so the constant's definition does not reference the handler (`(val OS-CONSOLE "controllers.foundation.console:os-console")`) — the spelling itself counts as naming it.
fn spelled_handlers(root: &Path, graph: &DefinitionGraph, hy: &HashMap<String, HyFileIndex>) -> HashMap<String, Vec<usize>> {
    use doeff_indexer::hy_index::reader::{Form, Node, Reader, StrKind};
    let mut out: HashMap<String, Vec<usize>> = HashMap::new();
    for rel in &graph.rels {
        let Ok(source) = std::fs::read_to_string(root.join(rel.as_str())) else { continue };
        let definitions = &hy[*rel].definitions;
        let first = graph.base[rel.as_str()];
        let starts: Vec<usize> = std::iter::once(0).chain(source.match_indices('\n').map(|(at, _)| at + 1)).collect();
        let forms = Reader::new(&source, 0, source.len()).read_all();
        let mut stack: Vec<&Form> = forms.iter().collect();
        while let Some(form) = stack.pop() {
            let body = match &form.node {
                Node::Seq { items, .. } => {
                    stack.extend(items.iter());
                    continue;
                }
                Node::Str { kind: StrKind::Plain, body } => source.get(body.start..body.end).unwrap_or(""),
                _ => continue,
            };
            let spelling = body.split_once('@').map_or(body, |(head, _)| head);
            let Some(named) = architecture::DefinitionRef::parse(spelling) else { continue };
            // The innermost definition enclosing the string's line (the one that starts latest).
            let line = starts.partition_point(|start| *start <= form.span.start).saturating_sub(1);
            let owner = definitions
                .iter()
                .enumerate()
                .filter(|(_, d)| (d.full_range.start.line as usize) <= line && line <= d.full_range.end.line as usize)
                .max_by_key(|(_, d)| d.full_range.start)
                .map(|(index, _)| index);
            if let Some(owner) = owner {
                out.entry(named.target()).or_default().push(first + owner);
            }
        }
    }
    out
}

/// Decides the DOEFF137 hits from a handler's edge-test declaration and whether the edge test reaches it (asked only when required).
fn contract_test_breach(declared: &architecture::ContractTest, covered: impl FnOnce() -> bool) -> Option<ContractTestBreach> {
    match declared {
        architecture::ContractTest::Waived(_) => None,
        architecture::ContractTest::NoneWithoutReason => Some(ContractTestBreach::NoneWithoutReason),
        architecture::ContractTest::Required => (!covered()).then_some(ContractTestBreach::NoEdgeTest),
    }
}

/// The forward edges of the definition-edge graph (callees[n] = definitions reachable from n). Edges inside system values (the body of defsystem, the arguments of :carriers) are also run by the system in production,
/// so they count as reachable (separate from how DOEFF133 counts edges — #1390). Used by DOEFF143, 155 and 156.
fn forward_edges(graph: &DefinitionGraph) -> Vec<Vec<usize>> {
    let mut callees: Vec<Vec<usize>> = vec![Vec::new(); graph.nodes.len()];
    for (callee, (callers, carried)) in graph.callers.iter().zip(&graph.carried).enumerate() {
        for &caller in callers.iter().chain(carried) {
            callees[caller].push(callee);
        }
    }
    callees
}

/// A reading that resolves an effect's name (fully qualified) to the canonical name of its defining origin (the resolver of `crate::origins`), and the spellings that could not be resolved. The keys, table matching and layer judgments of DOEFF143, 157, 164, 167 and 206
/// read the names resolved here, on both the clause side and the table-row side (they do not match by import spelling — merely re-importing the same effect from an entry that re-exports it
/// changed the key and table rows stopped matching).
struct EffectNames<'a> {
    origins: crate::origins::Origins<'a>,
    /// Spellings that could not be resolved -> where first seen.
    unresolved: BTreeMap<String, String>,
}

impl<'a> EffectNames<'a> {
    fn new(origins: crate::origins::Origins<'a>) -> Self {
        Self { origins, unresolved: BTreeMap::new() }
    }

    /// The canonical name for the spelling `written` (a name written in file `from` — if none, looked up from the root origin only). If it cannot be resolved, the spelling is returned as is and the fact that it could not be resolved is
    /// remembered (shown in the findings — it does not silently fall back to the spelling).
    fn name(&mut self, written: &str, from: Option<&str>, at: impl FnOnce() -> String) -> String {
        match self.origins.origin_from(written, from) {
            Some(origin) => origin,
            None => {
                self.unresolved.entry(written.to_string()).or_insert_with(at);
                written.to_string()
            }
        }
    }

    /// For a key `<path>::<handler>::<effect>` (a key without `::` is the effect's fully qualified name itself), the key with the effect part resolved to the canonical name.
    fn key(&mut self, key: &str, at: impl FnOnce() -> String) -> String {
        match key.rsplit_once("::") {
            Some((place, effect)) => {
                let file = place.split("::").next();
                format!("{}::{}", place, self.name(effect, file, at))
            }
            None => self.name(key, None, at),
        }
    }

    /// A table (key -> reason) with keys resolved to canonical names, and the mapping from resolved key -> key written in the table. If two rows hit the same canonical key, it is reported.
    fn table(&mut self, table: &BTreeMap<String, String>, dir: Option<&str>, problems: &mut Vec<String>) -> (BTreeMap<String, String>, BTreeMap<String, String>) {
        let mut out = BTreeMap::new();
        let mut written = BTreeMap::new();
        let shown = dir.unwrap_or("-");
        for (key, reason) in table {
            let canonical = self.key(key, || format!("table {} row {}", shown, key));
            if let Some(first) = written.insert(canonical.clone(), key.clone()) {
                problems.push(format!("rows {} and {} of table {} get the same key {} by the effect's defining-origin name — merge them into one row", first, key, shown, canonical));
            }
            out.insert(canonical, reason.clone());
        }
        (out, written)
    }

    /// Findings for the spellings that could not be resolved (one line per spelling).
    fn problems(&self) -> Vec<String> {
        self.unresolved
            .iter()
            .map(|(written, at)| {
                format!("cannot resolve the defining origin of effect {} ({}) — neither the defining module's file nor a re-export leading to it was found. Matched by the spelling as written", written, at)
            })
            .collect()
    }
}

/// All the effect clauses in the graph, and their shapes (clauses of unread files are not counted). The shape of a clause is computed only once here, and DOEFF143, 155, 156, 157, 158, 164,
/// 167 and 206 read the same thing. If `names` is passed, the clause's effect is resolved to the canonical name of its defining origin (if not passed, the index's spelling as is — DOEFF155 and 156 match against the reference spelling).
fn effect_clauses(
    root: &Path,
    graph: &DefinitionGraph,
    hy: &HashMap<String, HyFileIndex>,
    decl: &architecture::BusinessFakes,
    mut names: Option<&mut EffectNames>,
) -> Vec<business_fakes::Clause> {
    use business_fakes::FileRole;
    let mut clauses: Vec<business_fakes::Clause> = Vec::new();
    let mut shapes: HashMap<&str, HashMap<(String, String), business_fakes::ClauseShape>> = HashMap::new();
    for (node, &(rel, index)) in graph.nodes.iter().enumerate() {
        let d = &hy[rel].definitions[index];
        let role = business_fakes::role_of(rel, decl);
        if d.kind != DefinitionKind::EffectClause || role == FileRole::Skipped {
            continue;
        }
        let Some(written) = d.handles.as_ref().and_then(|h| h.target.clone()) else { continue };
        let handler = d.container.clone().unwrap_or_default();
        let effect = match names.as_deref_mut() {
            Some(names) => names.name(&written, Some(rel), || format!("the clause of {} in {}", handler, rel)),
            None => written.clone(),
        };
        let file_shapes =
            shapes.entry(rel).or_insert_with(|| std::fs::read_to_string(root.join(rel)).map(|s| business_fakes::clause_shapes_in(&s)).unwrap_or_default());
        let head = d.handles.as_ref().map(|h| h.name.clone()).unwrap_or_default();
        let shape = file_shapes.get(&(handler.clone(), head)).copied().unwrap_or(business_fakes::ClauseShape::ANSWERING);
        clauses.push(business_fakes::Clause { node, rel: rel.to_string(), handler, effect, written, shape });
    }
    clauses
}

/// DOEFF155 and 156: turns breaches of the assembly shape into notices (the judgment is in assembly_shape). Needs the whole index (whole runs only).
fn judge_assembly_shape(
    root: &Path,
    architecture: &architecture::Architecture,
    layers: Option<&LayerSettings>,
    decl: &architecture::BusinessFakes,
    shape: &architecture::AssemblyShape,
    hy: &HashMap<String, HyFileIndex>,
    enabled: &BTreeSet<ProjectRule>,
) -> (Vec<Draft>, Vec<String>) {
    // Build the judgment inputs from the definition graph and pass them on (the graph's types and assembly are in mod.rs, and assembly_shape does not read mod.rs — #2121).
    let graph = definition_graph(architecture, hy);
    let inputs = assembly_shape::GraphInputs {
        callees: forward_edges(&graph),
        clauses: effect_clauses(root, &graph, hy, decl, None),
        layers: layers
            .map(|l| {
                graph
                    .rels
                    .iter()
                    .filter_map(|rel| classify_layer_file(rel, l).map(|(site, _)| (rel.to_string(), (l.layers[site.layer.0].name.clone(), site.service))))
                    .collect()
            })
            .unwrap_or_default(),
        nodes: graph.nodes.len(),
        base: graph.base,
        rels: graph.rels,
    };
    let (breaches, problems) = assembly_shape::find(root, architecture, decl, shape, hy, inputs);
    let drafts = breaches
        .into_iter()
        .map(|breach| {
            let rule = if breach.is_shape() { ProjectRule::AssemblyShapeBroken } else { ProjectRule::AssemblyAnswerMisplaced };
            let range = breach
                .at
                .as_ref()
                .and_then(|at| hy.get(&breach.rel)?.definitions.iter().find(|d| &d.qualified_name == at).map(|d| d.range))
                .unwrap_or_else(zero_range);
            let (subject, reason) = breach.explain(shape);
            Draft {
                rule,
                layer: None,
                path: root.join(&breach.rel),
                rel: breach.rel.clone(),
                range,
                message: format!("{} — {}", subject, reason),
                detail: Some(breach.detail()),
                base: Severity::Error,
                explain: Explain::AssemblyShape { subject, reason },
            }
        })
        .filter(|draft| enabled.contains(&draft.rule))
        .collect();
    (drafts, problems)
}

/// DOEFF143: walks the definition-edge graph forward from the simulation roots and the production entries, and reports where an effect clause (a fake) reachable only from the simulation root answers a business effect, not as a tap,
/// and the rot of the outside-world table and the counterexample table. Needs the whole index (whole runs only).
/// The judgments, the reasons for unreadable tables (into errors), and the notices for tables read as empty because absent (into notes).
struct FakesJudgement {
    drafts: Vec<Draft>,
    problems: Vec<String>,
    notes: Vec<String>,
}

/// The production entries (one place read by DOEFF143, 164, 165 and DOEFF207): the definitions named by the clauses of the production assembly functions, defsystem and __main__ in the production code's assembly files, and
/// the definitions that the entry strings point to (#3496). Returned as indexes of the graph's clauses (the same clause may enter twice).
fn production_entry_roots(
    root: &Path,
    decl: &architecture::BusinessFakes,
    graph: &DefinitionGraph,
    hy: &HashMap<String, HyFileIndex>,
) -> Vec<usize> {
    let definition = |node: usize| {
        let (rel, index) = graph.nodes[node];
        &hy[rel].definitions[index]
    };
    let mut by_name: HashMap<&str, usize> = HashMap::new();
    for node in 0..graph.nodes.len() {
        by_name.entry(definition(node).qualified_name.as_str()).or_insert(node);
    }
    let production_code = |rel: &str| business_fakes::production_code(rel, decl);
    let mut production_roots: Vec<usize> = (0..graph.nodes.len())
        .filter(|&n| {
            let (rel, _) = graph.nodes[n];
            let d = definition(n);
            production_code(rel)
                && (business_fakes::set_member(rel, &d.name, decl.production_prefix.as_deref(), decl) || d.kind == DefinitionKind::Defsystem)
        })
        .collect();
    let mut entry_strings: Vec<String> = Vec::new();
    for rel in graph.rels.iter().filter(|r| production_code(r.as_str())) {
        let Ok(source) = std::fs::read_to_string(root.join(rel.as_str())) else { continue };
        entry_strings.extend(business_fakes::entry_names(&source, decl));
        let guards = business_fakes::main_guard_lines(&source);
        if guards.is_empty() {
            continue;
        }
        let file = &hy[rel.as_str()];
        let inside = |line: u32| guards.iter().any(|(s, e)| *s <= line && line <= *e);
        let targets = file
            .references
            .iter()
            .filter(|r| inside(r.range.start.line))
            .filter_map(|r| r.target.as_deref())
            .chain(file.calls.iter().filter(|c| inside(c.range.start.line)).filter_map(|c| c.target.as_deref()));
        production_roots.extend(targets.filter_map(|t| by_name.get(t).copied()));
    }
    for path in WalkDir::new(root).follow_links(false).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()) {
        let Some(rel) = relative_path(root, path.path()) else { continue };
        if rel.starts_with('.') || !decl.entry_string_files.iter().any(|p| glob_matches(p, &rel)) {
            continue;
        }
        if let Ok(source) = std::fs::read_to_string(path.path()) {
            entry_strings.extend(business_fakes::entry_names(&source, decl));
        }
    }
    // A string spelling is shrunk to the definition's fully qualified name (an attribute spelling goes to the definition).
    for spelling in entry_strings {
        let mut name = spelling.as_str();
        loop {
            if let Some(&node) = by_name.get(name) {
                production_roots.push(node);
                break;
            }
            match name.rsplit_once('.') {
                Some((head, _)) => name = head,
                None => break,
            }
        }
    }
    production_roots
}

/// The clauses reachable from `roots` by forward edges (`forward_edges`) (including the roots themselves).
fn reached_from(callees: &[Vec<usize>], roots: &[usize]) -> Vec<bool> {
    let mut seen = vec![false; callees.len()];
    let mut queue: std::collections::VecDeque<usize> = roots.iter().copied().collect();
    roots.iter().for_each(|&n| seen[n] = true);
    while let Some(node) = queue.pop_front() {
        for &next in &callees[node] {
            if !seen[next] {
                seen[next] = true;
                queue.push_back(next);
            }
        }
    }
    seen
}

/// The judgment of DOEFF143 (returns `FakesJudgement`).
fn judge_business_fakes(
    root: &Path,
    architecture: &architecture::Architecture,
    layers: Option<&LayerSettings>,
    decl: &architecture::BusinessFakes,
    shape: Option<&architecture::AssemblyShape>,
    hy: &HashMap<String, HyFileIndex>,
    enabled: &BTreeSet<ProjectRule>,
) -> FakesJudgement {
    use business_fakes::{FileRole, Verdict};
    let mut problems = Vec::new();
    let mut notes = Vec::new();
    let mut table = |dir: &Option<String>, absent: registry::Absent| -> BTreeMap<String, String> {
        let Some(dir) = dir else { return BTreeMap::new() };
        let judged = registry::JudgedKeys::load(root, std::slice::from_ref(dir), absent);
        problems.extend(judged.problems);
        notes.extend(judged.notes);
        judged.reasons
    };
    // The outside-world table and the counterexample table are declarations (a nonexistent dir is a spelling error); :unserved is a shrink-only table that pins violations (nonexistent dir = empty — #1918)
    let external = table(&decl.external_effects, registry::Absent::Unreadable);
    let counterexamples = table(&decl.counterexamples, registry::Absent::Unreadable);
    let unserved = table(&decl.unserved, registry::Absent::Empty);
    // A clause's effect and a table row's effect are both matched by the canonical name of their defining origin (a table row stays in the spelling as written — notices are given with the key as written).
    let mut names = EffectNames::new(crate::origins::Origins::new(root, hy));
    let (external, external_written) = names.table(&external, decl.external_effects.as_deref(), &mut problems);
    let (counterexamples, counterexample_written) = names.table(&counterexamples, decl.counterexamples.as_deref(), &mut problems);
    let (unserved, _) = names.table(&unserved, decl.unserved.as_deref(), &mut problems);
    let graph = definition_graph(architecture, hy);
    let count = graph.nodes.len();
    let callees = forward_edges(&graph);
    let definition = |node: usize| {
        let (rel, index) = graph.nodes[node];
        &hy[rel].definitions[index]
    };
    let role = |node: usize| business_fakes::role_of(graph.nodes[node].0, decl);
    let mut by_name: HashMap<&str, usize> = HashMap::new();
    for node in 0..count {
        by_name.entry(definition(node).qualified_name.as_str()).or_insert(node);
    }
    // Simulation roots: definitions of the simulation environment and assembly layer, and the simulation assembly functions in the assembly files.
    let simulation_roots: Vec<usize> = (0..count)
        .filter(|&n| {
            let (rel, _) = graph.nodes[n];
            matches!(role(n), FileRole::Simulation | FileRole::Assembly)
                || (role(n) == FileRole::Production && business_fakes::set_member(rel, &definition(n).name, decl.simulation_prefix.as_deref(), decl))
        })
        .collect();
    let production_code = |rel: &str| business_fakes::production_code(rel, decl);
    let production_roots = production_entry_roots(root, decl, &graph, hy);
    let reach = |roots: &[usize]| reached_from(&callees, roots);
    let simulated_nodes = reach(&simulation_roots);
    let produced_nodes = reach(&production_roots);
    // Test roots: all definitions of test files (business copies in the production code's location that only tests use are also reached from here).
    let test_roots: Vec<usize> = (0..count).filter(|&n| role(n) == FileRole::Test).collect();
    let tested_nodes = reach(&test_roots);
    let clauses = effect_clauses(root, &graph, hy, decl, Some(&mut names));
    let simulated: Vec<bool> = clauses.iter().map(|c| simulated_nodes[c.node]).collect();
    let produced: Vec<bool> = clauses.iter().map(|c| produced_nodes[c.node]).collect();
    let tested: Vec<bool> = clauses.iter().map(|c| tested_nodes[c.node]).collect();
    // Layer names (intent and translation) are read from :assembly-shape. An effect's layer is decided by the location of its defining-origin module.
    let layer_of = |rel: &str| layers.and_then(|l| classify_layer_file(rel, l).map(|(site, _)| l.layers[site.layer.0].name.clone()));
    // Whether an effect (fully qualified name) is an intent-layer effect — DOEFF158 and 165's clause reading and DOEFF206's clause and table-row reading use this same one.
    let effect_in_intent = |effect: &str| {
        let base = business_fakes::module_of_effect(effect).replace('.', "/");
        let layer = layer_of(&format!("{}.hy", base)).or_else(|| layer_of(&format!("{}.py", base)));
        shape.is_some_and(|s| layer.as_deref() == Some(s.intent_layer.as_str()))
    };
    let intent_effect: Vec<bool> = clauses.iter().map(|c| effect_in_intent(&c.effect)).collect();
    let translation_file: Vec<bool> =
        clauses.iter().map(|c| shape.is_some_and(|s| layer_of(&c.rel).as_deref() == Some(s.translation_layer.as_str()))).collect();
    // Effects that production Python handlers (outside the index graph) answer with isinstance.
    let python_answered: BTreeSet<String> = WalkDir::new(root)
        .follow_links(false)
        .into_iter()
        .filter_entry(|e| !e.file_name().to_string_lossy().starts_with('.') && e.file_name() != "__pycache__")
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file() && e.path().extension().is_some_and(|x| x == "py"))
        .filter_map(|e| relative_path(root, e.path()).map(|rel| (rel, e.path().to_path_buf())))
        .filter(|(rel, _)| decl.entry_string_modules.iter().any(|m| rel.starts_with(&format!("{}/", m.replace('.', "/")))) && production_code(rel))
        .filter_map(|(rel, path)| std::fs::read_to_string(path).ok().map(|source| (module_of(&rel), source)))
        .flat_map(|(module, source)| business_fakes::python_isinstance_effects(&source, &module).into_iter().map(move |effect| (effect, module.clone())))
        .collect::<Vec<_>>()
        .into_iter()
        .map(|(effect, module)| names.name(&effect, None, || format!("isinstance in module {}", module)))
        .collect();
    let inputs = business_fakes::Inputs {
        clauses: &clauses,
        simulated: &simulated,
        produced: &produced,
        tested: &tested,
        intent_effect: &intent_effect,
        translation_file: &translation_file,
        external: &external,
        counterexamples: &counterexamples,
        unserved: &unserved,
        python_answered: &python_answered,
    };
    // Notices of table rot. Stale rows of the counterexample table are DOEFF176; rot of the outside-world table is DOEFF143 (the number of fakes and the number of stale rows are counted under different numbers).
    let table_draft = |rule: ProjectRule, dir: &Option<String>, detail: String, subject: String, reason: &str| {
        let rel = dir.clone().unwrap_or_else(|| "architecture.hy".to_string());
        Draft {
            rule,
            layer: None,
            path: root.join(&rel),
            rel,
            range: zero_range(),
            message: subject.clone(),
            detail: Some(detail),
            base: Severity::Error,
            explain: Explain::BusinessEffectFake { subject, reason: reason.to_string() },
        }
    };
    // Notice for one clause (key detail = `[<kind>:]<handler>::<effect>`).
    let clause_draft = |rule: ProjectRule, i: usize, kind: Option<&str>, what: String, reason: &str| {
        let clause = &clauses[i];
        let explain = Explain::BusinessEffectFake { subject: format!("clause {} of handler {}", clause.written, clause.handler), reason: reason.to_string() };
        Draft {
            rule,
            layer: None,
            path: root.join(&clause.rel),
            rel: clause.rel.clone(),
            range: definition(clause.node).range,
            message: format!("{}: handler {} {}", clause.rel, clause.handler, what),
            detail: Some(match kind {
                Some(kind) => format!("{}:{}::{}", kind, clause.handler, clause.written),
                None => format!("{}::{}", clause.handler, clause.written),
            }),
            base: Severity::Error,
            explain,
        }
    };
    // DOEFF165 (#1561 K3): the coverage table per intent-layer effect — reads 3 columns from the same reach (simulation roots and production entries) and from the definitions reachable forward from deftests.
    let coverage: Vec<Draft> = match shape.filter(|_| enabled.contains(&ProjectRule::IntentEffectUncovered)) {
        None => Vec::new(),
        Some(shape) => {
            // The emitting-side roots are only the deftests of test files (definitions merely reachable from a test helper are not counted as "tested" — separate from DOEFF157's test roots).
            let deftest_roots: Vec<usize> = test_roots.iter().copied().filter(|&n| definition(n).kind == DefinitionKind::Deftest).collect();
            let deftested = reach(&deftest_roots);
            let service_root = layers::normalize_dir(&architecture.root);
            let services: Vec<(String, String)> =
                architecture.services.iter().map(|s| (s.name.clone(), format!("{}/{}", service_root, s.dir))).collect();
            let answerers = |effect: &str, reached: &[bool]| -> Vec<String> {
                let names: BTreeSet<String> =
                    clauses.iter().zip(reached).filter(|(c, r)| **r && c.effect == effect).map(|(c, _)| c.handler.clone()).collect();
                names.into_iter().collect()
            };
            let facts: Vec<intent_coverage::EffectFacts> = (0..count)
                .filter(|&n| definition(n).kind == DefinitionKind::Defeffect && layer_of(graph.nodes[n].0).as_deref() == Some(shape.intent_layer.as_str()))
                .map(|n| {
                    let effect = definition(n).qualified_name.clone();
                    // Emitting side = among the definitions that name this effect, those excluding the responders (the effect clause and its handler) and the declaration itself, that are reachable from a deftest.
                    let emitters: BTreeSet<String> = graph.callers[n]
                        .iter()
                        .chain(&graph.carried[n])
                        .filter(|&&m| deftested[m] && !matches!(definition(m).kind, DefinitionKind::EffectClause | DefinitionKind::Defhandler | DefinitionKind::Defeffect))
                        .map(|&m| definition(m).qualified_name.clone())
                        .collect();
                    let rel = graph.nodes[n].0.to_string();
                    intent_coverage::EffectFacts {
                        service: intent_coverage::service_of(&rel, &services).map(str::to_string),
                        simulated: answerers(&effect, &simulated),
                        produced: answerers(&effect, &produced),
                        emitters: emitters.into_iter().collect(),
                        effect,
                        rel,
                    }
                })
                .collect();
            intent_coverage::table(facts)
                .into_iter()
                .filter(|row| !row.gaps.is_empty())
                .map(|row| {
                    let node = by_name.get(row.facts.effect.as_str()).copied();
                    Draft {
                        rule: ProjectRule::IntentEffectUncovered,
                        layer: None,
                        path: root.join(&row.facts.rel),
                        rel: row.facts.rel.clone(),
                        range: node.map(|n| definition(n).range).unwrap_or_else(zero_range),
                        message: format!("intent effect {} has a coverage gap: {} — {}", row.facts.effect, row.gap_words(), row.columns()),
                        detail: Some(row.detail()),
                        // A gap is a failure (#1562 K4). Existing gaps go in the repo's registry, and finish lowers registered gaps to warning.
                        base: Severity::Error,
                        explain: Explain::IntentEffectCoverage {
                            effect: row.facts.effect.clone(),
                            service: row.facts.service.clone().unwrap_or_else(|| "-".to_string()),
                            columns: row.columns(),
                            gaps: row.gap_words(),
                        },
                    }
                })
                .collect()
        }
    };
    // DOEFF206 (#3407): table rows that let a violation through (one key per file — applied to the row's file) and clauses inside the verification environment's dir.
    let pass_rows: Vec<business_fakes::PassRow> = if enabled.contains(&ProjectRule::IntentFakedInVerification) {
        let mut rows = Vec::new();
        // Read errors of the outside-world table are reported by the table above. For the lower-layer pass-through table and the test-only fake table, a nonexistent dir is read as empty (Registry's reading).
        for (dir, report) in [(&decl.external_effects, false), (&decl.lower_layer_passages, true), (&decl.test_only_fakes, true)] {
            let Some(dir) = dir else { continue };
            let read = Registry::load(root, std::slice::from_ref(dir), &[]);
            if report {
                problems.extend(read.problems);
                notes.extend(read.notes);
            }
            rows.extend(read.keys.into_iter().map(|key| business_fakes::PassRow {
                origin: read.origins.get(&key).cloned().unwrap_or_else(|| dir.clone()),
                table: dir.clone(),
                key,
            }));
        }
        rows
    } else {
        Vec::new()
    };
    let intent_fakes: Vec<Verdict> = if enabled.contains(&ProjectRule::IntentFakedInVerification) {
        // A row's effect also has its layer judged by its defining-origin name (a row spelled via a re-exporting entry is also read as an intent effect).
        let row_intent: Vec<bool> = pass_rows
            .iter()
            .map(|row| {
                let canonical = business_fakes::PassRow { key: names.key(&row.key, || format!("table {} row {}", row.table, row.key)), ..row.clone() };
                effect_in_intent(canonical.effect())
            })
            .collect();
        let in_verification: Vec<bool> = clauses.iter().map(|c| architecture.in_verification_environment(&c.rel)).collect();
        business_fakes::judge_intent_fakes(&business_fakes::IntentFakeInputs {
            clauses: &clauses,
            intent_effect: &intent_effect,
            in_verification: &in_verification,
            counterexamples: &counterexamples,
            rows: &pass_rows,
            row_intent: &row_intent,
        })
    } else {
        Vec::new()
    };
    let mut drafts: Vec<Draft> = business_fakes::judge(&inputs, decl)
        .into_iter()
        .chain(intent_fakes)
        .map(|verdict| match verdict {
            Verdict::Fake(i) => clause_draft(
                ProjectRule::BusinessEffectFake,
                i,
                None,
                format!("is a fake answering business effect {} (reachable only from the simulation root)", clauses[i].written),
                "A definition reachable from the simulation root but not from the production entry builds answers to a business effect. Write business operations with a defk that emits lower-layer effects, and have tests swap only the outside-world handlers.",
            ),
            Verdict::LowerLayerFake(i) => clause_draft(
                ProjectRule::BusinessEffectFake,
                i,
                Some("lower"),
                format!("is a second fake answering lower-layer effect {} (reachable only from the simulation root)", clauses[i].written),
                "The fake answering a lower-layer effect is only the single canonical one that the lower layer owns. The simulation is built from outside-world handlers and the canonical fake.",
            ),
            Verdict::TestOnlyFake(i) => clause_draft(
                ProjectRule::TestOnlyFake,
                i,
                None,
                format!("is a test-only fake answering {} (reachable only from test definitions)", clauses[i].written),
                "A definition reachable only from tests builds answers to a business effect or a lower-layer effect. The business handler is only the single production one, and tests are built by swapping handlers of the foundation (record effects, clock, outside parties). If it is a deliberately broken counterexample, list it in the counterexample table.",
            ),
            Verdict::IntentAnsweredOutside(i) => clause_draft(
                ProjectRule::IntentAnswererNotTranslation,
                i,
                Some("outside"),
                format!("answers intent effect {} outside the translation layer (reachable from the production entry)", clauses[i].written),
                "Only a single translation-layer handler answers an intent effect (the same in production and simulation). Do not answer with a separate responder per environment or with a foundation handler; the simulation swaps the foundation.",
            ),
            Verdict::IntentAnsweredTwice(i, n) => clause_draft(
                ProjectRule::IntentAnswererNotTranslation,
                i,
                Some("shared"),
                format!("is one of {1} translation handlers answering intent effect {0}", clauses[i].written, n),
                "Only a single translation handler answers one intent effect. Merge the answers into one handler.",
            ),
            Verdict::StaleCounterexample(key) => {
                let key = counterexample_written.get(&key).cloned().unwrap_or(key);
                table_draft(
                ProjectRule::StaleCounterexampleRow,
                &decl.counterexamples,
                format!("counterexample-unused::{}", key),
                format!("{} of the counterexample table no longer matches", key),
                "A row of the counterexample table is kept only while the deliberately broken counterexample handler still exists — remove it from the table.",
            )
            }
            Verdict::UnusedExternal(effect) => {
                let effect = external_written.get(&effect).cloned().unwrap_or(effect);
                table_draft(
                ProjectRule::BusinessEffectFake,
                &decl.external_effects,
                format!("external-unused::{}", effect),
                format!("no fake answers {} of the outside-world table", effect),
                "The outside-world table declares outside-world effects that fakes answer — remove a row with no answering fake from the table (do not let the table rot).",
            )
            }
            Verdict::IntentPassedByTable(r) => {
                let row = &pass_rows[r];
                let subject = format!("table {} row {}", row.table, row.key);
                Draft {
                    rule: ProjectRule::IntentFakedInVerification,
                    layer: None,
                    path: root.join(&row.origin),
                    rel: row.origin.clone(),
                    range: zero_range(),
                    message: format!("{} passes the intent-layer effect {} (a table row allowing the verification environment its own answer)", subject, row.effect()),
                    detail: Some(format!("pass:{}", row.key)),
                    base: Severity::Error,
                    explain: Explain::IntentFakedInVerification {
                        subject,
                        reason: "An intent-layer effect is not allowed to be declared outside-world by the table — what touches the outside world is a foundation effect, and an intent effect is answered by the production translation handler (layer protocol) even in the verification environment. Delete the table row and swap the foundation handler under the translation.".to_string(),
                    },
                }
            }
            Verdict::IntentAnsweredInVerification(i) => {
                let clause = &clauses[i];
                Draft {
                    rule: ProjectRule::IntentFakedInVerification,
                    layer: None,
                    path: root.join(&clause.rel),
                    rel: clause.rel.clone(),
                    range: definition(clause.node).range,
                    message: format!("{}: handler {} answers the intent-layer effect {} by itself (inside the verification environment)", clause.rel, clause.handler, clause.written),
                    detail: Some(format!("verification:{}::{}", clause.handler, clause.written)),
                    base: Severity::Error,
                    explain: Explain::IntentFakedInVerification {
                        subject: format!("clause {} of verification-environment handler {}", clause.written, clause.handler),
                        reason: "If the verification environment answers an intent effect, it goes green without the production translation handler ever running locally. Leave the answer to the production translation handler and swap only the foundation handler beneath it. The only exceptions are a clause that passes the received effect straight outward at every ending (resumes the re-emitted answer as is, reperforms it, or a :when clause), and a deliberately broken clause listed in the counterexample table. A clause that answers by itself in only one branch also matches if there is no table row.".to_string(),
                    },
                }
            }
            Verdict::UnservedExternal(effect) => {
                let effect = external_written.get(&effect).cloned().unwrap_or(effect);
                table_draft(
                ProjectRule::BusinessEffectFake,
                &decl.external_effects,
                format!("external-unserved::{}", effect),
                format!("no responder reachable from the production entry for {} of the outside-world table", effect),
                "An effect declared outside-world needs a production responder (a single fake does not let it pass) — write the production handler, or if it is a business effect, write it with a lower-layer effect. List the current shortfall in the :unserved table with a reason and an owner.",
            )
            }
        })
        .filter(|draft| enabled.contains(&draft.rule))
        .chain(coverage)
        .collect();
    if enabled.contains(&ProjectRule::ServiceWithoutCounterexample) {
        drafts.extend(
            judge_counterexample_coverage(root, architecture, &graph, hy, &clauses, &produced, &counterexamples)
                .into_iter()
                .map(|d| Draft { path: root.join(&d.rel), ..d }),
        );
    }
    if enabled.contains(&ProjectRule::ClauseWithoutCounterexample) {
        let mut claims = match decl.counterexamples.as_deref() {
            Some(dir) => clause_coverage::ClauseClaims::load(root, dir),
            None => clause_coverage::ClauseClaims::default(),
        };
        // The declared key is also matched by the same defining-origin name as the clause's key.
        claims.by_key = std::mem::take(&mut claims.by_key)
            .into_iter()
            .map(|(key, breaks)| (names.key(&key, || format!("counterexample table row {}", key)), breaks))
            .collect();
        let (found, claim_problems) = judge_clause_coverage(root, architecture, &graph, hy, &clauses, &produced, &counterexamples, &claims);
        drafts.extend(found.into_iter().map(|d| Draft { path: root.join(&d.rel), ..d }));
        problems.extend(claims.problems);
        problems.extend(claim_problems);
    }
    problems.extend(names.problems());
    FakesJudgement { drafts, problems, notes }
}

/// DOEFF136: for each service, walks callers backward from the entry-layer definitions, and if no deftest under the simulation environment (:verification-environment)
/// is reached, reports that defservice. A service with no entry layer is not counted.
fn judge_untested_services(repo: &Path, architecture: &architecture::Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<Draft> {
    let Some(place) = architecture.verification_environment.as_deref() else { return Vec::new() };
    let root = layers::normalize_dir(&architecture.root);
    let sim = format!("{}/{}", root, place);
    let graph = definition_graph(architecture, hy);
    let definition = |node: usize| {
        let (rel, index) = graph.nodes[node];
        &hy[rel].definitions[index]
    };
    let on_sim = |node: usize| under(graph.nodes[node].0, &sim) && definition(node).kind == DefinitionKind::Deftest;
    let mut drafts = Vec::new();
    for (service, seeds, entry) in architecture.services.iter().filter_map(|s| entry_seeds(&root, s, &graph).map(|(n, e)| (s, n, e))) {
        // If the entry layer is declared but has 0 definitions, there is no assembly to run (not counted).
        if seeds.is_empty() {
            continue;
        }
        let mut seen = vec![false; graph.nodes.len()];
        let mut queue: std::collections::VecDeque<usize> = seeds.iter().copied().collect();
        seeds.iter().for_each(|n| seen[*n] = true);
        let mut tested = false;
        while let Some(node) = queue.pop_front() {
            if on_sim(node) {
                tested = true;
                break;
            }
            // Edges inside system values are also counted (even if the simulation environment's test only builds the system, it reaches the assembly's entry).
            for &caller in graph.callers[node].iter().chain(graph.carried[node].iter()) {
                if !seen[caller] {
                    seen[caller] = true;
                    queue.push_back(caller);
                }
            }
        }
        if tested {
            continue;
        }
        drafts.push(Draft {
            rule: ProjectRule::ServiceUntestedOnSim,
            layer: None,
            rel: declared_rel(repo, &service.file),
            path: service.file.clone(),
            range: service.range,
            message: format!("service {}'s entry layer ({} definitions) is not reached by any deftest of the simulation environment {}", service.name, seeds.len(), sim),
            detail: Some(service.name.clone()),
            base: Severity::Error,
            explain: Explain::ServiceUntestedOnSim { service: service.name.clone(), entry, definitions: seeds.len(), sim: sim.clone() },
        });
    }
    drafts
}

/// DOEFF163: turns gaps in a code-holding service's invariant declarations into drafts at the defservice's position (key detail = the service's name; a missing function is named with `::`).
fn judge_service_invariants(root: &Path, architecture: &architecture::Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<Draft> {
    invariants::gaps(architecture, hy)
        .into_iter()
        .map(|(service, gap)| {
            let message = gap.describe(&service.name);
            Draft {
                rule: ProjectRule::ServiceInvariantsMissing,
                layer: None,
                rel: declared_rel(root, &service.file),
                path: service.file.clone(),
                range: service.range,
                detail: Some(match gap.detail() {
                    Some(spelling) => format!("{}::{}", service.name, spelling),
                    None => service.name.clone(),
                }),
                base: Severity::Error,
                explain: Explain::ServiceInvariantsMissing { service: service.name.clone(), gap: message.clone() },
                message,
            }
        })
        .collect()
}

/// DOEFF173: turns gaps in a code-holding service's system declarations into drafts at the defservice's position (key detail = the service's name; a missing system is named with `::`).
fn judge_service_systems(root: &Path, architecture: &architecture::Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<Draft> {
    system_decls::gaps(root, architecture, hy)
        .into_iter()
        .map(|(service, gap)| {
            let message = gap.describe(&service.name);
            Draft {
                rule: ProjectRule::ServiceSystemMissing,
                layer: None,
                rel: declared_rel(root, &service.file),
                path: service.file.clone(),
                range: service.range,
                detail: Some(match gap.detail() {
                    Some(spelling) => format!("{}::{}", service.name, spelling),
                    None => service.name.clone(),
                }),
                base: Severity::Error,
                explain: Explain::ServiceSystemMissing { service: service.name.clone(), gap: message.clone() },
                message,
            }
        })
        .collect()
}

/// Non-job writers of DOEFF207: the defk and deff reachable forward from the production entries (the same `production_entry_roots` as DOEFF143) that are the pairs declared by the contract's `:writes`
/// (a task Program that passes from a system job as a foundation value). The production entries are decided by the `:business-fakes` declaration,
/// so it is empty in a repo with no declaration or a repo that does not write `:outside-writers` (only the job's :writes are counted). Simulation-only definitions are not reached, so not counted.
fn reachable_function_writers(root: &Path, architecture: &architecture::Architecture, hy: &HashMap<String, HyFileIndex>) -> BTreeSet<String> {
    let (Some(_), Some(decl)) = (architecture.outside_writers.as_ref(), architecture.business_fakes.as_ref()) else {
        return BTreeSet::new();
    };
    let graph = definition_graph(architecture, hy);
    let callees = forward_edges(&graph);
    let reached = reached_from(&callees, &production_entry_roots(root, decl, &graph, hy));
    let mut sources: HashMap<&str, Option<String>> = HashMap::new();
    let mut writers = BTreeSet::new();
    for (node, &(rel, index)) in graph.nodes.iter().enumerate() {
        let definition = &hy[rel].definitions[index];
        if !reached[node] || !matches!(definition.kind, DefinitionKind::Defk | DefinitionKind::Deff) {
            continue;
        }
        let source = sources.entry(rel).or_insert_with(|| std::fs::read_to_string(root.join(rel)).ok());
        if let Some(written) = source.as_deref().and_then(|text| system_access::function_writes(text, &definition.name)) {
            writers.extend(written);
        }
    }
    writers
}

/// DOEFF207: turns gaps in the read/write fields of production system jobs, and reads with no writer, into drafts at the job row's position (key detail = system::job::field or system::job::pair).
fn judge_system_access(root: &Path, architecture: &architecture::Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<Draft> {
    system_access::gaps(root, architecture, hy, &reachable_function_writers(root, architecture, hy))
        .into_iter()
        .map(|found| {
            let message = found.describe();
            let start = Position { line: found.line, character: 0 };
            Draft {
                rule: ProjectRule::SystemAccessUnwritten,
                layer: None,
                path: root.join(&found.rel),
                rel: found.rel.clone(),
                range: Range { start, end: start },
                detail: Some(found.detail()),
                base: Severity::Error,
                explain: Explain::SystemAccessUnwritten { subject: format!("{}::{}", found.system, found.job), reason: message.clone() },
                message,
            }
        })
        .collect()
}

/// DOEFF208: turns the places where production code wakes a job by waiting on a record change into drafts at the call head's position (key detail = definition name::head spelling), and
/// the reasons for unreadable files.
fn judge_record_waits(root: &Path, decl: &architecture::BusinessFakes) -> (Vec<Draft>, Vec<String>) {
    let (found, problems) = record_waits::find(root, decl);
    let drafts = found
        .into_iter()
        .map(|found| {
            let message = format!("{} — {}", found.rel, found.describe());
            Draft {
                rule: ProjectRule::RecordChangeWakesJob,
                layer: None,
                path: root.join(&found.rel),
                range: found.range,
                detail: Some(found.detail()),
                base: Severity::Error,
                explain: Explain::RecordChangeWakesJob {
                    subject: found.subject(),
                    reason: "A record is a record, and a start is a start. If you wake a job by waiting for a record change, the record store doubles as the carrier of events, the reaction is delayed by the waiting seconds and the re-reading, and the store's load grows in proportion to the number of waiters. Receive the waking notice from the event infrastructure and only write the record — a catch-up read at startup or reconnection is enough as a single non-waiting read (0 seconds).".to_string(),
                },
                rel: found.rel,
                message,
            }
        })
        .collect();
    (drafts, problems)
}

/// DOEFF209: turns the places where production code's Program repeatedly fetches the same thing with time waits in between into drafts at the call head's position (key detail =
/// definition name::head spelling::category), and the reasons for unreadable files.
fn judge_timed_refetch(root: &Path, decl: &architecture::BusinessFakes) -> (Vec<Draft>, Vec<String>) {
    let (found, problems) = timed_refetch::find(root, decl);
    let drafts = found
        .into_iter()
        .map(|found| {
            let message = format!("{} — {}", found.rel, found.describe());
            Draft {
                rule: ProjectRule::PollingOnTimer,
                layer: None,
                path: root.join(&found.rel),
                range: found.range,
                detail: Some(found.detail()),
                base: Severity::Error,
                explain: Explain::PollingOnTimer {
                    subject: format!("{} (category {})", found.subject(), found.refetch.label()),
                    reason: "Polling by time is a last resort (every polling loop is a target to fix). Fetching the same thing at intervals delays the reaction to a change by the interval, keeps reading the other party while nothing changes, and makes the load grow in proportion to the number of pollers. What wakes should be the other party's change notice (an event), and for a deadline whose wake time is decided by a row or a schedule, schedule that time.".to_string(),
                },
                rel: found.rel,
                message,
            }
        })
        .collect();
    (drafts, problems)
}

/// The deftests reached by walking callers backward from seeds (the same edges as DOEFF136 — calls, references, nesting, and edges inside system values).
fn deftests_reaching(graph: &DefinitionGraph, hy: &HashMap<String, HyFileIndex>, seeds: &[usize]) -> BTreeSet<usize> {
    let mut seen = vec![false; graph.nodes.len()];
    let mut queue: std::collections::VecDeque<usize> = seeds.iter().copied().collect();
    seeds.iter().for_each(|n| seen[*n] = true);
    let mut found = BTreeSet::new();
    while let Some(node) = queue.pop_front() {
        let (rel, index) = graph.nodes[node];
        if hy[rel].definitions[index].kind == DefinitionKind::Deftest {
            found.insert(node);
        }
        for &caller in graph.callers[node].iter().chain(graph.carried[node].iter()) {
            if !seen[caller] {
                seen[caller] = true;
                queue.push_back(caller);
            }
        }
    }
    found
}

/// A service's entry definitions (the graph's clauses), and the entry spelling written in notices — the population of DOEFF136, 164 and 167, and the seeds from which reachability is traced. A service that writes `:entry-modules`
/// uses the definitions in those modules' files (a repo with no layer dirs — decided the same as DOEFF163's "code-holding service"),
/// and a service that does not uses the definitions under the entry-layer dir (`<root>/<dir>/entry`). A service with neither is None (not counted).
fn entry_seeds(root: &str, service: &architecture::ArchService, graph: &DefinitionGraph) -> Option<(Vec<usize>, String)> {
    match &service.entry_modules {
        Some(modules) => {
            let files: Vec<String> = modules.iter().map(|m| invariants::module_path(m)).collect();
            let seeds = (0..graph.nodes.len()).filter(|n| files.iter().any(|f| graph.nodes[*n].0 == f)).collect();
            Some((seeds, files.join(", ")))
        }
        None if service.layers.iter().any(|l| l == "entry") => {
            let entry = format!("{}/{}/entry", root, service.dir);
            Some(((0..graph.nodes.len()).filter(|n| under(graph.nodes[*n].0, &entry)).collect(), entry))
        }
        None => None,
    }
}

/// DOEFF164: whether each service has a counterexample of a broken handler. A counterexample clause = a clause that matches a key of the counterexample table and is not reachable from the production entry.
/// The owner is the service dir that contains the file of the clause's effect's defining origin (an effect under no service's dir is a foundation effect). It counts as present if even one deftest reaching a counterexample clause
/// reaches the service's entry-layer definitions (walking the same graph as DOEFF136 backward). The population is the same as DOEFF136 (services with definitions in the entry layer).
fn judge_counterexample_coverage(
    repo: &Path,
    architecture: &architecture::Architecture,
    graph: &DefinitionGraph,
    hy: &HashMap<String, HyFileIndex>,
    clauses: &[business_fakes::Clause],
    produced: &[bool],
    counterexamples: &BTreeMap<String, String>,
) -> Vec<Draft> {
    let root = layers::normalize_dir(&architecture.root);
    let service_dir = |service: &architecture::ArchService| format!("{}/{}", root, service.dir);
    let mut keys = BTreeSet::new();
    let mut cases = Vec::new();
    for (i, clause) in clauses.iter().enumerate() {
        if !clause.counterexample_in(produced[i], counterexamples) || !keys.insert(clause.key()) {
            continue;
        }
        let base = business_fakes::module_of_effect(&clause.effect).replace('.', "/");
        let owner = architecture.services.iter().find(|s| under(&base, &service_dir(s))).map(|s| s.name.clone());
        cases.push(business_fakes::CounterexampleCase { owner, tests: deftests_reaching(graph, hy, &[clause.node]) });
    }
    let mut services = Vec::new();
    let mut entries = Vec::new();
    for (service, seeds, entry) in architecture.services.iter().filter_map(|s| entry_seeds(&root, s, graph).map(|(n, e)| (s, n, e))) {
        // If the entry layer is declared but has 0 definitions, there is no assembly to run (not counted, same as DOEFF136).
        if seeds.is_empty() {
            continue;
        }
        services.push(business_fakes::ServiceCase { name: service.name.clone(), entry_tests: deftests_reaching(graph, hy, &seeds) });
        entries.push((service, entry));
    }
    business_fakes::services_without_counterexample(&cases, &services)
        .into_iter()
        .map(|missing| {
            let (service, entry) = &entries[missing.service];
            Draft {
                rule: ProjectRule::ServiceWithoutCounterexample,
                layer: None,
                rel: declared_rel(repo, &service.file),
                path: service.file.clone(),
                range: service.range,
                message: format!(
                    "service {} has no counterexample of a broken handler (of {} counterexample-table clauses, {} are candidates — no deftest reaching any of them reaches {})",
                    service.name,
                    cases.len(),
                    missing.candidates,
                    entry
                ),
                detail: Some(service.name.clone()),
                base: Severity::Error,
                explain: Explain::ServiceWithoutCounterexample {
                    service: service.name.clone(),
                    entry: entry.clone(),
                    counterexamples: cases.len(),
                    candidates: missing.candidates,
                },
            }
        })
        .collect()
}

/// DOEFF167: counterexample coverage per service and per clause. The effective counterexample clauses and the population are the same as DOEFF164 (clauses in the counterexample table that are not reachable from
/// the production entry, and services with definitions in the entry layer). The clause a counterexample clause claims is read from the counterexample table row's `breaks:` (claims), and if even one deftest reaching the clause reaches
/// the service's entry layer, that clause is covered. The second return value = the reasons for `breaks:` claiming a service or clause not in the declaration (unreadable rows of the table).
fn judge_clause_coverage(
    repo: &Path,
    architecture: &architecture::Architecture,
    graph: &DefinitionGraph,
    hy: &HashMap<String, HyFileIndex>,
    clauses: &[business_fakes::Clause],
    produced: &[bool],
    counterexamples: &BTreeMap<String, String>,
    claims: &clause_coverage::ClauseClaims,
) -> (Vec<Draft>, Vec<String>) {
    let root = layers::normalize_dir(&architecture.root);
    let mut keys = BTreeSet::new();
    let mut cases = Vec::new();
    for (i, clause) in clauses.iter().enumerate() {
        if !clause.counterexample_in(produced[i], counterexamples) || !keys.insert(clause.key()) {
            continue;
        }
        let breaks = claims.by_key.get(&clause.key()).cloned().unwrap_or_default();
        if !breaks.is_empty() {
            cases.push(clause_coverage::ClauseCase { breaks, tests: deftests_reaching(graph, hy, &[clause.node]) });
        }
    }
    let mut services = Vec::new();
    let mut declared_services = Vec::new();
    for (service, seeds, _) in architecture.services.iter().filter_map(|s| entry_seeds(&root, s, graph).map(|(n, e)| (s, n, e))) {
        // If the entry layer is declared but has 0 definitions, there is no assembly to run (not counted, same as DOEFF164).
        if seeds.is_empty() {
            continue;
        }
        services.push(clause_coverage::ServiceClauses {
            name: service.name.clone(),
            clauses: service.clauses.clone(),
            exempt: service.clause_exemptions.iter().map(|e| e.clause.clone()).collect(),
            entry_tests: deftests_reaching(graph, hy, &seeds),
        });
        declared_services.push(service);
    }
    let declared: BTreeMap<String, BTreeSet<String>> = architecture
        .services
        .iter()
        .map(|s| (s.name.clone(), s.clauses.iter().flatten().cloned().collect()))
        .collect();
    let problems = clause_coverage::unknown_claims(claims, &declared);
    let drafts = clause_coverage::gaps(&cases, &services)
        .into_iter()
        .map(|(index, gap)| {
            let service = declared_services[index];
            let message = gap.describe(&service.name);
            Draft {
                rule: ProjectRule::ClauseWithoutCounterexample,
                layer: None,
                rel: declared_rel(repo, &service.file),
                path: service.file.clone(),
                range: service.range,
                detail: Some(match gap.detail() {
                    Some(clause) => format!("{}::{}", service.name, clause),
                    None => service.name.clone(),
                }),
                base: Severity::Error,
                explain: Explain::ClauseWithoutCounterexample {
                    service: service.name.clone(),
                    clause: gap.detail().map(str::to_string),
                    gap: message.clone(),
                },
                message,
            }
        })
        .collect();
    (drafts, problems)
}

/// Among direct evidence of raw side effects, nested overlapping ones are attributed once to the innermost definition (the population of DOEFF106).
/// Within the modules of boundary parts (:boundary-parts of architecture.hy), whether the evidence is of a raw side effect whose target falls within the declaration
/// (DOEFF106 does not count it as a hit). Evidence outside the kinds, and evidence in modules with no declaration, still match as before.
fn boundary_allows(raw: &raw_settings::RawSettingsSpec, module: &str, category: hy_index::RawCategory) -> bool {
    raw.boundary
        .get(&architecture::mangle_dotted(module))
        .is_some_and(|touches| touches.contains(&world_catalog::touch_of_raw(category)))
}

fn innermost_raw_evidence(definitions: &[Definition]) -> Vec<EvidenceRef> {
    let mut chosen: BTreeMap<EvidenceSpot, EvidenceRef> = BTreeMap::new();
    for (definition_index, definition) in definitions.iter().enumerate() {
        for (evidence_index, evidence) in definition.raw.direct.iter().enumerate() {
            let spot = EvidenceSpot { path: evidence.path.clone(), start: evidence.range.start, end: evidence.range.end, name: evidence.name.clone() };
            let candidate = EvidenceRef { definition: definition_index, evidence: evidence_index };
            let keep_current = chosen.get(&spot).is_some_and(|current| definitions[current.definition].full_range.start >= definition.full_range.start);
            if !keep_current {
                chosen.insert(spot, candidate);
            }
        }
    }
    chosen.into_values().collect()
}

/// One hit of DOEFF131 (the owner definition's index — None for a top-level expression; the handler's spelling; the roster definition's sentence that may name it; the position).
struct WorldSpot {
    owner: Option<usize>,
    spelling: String,
    allowed: String,
    range: Range,
}

/// Places that name a catalog real-I/O handler outside the roster definitions (and the nested definitions inside them) (references and calls — import lines are not counted).
/// The same handler in the same definition is one finding.
fn world_handler_spots(hy_file: &HyFileIndex, architecture: &architecture::Architecture) -> Vec<WorldSpot> {
    let wrapped = architecture.world_catalog().world_targets(architecture);
    let listed = architecture.world_definition_targets();
    let definitions = &hy_file.definitions;
    let in_import = |range: &Range| hy_file.imports.iter().any(|imp| imp.range.start <= range.start && range.end <= imp.range.end);
    let spots = hy_file
        .references
        .iter()
        .filter(|r| !in_import(&r.range))
        .filter_map(|r| r.target.as_ref().map(|t| (t, r.range)))
        .chain(hy_file.calls.iter().filter_map(|c| c.target.as_ref().map(|t| (t, c.range))));
    let catalog = architecture.world_catalog();
    let mut chosen: BTreeMap<(Option<usize>, String), Range> = BTreeMap::new();
    for (target, range) in spots {
        // Catalog rows that are not counted (migration-period rows that can only be written in :wraps) are not counted.
        if !wrapped.contains_key(target) || !catalog.handlers.get(target).is_some_and(|h| h.counts()) {
            continue;
        }
        let inside = |d: &Definition| d.full_range.start <= range.start && range.end <= d.full_range.end;
        if definitions.iter().any(|d| inside(d) && listed.contains_key(&d.qualified_name)) {
            continue;
        }
        let owner = definitions.iter().enumerate().filter(|(_, d)| inside(d)).max_by_key(|(_, d)| d.full_range.start).map(|(index, _)| index);
        chosen.entry((owner, target.clone())).or_insert(range);
    }
    chosen
        .into_iter()
        .map(|((owner, target), range)| {
            let (spelling, by, _) = &wrapped[&target];
            let allowed = if by.is_empty() { "no definition in the roster lists this handler in :wraps".to_string() } else { by.join(", ") };
            WorldSpot { owner, spelling: spelling.clone(), allowed, range }
        })
        .collect()
}

/// Also applies the roster-permission rules DOEFF106 and 131 to Hy files outside the layer locations (dirs outside the layers, :raw-io-roots outside architecture's :root)
/// — only the roster definitions may touch the outside world, and files breaching the locations (DOEFF114) are no exception. Test files
/// (tests in a path segment, a name that is test_ or conftest) and :exclude segments are removed (edge tests belong to DOEFF133).
fn judge_unplaced_world(
    root: &Path,
    architecture: &architecture::Architecture,
    raw: &raw_settings::RawSettingsSpec,
    placed: &BTreeSet<&str>,
    hy: &HashMap<String, HyFileIndex>,
    enabled: &BTreeSet<ProjectRule>,
) -> Vec<Draft> {
    let roots: Vec<String> = match &architecture.raw_io_roots {
        Some(roots) => roots.iter().map(|r| layers::normalize_dir(r)).collect(),
        None => vec![layers::normalize_dir(&architecture.root)],
    };
    let modules = raw.world_modules.clone().unwrap_or_default();
    let mut rels: Vec<&String> = hy.keys().filter(|rel| !placed.contains(rel.as_str())).collect();
    rels.sort();
    let mut drafts = Vec::new();
    for rel in rels {
        let parts: Vec<&str> = rel.split('/').collect();
        let name = parts.last().copied().unwrap_or("");
        let under_root = roots.iter().any(|r| rel.starts_with(&format!("{}/", r)));
        let excluded = parts.iter().any(|p| architecture.exclude.iter().any(|e| e == p)) || name.starts_with("test_") || name.starts_with("conftest");
        if !under_root || excluded {
            continue;
        }
        let file = &hy[rel];
        let definitions = &file.definitions;
        // A volume of executable ADRs (a file with defadr) is a volume of tests that pytest collects — excluded like test files.
        if definitions.iter().any(|d| d.kind == DefinitionKind::Defadr) {
            continue;
        }
        // Real I/O inside a deftest (including the law tests of ADR volumes) is the tests' share (edge tests — DOEFF133) and is not counted here.
        let in_test = |index: usize| {
            let target = &definitions[index];
            target.kind == DefinitionKind::Deftest
                || definitions.iter().any(|d| d.kind == DefinitionKind::Deftest && d.full_range.start <= target.full_range.start && target.full_range.end <= d.full_range.end)
        };
        let path = root.join(rel);
        let draft = |rule: ProjectRule, range: Range, message: String, detail: String, base: Severity| Draft {
            rule,
            layer: None,
            rel: rel.clone(),
            path: path.clone(),
            range,
            message: message.clone(),
            detail: Some(detail),
            base,
            explain: Explain::WorldOutsideLayers { subject: message },
        };
        if enabled.contains(&ProjectRule::RawSideEffectDirect) && !modules.contains(&architecture::mangle_dotted(&file.module)) {
            for found in innermost_raw_evidence(definitions)
                .into_iter()
                .filter(|f| !in_test(f.definition))
                .filter(|f| !boundary_allows(raw, &file.module, definitions[f.definition].raw.direct[f.evidence].category))
            {
                let definition = &definitions[found.definition];
                let evidence = &definition.raw.direct[found.evidence];
                drafts.push(draft(
                    ProjectRule::RawSideEffectDirect,
                    evidence.range,
                    format!(
                        "definition {} (outside the layer locations) directly touches raw side effect {} ({}) — only the modules of definitions in architecture.hy's :world-handlers may touch raw side effects",
                        definition.name,
                        evidence.name,
                        evidence.category.as_str()
                    ),
                    format!("{}::{}", definition_label(definition), evidence.name),
                    if evidence.strength == RawStrength::Strong { Severity::Error } else { Severity::Warning },
                ));
            }
        }
        if enabled.contains(&ProjectRule::WorldHandlerNamedOutsideList) {
            for spot in world_handler_spots(file, architecture).into_iter().filter(|s| !s.owner.is_some_and(in_test)) {
                let label = spot.owner.map(|i| definition_label(&definitions[i])).unwrap_or_else(|| "module の top level".to_string());
                let shown = spot.owner.map(|i| definitions[i].name.clone()).unwrap_or_else(|| "module top level".to_string());
                drafts.push(draft(
                    ProjectRule::WorldHandlerNamedOutsideList,
                    spot.range,
                    format!(
                        "{} (outside the layer locations) names doeff's real I/O handler {} — only the definitions in architecture.hy's :world-handlers ({}) may name it",
                        shown, spot.spelling, spot.allowed
                    ),
                    format!("{}::world::{}", label, spot.spelling),
                    Severity::Error,
                ));
            }
        }
    }
    drafts
}

// --- Semantic rules (DOEFF201, 202 — Jev) ---------------------------------------------------

/// The kind of definition to which a semantic question is applied (types, functions and handlers — value bindings, tests and macros are not asked).
fn semantic_kind(kind: DefinitionKind) -> bool {
    matches!(
        kind,
        DefinitionKind::Defn
            | DefinitionKind::DefnAsync
            | DefinitionKind::Defk
            | DefinitionKind::Deff
            | DefinitionKind::Defp
            | DefinitionKind::Defpp
            | DefinitionKind::Defhandler
            | DefinitionKind::Defeffect
            | DefinitionKind::Defclass
            | DefinitionKind::Defrecord
            | DefinitionKind::Defenum
    )
}

/// A list for making the spelling of a calibration example's kind 'static (the same kinds as semantic_kind).
const SEMANTIC_KIND_NAMES: &[&str] = &["defn", "defn/a", "defk", "deff", "defp", "defpp", "defhandler", "defeffect", "defclass", "defrecord", "defenum"];

/// Input of DOEFF203 and 204 — business Hy files; architecture.hy's acceptable reasons and unacceptable types (name, description, how to fix); the comment marker;
/// how to read the definition rules' tags (for reading the class shape); and the hy-index files (for choosing the classes to ask about with the same evidence as DOEFF119).
struct PlainCallableInput<'a> {
    files: Vec<(SourceFile, Option<String>)>,
    accepted: Vec<architecture::ReasonKind>,
    rejected: Vec<architecture::ReasonKind>,
    marker: String,
    tags: Option<layers::TagReading>,
    indexes: &'a HashMap<String, HyFileIndex>,
}

/// Correspondence between rules and questions.
fn semantic_rule(question: semantic::SemanticQuestion) -> ProjectRule {
    match question {
        semantic::SemanticQuestion::BusinessDecision => ProjectRule::SemanticBusinessDecision,
        semantic::SemanticQuestion::TransportKnowledge => ProjectRule::SemanticTransportKnowledge,
        semantic::SemanticQuestion::PlainCallable => ProjectRule::SemanticPlainCallable,
        semantic::SemanticQuestion::ClassRole => ProjectRule::SemanticClassRole,
        semantic::SemanticQuestion::MixedConcerns => ProjectRule::SemanticMixedConcerns,
    }
}

/// One definition that has a Jev answer — material for comparison with human judgment (including definitions that did not reach the threshold and so were not violations).
struct SemanticProbe {
    rule: ProjectRule,
    /// The layer used to look up the key's law (only for the rules that ask about layers, DOEFF201 and 202).
    layer: Option<LayerId>,
    rel: String,
    /// Key detail (the mangled definition name).
    detail: String,
    probability: f64,
    /// Whether it is a violation at the current threshold.
    flagged: bool,
}

/// DOEFF201 and 202: turns the top-level definitions of the configured layers' Hy into Jev questions, and reads the cache (default) or fires (--semantic). A definition with no answer counts as unjudged.
fn judge_semantic(
    root: &Path,
    settings: &semantic::SemanticSettings,
    layers: &LayerSettings,
    enabled: &BTreeSet<ProjectRule>,
    files: &[(LayerFile, Option<String>)],
    mode: &semantic::SemanticMode,
    plain: &PlainCallableInput<'_>,
) -> (Vec<Draft>, semantic::SemanticSummary, Vec<String>, Vec<SemanticProbe>) {
    let target = semantic::target_for_repo(settings.proxy.as_ref());
    let model = target.model.clone();
    let wire = format!("{:?}({})", target.wire, target.source).to_lowercase();
    let mut errors = Vec::new();
    let gateway: Option<semantic::HttpGateway> = match mode {
        semantic::SemanticMode::CacheOnly => None,
        // A question only if remembered is fired only at the proxy (for a non-proxy destination only the cache is used — the real Jev is not called). Without a token, silently only the cache.
        semantic::SemanticMode::Peek => match (target.proxy, settings.proxy.as_ref()) {
            (true, Some(proxy)) => semantic::HttpGateway::new(target, proxy.peek_timeout).ok(),
            _ => None,
        },
        _ => match semantic::HttpGateway::new(target, settings.timeout) {
            Ok(gateway) => Some(gateway),
            Err(reason) => {
                errors.push(format!("semantic rules: {}", reason));
                None
            }
        },
    };
    let mut items = Vec::new();
    // The per-file Hy index is shared by the question sections below (layer questions, DOEFF203, DOEFF205) — re-indexing the same file per section cost, in a single-file run,
    // about 0.02 seconds each, 3 times, for a 34KB file. Every section reads the same file with the same contents (stdin contents for a single-file run,
    // disk for a whole run), so the answer does not change.
    let mut indexed: HashMap<String, HyFileIndex> = HashMap::new();
    let mut roles_of: HashMap<String, Vec<String>> = HashMap::new();
    for (file, source) in files {
        if file.file.language != Language::Hy {
            continue;
        }
        let questions: Vec<semantic::SemanticQuestion> = semantic::SemanticQuestion::ALL
            .into_iter()
            .filter(|q| enabled.contains(&semantic_rule(*q)) && settings.questions.get(q).is_some_and(|s| s.layers.contains(&file.site.layer)))
            .collect();
        if questions.is_empty() {
            continue;
        }
        let text = match source {
            Some(text) => text.clone(),
            None => match std::fs::read_to_string(&file.file.path) {
                Ok(text) => text,
                Err(error) => {
                    errors.push(format!("{}: could not read: {}", file.file.rel, error));
                    continue;
                }
            },
        };
        let facts = crate::timing::timed("semantic.read-facts", || read_facts(Language::Hy, &text, &file.module, &layers.tags));
        let mut roles: Vec<String> = Vec::new();
        for tags in facts.tag_sets() {
            if let Some(role) = tags.role.clone().filter(|r| !r.is_empty() && !roles.contains(r)) {
                roles.push(role);
            }
        }
        roles_of.insert(file.file.rel.clone(), roles);
        let index = &*indexed.entry(file.file.rel.clone()).or_insert_with(|| crate::timing::timed("semantic.index", || hy_index::index_source(root, &file.file.path, &text)));
        let spec = &layers.layers[file.site.layer.0];
        for definition in index.definitions.iter().filter(|d| d.container.is_none() && semantic_kind(d.kind)) {
            let start = crate::position::offset_of(&text, definition.full_range.start);
            let end = crate::position::offset_of(&text, definition.full_range.end);
            let body = text.get(start..end).unwrap_or("");
            for question in &questions {
                items.push(semantic::item(
                    settings,
                    &model,
                    *question,
                    &file.file.rel,
                    &file.file.path,
                    &definition.name,
                    definition.kind.as_str(),
                    definition.range,
                    body,
                    file.site.layer,
                    &spec.name,
                    &spec.description,
                ));
            }
        }
    }
    // DOEFF203: for each deff that declared a kind, have Jev choose a reason from the list of kinds + none.
    let pairs = |reasons: &[architecture::ReasonKind]| reasons.iter().map(|r| (r.name.clone(), r.description.clone())).collect::<Vec<_>>();
    let (accepted, rejected) = (pairs(&plain.accepted), pairs(&plain.rejected));
    if settings.plain_callable.is_some() && enabled.contains(&ProjectRule::SemanticPlainCallable) && !plain.accepted.is_empty() {
        for (file, source) in &plain.files {
            let text = match source {
                Some(text) => text.clone(),
                None => match std::fs::read_to_string(&file.path) {
                    Ok(text) => text,
                    Err(error) => {
                        errors.push(format!("{}: could not read: {}", file.rel, error));
                        continue;
                    }
                },
            };
            let index = &*indexed.entry(file.rel.clone()).or_insert_with(|| crate::timing::timed("semantic.index", || hy_index::index_source(root, &file.path, &text)));
            for definition in index.definitions.iter().filter(|d| d.container.is_none() && d.kind == DefinitionKind::Deff) {
                let start = crate::position::offset_of(&text, definition.full_range.start);
                let (line, previous) = line_and_previous(&text, start);
                let comment = parse_reason_comment(&line, &plain.marker).or_else(|| {
                    previous.trim_start().starts_with(';').then(|| parse_reason_comment(&previous, &plain.marker)).flatten()
                });
                // Ask only about a deff that has a reason comment that is neither empty nor "same as above" (anything else is emitted deterministically by DOEFF111).
                let Some(ReasonComment { kind: stated_kind, detail }) = comment else { continue };
                if is_not_a_reason(&detail) {
                    continue;
                }
                let end = crate::position::offset_of(&text, definition.full_range.end);
                items.push(semantic::plain_callable_item(
                    settings,
                    &model,
                    &accepted,
                    &rejected,
                    &file.rel,
                    &file.path,
                    &definition.name,
                    definition.kind.as_str(),
                    definition.range,
                    text.get(start..end).unwrap_or(""),
                    stated_kind.as_deref(),
                    &detail,
                ));
            }
        }
    }
    // DOEFF204: only classes for which DOEFF119 emits nothing and that have a method with processing, have Jev choose from value / external-world / stateful / other.
    if settings.class_role.is_some() && enabled.contains(&ProjectRule::SemanticClassRole) {
        if let Some(reading) = &plain.tags {
            for (file, source) in &plain.files {
                let text = match source {
                    Some(text) => text.clone(),
                    None => match std::fs::read_to_string(&file.path) {
                        Ok(text) => text,
                        Err(error) => {
                            errors.push(format!("{}: could not read: {}", file.rel, error));
                            continue;
                        }
                    },
                };
                let facts = crate::timing::timed("semantic.read-facts", || read_facts(Language::Hy, &text, &module_of(&file.rel), reading));
                if facts.classes.is_empty() {
                    continue;
                }
                let class_root = repo_root_of(file);
                let local: BTreeSet<&str> = facts.classes.iter().map(|c| c.name.name.as_str()).collect();
                let lines = LineIndex::new(&text);
                for class in facts.classes.iter().filter(|c| !c.compile_time) {
                    if judge_class(class, &facts.bindings, &local, class_root.as_deref(), plain.indexes.get(&file.rel)) != ClassJudgement::AskJev {
                        continue;
                    }
                    items.push(semantic::class_item(
                        settings,
                        &model,
                        &file.rel,
                        &file.path,
                        &class.name.name,
                        lines.range(class.name.span.start, class.name.span.end),
                        text.get(class.span.start..class.span.end).unwrap_or(""),
                        &class.fields,
                    ));
                }
            }
        }
    }
    // DOEFF205: ask whether a definition whose role is judgment / program (the definition's :tags or the module-head tags) mixes shape checking and judgment.
    // The population is the definitions files minus paths matching the layer declaration's exclude (`tests`, `conftest.py`, etc.) — excluded the same way as DOEFF201 and 202 (collected from the layer
    // files). Otherwise even the definitions in test locations would be asked of Jev and mix into the hits for business judgment.
    if let (Some(mixed), Some(reading)) = (&settings.mixed_concerns, &plain.tags) {
        if enabled.contains(&ProjectRule::SemanticMixedConcerns) {
            let spec = &layers.layers[mixed.layer.0];
            for (file, source) in plain.files.iter().filter(|(file, _)| !file.rel.split('/').any(|part| layers.exclude.contains(part))) {
                let text = match source {
                    Some(text) => text.clone(),
                    None => match std::fs::read_to_string(&file.path) {
                        Ok(text) => text,
                        Err(error) => {
                            errors.push(format!("{}: could not read: {}", file.rel, error));
                            continue;
                        }
                    },
                };
                let module_role = crate::timing::timed("semantic.read-facts", || read_facts(Language::Hy, &text, &module_of(&file.rel), reading)).module_tags.and_then(|t| t.role);
                let index = &*indexed.entry(file.rel.clone()).or_insert_with(|| crate::timing::timed("semantic.index", || hy_index::index_source(root, &file.path, &text)));
                for definition in index.definitions.iter().filter(|d| d.container.is_none() && is_judgment_kind(d.kind)) {
                    let role = definition.tags.as_ref().and_then(|t| t.get("role").cloned()).or_else(|| module_role.clone());
                    if !role.is_some_and(|r| mixed.roles.contains(&r)) {
                        continue;
                    }
                    let start = crate::position::offset_of(&text, definition.full_range.start);
                    let end = crate::position::offset_of(&text, definition.full_range.end);
                    items.push(semantic::mixed_item(
                        settings,
                        &model,
                        &file.rel,
                        &file.path,
                        &definition.name,
                        definition.kind.as_str(),
                        definition.range,
                        text.get(start..end).unwrap_or(""),
                        mixed.layer,
                        &spec.name,
                        &spec.description,
                    ));
                }
            }
        }
    }
    // Build the calibration watch's examples (the bundled positive and counter examples) with the description of the first layer to which that question applies.
    let calibration: Vec<(semantic::SemanticItem, bool)> = semantic::calibration_examples()
        .into_iter()
        .filter_map(|example| {
            let rule = ProjectRule::parse(&example.rule)?;
            // Calibration covers only the questions of enabled rules (a run with only DOEFF203 does not fire the DOEFF201 and 202 examples).
            if !enabled.contains(&rule) {
                return None;
            }
            let range = Range { start: Position { line: 0, character: 0 }, end: Position { line: 0, character: 0 } };
            let question = match rule {
                ProjectRule::SemanticBusinessDecision => semantic::SemanticQuestion::BusinessDecision,
                ProjectRule::SemanticTransportKnowledge => semantic::SemanticQuestion::TransportKnowledge,
                ProjectRule::SemanticMixedConcerns => {
                    let mixed = settings.mixed_concerns.as_ref()?;
                    let spec = &layers.layers[mixed.layer.0];
                    let kind: &'static str = SEMANTIC_KIND_NAMES.iter().find(|k| **k == example.kind).copied().unwrap_or("defk");
                    let item = semantic::mixed_item(
                        settings,
                        &model,
                        &example.path,
                        Path::new(&example.path),
                        &example.name,
                        kind,
                        range,
                        &example.source,
                        mixed.layer,
                        &spec.name,
                        &spec.description,
                    );
                    return Some((item, example.expect));
                }
                ProjectRule::SemanticClassRole => {
                    settings.class_role?;
                    let item = semantic::class_item(settings, &model, &example.path, Path::new(&example.path), &example.name, range, &example.source, &example.fields);
                    return Some((item, example.expect));
                }
                _ => return None,
            };
            let layer = *settings.questions.get(&question)?.layers.iter().next()?;
            let spec = &layers.layers[layer.0];
            let kind: &'static str = SEMANTIC_KIND_NAMES.iter().find(|k| **k == example.kind).copied().unwrap_or("defn");
            let item = semantic::item(settings, &model, question, &example.path, Path::new(&example.path), &example.name, kind, range, &example.source, layer, &spec.name, &spec.description);
            Some((item, example.expect))
        })
        .collect();
    let outcome = crate::timing::timed("semantic.evaluate", || semantic::evaluate(root, settings, items, mode, gateway.as_ref().map(|g| g as &dyn semantic::Gateway), &calibration));
    errors.extend(outcome.errors);
    let mut summary = outcome.summary;
    summary.model = model;
    summary.wire = wire;
    let draft_of = |item: &semantic::SemanticItem, answer: &semantic::Answer| -> Option<Draft> {
            let probability = answer.probability;
            if item.question == semantic::SemanticQuestion::MixedConcerns {
                let chosen = answer.choice.clone().unwrap_or_else(|| "neither".to_string());
                let base = settings.mixed_concerns_severity(&chosen, probability)?;
                let layer = settings.mixed_concerns.as_ref().map(|m| layers.layers[m.layer.0].name.clone()).unwrap_or_default();
                return Some(Draft {
                    rule: ProjectRule::SemanticMixedConcerns,
                    layer: None,
                    rel: item.rel.clone(),
                    path: item.path.clone(),
                    range: item.range,
                    message: format!("{}: {} is judged by Jev to mix shape checking and judgment (p={:.2})", item.rel, item.name, probability),
                    detail: Some(hy_mangle(&item.name)),
                    base,
                    explain: Explain::MixedConcerns { name: item.name.clone(), kind: item.kind, probability, layer },
                });
            }
            if item.question == semantic::SemanticQuestion::ClassRole {
                let chosen = answer.choice.clone().unwrap_or_else(|| "other".to_string());
                let base = settings.class_role_severity(&chosen, probability)?;
                let fields: Vec<String> = item
                    .state
                    .pointer("/definition/fields")
                    .and_then(|v| v.as_array())
                    .map(|a| a.iter().filter_map(|f| f.as_str().map(str::to_string)).collect())
                    .unwrap_or_default();
                return Some(Draft {
                    rule: ProjectRule::SemanticClassRole,
                    layer: None,
                    rel: item.rel.clone(),
                    path: item.path.clone(),
                    range: item.range,
                    message: format!("{}: defclass {} is judged by Jev to be {} (p={:.2})", item.rel, item.name, chosen, probability),
                    detail: Some(hy_mangle(&item.name)),
                    base,
                    explain: Explain::ClassRoleDoubt { name: item.name.clone(), chosen, probability, fields },
                });
            }
            if item.question == semantic::SemanticQuestion::PlainCallable {
                let chosen = answer.choice.clone().unwrap_or_else(|| "none".to_string());
                let accepted_kind = plain.accepted.iter().find(|r| r.name == chosen);
                let rejected_kind = plain.rejected.iter().find(|r| r.name == chosen);
                let probabilities = answer.probabilities.clone().unwrap_or_default();
                let rejected_total: f64 = probabilities
                    .iter()
                    .filter(|(name, _)| !plain.accepted.iter().any(|r| &r.name == *name))
                    .map(|(_, p)| *p)
                    .sum();
                let base = settings.plain_callable_severity(accepted_kind.is_some(), probability, rejected_total)?;
                let stated = item.state.pointer("/stated_reason/text").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let verdict = if accepted_kind.is_some() { "close to an accepted reason but doubtful" } else { "not accepted" };
                return Some(Draft {
                    rule: ProjectRule::SemanticPlainCallable,
                    layer: None,
                    rel: item.rel.clone(),
                    path: item.path.clone(),
                    range: item.range,
                    message: format!("{}: the reason of deff {} is {} (Jev's choice = {} p={:.2})", item.rel, item.name, verdict, chosen, probability),
                    detail: Some(hy_mangle(&item.name)),
                    base,
                    explain: Explain::PlainCallableDoubt {
                        definition: item.name.clone(),
                        kind: item.kind,
                        stated,
                        chosen_accepted: accepted_kind.is_some(),
                        chosen_description: accepted_kind.or(rejected_kind).map(|r| r.description.clone()),
                        fix: rejected_kind.and_then(|r| r.fix.clone()),
                        chosen,
                        chosen_probability: probability,
                        rejected_total,
                    },
                });
            }
            let base = settings.severity(item.question, probability)?;
            let spec = &layers.layers[item.layer.0];
            let placement = Placement { layer: item.layer, dir: String::new(), service: None, roles: Vec::new(), by_tags: false };
            let file_layer = files.iter().find(|(f, _)| f.file.rel == item.rel).map(|(f, _)| f.site.clone());
            let placement = match file_layer {
                Some(site) => Placement {
                    layer: site.layer,
                    dir: site.dir,
                    service: site.service,
                    roles: roles_of.get(&item.rel).cloned().unwrap_or_default(),
                    by_tags: site.by_tags,
                },
                None => placement,
            };
            Some(Draft {
                rule: semantic_rule(item.question),
                layer: Some(item.layer),
                rel: item.rel.clone(),
                path: item.path.clone(),
                range: item.range,
                message: format!(
                    "{}: {} ({}) — Jev's judgment p={:.2}: {}",
                    item.rel,
                    item.name,
                    spec.name,
                    probability,
                    item.question.meaning()
                ),
                detail: Some(hy_mangle(&item.name)),
                base,
                explain: Explain::Semantic { placement, definition: item.name.clone(), kind: item.kind, question: item.question, probability },
            })
    };
    let mut drafts = Vec::new();
    let mut probes = Vec::new();
    for (item, answer) in &outcome.answered {
        let draft = draft_of(item, answer);
        let layered = matches!(item.question, semantic::SemanticQuestion::BusinessDecision | semantic::SemanticQuestion::TransportKnowledge);
        probes.push(SemanticProbe {
            rule: semantic_rule(item.question),
            layer: layered.then_some(item.layer),
            rel: item.rel.clone(),
            detail: hy_mangle(&item.name),
            // A choosing question uses the probability of the target word (the same reading as the calibration watch — not the probability of the chosen word)
            probability: semantic::target_probability(item.question, answer),
            flagged: draft.is_some(),
        });
        drafts.extend(draft);
    }
    (drafts, summary, errors, probes)
}

// --- How definitions are written (DOEFF110-112) ------------------------------------------------------

/// Whether any of the definition-writing rules is enabled.
fn wants_definitions(enabled: &BTreeSet<ProjectRule>) -> bool {
    if enabled.iter().any(|rule| rule.is_smell())
        || enabled.contains(&ProjectRule::DefkCalledBare)
        || enabled.contains(&ProjectRule::EffectsDisagreeWithInference)
        || enabled.contains(&ProjectRule::StaleOutcomeBranch)
    {
        return true;
    }
    [ProjectRule::DefnForbidden, ProjectRule::DeffNeedsReason, ProjectRule::DefinitionTagsRequired, ProjectRule::TestIsDeftest, ProjectRule::ClassWithBehaviour]
        .iter()
        .any(|rule| enabled.contains(rule))
}

/// Whether it is a Hy file in the test locations (the population of DOEFF118).
fn is_test_file(rel: &str, definitions: &settings::DefinitionSettings) -> bool {
    language_of(Path::new(rel)) == Some(Language::Hy) && definitions.test_paths.iter().any(|pattern| glob_matches(pattern, rel))
}

/// Whether it is a file in the population of the definition rules (under one of the locations — if the locations are empty, all — and a Hy file not matching an excluded location or separator).
fn is_definition_file(rel: &str, definitions: &settings::DefinitionSettings) -> bool {
    language_of(Path::new(rel)) == Some(Language::Hy)
        && (definitions.paths.is_empty() || definitions.paths.iter().any(|place| matches_place(rel, place)))
        && !definitions.exclude.iter().any(|place| matches_place(rel, place))
        && !rel.split('/').any(|part| definitions.exclude_parts.contains(part))
}

/// The definition-writing rules (only those of ProjectRule::exemptible_by_layer) that the layer declaration's (the nearest architecture.hy above the file) `:exempt` removed by naming this file's module,
/// and, only when DOEFF110 is removed, the "names reachable from any
/// defmacro of the package" (macro_reach). Decided at the same one point as Python's per-statement rules (crate::population). If the declaration cannot be read, nothing is
/// removed — it does not silently go green (an unreadable declaration is named by DOEFF032 of the Python files of the same package).
struct LayerExemption {
    rules: BTreeSet<ProjectRule>,
    reached: Option<Arc<BTreeSet<String>>>,
}

fn layer_exemption(path: &Path) -> LayerExemption {
    match crate::population::population_of(path) {
        Ok(crate::population::FilePopulation::Exempt { rules, declaration, .. }) => {
            let rules: BTreeSet<ProjectRule> =
                rules.iter().filter_map(|id| ProjectRule::parse(id)).filter(|rule| rule.exemptible_by_layer()).collect();
            let reached = rules
                .contains(&ProjectRule::DefnForbidden)
                .then(|| declaration.parent().map(macro_reach::reached_in_package))
                .flatten();
            LayerExemption { rules, reached }
        }
        Ok(crate::population::FilePopulation::Plain) | Err(_) => LayerExemption { rules: BTreeSet::new(), reached: None },
    }
}

/// Drops the hits of removed rules. For DOEFF110, drops only the defn reachable from any defmacro of the package, and keeps a defn that is not reachable (a definition at
/// execution time — the watch of decision 2 of #2877) with the reason added.
fn without_exempted(found: Vec<Draft>, exemption: &LayerExemption) -> Vec<Draft> {
    found
        .into_iter()
        .filter_map(|draft| {
            if !exemption.rules.contains(&draft.rule) {
                return Some(draft);
            }
            let reached = draft.rule != ProjectRule::DefnForbidden
                || matches!((&exemption.reached, &draft.detail), (Some(reached), Some(name)) if reached.contains(name));
            (!reached).then(|| Draft {
                message: format!(
                    "{} — a module the layer declaration excluded from DOEFF110, but no defmacro of the package reaches it (a definition at execution time)",
                    draft.message
                ),
                ..draft
            })
        })
        .collect()
}

/// The kinds of reason for allowing a plain function that architecture.hy declared (empty if none).
fn plain_callable_reasons(settings: &ProjectSettings) -> &[architecture::ReasonKind] {
    settings.architecture.as_ref().map(|a| a.plain_callable_reasons.as_slice()).unwrap_or(&[])
}

/// Derives the repo root from the file's path and its path from the root (to see whether a base module is in the repo).
fn repo_root_of(file: &SourceFile) -> Option<PathBuf> {
    let depth = file.rel.split('/').count();
    file.path.ancestors().nth(depth).map(Path::to_path_buf)
}

/// Allowed bases recognized by name, such as exceptions, Enum and Protocol (looked at by the end of the name, so children of exceptions defined inside the repo are included).
fn is_allowed_base_name(base: &str) -> bool {
    let last = base.rsplit('.').next().unwrap_or(base);
    ["Error", "Exception", "Warning", "Enum", "Flag", "Protocol", "NamedTuple", "TypedDict"].iter().any(|suffix| last.ends_with(suffix))
}

/// Whether a base is a class outside the repo (an outside library or a built-in). If the first segment of the bound module is in the repo root, it is inside the repo.
fn is_external_base(base: &str, bindings: &BTreeMap<String, String>, local: &BTreeSet<&str>, root: Option<&Path>) -> bool {
    let head = base.split('.').next().unwrap_or(base);
    if local.contains(base) {
        return false;
    }
    let module = match bindings.get(base).or_else(|| bindings.get(head)) {
        Some(module) => module.clone(),
        // A bare name with no binding is a built-in (Exception, object ...).
        None if !base.contains('.') => return true,
        None => base.to_string(),
    };
    let top = module.split('.').next().unwrap_or(&module);
    match root {
        Some(root) => !(root.join(top).is_dir() || root.join(format!("{}.hy", top)).is_file() || root.join(format!("{}.py", top)).is_file()),
        None => true,
    }
}

/// The verdict for one class under DOEFF119 (a closed set).
#[derive(Debug, Clone, PartialEq)]
enum ClassJudgement {
    /// A class that inherits an exception, Enum, Protocol or a base from an outside library, or one with only fields and no method that has processing — emits nothing.
    Allowed,
    /// A classification decided deterministically (outside world / state / fields only).
    Found(explain::ClassShapeFacts, explain::ClassVerdict),
    /// There is a method with processing, but neither evidence nor rewriting is visible — ask Jev (DOEFF204) whether it is a value class or a gateway.
    AskJev,
}

/// DOEFF119: classify a defclass by the evidence in its contents — raw side effects (strong evidence, direct or via a call) in a method or a field's initial value mean outside world,
/// a method that rewrites a self field outside __init__ and the like means state, and if there is no method with processing it is fields only. It is not judged by name.
fn judge_class(
    class: &facts::ClassFact,
    bindings: &BTreeMap<String, String>,
    local: &BTreeSet<&str>,
    root: Option<&Path>,
    hy: Option<&HyFileIndex>,
) -> ClassJudgement {
    let bases: Vec<String> = class.bases.iter().filter(|b| b.as_str() != "object").cloned().collect();
    if bases.iter().any(|b| is_allowed_base_name(b) || is_external_base(b, bindings, local, root)) {
        return ClassJudgement::Allowed;
    }
    let dataclass = class.decorators.iter().any(|d| d == "dataclass" || d.ends_with(".dataclass"));
    let shape = explain::ClassShapeFacts { bases, dataclass };
    // Evidence of raw side effects — the class definition itself (including the expressions of field initial values) and the definitions inside the class (methods, fields).
    let own = |d: &&doeff_indexer::hy_index::Definition| {
        (d.container.is_none() && hy_mangle(&d.name) == class.name.name && d.kind == DefinitionKind::Defclass)
            || d.container.as_deref().is_some_and(|c| hy_mangle(c) == class.name.name)
    };
    let mut evidence: Vec<String> = Vec::new();
    for definition in hy.map(|f| f.definitions.iter().filter(own).collect::<Vec<_>>()).unwrap_or_default() {
        let strong = definition
            .raw
            .direct
            .iter()
            .chain(definition.raw.via.iter().map(|v| &v.evidence))
            .filter(|e| e.strength == doeff_indexer::hy_index::RawStrength::Strong);
        for found in strong {
            let text = format!("{}: {}", definition.name, found.name);
            if !evidence.contains(&text) {
                evidence.push(text);
            }
        }
    }
    if !evidence.is_empty() {
        evidence.truncate(4);
        return ClassJudgement::Found(shape, explain::ClassVerdict::ExternalWorld { evidence });
    }
    let dunder = |name: &str| name.starts_with("__") && name.ends_with("__");
    // Placing fields at construction time (__init__, __post_init__, __new__) does not count as rewriting.
    let mutations: Vec<String> = class
        .methods
        .iter()
        .filter(|m| !matches!(m.name.as_str(), "__init__" | "__post_init__" | "__new__"))
        .flat_map(|m| m.mutates.iter().map(move |field| format!("{}: self.{}", m.name, field)))
        .collect();
    if !mutations.is_empty() {
        return ClassJudgement::Found(shape, explain::ClassVerdict::Stateful { mutations });
    }
    match class.methods.iter().any(|m| m.has_body && !dunder(&m.name)) {
        false => ClassJudgement::Found(shape, explain::ClassVerdict::DataOnly),
        true => ClassJudgement::AskJev,
    }
}

/// The result of reading a reason comment `; <marker>(<kind>): <detail>` (the parentheses of the kind may be half-width or full-width. In the old form with no kind, kind = None).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReasonComment {
    pub kind: Option<String>,
    pub detail: String,
}

/// The deff reason marker when the settings give none (`definitions.deff_reason_marker`).
pub const DEFAULT_REASON_MARKER: &str = "cannot be a defk:";

/// The earlier default spelling of the marker (Japanese for "cannot be a defk:"). It is read alongside the configured marker, so the
/// comments written before the English default, and repos whose settings still name it, keep their meaning.
pub const LEGACY_REASON_MARKER: &str = "defk にできない:";

/// The spellings of the marker a reason comment may use: the configured one, the default, and the earlier default (trailing `:` removed).
pub fn reason_marker_spellings(marker: &str) -> Vec<&str> {
    let mut spellings: Vec<&str> = Vec::new();
    for spelling in [marker, DEFAULT_REASON_MARKER, LEGACY_REASON_MARKER] {
        let base = spelling.trim_end_matches([':', '：']).trim();
        if !base.is_empty() && !spellings.contains(&base) {
            spellings.push(base);
        }
    }
    spellings
}

/// Reads a reason comment from the comment of a line (after `;`). The marker is the settings' deff_reason_marker or one of the other spellings of
/// `reason_marker_spellings` (compared with the trailing `:` removed; the earliest one in the comment wins).
pub fn parse_reason_comment(line: &str, marker: &str) -> Option<ReasonComment> {
    let (_, comment) = line.split_once(';')?;
    let (at, base) = reason_marker_spellings(marker).into_iter().filter_map(|base| comment.find(base).map(|at| (at, base))).min_by_key(|(at, _)| *at)?;
    let rest = comment[at + base.len()..].trim_start();
    let (kind, rest) = match rest.chars().next() {
        Some('(') | Some('\u{ff08}') => {
            let close = rest.find([')', '\u{ff09}'])?;
            let open_len = rest.chars().next().map(char::len_utf8).unwrap_or(1);
            (Some(rest[open_len..close].trim().to_string()), &rest[close + rest[close..].chars().next().map(char::len_utf8).unwrap_or(1)..])
        }
        _ => (None, rest),
    };
    let detail = rest.trim_start().trim_start_matches([':', '\u{ff1a}']).trim().to_string();
    Some(ReasonComment { kind: kind.filter(|k| !k.is_empty()), detail })
}

/// Whether the reason text fails to be a reason specific to that definition (empty, "same as above" and its variants).
fn is_not_a_reason(detail: &str) -> bool {
    let text = detail.trim();
    text.is_empty() || ["同上", "上と同じ", "上に同じ", "前と同じ", "同前"].iter().any(|word| text.starts_with(word))
}

/// Returns the text of the line at the head byte position (the line containing the offset, and the line before it).
fn line_and_previous(source: &str, offset: usize) -> (String, String) {
    let offset = offset.min(source.len());
    let line_start = source[..offset].rfind('\n').map(|at| at + 1).unwrap_or(0);
    let line_end = source[offset..].find('\n').map(|at| offset + at).unwrap_or(source.len());
    let previous = if line_start == 0 {
        String::new()
    } else {
        let before = &source[..line_start - 1];
        before[before.rfind('\n').map(|at| at + 1).unwrap_or(0)..].to_string()
    };
    (source[line_start..line_end].to_string(), previous)
}

/// Collects, from the repo's Hy files, the failure-type declarations for DOEFF122 (only when the rule is enabled — otherwise empty). Reads only files
/// that contain the spelling `:failure` / `:absent`, and resolves the declared types to names including the module using the file's imports and module. Unreadable files are skipped.
fn failure_types_for(root: &Path, enabled: &BTreeSet<ProjectRule>, reading: &layers::TagReading) -> smells::FailureTypes {
    let mut all = smells::FailureTypes::default();
    // Failure types are consulted by DOEFF122 and per-effect declarations by DOEFF175 — if both are disabled, declarations are not collected.
    if !enabled.contains(&ProjectRule::FailureRethrow) && !enabled.contains(&ProjectRule::StaleOutcomeBranch) {
        return all;
    }
    // A file's failure types are decided by the file's contents and the tag-reading setting — the fingerprint of the reading is included in the mark, remembered per file, and
    // only changed files are re-read (#1033). It is a union of sets, so the order of combining does not change the answer.
    let files = hy_rel_files(root);
    let found: Vec<smells::FailureTypes> =
        facts_cache::per_file_keyed(root, smells::FAILURE_TYPES_KIND, &smells::failure_types_key(reading), &files, |rel, path| smells::failure_types_of_file(rel, path, reading));
    for one in found {
        all.extend(one);
    }
    all
}

/// Turns unreadable Hy files into DOEFF128 violations (whole = read the Hy files that rules read, one after another; single file = only the pre-save contents).
fn unreadable_findings(root: &Path, settings: &ProjectSettings, single: &Option<(PathBuf, String)>) -> Vec<Finding> {
    match single {
        Some((path, source)) => {
            let is_hy = language_of(path) == Some(Language::Hy);
            let rel = relative_path(root, path).unwrap_or_else(|| path.to_string_lossy().into_owned());
            if is_hy && is_judged_file(&rel, source, settings) {
                unreadable::finding(&rel, &root.join(&rel), source).into_iter().collect()
            } else {
                Vec::new()
            }
        }
        // Files in the walked range (under the root, and the settings' include) — the same list as the population of the definition rules.
        None => hy_files::walked(root, &settings.include)
            .par_iter()
            .filter_map(|(rel, path)| {
                let source = std::fs::read_to_string(path).ok()?;
                is_judged_file(rel, &source, settings).then(|| unreadable::finding(rel, path, &source)).flatten()
            })
            .collect(),
    }
}

/// Whether any rule reads the file (layer locations, the population of definition rules, test locations, the population of business names) — a file that no rule reads (such as an excerpt inside a
/// document) does not lose any violation even if unreadable, so it is not reported.
fn is_judged_file(rel: &str, source: &str, settings: &ProjectSettings) -> bool {
    let layered = settings.layers.as_ref().is_some_and(|layers| classify_layer_file(rel, layers).or_else(|| infer_layer_site(rel, source, layers)).is_some());
    let defined = settings.definitions.as_ref().is_some_and(|d| is_definition_file(rel, d) || is_test_file(rel, d));
    let named = settings.environment.as_ref().is_some_and(|env| is_environment_file(rel, env));
    let programs = settings.program_sleep.as_ref().is_some_and(|places| is_program_sleep_file(rel, places));
    let tests = settings.test_sleep.as_ref().is_some_and(|places| is_test_sleep_file(rel, places));
    layered || defined || named || programs || tests
}

/// Collects the set of defks for DOEFF126 from the repo's Hy files (only when the rule is enabled — otherwise empty). Reads only files that contain the spelling `(defk`, and
/// makes the top-level defk names into names including the module. Unreadable files are skipped.
fn defk_names_for(root: &Path, enabled: &BTreeSet<ProjectRule>) -> bare_calls::DefkNames {
    bare_calls::defk_names_over(root, enabled, facts_cache::Reach::Whole, &hy_rel_files(root))
}

/// The repo's Hy files (path from the root and absolute path — in walk order).
fn hy_rel_files(root: &Path) -> Vec<(String, PathBuf)> {
    hy_files::collect(root).into_iter().filter_map(|path| relative_path(root, &path).map(|rel| (rel, path))).collect()
}

/// Collects, from the repo's Hy files, the record fields that DOEFF126 does not count as answers (fields whose declared type names Program or EffectBase) (only when the rule
/// is enabled — otherwise empty). Reads only files that contain the spelling `(defrecord`. Like the defk names, remembered per file, and only changed files are re-read.
/// The classes of the repo's Python files are also read, as types that cannot accept a Program (`bare_calls::py_value_classes_in`).
fn program_fields_for(root: &Path, enabled: &BTreeSet<ProjectRule>) -> bare_calls::ProgramFields {
    if !enabled.contains(&ProjectRule::DefkCalledBare) {
        return bare_calls::ProgramFields::default();
    }
    let mut files = hy_rel_files(root);
    files.extend(handler_arguments::walk(root, &["py"]));
    program_fields_over(root, enabled, facts_cache::Reach::Whole, &files)
}

/// The Python files of the modules a written file names (`named_modules::of_file` over a probe of .py files) — the single-file run reads the Python classes from only these.
fn named_python_files(root: &Path, rel: &str, source: &str) -> Vec<(String, PathBuf)> {
    let known = hy_files::ModuleFiles::new(root, &["py"], doeff_indexer::hy_index::SKIPPED_DIRS);
    named_modules::files_of(&known, &named_modules::of_file(&known, rel, source), None)
}

/// The body of `program_fields_for` — receives the files to collect and how to look up (the same as `defk_names_over`. A record's type is looked up by a name including the module).
fn program_fields_over(root: &Path, enabled: &BTreeSet<ProjectRule>, reach: facts_cache::Reach, files: &[(String, PathBuf)]) -> bare_calls::ProgramFields {
    let mut all = bare_calls::ProgramFields::default();
    if !enabled.contains(&ProjectRule::DefkCalledBare) {
        return all;
    }
    let found: Vec<bare_calls::ProgramFields> = facts_cache::per_file_in(reach, root, "program-fields", "", files, |rel, path| {
        let source = std::fs::read_to_string(path).ok()?;
        if rel.ends_with(".py") {
            return Some(bare_calls::py_value_classes_in(&source, &module_of(rel))).filter(|f| !f.is_empty());
        }
        ["(defrecord", "(defwire", "(defenum"]
            .iter()
            .any(|head| source.contains(head))
            .then(|| bare_calls::program_fields_in(&source, &module_of(rel)))
            .filter(|f| !f.is_empty())
    });
    for fields in found {
        all.extend(fields);
    }
    all
}

/// The table for DOEFF127, 130 and 174 (types, effects, defks and inference over all the repo's Hy files) — built once, only when any of the rules is enabled. A single-file run reads that file from
/// the stdin contents. How inference is read is in the one place `signatures::World`, the same as the defk headings (signatures of editor-json).
fn effect_world_for(root: &Path, settings: &ProjectSettings, enabled: &BTreeSet<ProjectRule>, overlay: Option<(&str, &str)>) -> Option<signatures::World> {
    let definitions = settings.definitions.is_some() && enabled.contains(&ProjectRule::EffectsDisagreeWithInference);
    let translation = settings.translation.is_some() && settings.layers.is_some() && enabled.contains(&ProjectRule::TranslationEmitsIntent);
    let lower_layer = settings.translation.is_some() && settings.layers.is_some() && lower_layer_decl(settings).is_some() && enabled.contains(&ProjectRule::LowerLayerInDomain);
    // A single-file run (overlay = the pre-save contents) builds the table only from the files of the modules the written file names (`World::build_for_file`).
    (definitions || translation || lower_layer).then(|| match overlay {
        Some((rel, source)) => signatures::World::build_for_file(root, rel, source),
        None => signatures::World::build(root, None),
    })
}

/// DOEFF142: matches of defhandler parameters in one file -> drafts (critical — a boundary of responsibility).
fn judge_handler_arguments(root: &Path, rel: &str, source: &str, decl: &architecture::HandlerArguments, classes: &dyn handler_arguments::ClassLookup) -> Vec<Draft> {
    let lines = LineIndex::new(source);
    handler_arguments::findings_in(source, decl, classes)
        .into_iter()
        .map(|found| {
            let type_part = if found.type_text.is_empty() { String::new() } else { format!(" {}", found.type_text) };
            Draft {
                rule: ProjectRule::HandlerArgumentHoldsState,
                layer: None,
                path: root.join(rel),
                rel: rel.to_string(),
                range: lines.range(found.span.start, found.span.end),
                message: format!("{}: {} takes parameter {} ({}{}) — hold the connection target and settings with Ask, a client with (session val …), and state with (session var …)", rel, found.handler, found.param, found.kind.word(), type_part),
                detail: Some(found.detail()),
                base: Severity::Error,
                explain: Explain::HandlerArgumentHoldsState { handler: found.handler.clone(), param: found.param.clone(), kind: found.kind.word(), type_text: found.type_text.clone() },
            }
        })
        .collect()
}

/// The population of DOEFF130 — Hy modules of the translation layer (the settings' handler_layers).
fn is_translation_file(file: &LayerFile, translation: &settings::TranslationSettings) -> bool {
    file.file.language == Language::Hy && translation.handler_layers.contains(&file.site.layer)
}

/// DOEFF130: judge where a translation-layer handler emits a business intent (error — a violation of the responsibility boundary). A business intent = a fired call whose head is
/// a capitalized name (a type) of a module in the settings' intent_layers and belongs to the same service as the handler (public intents of other services are not counted). If the fired call's head is a repo defk, follow it down to max_depth levels —
/// names in the definition's own text alone would let an intent through via an imported function (the independent review of #942).
fn judge_translation_intents(
    file: &LayerFile,
    source: &str,
    layers: &LayerSettings,
    translation: &settings::TranslationSettings,
    index: &SiteIndex<'_>,
    world: &signatures::World,
) -> Vec<Draft> {
    let intent_layer_of = |qualified: &str| -> Option<LayerId> {
        let (module, name) = qualified.rsplit_once('.')?;
        // Only type names (capitalized head) — the intent layer holds only types
        if !name.starts_with(|c: char| c.is_ascii_uppercase()) {
            return None;
        }
        let site = index.get(module).or_else(|| index.get(&format!("{}.__init__", module)))?;
        // Emitting another service's public intent is the translation's job (the same reading as DOEFF156's "public effects of other services").
        // Only intents of the same service as the handler are stopped. If either service cannot be decided (a layer-first location), it is counted as before.
        let other_service = matches!((&site.service, &file.site.service), (Some(intent), Some(handler)) if intent != handler);
        (translation.intent_layers.contains(&site.layer) && !other_service).then_some(site.layer)
    };
    let is_intent = |qualified: &str| intent_layer_of(qualified).is_some();
    let lines = LineIndex::new(source);
    let handler_layer = &layers.layers[file.site.layer.0].name;
    signatures::translation_intents(world, &file.file.rel, source, &is_intent, translation.max_depth)
        .into_iter()
        .map(|intent| {
            let intent_layer = intent_layer_of(&intent.qualified).map(|id| layers.layers[id.0].name.clone()).unwrap_or_default();
            let message = match intent.via() {
                Some(via) => format!(
                    "{}: handler {} (layer {}) goes through {} and emits, from layer {}, the business intent {} — a translation handler emits only doeff's generic effects",
                    file.file.rel, intent.handler, handler_layer, via, intent_layer, intent.effect()
                ),
                None => format!(
                    "{}: handler {} (layer {}) emits, from layer {}, the business intent {} — a translation handler emits only doeff's generic effects",
                    file.file.rel, intent.handler, handler_layer, intent_layer, intent.effect()
                ),
            };
            Draft {
                rule: ProjectRule::TranslationEmitsIntent,
                layer: Some(file.site.layer),
                rel: file.file.rel.clone(),
                path: file.file.path.clone(),
                range: lines.range(intent.start, intent.end),
                message,
                detail: Some(format!("{}::{}", hy_mangle(&intent.handler), intent.qualified)),
                base: Severity::Error,
                explain: Explain::TranslationIntent { intent, handler_layer: handler_layer.clone(), intent_layer },
            }
        })
        .collect()
}

/// The population of DOEFF174 — Hy modules of the business layers (the settings' domain_layers).
fn is_domain_file(file: &LayerFile, translation: &settings::TranslationSettings) -> bool {
    file.file.language == Language::Hy && translation.domain_layers.contains(&file.site.layer)
}

/// The lower-layer declaration of DOEFF174 — :lower-layer-modules of :business-fakes in architecture.hy (not judged if empty).
fn lower_layer_decl(settings: &ProjectSettings) -> Option<&architecture::BusinessFakes> {
    settings.architecture.as_ref()?.business_fakes.as_ref().filter(|decl| !decl.lower_layer_modules.is_empty())
}

/// The handler package of DOEFF174 — for one spelling of :lower-layer-modules, the module with the last separator removed (`doeff_records.effects` ->
/// `doeff_records`). If there is no separator, the spelling itself. So that handlers of other modules of the same package (signal source, record client) are also counted.
fn lower_layer_package(pattern: &str) -> &str {
    let module = pattern.trim_end_matches('*').trim_end_matches('.');
    module.rsplit_once('.').map(|(package, _)| package).unwrap_or(module)
}

/// DOEFF174: judge where a business-layer definition emits a lower-layer effect (including forms reached by following defks) and where it wraps a handler of a lower-layer package
/// (error — a violation of the responsibility boundary).
fn judge_lower_layer_uses(
    file: &LayerFile,
    source: &str,
    layers: &LayerSettings,
    translation: &settings::TranslationSettings,
    decl: &architecture::BusinessFakes,
    world: &signatures::World,
) -> Vec<Draft> {
    let packages: Vec<&str> = decl.lower_layer_modules.iter().map(|p| lower_layer_package(p)).collect();
    let is_handler = |qualified: &str| {
        let module = business_fakes::module_of_effect(qualified);
        packages.iter().any(|package| module == *package || module.starts_with(&format!("{}.", package)))
    };
    // Effects = those of modules in :lower-layer-modules. Running a definition outside the repo in another module of the same package (a defk that wraps an effect, such as read-typed of doeff_records.typed)
    // is also counted — a definition outside the repo is not in the inference table and its effects cannot be followed, so the moment it is run it is regarded as a raw operation.
    let is_effect = |qualified: &str| {
        business_fakes::lower_layer_module(business_fakes::module_of_effect(qualified), decl)
            || (is_handler(qualified) && !world.definitions.contains_key(qualified))
    };
    let lines = LineIndex::new(source);
    let layer = layers.layers[file.site.layer.0].name.clone();
    signatures::lower_layer_uses(world, &file.file.rel, source, &is_effect, &is_handler, translation.max_depth)
        .into_iter()
        .map(|found| {
            let message = match (&found.kind, found.via()) {
                (signatures::LowerLayerKind::Handler, _) => format!(
                    "{}: definition {} (layer {}) wraps the lower-layer handler {} — wrapping a handler that answers the record service's raw operations is the job of the assembly layer; a business layer emits only domain effects",
                    file.file.rel, found.definition, layer, found.name()
                ),
                (_, Some(via)) => format!(
                    "{}: definition {} (layer {}) goes through {} and emits the lower-layer effect {} — raw operations of the record service are the handler's job; a business layer emits only domain effects",
                    file.file.rel, found.definition, layer, via, found.name()
                ),
                (_, None) => format!(
                    "{}: definition {} (layer {}) emits the lower-layer effect {} — raw operations of the record service are the handler's job; a business layer emits only domain effects",
                    file.file.rel, found.definition, layer, found.name()
                ),
            };
            Draft {
                rule: ProjectRule::LowerLayerInDomain,
                layer: Some(file.site.layer),
                rel: file.file.rel.clone(),
                path: file.file.path.clone(),
                range: lines.range(found.start, found.end),
                message,
                detail: Some(format!("{}::{}", hy_mangle(&found.definition), found.qualified)),
                base: Severity::Error,
                explain: Explain::LowerLayerUse { found, layer: layer.clone() },
            }
        })
        .collect()
}

/// DOEFF127: among the defks of business Hy files, for those that declare `:effects`, judge where the declaration and the inference disagree. Violation location = the head of the fired call that reaches an effect
/// not in the declaration / the name inside `:effects` of an effect that is not raised (shown at the violating place rather than as a count in the heading — #849).
fn judge_effect_mismatches(
    file: &SourceFile,
    source: &str,
    definitions: &settings::DefinitionSettings,
    world: Option<&signatures::World>,
) -> Vec<Draft> {
    let Some(world) = world else { return Vec::new() };
    if !is_definition_file(&file.rel, definitions) || !source.contains(":effects") {
        return Vec::new();
    }
    let lines = LineIndex::new(source);
    signatures::effect_mismatches(world, &file.rel, source)
        .into_iter()
        .map(|mismatch| {
            let (start, end) = mismatch.span();
            let message = match &mismatch {
                signatures::EffectMismatch::Undeclared { definition, effect, via: Some(via), .. } => {
                    format!("{}: defk {} goes through {} and raises effect {}, which is not in :effects", file.rel, definition, via, effect)
                }
                signatures::EffectMismatch::Undeclared { definition, effect, via: None, .. } => {
                    format!("{}: defk {} fires effect {}, which is not in :effects", file.rel, definition, effect)
                }
                signatures::EffectMismatch::Unused { definition, effect, .. } => {
                    format!("{}: defk {} lists {} in :effects but does not raise it", file.rel, definition, effect)
                }
            };
            Draft {
                rule: ProjectRule::EffectsDisagreeWithInference,
                layer: None,
                rel: file.rel.clone(),
                path: file.path.clone(),
                range: lines.range(start, end),
                message,
                detail: Some(format!("{}::{}", hy_mangle(mismatch.definition()), mismatch.effect())),
                base: Severity::Warning,
                explain: Explain::EffectMismatch { mismatch },
            }
        })
        .collect()
}

/// If this is a file for which a single-file run judges DOEFF126 (a Hy file in the population of the definition rules), its name (relative to the root — if outside the root but under a settings include dir, a relative path containing `..`).
/// With the same condition as the definition-style rule branch of `Target::Single`, decides whether to start prefetching at the start of the run (the answer does not change if it does not match — it is read when looked up).
fn bare_call_population_rel(root: &Path, settings: &ProjectSettings, enabled: &BTreeSet<ProjectRule>, path: &Path) -> Option<String> {
    let definitions = settings.definitions.as_ref()?;
    let rel = relative_path(root, path).or_else(|| hy_files::walked_rel(root, &settings.include, path))?;
    let in_population = language_of(path) == Some(Language::Hy) && (is_definition_file(&rel, definitions) || is_test_file(&rel, definitions));
    (wants_definitions(enabled) && in_population).then_some(rel)
}

/// DOEFF126: judge where a business Hy file (test locations too) calls a defk definition bare (error — because it is a silent mistake).
fn judge_bare_calls(
    file: &SourceFile,
    source: &str,
    definitions: &settings::DefinitionSettings,
    enabled: &BTreeSet<ProjectRule>,
    defks: &bare_calls::DefkNames,
    program_params: &dyn param_calls::ProgramParamsLookup,
    program_fields: &bare_calls::ProgramFields,
) -> Vec<Draft> {
    let in_population = is_definition_file(&file.rel, definitions) || is_test_file(&file.rel, definitions);
    if !enabled.contains(&ProjectRule::DefkCalledBare) || !in_population {
        return Vec::new();
    }
    let lines = LineIndex::new(source);
    let module = module_of(&file.rel);
    let facts = read_facts(Language::Hy, source, &module, &definitions.tags);
    let scope = smells::Scope { module: &module, bindings: &facts.bindings };
    // Bare calls placed in a record field whose declared type names Program or EffectBase are not counted.
    let mut drafts: Vec<Draft> = bare_calls::bare_calls_with(source, scope, defks, program_fields)
        .into_iter()
        .map(|call| Draft {
            rule: ProjectRule::DefkCalledBare,
            layer: None,
            rel: file.rel.clone(),
            path: file.path.clone(),
            range: lines.range(call.span.start, call.span.end),
            message: format!("{}: {} calls defk {} bare — a Program is returned, not the answer", file.rel, call.definition, call.callee),
            detail: Some(call.detail()),
            base: Severity::Error,
            explain: Explain::BareDefkCall { call },
        })
        .collect();
    // Forms that call bare a function received as an argument — only when the caller passes a defk or fnk as that argument.
    drafts.extend(param_calls::param_calls_in(source, scope, defks, program_params).into_iter().map(|call| Draft {
        rule: ProjectRule::DefkCalledBare,
        layer: None,
        rel: file.rel.clone(),
        path: file.path.clone(),
        range: lines.range(call.span.start, call.span.end),
        message: format!(
            "{}: {} calls argument {} bare — the caller ({}) passes {}, so a Program is returned, not the answer",
            file.rel, call.definition, call.param, call.caller, call.passed
        ),
        detail: Some(call.detail()),
        base: Severity::Error,
        explain: Explain::ParamCalledBare { call },
    }));
    drafts
}

/// Material for the second form of DOEFF126 — among all calls in the repo, those that pass a defk or fnk as an argument (only when the rule is enabled).
fn program_params_for(
    root: &Path,
    enabled: &BTreeSet<ProjectRule>,
    defks: &bare_calls::DefkNames,
) -> param_calls::ProgramParams {
    let mut params = param_calls::ProgramParams::default();
    if !enabled.contains(&ProjectRule::DefkCalledBare) || defks.is_empty() {
        return params;
    }
    // A file's result is decided by the file's contents and the names of defks across the whole repo (defks). The fingerprint of defks is included in the mark and remembered per file,
    // and while defks does not change, only changed files are re-analyzed (if defks changes, everything is rebuilt, #1026).
    let files = hy_rel_files(root);
    let found: Vec<param_calls::ProgramParams> =
        facts_cache::per_file_keyed(root, "program-params", &defks.digest(), &files, |rel, path| {
            let source = std::fs::read_to_string(path).ok()?;
            let module = module_of(rel);
            let bindings = facts::hy_bindings(&source, &module);
            let mut one = param_calls::ProgramParams::default();
            one.collect(&source, rel, smells::Scope { module: &module, bindings: &bindings }, defks);
            (!one.is_empty()).then_some(one)
        });
    for one in found {
        params.merge(one);
    }
    params
}

/// The kinds of definitions to which the DOEFF121 question applies (functions and handlers).
fn is_judgment_kind(kind: DefinitionKind) -> bool {
    matches!(kind, DefinitionKind::Defk | DefinitionKind::Deff | DefinitionKind::Defn | DefinitionKind::DefnAsync | DefinitionKind::Defp | DefinitionKind::Defpp)
}

/// DOEFF175: judge stale branches on an effect whose declaration was switched (a branch that separates, as values, a type the effect declared as absent or failure, on a variable bound to its answer).
/// The target is all the Hy files of the definition-rule population (definition locations and test locations) — test callers also go stale when an effect is switched. The severity is error (critical).
fn judge_stale_branches(
    file: &SourceFile,
    source: &str,
    definitions: &settings::DefinitionSettings,
    enabled: &BTreeSet<ProjectRule>,
    failure: &dyn smells::FailureLookup,
) -> Vec<Draft> {
    if !enabled.contains(&ProjectRule::StaleOutcomeBranch) || !(is_definition_file(&file.rel, definitions) || is_test_file(&file.rel, definitions)) {
        return Vec::new();
    }
    let lines = LineIndex::new(source);
    let module = module_of(&file.rel);
    let facts = read_facts(Language::Hy, source, &module, &definitions.tags);
    stale_outcome_branches::stale_branches_in(source, smells::Scope { module: &module, bindings: &facts.bindings }, failure)
        .into_iter()
        .map(|branch| Draft {
            rule: ProjectRule::StaleOutcomeBranch,
            layer: None,
            rel: file.rel.clone(),
            path: file.path.clone(),
            range: lines.range(branch.span.start, branch.span.end),
            message: format!(
                "{} {} — {} declared {} as {}. <- does not bind {} to {}, so this branch ({}) is never taken — use maybe / on-raise / :absent",
                file.rel,
                branch.definition,
                branch.effect,
                branch.ty,
                branch.declared_word(),
                branch.subject,
                branch.ty,
                branch.form.word()
            ),
            detail: Some(branch.detail()),
            base: Severity::Error,
            explain: Explain::StaleBranch { branch },
        })
        .collect()
}

/// DOEFF121-125: judge smells in business Hy files (the default severity is warning — raised from info because Absent / Raise entered the mainline (ADR-DOE-HY-007 R9).
/// It can be lowered to info with the settings' rules.<ID>.severity. A warning listed in the registry becomes info).
fn judge_smells(
    file: &SourceFile,
    source: &str,
    settings: &ProjectSettings,
    definitions: &settings::DefinitionSettings,
    enabled: &BTreeSet<ProjectRule>,
    failure: &dyn smells::FailureLookup,
) -> Vec<Draft> {
    if !is_definition_file(&file.rel, definitions) || !enabled.iter().any(|rule| rule.is_smell()) {
        return Vec::new();
    }
    // DOEFF121 applies only to files of judgment layers (smells.shape_check_layers) — the layer is inferred from the path location or, for a layer-first dir, from the tag.
    let shape_checks = enabled.contains(&ProjectRule::ShapeCheckInJudgment)
        && match (&settings.smells, &settings.layers) {
            (Some(smell), Some(layers)) => classify_layer_file(&file.rel, layers)
                .or_else(|| infer_layer_site(&file.rel, source, layers))
                .is_some_and(|(site, _)| smell.shape_check_layers.contains(&site.layer)),
            _ => false,
        };
    let lines = LineIndex::new(source);
    let module = module_of(&file.rel);
    let facts = read_facts(Language::Hy, source, &module, &definitions.tags);
    smells::smells_in(source, smells::Scope { module: &module, bindings: &facts.bindings }, failure, shape_checks)
        .into_iter()
        .filter_map(|smell| {
            let rule = match smell.kind {
                smells::SmellKind::ShapeCheck { .. } => ProjectRule::ShapeCheckInJudgment,
                smells::SmellKind::FailureRethrow { .. } => ProjectRule::FailureRethrow,
                smells::SmellKind::BindThenReturn { .. } => ProjectRule::BindThenReturn,
                smells::SmellKind::FieldsJoined { .. } => ProjectRule::FieldsJoinedIntoText,
                smells::SmellKind::RebuiltAccumulator { .. } => ProjectRule::RebuiltAccumulator,
            };
            if !enabled.contains(&rule) {
                return None;
            }
            let message = match &smell.kind {
                smells::SmellKind::ShapeCheck { field, .. } => format!("{}: {} checks the shape of field \"{}\" with isinstance", file.rel, smell.definition, field),
                smells::SmellKind::FailureRethrow { failure_type, subject } => {
                    format!("{}: {} returns failure {} ({}) again", file.rel, smell.definition, failure_type, subject)
                }
                smells::SmellKind::BindThenReturn { name } => format!("{}: {} binds {} and returns it right away", file.rel, smell.definition, name),
                smells::SmellKind::FieldsJoined { value, fields } => {
                    format!("{}: {} joins into a string the fields of {}: {}", file.rel, smell.definition, value, fields.join("・"))
                }
                smells::SmellKind::RebuiltAccumulator { name } => format!("{}: {} rebuilds {} inside a loop", file.rel, smell.definition, name),
            };
            Some(Draft {
                rule,
                layer: None,
                rel: file.rel.clone(),
                path: file.path.clone(),
                range: lines.range(smell.span.start, smell.span.end),
                message,
                detail: Some(smell.detail()),
                base: Severity::Warning,
                explain: Explain::Smell { smell },
            })
        })
        .collect()
}

/// DOEFF110-112: judge how a file's definitions are written (the ban on defn, the reason comment of deff, required tags on definitions).
fn judge_definitions(
    file: &SourceFile,
    source: &str,
    definitions: &settings::DefinitionSettings,
    enabled: &BTreeSet<ProjectRule>,
    reasons: &[architecture::ReasonKind],
    hy: Option<&HyFileIndex>,
) -> Vec<Draft> {
    let facts = read_facts(Language::Hy, source, &module_of(&file.rel), &definitions.tags);
    let lines = LineIndex::new(source);
    let reading = &definitions.tags;
    let module_keys: BTreeSet<String> = match (&facts.module_tags, reading.module_default) {
        (Some(tags), true) => tags.keys.clone(),
        _ => BTreeSet::new(),
    };
    let draft = |rule: ProjectRule, span: ByteSpan, message: String, detail: String, explain: Explain| Draft {
        rule,
        layer: None,
        rel: file.rel.clone(),
        path: file.path.clone(),
        range: lines.range(span.start, span.end),
        message,
        detail: Some(detail),
        base: Severity::Error,
        explain,
    };
    let mut drafts = Vec::new();
    let (in_scope, in_tests) = (is_definition_file(&file.rel, definitions), is_test_file(&file.rel, definitions));
    if in_scope && enabled.contains(&ProjectRule::ClassWithBehaviour) {
        let root = repo_root_of(file);
        let local: BTreeSet<&str> = facts.classes.iter().map(|c| c.name.name.as_str()).collect();
        for class in facts.classes.iter().filter(|c| !c.compile_time) {
            let (shape, verdict) = match judge_class(class, &facts.bindings, &local, root.as_deref(), hy) {
                ClassJudgement::Found(shape, verdict) => (shape, verdict),
                ClassJudgement::Allowed | ClassJudgement::AskJev => continue,
            };
            let name = class.name.name.clone();
            let (message, base) = match &verdict {
                explain::ClassVerdict::ExternalWorld { evidence } => {
                    (format!("{}: defclass {} touches the outside world ({}) — make it a foundation handler", file.rel, name, evidence.join("・")), Severity::Error)
                }
                explain::ClassVerdict::Stateful { mutations } => {
                    (format!("{}: defclass {} holds mutable state ({}) — put state in a handler's (session var …)", file.rel, name, mutations.join("・")), Severity::Warning)
                }
                explain::ClassVerdict::DataOnly => (format!("{}: defclass {} is fields only — it can be a defrecord", file.rel, name), Severity::Info),
            };
            let mut found = draft(ProjectRule::ClassWithBehaviour, class.name.span, message, name.clone(), Explain::ClassShape { name, shape, verdict });
            found.base = base;
            drafts.push(found);
        }
    }
    for definition in &facts.definitions {
        let name = definition.name.name.clone();
        let head = definition.head.as_str();
        if in_tests
            && enabled.contains(&ProjectRule::TestIsDeftest)
            && matches!(head, "defn" | "defn/a" | "deff" | "defk" | "fn")
            && name.starts_with("test_")
        {
            drafts.push(draft(
                ProjectRule::TestIsDeftest,
                definition.name.span,
                format!("{}: {} is a test written with {} — write it with deftest", file.rel, name, head),
                name.clone(),
                Explain::TestNotDeftest { name: name.clone(), head: head.to_string() },
            ));
        }
        if !in_scope || head == "fn" {
            continue;
        }
        let (line, previous) = line_and_previous(source, definition.start);
        let same_line = parse_reason_comment(&line, &definitions.deff_reason_marker);
        // The previous line is read only when it is a comment-only line (so the end-of-line comment of the previous definition is not mistaken).
        let comment = same_line.clone().or_else(|| {
            previous.trim_start().starts_with(';').then(|| parse_reason_comment(&previous, &definitions.deff_reason_marker)).flatten()
        });
        let declared_kind = same_line
            .as_ref()
            .and_then(|c| c.kind.clone())
            .and_then(|kind| reasons.iter().find(|r| r.name == kind).cloned());
        if enabled.contains(&ProjectRule::DefnForbidden) && matches!(head, "defn" | "defn/a") && !definition.compile_time {
            drafts.push(draft(
                ProjectRule::DefnForbidden,
                definition.name.span,
                format!("{}: {} is written with {} — write it with defk", file.rel, name, head),
                name.clone(),
                Explain::DefnForbidden { name: name.clone(), head: head.to_string(), declared_kind: declared_kind.clone() },
            ));
        }
        if enabled.contains(&ProjectRule::DeffNeedsReason) && head == "deff" {
            // What is judged deterministically is only "no comment, empty reason, same as above and its variants". Whether to accept the reason is decided by Jev (DOEFF203).
            let problem = match &comment {
                None => Some(explain::DeffReasonProblem::Missing),
                Some(ReasonComment { detail, .. }) if is_not_a_reason(detail) => Some(explain::DeffReasonProblem::NoDetail { detail: detail.clone() }),
                Some(_) => None,
            };
            if let Some(problem) = problem {
                let found = draft(
                    ProjectRule::DeffNeedsReason,
                    definition.name.span,
                    format!("{}: reason comment of deff {} — {}", file.rel, name, problem.short()),
                    name.clone(),
                    Explain::DeffWithoutReason {
                        name: name.clone(),
                        marker: definitions.deff_reason_marker.clone(),
                        problem,
                        kinds: reasons.to_vec(),
                    },
                );
                drafts.push(found);
            }
        }
        if enabled.contains(&ProjectRule::DefinitionTagsRequired) && reading.require_on.contains(head) {
            let own: BTreeSet<String> = definition.tags.as_ref().map(|t| t.keys.clone()).unwrap_or_default();
            let missing: Vec<String> = reading.required.iter().filter(|key| !own.contains(*key) && !module_keys.contains(*key)).cloned().collect();
            if !missing.is_empty() {
                drafts.push(draft(
                    ProjectRule::DefinitionTagsRequired,
                    definition.name.span,
                    format!("{}: {} ({}) lacks {} in :tags", file.rel, name, head, missing.join("・")),
                    name.clone(),
                    Explain::DefinitionTagsMissing {
                        name: name.clone(),
                        head: head.to_string(),
                        missing,
                        has_tags: definition.tags.is_some(),
                        module_default: reading.module_default,
                    },
                ));
            }
        }
    }
    drafts
}

// --- Where JsonValue is used (DOEFF120) ---------------------------------------------------

/// Names counted by DOEFF120 — types that merely wrap a plain dict, list, str ... with a name (JsonObject = dict[str, JsonValue] is the same escape hatch).
const JSON_VALUE_NAMES: &[&str] = &["JsonValue", "JSONValue", "JsonObject", "JSONObject"];

/// Modules of the generic resolvers that may use JsonValue in any repo (the runtime of doeff-hy's defwire, and doeff-records' wire).
/// Matched against the last segment of the module spelling (doeff's repo `packages/doeff-hy/src/doeff_hy/_wire.hy` = `packages.doeff-hy.src.doeff_hy._wire` also matches).
const BUILTIN_WIRE_MODULES: &[&str] = &["doeff_hy._wire", "doeff_records._wire"];

/// Dirs not descended into in the DOEFF120 population (hidden dirs starting with `.` are also not descended into — `.venv`, `.git`, `.worktrees` ...).
const JSON_VALUE_SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// The allowance verdict of DOEFF120 (a closed set).
enum JsonValueAllowance {
    /// JsonValue may be used.
    Allowed,
    /// May not be used (the reason is shown in the explanation).
    Refused(explain::JsonValueRefusal),
}

/// The allowance policy of DOEFF120 — decides in this one place only whether a module may use JsonValue.
///
/// The policy is designed to be swappable: when changing how the allowed places are decided (e.g. moving to allow only inside the resolvers generated by the defwire macro),
/// change only the body of this function. The callers (population, how names are counted, key, shape of the explanation) do not change.
///
/// The current policy (only the generic resolvers and the foundation that does the sending and receiving itself may touch JsonValue):
/// 1. The built-in generic resolvers (`BUILTIN_WIRE_MODULES`) are allowed.
/// 2. A module that matches a `:wire-modules` pattern of architecture.hy and is in the foundation layer (the location of the `:foundation` layer) is allowed.
/// 3. A module that matches `:wire-modules` but is outside foundation is not allowed (the reason is shown in the explanation). One that does not match is not allowed.
fn json_value_allowance(rel: &str, module: &str, architecture: &architecture::Architecture, layers: Option<&LayerSettings>) -> JsonValueAllowance {
    if BUILTIN_WIRE_MODULES.iter().any(|parser| module == *parser || module.ends_with(&format!(".{}", parser))) {
        return JsonValueAllowance::Allowed;
    }
    let Some(pattern) = architecture.wire_modules.iter().find(|pattern| module_pattern_matches(pattern, module)) else {
        return JsonValueAllowance::Refused(explain::JsonValueRefusal::NotListed);
    };
    let in_foundation = match (&architecture.foundation, layers) {
        (Some(foundation), Some(layers)) => classify_layer_file(rel, layers).is_some_and(|(site, _)| layers.layers[site.layer.0].name == *foundation),
        _ => false,
    };
    match in_foundation {
        true => JsonValueAllowance::Allowed,
        false => JsonValueAllowance::Refused(explain::JsonValueRefusal::ListedOutsideFoundation {
            pattern: pattern.clone(),
            foundation_dir: architecture.foundation.as_ref().map(|f| format!("{}/{}", layers::normalize_dir(&architecture.root), f)),
        }),
    }
}

/// Matching a module-spelling pattern (`.`-separated — `*` is any spelling within a segment and `**` is zero or more segments. The same matching as the glob of test_paths).
fn module_pattern_matches(pattern: &str, module: &str) -> bool {
    let pattern: Vec<&str> = pattern.split('.').collect();
    let module: Vec<&str> = module.split('.').collect();
    segments_match(&pattern, &module)
}

/// Whether a file is in the DOEFF120 population — a Hy or Python file not under a hidden dir or `JSON_VALUE_SKIPPED_DIRS`.
fn is_json_value_file(rel: &str) -> bool {
    let parts: Vec<&str> = rel.split('/').collect();
    let dirs = &parts[..parts.len().saturating_sub(1)];
    language_of(Path::new(rel)).is_some() && !dirs.iter().any(|dir| dir.starts_with('.') || JSON_VALUE_SKIPPED_DIRS.contains(dir))
}

/// The DOEFF120 population (all the repo's Hy and Python files — in path order).
fn collect_json_value_files(root: &Path) -> Vec<SourceFile> {
    let walker = WalkDir::new(root).follow_links(false).into_iter().filter_entry(|entry| {
        let name = entry.file_name().to_string_lossy();
        entry.depth() == 0 || !entry.file_type().is_dir() || !(name.starts_with('.') || JSON_VALUE_SKIPPED_DIRS.contains(&name.as_ref()))
    });
    let mut files: Vec<SourceFile> = walker
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .filter_map(|entry| {
            let path = entry.into_path();
            let rel = relative_path(root, &path)?;
            let language = language_of(&path)?;
            is_json_value_file(&rel).then_some(SourceFile { rel, path, language })
        })
        .collect();
    files.sort_by(|a, b| a.rel.cmp(&b.rel));
    files
}

/// DOEFF120: count the JsonValue uses of one module and, if the module is not allowed, emit one finding (the position is the first use; the count and other lines go in the explanation).
fn judge_json_value(file: &SourceFile, source: &str, architecture: &architecture::Architecture, layers: Option<&LayerSettings>) -> Option<Draft> {
    if !JSON_VALUE_NAMES.iter().any(|name| source.contains(name)) {
        return None;
    }
    let module = module_of(&file.rel);
    let refusal = match json_value_allowance(&file.rel, &module, architecture, layers) {
        JsonValueAllowance::Allowed => return None,
        JsonValueAllowance::Refused(refusal) => refusal,
    };
    let found = facts::name_occurrences(file.language, source, JSON_VALUE_NAMES);
    let first = *found.first()?;
    let lines = LineIndex::new(source);
    let line_of = |span: &ByteSpan| lines.range(span.start, span.end).start.line as usize + 1;
    let first_line = line_of(&first);
    let mut names: Vec<String> = Vec::new();
    let mut other_lines: Vec<usize> = Vec::new();
    for span in &found {
        let name = source.get(span.start..span.end).unwrap_or("").to_string();
        if !names.contains(&name) {
            names.push(name);
        }
        let line = line_of(span);
        if line != first_line && !other_lines.contains(&line) {
            other_lines.push(line);
        }
    }
    let uses = explain::JsonValueUses { names: names.clone(), count: found.len(), first_line, other_lines };
    Some(Draft {
        rule: ProjectRule::JsonValueOutsideWire,
        layer: None,
        rel: file.rel.clone(),
        path: file.path.clone(),
        range: lines.range(first.start, first.end),
        message: format!(
            "{} uses {} in {} places — JsonValue may be touched only by the generic resolvers and the foundation send/receive modules listed in :wire-modules",
            file.rel,
            names.join("・"),
            uses.count
        ),
        detail: None,
        base: Severity::Error,
        explain: Explain::JsonValueUse { module, uses, refusal },
    })
}

// --- Walk of the sleep rules (DOEFF210, 211) ----------------------------------------------------

/// Whole run of the sleep rules: reads and judges, one at a time, the Hy files in the walked range (under the root, and the settings' include) that match the population —
/// if there are named paths, judges only the files under them (the same predicate as the definition rules).
struct SleepWalk<'a> {
    root: &'a Path,
    include: &'a [PathBuf],
    focus: Option<&'a [PathBuf]>,
    /// Name of the time measurement.
    label: &'static str,
}

impl SleepWalk<'_> {
    /// Drafts per file of the population (the reason for an unreadable file).
    fn judge(&self, in_population: impl Fn(&str) -> bool, judge: impl Fn(&SourceFile, &str) -> Vec<Draft> + Sync) -> Vec<Result<Vec<Draft>, String>> {
        let files: Vec<SourceFile> = hy_files::walked(self.root, self.include)
            .into_iter()
            .filter(|(rel, _)| in_population(rel))
            .map(|(rel, path)| SourceFile { rel, path, language: Language::Hy })
            .filter(|file| self.focus.is_none_or(|only| only.iter().any(|p| file.path.starts_with(p))))
            .collect();
        crate::timing::timed(self.label, || {
            files
                .par_iter()
                .map(|file| {
                    std::fs::read_to_string(&file.path).map(|source| judge(file, &source)).map_err(|error| format!("{}: could not read: {}", file.rel, error))
                })
                .collect()
        })
    }
}

// --- Sleep in programs (DOEFF210) ----------------------------------------------------------

/// Whether a file is in a program location (under one of the locations — all if the locations are empty — and a Hy file that matches neither an excluded location nor a separator).
fn is_program_sleep_file(rel: &str, places: &ProgramSleepSettings) -> bool {
    language_of(Path::new(rel)) == Some(Language::Hy)
        && (places.paths.is_empty() || places.paths.iter().any(|place| matches_place(rel, place)))
        && !places.exclude.iter().any(|place| matches_place(rel, place))
        && !rel.split('/').any(|part| places.exclude_parts.contains(part))
}

/// DOEFF210: turn wall-clock sleeps inside programs (defk, defp) into drafts at the call form's position (key detail = program name::sleep function).
fn judge_program_sleep(file: &SourceFile, source: &str) -> Vec<Draft> {
    let lines = LineIndex::new(source);
    program_sleep::judge(source)
        .into_iter()
        .map(|hit| Draft {
            rule: ProjectRule::ProgramSleepsOnWallClock,
            layer: None,
            path: file.path.clone(),
            rel: file.rel.clone(),
            range: lines.range(hit.start, hit.end),
            message: format!("{} — program {} waits on the wall clock with {} (wait using doeff-time effects for time)", file.rel, hit.definition, hit.callee),
            detail: Some(hit.detail()),
            base: Severity::Error,
            explain: Explain::ProgramSleepsOnWallClock {
                subject: format!("call of {} in program {}", hit.callee, hit.definition),
                reason: "If a program calls time.sleep or asyncio.sleep, it waits on the wall clock whatever time handler is installed — its test cannot run on a virtual clock (sim-time-handler), is slowed by the real time, and on a busy machine goes red by timeout. A program emits doeff-time effects (such as Delay), and what one second means is decided by the time handler the caller installs. Waiting on the wall clock is the responsibility of the handler implementation (doeff's ADR-DOE-TIME-001 R1, R2).".to_string(),
            },
        })
        .collect()
}

// --- Sleep in tests (DOEFF211) ---------------------------------------------------------------

/// Whether a file is in a test location (a Hy file whose path has a test-location dir name as a segment, or whose file name matches the test-name glob, and that does not match an excluded location (under a dir, or
/// the named file itself)).
fn is_test_sleep_file(rel: &str, places: &TestSleepSettings) -> bool {
    let (dirs, name) = match rel.rsplit_once('/') {
        Some((dirs, name)) => (dirs, name),
        None => ("", rel),
    };
    language_of(Path::new(rel)) == Some(Language::Hy)
        && (dirs.split('/').any(|part| places.parts.contains(part)) || places.names.iter().any(|pattern| glob_matches(pattern, name)))
        && !places.exclude.iter().any(|place| matches_place(rel, place))
}

/// DOEFF211: turn wall-clock sleeps inside tests that do not claim realtime into drafts at the call form's position (key detail = enclosing definition name::sleep function).
fn judge_test_sleep(file: &SourceFile, source: &str) -> Vec<Draft> {
    let lines = LineIndex::new(source);
    test_sleep::judge(source)
        .into_iter()
        .map(|hit| Draft {
            rule: ProjectRule::TestSleepsOnWallClock,
            layer: None,
            path: file.path.clone(),
            rel: file.rel.clone(),
            range: lines.range(hit.start, hit.end),
            message: format!("{} — test {} waits on the wall clock with {} (if real time is the subject, claim realtime; otherwise wait on an event or a virtual clock)", file.rel, hit.definition, hit.callee),
            detail: Some(hit.detail()),
            base: Severity::Error,
            explain: Explain::TestSleepsOnWallClock {
                subject: format!("call of {} in test {}", hit.callee, hit.definition),
                reason: "If a test waits with time.sleep or asyncio.sleep, what it waits for (an event) is not named, it is slowed by the real time, and on a busy machine goes red by timeout. A test either has the program emit doeff-time effects (such as Delay) and runs it on a virtual clock (sim-time-handler), or names the event to wait for. Only tests whose subject is real time itself (a real thread, a real asyncio loop, a real external process) claim realtime, and they are listed in the roster together with a reason (doeff's ADR-DOE-TIME-001 R3).".to_string(),
            },
        })
        .collect()
}

// --- Environment words (DOEFF108) ---------------------------------------------------------------

/// Whether a file is a business file (under one of the locations, not matching an excluded location or separator, and with a matching extension).
fn is_environment_file(rel: &str, env: &EnvironmentSettings) -> bool {
    let extension_ok = Path::new(rel).extension().and_then(|e| e.to_str()).is_some_and(|e| env.extensions.contains(e));
    extension_ok
        && env.paths.iter().any(|place| matches_place(rel, place))
        && !env.exclude.iter().any(|place| matches_place(rel, place))
        && !rel.split('/').any(|part| env.exclude_parts.contains(part))
}

/// Collects all business files (in path order).
fn collect_environment_files(root: &Path, env: &EnvironmentSettings) -> Vec<SourceFile> {
    let mut found = BTreeMap::new();
    for place in &env.paths {
        let base = match place.strip_suffix('*') {
            Some(prefix) => prefix.rsplit_once('/').map(|(dir, _)| dir.to_string()).unwrap_or_default(),
            None => place.clone(),
        };
        for path in walk_files(&root.join(&base)) {
            let Some(rel) = relative_path(root, &path) else { continue };
            if !is_environment_file(&rel, env) {
                continue;
            }
            if let Some(language) = language_of(&path) {
                found.entry(rel.clone()).or_insert(SourceFile { rel, path, language });
            }
        }
    }
    found.into_values().collect()
}

/// Whether a definition is a target whose name is checked — a handler (defhandler, and a do function receiving `[effect k]`) and a top-level definition of an assembly file.
fn names_business_handler(definition: &Definition, assembly_file: bool) -> bool {
    if definition.container.is_some() {
        return false;
    }
    let dispatch = matches!(definition.kind, DefinitionKind::Defn | DefinitionKind::Defk)
        && definition.params.len() == 2
        && definition.params.first().is_some_and(|p| p == "effect");
    assembly_file || definition.kind == DefinitionKind::Defhandler || dispatch
}

/// DOEFF108: check that the names of business files and of handlers and assembly functions have no environment words (judged by name alone — no reachability analysis).
fn judge_environment_names(file: &SourceFile, source: &str, env: &EnvironmentSettings, hy_file: Option<&HyFileIndex>) -> Vec<Draft> {
    let mut drafts = Vec::new();
    let file_name = file.rel.rsplit('/').next().unwrap_or(&file.rel);
    let stem = file_name.rsplit_once('.').map(|(stem, _)| stem).unwrap_or(file_name);
    let draft = |range: Range, message: String, detail: Option<String>, explain: Explain| Draft {
        explain,
        rule: ProjectRule::EnvironmentName,
        layer: None,
        rel: file.rel.clone(),
        path: file.path.clone(),
        range,
        message,
        detail,
        base: Severity::Error,
    };
    let words = environment_words_of(stem, &env.words);
    if !words.is_empty() {
        drafts.push(draft(
            first_line_range(source),
            format!("{}: the file name contains an environment word ({}) — business files and handlers do not know the environment. Only the generic handlers of the foundation are swapped per environment", file.rel, words.join("・")),
            None,
            Explain::EnvironmentName { subject: NameSubject::File { stem: stem.to_string() }, words: words.clone() },
        ));
    }
    let assembly_file = env.assembly_files.contains(file_name);
    let mut seen = BTreeSet::new();
    for definition in hy_file.map(|f| f.definitions.as_slice()).unwrap_or(&[]) {
        if !names_business_handler(definition, assembly_file) {
            continue;
        }
        let name = hy_mangle(&definition.name);
        if is_upper_name(&name.replace('_', "")) || !seen.insert(name.clone()) {
            continue;
        }
        let words = environment_words_of(&name, &env.words);
        if words.is_empty() {
            continue;
        }
        drafts.push(draft(
            definition.range,
            format!("{}: {} contains an environment word ({}) — business files and handlers do not know the environment. Only the generic handlers of the foundation are swapped per environment", file.rel, name, words.join("・")),
            Some(name.clone()),
            Explain::EnvironmentName { subject: NameSubject::Definition { name, kind: definition.kind.as_str() }, words },
        ));
    }
    drafts
}

// --- Finishing -------------------------------------------------------------------------------

/// The violation key `<path from the repo root>::<law name or rule ID>[::<detail>]` (the spelling that the registry and the false-positive list match against — built in this one place).
fn finding_key(law: Option<&LawSpec>, rule: ProjectRule, rel: &str, detail: Option<&str>) -> String {
    let segment = law.map(|l| l.name.as_str()).unwrap_or_else(|| rule.id());
    match detail {
        Some(detail) => format!("{}::{}::{}", rel, segment, detail),
        None => format!("{}::{}", rel, segment),
    }
}

/// Among the rules named by the key's separator (the second of `<path>::<law name or rule ID>[::<detail>]`), those judged in this run (enabled, not a semantic rule, and
/// not being reconciled). Keys whose rule cannot be looked up from the separator (keys of other checks sharing the registry dir) and keys of rules that were not judged are empty.
fn judged_rules_of(key: &str, settings: &ProjectSettings, enabled: &BTreeSet<ProjectRule>) -> Vec<ProjectRule> {
    named_rules_of(key, settings).into_iter().filter(|rule| enabled.contains(rule)).collect()
}

/// The rules named by any registry key (those DOEFF166 can judge) — the list that a whole run with 166 enabled applies in the same run even if absent from enable.
fn rules_named_by_registry(registry: &Registry, settings: &ProjectSettings) -> BTreeSet<ProjectRule> {
    registry.keys.iter().flat_map(|key| named_rules_of(key, settings)).collect()
}

/// Among the rules named by a key's separator, those DOEFF166 can judge (not semantic rules, not being reconciled — semantic rules ask Jev, so they are not
/// applied automatically). Keys whose rule cannot be looked up from the separator are empty.
fn named_rules_of(key: &str, settings: &ProjectSettings) -> Vec<ProjectRule> {
    let Some(segment) = key.split("::").nth(1) else { return Vec::new() };
    let named: Vec<ProjectRule> = match ProjectRule::parse(segment) {
        Some(rule) => vec![rule],
        None => settings
            .laws
            .iter()
            .filter(|law| law.name == segment)
            .flat_map(|law| law.rules.iter())
            .filter_map(|rule| match rule {
                law::ProjectRuleOrExternal::Project(rule) => Some(*rule),
                law::ProjectRuleOrExternal::External(_) => None,
            })
            .collect(),
    };
    named.into_iter().filter(|rule| !rule.is_semantic() && !settings.registry.reconciling.contains(rule)).collect()
}

/// DOEFF166: among registry keys, those for rules judged in this run that match none of the drafts' keys become drafts (in key order; key detail = the registry key).
fn stale_registry_drafts(root: &Path, settings: &ProjectSettings, enabled: &BTreeSet<ProjectRule>, registry: &Registry, drafts: &[Draft]) -> Vec<Draft> {
    let found: BTreeSet<String> = drafts
        .iter()
        .map(|draft| finding_key(settings.law_for(draft.rule, draft.layer), draft.rule, &draft.rel, draft.detail.as_deref()))
        .collect();
    registry
        .keys
        .iter()
        .filter(|key| !found.contains(*key))
        .filter_map(|key| {
            let rules = judged_rules_of(key, settings, enabled);
            if rules.is_empty() {
                return None;
            }
            let source = registry.origins.get(key).cloned().unwrap_or_else(|| "registry".to_string());
            let ids: Vec<String> = rules.iter().map(|rule| rule.id().to_string()).collect();
            Some(Draft {
                rule: ProjectRule::RegistryEntryStale,
                layer: None,
                path: root.join(&source),
                rel: source.clone(),
                range: zero_range(),
                message: format!("registry {} key {} no longer matches ({} was applied to the whole repo) — delete the line", source, key, ids.join("・")),
                detail: Some(key.clone()),
                base: Severity::Error,
                explain: Explain::RegistryEntryStale { key: key.clone(), source, rules: ids },
            })
        })
        .collect()
}

/// The list of human verdicts (false positives and true positives of semantic rules — key -> reason).
#[derive(Debug, Default)]
struct JudgedLabels {
    false_positives: BTreeMap<String, String>,
    true_positives: BTreeMap<String, String>,
}

/// Reads the settings' list of false positives and list of true positives of semantic rules. A key listed in both is accumulated as a conflict reason and read as neither.
fn judged_labels(root: &Path, settings: &ProjectSettings, errors: &mut Vec<String>) -> JudgedLabels {
    let Some(semantic) = &settings.semantic else { return JudgedLabels::default() };
    let negatives = registry::JudgedKeys::load(root, &semantic.false_positives, registry::Absent::Unreadable);
    let positives = registry::JudgedKeys::load(root, &semantic.true_positives, registry::Absent::Unreadable);
    errors.extend(negatives.problems);
    errors.extend(positives.problems);
    let mut labels = JudgedLabels { false_positives: negatives.reasons, true_positives: positives.reasons };
    let both: Vec<String> = labels.false_positives.keys().filter(|k| labels.true_positives.contains_key(*k)).cloned().collect();
    for key in both {
        errors.push(format!("conflict in verdict lists: {} is in both the false-positive list and the true-positive list — read as neither", key));
        labels.false_positives.remove(&key);
        labels.true_positives.remove(&key);
    }
    labels
}

/// Matches human verdicts against Jev's answers (in key order). A definition that is listed but has no answer (unjudged, or no longer present) is counted only in listed.
fn labeled_summary(settings: &ProjectSettings, labels: &JudgedLabels, probes: &[SemanticProbe]) -> semantic::LabeledSummary {
    let mut summary = semantic::LabeledSummary::default();
    summary.positives.listed = labels.true_positives.len();
    summary.negatives.listed = labels.false_positives.len();
    for probe in probes {
        let key = finding_key(settings.law_for(probe.rule, probe.layer), probe.rule, &probe.rel, Some(&probe.detail));
        let expect = match (labels.true_positives.contains_key(&key), labels.false_positives.contains_key(&key)) {
            (true, _) => true,
            (false, true) => false,
            (false, false) => continue,
        };
        let count = if expect { &mut summary.positives } else { &mut summary.negatives };
        count.judged += 1;
        count.flagged += usize::from(probe.flagged);
        summary.items.push(semantic::LabeledAnswer { key, rule: probe.rule.id().to_string(), expect, probability: probe.probability, flagged: probe.flagged });
    }
    summary.items.sort_by(|a, b| a.key.cmp(&b.key));
    summary
}

/// Turns drafts into violations by applying law, key, registry and reconciling (in path and position order). Those hit by a semantic rule whose key is in the false-positive list are not emitted
/// (the returned count = the number removed).
fn finish(drafts: Vec<Draft>, settings: &ProjectSettings, registry: &Registry, false_positives: &BTreeMap<String, String>) -> (Vec<Finding>, usize) {
    let narrator = Narrator { layers: settings.layers.as_ref(), raw: settings.raw.as_ref() };
    let mut dropped = 0;
    let mut findings: Vec<Finding> = drafts
        .into_iter()
        .filter_map(|draft| {
            let law = settings.law_for(draft.rule, draft.layer);
            let key = finding_key(law, draft.rule, &draft.rel, draft.detail.as_deref());
            if draft.rule.is_semantic() && false_positives.contains_key(&key) {
                dropped += 1;
                return None;
            }
            // DOEFF206 is not lowered by the registry (even if listed, the hit stays untouched).
            let registered = registry.keys.contains(&key) && draft.rule.lowered_by_registry();
            let base = settings.severity.get(&draft.rule).copied().unwrap_or(draft.base);
            let reconciling = settings.registry.reconciling.contains(&draft.rule);
            let severity = if reconciling {
                Severity::Info
            } else {
                match (registered, base) {
                    (true, Severity::Error) => settings.registered_severity.get(&draft.rule).copied().unwrap_or(Severity::Warning),
                    // A warning listed in the registry (such as an old form during migration) is lowered to info.
                    (true, Severity::Warning) => Severity::Info,
                    (_, base) => base,
                }
            };
            let probability = match &draft.explain {
                Explain::Semantic { probability, .. } => Some(*probability),
                Explain::PlainCallableDoubt { chosen_probability, .. } => Some(*chosen_probability),
                Explain::ClassRoleDoubt { probability, .. } => Some(*probability),
                Explain::MixedConcerns { probability, .. } => Some(*probability),
                _ => None,
            };
            Some(Finding {
                origin: if probability.is_some() { FindingOrigin::Jev } else { FindingOrigin::Linter },
                probability,
                rule: draft.rule,
                law: law.map(|l| l.name.clone()),
                adr: law.and_then(|l| l.adr.clone()),
                severity,
                path: draft.path,
                rel: draft.rel,
                range: draft.range,
                message: draft.message,
                hint: narrator.hint(&draft.explain).unwrap_or_else(|| draft.rule.hint().to_string()),
                key,
                base_severity: base,
                standing: Standing::of(registered, reconciling),
                registered,
                explanation: narrator.explain(&draft.explain, law),
            })
        })
        .collect();
    findings.sort_by(|a, b| (&a.rel, a.range.start, a.rule).cmp(&(&b.rel, b.range.start, b.rule)));
    (findings, dropped)
}

/// The set of rules to judge (from the list of IDs with `enable` and `disable` expanded. None means all).
pub fn enabled_rules(enabled_ids: Option<&[String]>) -> BTreeSet<ProjectRule> {
    match enabled_ids {
        None => ProjectRule::ALL.iter().copied().collect(),
        Some(ids) => ids.iter().filter_map(|id| ProjectRule::parse(id)).collect(),
    }
}

#[cfg(test)]
mod contract_test_breach_tests {
    use super::architecture::{ContractTest, ContractTestWaiver, TestNodeId};
    use super::*;

    fn node(text: &str) -> TestNodeId {
        TestNodeId::parse(text).unwrap()
    }

    #[test]
    fn waived_none_with_doeff_contract_test_passes_without_an_edge_test() {
        // DOEFF137: a none with a reason (the name of a contract test on the doeff side) does not fire even if there is no edge test.
        let waived = ContractTest::Waived(ContractTestWaiver::DoeffContractTest(node("packages/doeff-core-effects/tests/test_http.hy::test-contract")));
        assert_eq!(contract_test_breach(&waived, || false), None);
        let covered_here = ContractTest::Waived(ContractTestWaiver::CoveredByRepoTest(node("tests/test_main.hy::test-main")));
        assert_eq!(contract_test_breach(&covered_here, || false), None);
    }

    #[test]
    fn none_without_reason_rings_even_when_an_edge_test_exists() {
        // A none without a reason fires — even if an edge test reaches it, it fires as a declaration error (it is not passed silently, #1796).
        assert_eq!(contract_test_breach(&ContractTest::NoneWithoutReason, || false), Some(ContractTestBreach::NoneWithoutReason));
        assert_eq!(contract_test_breach(&ContractTest::NoneWithoutReason, || true), Some(ContractTestBreach::NoneWithoutReason));
    }

    #[test]
    fn required_rings_only_without_an_edge_test() {
        assert_eq!(contract_test_breach(&ContractTest::Required, || false), Some(ContractTestBreach::NoEdgeTest));
        assert_eq!(contract_test_breach(&ContractTest::Required, || true), None);
    }
}

#[cfg(test)]
mod reason_comment_tests {
    use super::*;

    #[test]
    fn reads_kind_and_detail_in_half_and_full_width() {
        let marker = "defk にできない:";
        assert_eq!(
            parse_reason_comment("(deff k [x] x)  ; defk にできない(library-callback): sorted の key", marker),
            Some(ReasonComment { kind: Some("library-callback".into()), detail: "sorted の key".into() })
        );
        assert_eq!(
            parse_reason_comment("; defk にできない\u{ff08}process-entry\u{ff09}\u{ff1a}argparse の入口", marker),
            Some(ReasonComment { kind: Some("process-entry".into()), detail: "argparse の入口".into() })
        );
        assert_eq!(parse_reason_comment("; defk にできない: 同上", marker), Some(ReasonComment { kind: None, detail: "同上".into() }));
        assert_eq!(parse_reason_comment("(deff k [x] x)", marker), None);
    }

    #[test]
    fn reads_the_english_default_and_the_earlier_default_under_either_setting() {
        let english = Some(ReasonComment { kind: Some("library-callback".into()), detail: "key for sorted".into() });
        for marker in [DEFAULT_REASON_MARKER, LEGACY_REASON_MARKER] {
            assert_eq!(parse_reason_comment("(deff k [x] x)  ; cannot be a defk(library-callback): key for sorted", marker), english);
            assert_eq!(
                parse_reason_comment("; defk にできない: argparse entry", marker),
                Some(ReasonComment { kind: None, detail: "argparse entry".into() })
            );
            assert_eq!(parse_reason_comment("; Cannot be a defk: capitalised is not the marker", marker), None);
        }
        assert_eq!(
            parse_reason_comment("; my marker: custom", "my marker:"),
            Some(ReasonComment { kind: None, detail: "custom".into() })
        );
    }
}

#[cfg(test)]
mod judged_rules_tests {
    use super::*;

    #[test]
    fn a_single_file_run_does_not_judge_rules_judged_only_on_the_whole_repo() {
        // A single-file run does not judge rules judged only across the whole repo, such as DOEFF166 (registry lines that match nothing). A whole run
        // judges it, and a whole run with named paths judges everything except 166 (its hits may lie outside the named paths).
        let single = Target::Single { path: PathBuf::from("/repo/app/core/x.hy"), source: "" };
        let whole = Target::Whole { focus: None };
        let focus = [PathBuf::from("/repo/app/core/x.hy")];
        let named = Target::Whole { focus: Some(&focus) };
        assert!(!single.judges(ProjectRule::RegistryEntryStale));
        assert!(whole.judges(ProjectRule::RegistryEntryStale));
        assert!(!named.judges(ProjectRule::RegistryEntryStale));
        // Rules that walk the whole repo (DOEFF141, 162) are not judged in a single file. Rules that have a single-file branch (DOEFF101, 106, 110) are judged.
        for rule in [ProjectRule::BlindDefinitionReads, ProjectRule::EffectOutsideCensus, ProjectRule::VocabularyOutsideSinglePoint] {
            assert!(!single.judges(rule), "{}", rule.id());
            assert!(whole.judges(rule) && named.judges(rule), "{}", rule.id());
        }
        for rule in [ProjectRule::LayerImportDirection, ProjectRule::RawSideEffectDirect, ProjectRule::DefnForbidden] {
            assert!(single.judges(rule) && whole.judges(rule) && named.judges(rule), "{}", rule.id());
        }
        // A whole run judges all the enabled rules (a run with named paths excludes only 166).
        assert!(ProjectRule::ALL.iter().all(|rule| whole.judges(*rule)));
        let not_named: Vec<&str> = ProjectRule::ALL.iter().filter(|rule| !named.judges(**rule)).map(|rule| rule.id()).collect();
        assert_eq!(not_named, vec!["DOEFF166"]);
    }

    #[test]
    fn the_report_names_only_judged_rules_and_runs_nothing_else_on_a_single_file() {
        // A single-file run's report does not put in judged the rules that are enabled but not judged on a single file, and does include DOEFF128, which is emitted regardless of the enabled rules.
        let root = tempfile::TempDir::new().unwrap();
        let settings = ProjectSettings::default();
        let enabled: BTreeSet<ProjectRule> = [ProjectRule::RegistryEntryStale, ProjectRule::DefnForbidden].into_iter().collect();
        let path = root.path().join("x.hy");
        let single = run(root.path(), &settings, &enabled, Target::Single { path, source: "(defn f [] 1)\n" });
        assert_eq!(single.judged, [ProjectRule::UnreadableFile, ProjectRule::DefnForbidden].into_iter().collect::<BTreeSet<_>>());
        assert!(single.findings.iter().all(|f| single.judged.contains(&f.rule)), "{:?}", single.findings);
        let whole = run(root.path(), &settings, &enabled, Target::Whole { focus: None });
        assert_eq!(
            whole.judged,
            [ProjectRule::UnreadableFile, ProjectRule::DefnForbidden, ProjectRule::RegistryEntryStale].into_iter().collect::<BTreeSet<_>>()
        );
    }
}
