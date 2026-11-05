//! doeff-linter CLI

use clap::Parser;
use colored::*;
use doeff_linter::{
    check_slot, collect_python_files_with_options, config,
    editor::{self, EditorInput},
    lint_files_parallel, lint_source_at, one_path_per_file,
    logging::{LintLogEntry, LintLogger},
    models::{LintResult, Severity, Violation},
    position::offset_of,
    project::{self, rule::ProjectRule, settings::ProjectSettings, ProjectReport, Target},
    rule_info::get_rule_info,
    rules, should_exclude,
};
use std::collections::{BTreeMap, BTreeSet};
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

/// Binary allocator -- to speed up the small allocations and frees of reading, walking, and tearing down the repo-wide Hy index cache (see the note in Cargo.toml).
/// It does not change the answers.
#[global_allocator]
static GLOBAL: mimalloc::MiMalloc = mimalloc::MiMalloc;

#[derive(Parser, Debug)]
#[command(name = "doeff-linter")]
#[command(version = doeff_linter::VERSION_TEXT, about = "A linter for enforcing code quality and immutability patterns")]
#[command(after_help = r#"SUPPRESSING RULES:
  Use noqa comments to suppress rules on specific lines or entire files.

  Line-level suppression:
    def dict():  # noqa: DOEFF001        Suppress specific rule
    def list():  # noqa                  Suppress all rules on this line
    x = 1  # noqa: DOEFF001, DOEFF002    Suppress multiple rules
    y = 2  # noqa: DOEFF001 - reason     Suppress with explanation

  File-level suppression (must appear before any code):
    # noqa: file                         Suppress all rules for entire file
    # noqa: file=DOEFF001                Suppress specific rule for entire file
    # noqa: file=DOEFF001,DOEFF002       Suppress multiple rules for entire file

  Notes:
    - Rule IDs are case-insensitive (doeff001 = DOEFF001)
    - File-level noqa must appear before any code (comments/docstrings allowed before it)

EXIT CODES (the default of heavy modes; the whole repo for editor-json):
  0 = no errors / 1 = errors / 2 = argument or config error
  75 = not run -- the wait for the machine's check slot exceeded the limit and it finished without linting (can be re-run; neither red nor green -- check_slot.rs)

EXAMPLES:
  doeff-linter .                         Lint current directory
  doeff-linter --enable DOEFF001         Enable only specific rule
  doeff-linter --disable DOEFF001        Disable specific rule
  doeff-linter --modified                Lint only git-modified files
  doeff-linter --output-format json      Output in JSON format
  doeff-linter --output-format editor-json            JSON for editors (whole repo)
  doeff-linter --output-format editor-json --stdin --path <file>   one file's pre-save content
  doeff-linter --config <file> --root <dir> ...       specify the config file and the repo root
"#)]
struct Args {
    /// Files or directories to lint
    #[arg(default_value = ".")]
    paths: Vec<String>,

    /// Enable specific rules (comma-separated, or "ALL")
    #[arg(long, value_delimiter = ',')]
    enable: Vec<String>,

    /// Disable specific rules (comma-separated)
    #[arg(long, value_delimiter = ',')]
    disable: Vec<String>,

    /// Exclude paths matching patterns
    #[arg(long, value_delimiter = ',')]
    exclude: Vec<String>,

    /// Output format: text, json, editor-json
    #[arg(long, default_value = "text")]
    output_format: String,

    /// Ignore pyproject.toml configuration
    #[arg(long)]
    no_config: bool,

    /// Config file (the form of a pyproject.toml that has `[tool.doeff-linter]`, or a TOML holding only the section's contents). If absent, searches upward
    #[arg(long)]
    config: Option<PathBuf>,

    /// The repo root (the base for layer locations, registries, and key paths). Defaults to the dir of the pyproject.toml that was found; with --config, the current dir
    #[arg(long)]
    root: Option<PathBuf>,

    /// Read pre-save content from stdin (requires --path; only with editor-json)
    #[arg(long)]
    stdin: bool,

    /// Which file to judge the --stdin content as
    #[arg(long)]
    path: Option<PathBuf>,

    /// The editor-json output file run at the baseline (the tip of main). Reports in new_critical the criticals not in the baseline, and exits with code 4 if any
    /// (only with editor-json; spec section 1 "comparison with the baseline")
    #[arg(long)]
    baseline_report: Option<PathBuf>,

    /// Show verbose output
    #[arg(short, long)]
    verbose: bool,

    /// Run as Cursor stop hook (reads JSON from stdin, outputs hook response)
    #[arg(long)]
    hook: bool,

    /// Runs as a git commit hook -- applies the rules decided by one file to the staged sources and
    /// compares with the HEAD version; exits with code 1 if there are new breaches (whole-repo rules are not applied)
    #[arg(long)]
    commit_hook: bool,

    /// Upper limit per child linter run of --commit-hook (seconds). Wins over the config's commit_hook.timeout_s (default 20). 0 means no limit -- used to
    /// re-verify to the end, with the same comparison, a commit that stopped because it was cut off by the limit
    #[arg(long)]
    commit_hook_timeout_s: Option<u64>,

    /// The file to write the --commit-hook report to -- writes the two measurements (HEAD version and staged paths) (the list of measured paths, violations in editor-json form, and the rules judged)
    /// as one JSON (spec section 3 "commit hook report"). The blocking decision, exit code, and stderr text do not change
    #[arg(long)]
    report_out: Option<PathBuf>,

    /// Prints the list of rules as JSON and exits -- each rule's ID, and whether judging its hits needs the whole repo (whole_repo). The gate and the hook choose the rules to apply to the whole-repo
    /// comparison by this declaration (no hand-maintained list)
    #[arg(long)]
    list_rules: bool,

    /// Prints as JSON, and exits, the split of rules to apply to the changed paths (path arguments; from the repo root) -- {"quick", "whole", "declaration_changed"}.
    /// The single judgment shared with the commit hook (a change to a declaration file also moves rules decided by one file into whole),
    /// the interface the merge gate reads
    #[arg(long)]
    split_rules: bool,

    /// Prints as JSON with distances, and exits, the test files that directly or indirectly import (for Hy, also require) the changed paths (from the repo root; multiple)
    /// (the entry before registration adds them after the contract tests and the changed tests). Requires --test-pattern
    #[arg(long, num_args = 1.., value_name = "PATH")]
    affected_tests: Option<Vec<String>>,

    /// Name rules that --affected-tests treats as test files (fnmatch; a rule without `/` is matched against the file name; may be repeated). Pass the caller's collector definition
    #[arg(long, value_name = "GLOB")]
    test_pattern: Vec<String>,

    /// With --affected-tests, a module whose number of direct user files exceeds this is not passed through and is named in hubs (default 40)
    #[arg(long)]
    max_dependents: Option<usize>,

    /// The commit body file (--commit-hook and --split-rules) -- if the body has a `Lint-Baseline: declarations <reason>` line with a reason, it is read as a one-time directive to measure the baseline
    /// with the current declarations (config file and architecture.hy) (for a commit that starts to fire a rule)
    #[arg(long)]
    commit_message: Option<PathBuf>,

    /// Only lint git-modified files (tracked and untracked)
    #[arg(long)]
    modified: bool,

    /// Apply exclusion rules even to explicitly specified file paths
    /// By default, exclude patterns only apply when scanning directories
    #[arg(long)]
    force_exclude: bool,

    /// Log violations to a file (JSON Lines format) for later analysis
    /// Defaults to ".doeff-lint.jsonl". Use --no-log to disable.
    #[arg(long, default_value = ".doeff-lint.jsonl")]
    log_file: Option<String>,

    /// Disable logging to file
    #[arg(long)]
    no_log: bool,

    /// Ask Jev with the semantic rules (DOEFF201, 202, 203) -- targets are the files in the path arguments, or if none, the files changed in git (definitions with an answer in the cache are asked again).
    /// With no flag, the default is the same as --semantic-changed (uses the cache and asks only changed definitions)
    #[arg(long)]
    semantic: bool,

    /// Does not ask Jev for the semantic rules and only reads the cache (the old default -- a run that does not fire where there is no network. The count of unanswerable ones is not counted)
    #[arg(long)]
    semantic_cache_only: bool,

    /// Asks Jev about all definitions in the configured layers for the semantic rules
    #[arg(long)]
    semantic_all: bool,

    /// Asks Jev, for the semantic rules, only about the definitions among the target files (chosen the same way as --semantic) that have no answer in the local cache (definitions whose contents changed)
    /// (this is also the default with no flag -- the whole run also receives the proxy's memory on top of that)
    #[arg(long)]
    semantic_changed: bool,
}

/// Cursor hook input structure
#[derive(serde::Deserialize, Debug)]
struct HookInput {
    #[allow(dead_code)]
    status: Option<String>,
    #[allow(dead_code)]
    loop_count: Option<u32>,
    workspace_roots: Option<Vec<String>>,
}

/// Cursor hook output structure
#[derive(serde::Serialize)]
struct HookOutput {
    #[serde(skip_serializing_if = "Option::is_none")]
    followup_message: Option<String>,
}

/// Output format (a closed set). An unknown spelling is treated as text as before.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum OutputFormat {
    Text,
    Json,
    EditorJson,
}

impl OutputFormat {
    /// Reads the spelling of `--output-format`.
    fn parse(text: &str) -> OutputFormat {
        match text {
            "json" => OutputFormat::Json,
            "editor-json" => OutputFormat::EditorJson,
            _ => OutputFormat::Text,
        }
    }
}

/// The premises of a run decided by reading the config (config, repo root, enabled rules, exclusion patterns, settings for the layer rules).
struct Setup {
    config: Option<config::Config>,
    root: PathBuf,
    enabled_rules: Option<Vec<String>>,
    exclude_patterns: Vec<String>,
    settings: ProjectSettings,
    /// Unknown config keys and rule IDs (the rest was read without them -- reported by DOEFF100).
    notices: Vec<project::notice::ConfigNotice>,
    /// The service and layer declaration files that were read (the declaration rules judge them even if they are outside the root -- not counted as outside the named range).
    declaration: Option<PathBuf>,
}

impl Setup {
    /// Violations of notices (DOEFF100). Emitted even if not in the `enable` list (the list also exists in configs older than DOEFF100, and staying silent there
    /// would return to silently skipping unknown keys). They stop only when `disable` names DOEFF100 (prepare empties the notices).
    fn notice_findings(&self) -> Vec<project::Finding> {
        project::notice_findings::findings(&self.notices, &self.root)
    }

    /// Enabled layer rules (after applying `enable` and `disable`).
    fn project_rules(&self) -> BTreeSet<ProjectRule> {
        project::enabled_rules(self.enabled_rules.as_deref())
    }

    /// Among the layer rules, those whose config section exists and whose judgment is wired.
    fn project_wired(&self) -> BTreeSet<ProjectRule> {
        self.project_rules()
            .into_iter()
            .filter(|rule| match rule {
                ProjectRule::UnknownConfigKey | ProjectRule::UnreadableFile => true,
                ProjectRule::LayerImportDirection
                | ProjectRule::LayerForbiddenModule
                | ProjectRule::LayerTypesOnly
                | ProjectRule::MatchFieldHyphen
                | ProjectRule::ModuleDeclaresTags
                | ProjectRule::RoleMatchesLayer => self.settings.layers.is_some(),
                // If no layer that passes values by type only is declared, the judgment is not wired (do not let 0 findings read as "protected").
                ProjectRule::DictOutsideItsPlace => self.settings.layers.is_some(),
                ProjectRule::WireInWireFreeLayer | ProjectRule::BareMapInWireFreeLayer => {
                    self.settings.layers.as_ref().is_some_and(|l| l.layers.iter().any(|spec| spec.wire_free))
                }
                ProjectRule::RawSideEffectDirect | ProjectRule::RawSideEffectVia => self.settings.raw.is_some(),
                ProjectRule::EnvironmentName => self.settings.environment.is_some(),
                ProjectRule::ProgramSleepsOnWallClock => self.settings.program_sleep.is_some(),
                ProjectRule::TestSleepsOnWallClock => self.settings.test_sleep.is_some(),
                ProjectRule::ServiceBoundary => self.settings.services.is_some(),
                ProjectRule::ContextMatchesService => self.settings.services.is_some() || self.settings.architecture.is_some(),
                ProjectRule::SemanticBusinessDecision | ProjectRule::SemanticTransportKnowledge => self.settings.semantic.is_some(),
                ProjectRule::SemanticPlainCallable => self.settings.semantic.as_ref().is_some_and(|s| s.plain_callable.is_some()),
                ProjectRule::UndeclaredPlace
                | ProjectRule::UndeclaredDirectory
                | ProjectRule::ServiceDependency
                | ProjectRule::UnusedDependency => self.settings.architecture.is_some(),
                ProjectRule::DefnForbidden | ProjectRule::DeffNeedsReason | ProjectRule::DefinitionTagsRequired => {
                    self.settings.definitions.is_some()
                }
                ProjectRule::TestIsDeftest => self.settings.definitions.as_ref().is_some_and(|d| !d.test_paths.is_empty()),
                ProjectRule::ClassWithBehaviour => self.settings.definitions.is_some(),
                ProjectRule::DefkCalledBare => self.settings.definitions.is_some(),
                ProjectRule::EffectsDisagreeWithInference => self.settings.definitions.is_some(),
                // DOEFF175 requires the declaration of the definition rules' population (paths and test_paths of definitions) -- without it, it is not judged (do not let 0 findings read as "protected").
                ProjectRule::StaleOutcomeBranch => self.settings.definitions.is_some(),
                ProjectRule::TranslationEmitsIntent => self.settings.layers.is_some() && self.settings.translation.is_some(),
                // DOEFF174 applies only to repos that have both the business layers and the :lower-layer-modules declaration (without them the population is empty -- not judged).
                ProjectRule::LowerLayerInDomain => {
                    self.settings.layers.is_some()
                        && self.settings.translation.as_ref().is_some_and(|t| !t.domain_layers.is_empty())
                        && self.settings.architecture.as_ref().and_then(|a| a.business_fakes.as_ref()).is_some_and(|b| !b.lower_layer_modules.is_empty())
                }
                ProjectRule::JsonValueOutsideWire => self.settings.architecture.is_some(),
                ProjectRule::WorldHandlerNamedOutsideList | ProjectRule::WorldHandlerMisplaced => {
                    self.settings.architecture.as_ref().is_some_and(|a| !a.world_handlers.is_empty())
                }
                ProjectRule::TestFormNotDeftest => self.settings.architecture.as_ref().is_some_and(|a| a.test_forms.is_some()),
                ProjectRule::VocabularyOutsideSinglePoint => {
                    self.settings.architecture.as_ref().is_some_and(|a| !a.single_point_vocabulary.is_empty())
                }
                ProjectRule::SpellingOutsideItsFiles => self.settings.architecture.as_ref().is_some_and(|a| !a.confined_spellings.is_empty()),
                ProjectRule::SpellingCountDiffers => self.settings.architecture.as_ref().is_some_and(|a| !a.counted_spellings.is_empty()),
                ProjectRule::EffectOutsideCensus => self.settings.architecture.as_ref().is_some_and(|a| !a.effect_census.is_empty()),
                ProjectRule::FieldHoldersDiffer => self.settings.architecture.as_ref().is_some_and(|a| !a.field_holders.is_empty()),
                ProjectRule::PlacedDependency => self.settings.architecture.as_ref().is_some_and(|a| !a.placed_dependencies.is_empty()),
                ProjectRule::RetiredWord => self.settings.architecture.as_ref().is_some_and(|a| !a.retired_words.is_empty()),
                ProjectRule::RetiredCall => self.settings.architecture.as_ref().is_some_and(|a| !a.retired_calls.is_empty()),
                ProjectRule::EnvironmentBranch => {
                    self.settings.layers.is_some() && self.settings.architecture.as_ref().is_some_and(|a| a.environment_branches.is_some())
                }
                ProjectRule::BlindDefinitionReads => self.settings.architecture.as_ref().is_some_and(|a| !a.blind_definitions.is_empty()),
                ProjectRule::DefinitionCallsUnlistedHead => self.settings.architecture.as_ref().is_some_and(|a| !a.allowed_heads.is_empty()),
                ProjectRule::CallOutsideDeclaredSites => self.settings.architecture.as_ref().is_some_and(|a| !a.call_sites.is_empty()),
                ProjectRule::BroadCatchOutsideCarrier => self.settings.architecture.as_ref().is_some_and(|a| !a.broad_catches.is_empty()),
                ProjectRule::UntypedStructuredValue => self.settings.architecture.as_ref().is_some_and(|a| a.typed_values.is_some()),
                ProjectRule::RecordStubNotKwOnly => self.settings.architecture.as_ref().is_some_and(|a| a.record_stubs.is_some()),
                ProjectRule::ServiceUntestedOnSim => self.settings.architecture.as_ref().is_some_and(|a| a.verification_environment.is_some()),
                ProjectRule::ServiceInvariantsMissing => self.settings.architecture.is_some(),
                ProjectRule::ServiceSystemMissing => self.settings.architecture.is_some(),
                ProjectRule::SystemAccessUnwritten => self.settings.architecture.as_ref().is_some_and(|a| a.outside_writers.is_some()),
                ProjectRule::RecordChangeWakesJob | ProjectRule::PollingOnTimer => {
                    self.settings.architecture.as_ref().is_some_and(|a| a.business_fakes.is_some())
                }
                ProjectRule::HandlerArgumentHoldsState => self.settings.architecture.as_ref().is_some_and(|a| a.handler_arguments.is_some()),
                ProjectRule::BusinessEffectFake | ProjectRule::StaleCounterexampleRow | ProjectRule::TestOnlyFake => {
                    self.settings.architecture.as_ref().is_some_and(|a| a.business_fakes.is_some())
                }
                ProjectRule::ServiceWithoutCounterexample | ProjectRule::ClauseWithoutCounterexample | ProjectRule::IntentFakedInVerification => {
                    self.settings.architecture.as_ref().is_some_and(|a| a.business_fakes.is_some() && a.verification_environment.is_some())
                }
                ProjectRule::AssemblyShapeBroken
                | ProjectRule::AssemblyAnswerMisplaced
                | ProjectRule::IntentAnswererNotTranslation
                | ProjectRule::IntentEffectUncovered => {
                    self.settings.architecture.as_ref().is_some_and(|a| a.business_fakes.is_some() && a.assembly_shape.is_some())
                }
                // DOEFF137 uses the same whole-repo index and definition-edge graph as DOEFF133 (and the conditions for enabling it are the same).
                ProjectRule::TestKindMismatch | ProjectRule::WorldHandlerWithoutContractTest => {
                    self.settings.architecture.as_ref().is_some_and(|a| !a.world_handlers.is_empty() && a.edge_mark.is_some())
                }
                ProjectRule::ShapeCheckInJudgment => {
                    self.settings.definitions.is_some() && self.settings.smells.as_ref().is_some_and(|s| !s.shape_check_layers.is_empty())
                }
                ProjectRule::FailureRethrow | ProjectRule::BindThenReturn | ProjectRule::FieldsJoinedIntoText | ProjectRule::RebuiltAccumulator => {
                    self.settings.definitions.is_some()
                }
                ProjectRule::SemanticMixedConcerns => self.settings.semantic.as_ref().is_some_and(|s| s.mixed_concerns.is_some()),
                ProjectRule::SemanticClassRole => self.settings.semantic.as_ref().is_some_and(|s| s.class_role.is_some()),
                // Unmatched registry lines are judged only in repos that configured a registry (the keys judged are only the keys of rules judged in that run).
                ProjectRule::RegistryEntryStale => !self.settings.registry.dirs.is_empty() || !self.settings.registry.files.is_empty(),
            })
            .collect()
    }

    /// Whether any setting for the layer rules exists (if not, the layer rules are not run).
    fn has_project_rules(&self) -> bool {
        self.settings.layers.is_some() || self.settings.architecture.is_some() || self.settings.environment.is_some() || self.settings.raw.is_some() || self.settings.services.is_some()
            || self.settings.definitions.is_some()
            || self.settings.program_sleep.is_some()
            || self.settings.test_sleep.is_some()
    }
}

/// Decides the handling of the semantic rules from the arguments (--semantic-all = all; --semantic = specified files or files changed in git; --semantic-changed = among those,
/// only definitions with no answer; if none, cache only). Among runs that do not ask, whole runs (including the hook) ask "only when remembered" if a proxy exists
/// (Peek). A single file being edited (--stdin) is cache only (the editor asks anew with --semantic when saving and when typing stops).
fn semantic_mode(args: &Args, root: &Path, stdin_path: Option<&Path>) -> project::semantic::SemanticMode {
    use project::semantic::SemanticMode;
    if args.semantic_all {
        return SemanticMode::AskAll;
    }
    // Default = whole runs use the cache and ask only changed definitions (rules that use Jev are also run everywhere every time,
    // and by default use the cache). A single-file --stdin (the hook right after writing, and the editor) by default reads only the cache -- so as not to lose the layer rules' notices
    // within the hook's overall 3-second limit (decision A of #1190; to revert, make the Some(_) branch below AskChanged).
    // A whole run that only reads the cache is named with --semantic-cache-only.
    if args.semantic_cache_only || (stdin_path.is_some() && !args.semantic && !args.semantic_changed) {
        return match stdin_path {
            Some(_) => SemanticMode::CacheOnly,
            None => SemanticMode::Peek,
        };
    }
    let explicit: Vec<PathBuf> = match stdin_path {
        Some(path) => vec![path.to_path_buf()],
        None if args.paths.iter().any(|p| p != ".") => args.paths.iter().map(PathBuf::from).collect(),
        None => git_changed_files(root).into_iter().map(|rel| root.join(rel)).collect(),
    };
    let mut targets = BTreeSet::new();
    for path in explicit {
        let absolute = editor::normalize_path(&path);
        if absolute.is_dir() {
            for entry in walkdir::WalkDir::new(&absolute).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()) {
                if let Some(rel) = project::relative_path(root, entry.path()) {
                    targets.insert(rel);
                }
            }
        } else if let Some(rel) = project::relative_path(root, &absolute) {
            targets.insert(rel);
        }
    }
    match (args.semantic_changed, args.semantic, stdin_path) {
        (true, _, _) => SemanticMode::AskChanged(targets),
        (false, true, _) => SemanticMode::Ask(targets),
        // Default of a whole run with no flag: ask only changed definitions and also receive the proxy's memory (the default for a single file returned cache only above).
        (false, false, _) => SemanticMode::PeekThenAskChanged(targets),
    }
}

/// Files changed in git (including untracked files; paths from the repo root).
fn git_changed_files(root: &Path) -> Vec<String> {
    let output = Command::new("git").arg("-C").arg(root).args(["status", "--porcelain", "-uall"]).output();
    match output {
        Ok(output) if output.status.success() => String::from_utf8_lossy(&output.stdout)
            .lines()
            .filter(|line| line.len() > 3)
            .map(|line| {
                let file = &line[3..];
                file.split_once(" -> ").map(|(_, new)| new).unwrap_or(file).to_string()
            })
            .collect(),
        _ => Vec::new(),
    }
}

/// Finds and reads the config and decides the repo root and the enabled rules. When the config cannot be read or names mismatch, a reason sentence (exit code 2).
fn prepare(args: &Args) -> Result<Setup, String> {
    let cwd = std::env::current_dir().map_err(|e| format!("cannot read the current dir: {}", e))?;
    let loaded = if args.no_config {
        None
    } else {
        config::load_config_checked(args.config.as_deref(), &cwd)?
    };
    // The config's root (relative to the config file's dir).
    let declared_root = loaded
        .as_ref()
        .and_then(|l| l.config.root.as_ref().map(|r| l.path.parent().map(Path::to_path_buf).unwrap_or_else(|| cwd.clone()).join(r)));
    let root = match (&args.root, declared_root, &loaded, &args.config) {
        (Some(root), _, _, _) => root.clone(),
        (None, Some(declared), _, _) => declared,
        (None, None, Some(found), None) => found.path.parent().map(Path::to_path_buf).unwrap_or_else(|| cwd.clone()),
        _ => cwd.clone(),
    };
    let root = root.canonicalize().map_err(|e| format!("cannot read repo root {}: {}", root.display(), e))?;
    let config_dir = loaded.as_ref().and_then(|l| l.path.canonicalize().ok()).and_then(|p| p.parent().map(Path::to_path_buf));
    let mut notices: Vec<project::notice::ConfigNotice> = loaded.as_ref().map(|l| l.notices.clone()).unwrap_or_default();
    let config_file = loaded.as_ref().map(|l| (l.path.clone(), l.text.clone()));
    let config = loaded.map(|l| l.config);
    // The service and layer declaration: the config's architecture (from the config file's dir), or architecture.hy at the repo root.
    let architecture_path = match config.as_ref().and_then(|c| c.architecture.clone()) {
        Some(path) => Some(config_dir.clone().unwrap_or_else(|| root.clone()).join(path)),
        None => Some(root.join("architecture.hy")).filter(|p| p.is_file()),
    };
    let declaration = architecture_path.clone();
    let architecture = match architecture_path {
        Some(path) => Some(
            project::architecture::Architecture::load(&path, &root).map_err(|problems| format!("architecture.hy errors:\n  {}", problems.join("\n  ")))?,
        ),
        None => None,
    };
    // The population query (`population_of`) re-reads the same declaration with the declaration's dir as the root. When the way of reading is the same, pass what was read so it is not read twice.
    if let (Some(path), Some(architecture)) = (&declaration, &architecture) {
        if path.parent() == Some(root.as_path()) {
            doeff_linter::population::remember_declaration(path, std::sync::Arc::new(architecture.clone()));
        }
    }
    notices.extend(architecture.iter().flat_map(|a| a.notices.iter().cloned()));
    let mut settings = match (&config, architecture) {
        (Some(config), architecture) => {
            config.project_settings_with(architecture).map_err(|problems| format!("config errors:\n  {}", problems.join("\n  ")))?
        }
        (None, Some(architecture)) => config::Config::default()
            .project_settings_with(Some(architecture))
            .map_err(|problems| format!("config errors:\n  {}", problems.join("\n  ")))?,
        (None, None) => ProjectSettings::default(),
    };
    // Dirs to walk outside the root (from the config file's dir). A nonexistent dir is stopped as a config error rather than silently walking nothing.
    let include_base = config_dir.clone().unwrap_or_else(|| root.clone());
    settings.include = config.iter().flat_map(|c| c.include.iter()).map(|dir| include_base.join(dir)).collect();
    if let Some(missing) = settings.include.iter().find(|dir| !dir.is_dir()) {
        return Err(format!("settings include dir {} does not exist", missing.display()));
    }
    settings.config_dir = config_dir;
    if let Some((path, text)) = &config_file {
        notices.extend(settings.unknown_rules.iter().map(|unknown| project::notice::rule_notice(path, text, unknown)));
    }
    let (enabled_rules, exclude_patterns) = config::merge_config(config.as_ref(), &args.enable, &args.disable, &args.exclude);
    let notice_id = ProjectRule::UnknownConfigKey.id();
    let silenced = args.disable.iter().chain(config.iter().flat_map(|c| c.disable.iter())).any(|id| id.eq_ignore_ascii_case(notice_id));
    if silenced {
        notices.clear();
    }
    // The upper bound so that the rule that looks for a class's base in files of the same repo (DOEFF002) does not search dirs above the root.
    doeff_linter::rules::class_inheritance::set_repo_root(&root);
    Ok(Setup { config, root, enabled_rules, exclude_patterns, settings, notices, declaration })
}

/// The list of paths when narrowing the files that emit violations by the path arguments (None for the default "." = all). Includes the same-name .pyi next to a named .hy
/// (DOEFF145 hits a .pyi that falsifies the .hy's runtime shape -- naming only the .hy still judges and keeps it).
fn only_paths(paths: &[String]) -> Option<Vec<PathBuf>> {
    if paths.iter().all(|p| p == ".") {
        return None;
    }
    Some(project::record_stubs::with_sibling_stubs(paths.iter().map(|p| editor::normalize_path(Path::new(p))).collect()))
}

/// Maps the layer rules' violations to the form that the existing output (text, json, hook) reads (the same Violation as the Python rules' violations).
/// If only is given, keeps only violations of files under those paths.
fn project_results(report: &ProjectReport, only: Option<&[PathBuf]>) -> Vec<LintResult> {
    let mut by_path: BTreeMap<PathBuf, Vec<&project::Finding>> = BTreeMap::new();
    for finding in report.findings.iter().filter(|f| only.is_none_or(|only| only.iter().any(|p| f.path.starts_with(p)))) {
        by_path.entry(finding.path.clone()).or_default().push(finding);
    }
    by_path
        .into_iter()
        .map(|(path, findings)| {
            let path_text = path.to_string_lossy().into_owned();
            let source = match std::fs::read_to_string(&path) {
                Ok(text) => text,
                Err(error) => {
                    eprintln!("doeff-linter: {}: cannot read to find the position: {}", path_text, error);
                    String::new()
                }
            };
            let mut result = LintResult::new(path_text.clone());
            result.violations = findings
                .into_iter()
                .map(|f| {
                    // The form humans and agents read -- after a short line, lay out "This is" (the subject), "Why" (the reason), the law's sentence, the fix, and the key.
                    let head = match &f.law {
                        Some(law) => format!("[{}] {}", law, f.message),
                        None => f.message.clone(),
                    };
                    let mut lines = vec![head, format!("This is: {}", f.explanation.subject), format!("Why: {}", f.explanation.reason)];
                    if let Some(statement) = &f.explanation.law_statement {
                        lines.push(format!("law: {}", statement));
                    }
                    lines.push(format!("Fix: {}", f.hint));
                    lines.push(format!("Key: {}", f.key));
                    let message = lines.join("\n");
                    Violation::new(f.rule.id().to_string(), message, offset_of(&source, f.range.start), path_text.clone(), f.severity)
                })
                .collect();
            result
        })
        .collect()
}


/// `doeff-linter fix <transform>` -- the rewriting command (a separate entry from the main command that reports violations).
#[derive(Parser, Debug)]
#[command(name = "doeff-linter fix", about = "Rewrite violations that can be fixed mechanically")]
struct FixCli {
    #[command(subcommand)]
    command: FixCommand,
}

#[derive(clap::Subcommand, Debug)]
enum FixCommand {
    /// Rewrite Hy's defn to defk, and rewrite callers in the repo to <- / ! (DOEFF110, DOEFF126)
    DefnToDefk(DefnToDefkArgs),
}

#[derive(clap::Args, Debug)]
struct DefnToDefkArgs {
    /// Files or dirs to search for definitions to transform (multiple allowed)
    #[arg(long, required = true)]
    path: Vec<PathBuf>,
    /// The repo root (the range in which callers are searched). Defaults to the dir with .git found by searching upward from the path
    #[arg(long)]
    root: Option<PathBuf>,
    /// Output only the plan without writing files
    #[arg(long)]
    dry_run: bool,
    /// Output format: text (a table for humans) or json
    #[arg(long, default_value = "text")]
    output_format: String,
    /// The file to write the report JSON to (separate from the output format)
    #[arg(long)]
    report: Option<PathBuf>,
    /// Transform only functions whose callers can all be rewritten (by default it transforms, and puts callers that cannot be rewritten out as items needing a decision)
    #[arg(long)]
    strict: bool,
    /// Paths to exclude (prefix match from the root; added to the defaults clients/hy/acp_client and docs/design-checks)
    #[arg(long, value_delimiter = ',')]
    exclude: Vec<String>,
}

/// Runs the `fix` command. Exit code 0 = no bare calls remain, 1 = bare calls remain, 2 = argument or read/write error.
fn run_fix(argv: &[String]) -> ExitCode {
    let cli = FixCli::parse_from(argv.iter().skip(1));
    let FixCommand::DefnToDefk(args) = cli.command;
    let targets: Vec<PathBuf> = match args.path.iter().map(|p| p.canonicalize().map_err(|e| format!("cannot read {}: {}", p.display(), e))).collect() {
        Ok(targets) => targets,
        Err(reason) => {
            eprintln!("doeff-linter fix: {}", reason);
            return ExitCode::from(2);
        }
    };
    let root = match args.root.as_ref().map(|r| r.canonicalize()).transpose() {
        Ok(Some(root)) => root,
        Ok(None) => match targets.first().and_then(|t| t.ancestors().find(|a| a.join(".git").exists()).map(Path::to_path_buf)) {
            Some(root) => root,
            None => {
                eprintln!("doeff-linter fix: cannot find the repo root (a dir with .git) -- pass it with --root");
                return ExitCode::from(2);
            }
        },
        Err(error) => {
            eprintln!("doeff-linter fix: cannot read --root: {}", error);
            return ExitCode::from(2);
        }
    };
    // The mark for deff's reason comes from the repo's config ([tool.doeff-linter.definitions] deff_reason_marker) -- the default if unreadable.
    let reason_marker = config::load_config_checked(None, &root)
        .ok()
        .flatten()
        .and_then(|loaded| loaded.config.project_settings_with(None).ok())
        .and_then(|settings| settings.definitions.map(|d| d.deff_reason_marker))
        .unwrap_or_else(|| project::defn_to_defk::DEFAULT_REASON_MARKER.to_string());
    let mut excludes: Vec<String> = project::defn_to_defk::DEFAULT_EXCLUDES.iter().map(|s| s.to_string()).collect();
    excludes.extend(args.exclude.iter().cloned());
    let options = project::defn_to_defk::FixOptions { root, targets, excludes, reason_marker, write: !args.dry_run, strict: args.strict };
    let report = match project::defn_to_defk::run(&options) {
        Ok(report) => report,
        Err(reason) => {
            eprintln!("doeff-linter fix: {}", reason);
            return ExitCode::from(2);
        }
    };
    let json = match serde_json::to_string_pretty(&report) {
        Ok(json) => json,
        Err(error) => {
            eprintln!("doeff-linter fix: cannot turn the report into JSON: {}", error);
            return ExitCode::from(2);
        }
    };
    if let Some(path) = &args.report {
        if let Err(error) = std::fs::write(path, &json) {
            eprintln!("doeff-linter fix: cannot write {}: {}", path.display(), error);
            return ExitCode::from(2);
        }
    }
    match args.output_format.as_str() {
        "json" => println!("{}", json),
        _ => print!("{}", report.table()),
    }
    if report.residual_bare_calls.is_empty() {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    }
}

/// Upper limit of threads for a single-file run (see the note in main below).
const SINGLE_FILE_THREADS: usize = 4;

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().collect();
    if argv.get(1).map(String::as_str) == Some("fix") {
        return run_fix(&argv);
    }
    // A process that was handed the cleanup of the fact cache's upper limit (it does not take the machine's check slot -- no hook is waiting).
    if argv.get(1).map(String::as_str) == Some(doeff_linter::project::facts_cache::SWEEP_COMMAND) {
        return doeff_linter::project::facts_cache::run_handed_sweep(&argv[2..]);
    }
    // A resident process that remembers changes in the working tree via inotify and answers the queries of a single-file run.
    if argv.get(1).map(String::as_str) == Some(doeff_linter::project::fsmonitor::SERVE_COMMAND) {
        return doeff_linter::project::fsmonitor::daemon::serve();
    }
    let args = Args::parse();

    if args.list_rules {
        println!("{}", doeff_linter::config::rule_list_json());
        return ExitCode::SUCCESS;
    }

    if args.report_out.is_some() && (!args.commit_hook || args.split_rules) {
        eprintln!("doeff-linter: --report-out can be used only with --commit-hook (it cannot be used with --split-rules)");
        return ExitCode::from(2);
    }

    // Heavy modes take one of the machine's check slots before working (hand itself over via exec to the canonical command's wrapper -- if handed over, it does not return).
    check_slot::enter(check_slot::weight(check_slot::Mode {
        list_rules: args.list_rules,
        split_rules: args.split_rules,
        affected_tests: args.affected_tests.is_some(),
        stdin: args.stdin,
        hook: args.hook,
        commit_hook: args.commit_hook,
        editor_json: matches!(OutputFormat::parse(&args.output_format), OutputFormat::EditorJson),
    }));

    if args.commit_hook || args.split_rules {
        return run_commit_hook(&args);
    }

    if let Some(changed) = &args.affected_tests {
        // Reverse-dependency queries are also small work (0.05 seconds of CPU on doeff with a warm cache) -- on a machine with load 26, 36 threads used
        // 0.9 to 11 seconds in sys time alone, while 4 threads took 0.28 seconds cold and 0.07 seconds warm.
        if std::env::var_os("RAYON_NUM_THREADS").is_none() {
            let _ = rayon::ThreadPoolBuilder::new().num_threads(SINGLE_FILE_THREADS).build_global();
        }
        return run_affected_tests(&args, changed);
    }

    // A single-file run (--stdin, the hook right after writing, and the editor) is a sequence of small jobs, so more threads do not make it faster and only increase the CPU of
    // thread coordination (measured in #1033, on a 36-core machine: 36 threads 0.45 seconds of CPU / 4 threads 0.30 seconds, with the same wall clock of 0.27 seconds).
    // If RAYON_NUM_THREADS is set explicitly, follow it.
    if args.stdin && std::env::var_os("RAYON_NUM_THREADS").is_none() {
        let _ = rayon::ThreadPoolBuilder::new().num_threads(SINGLE_FILE_THREADS).build_global();
    }
    // A single-file run asks the resident process for the stat and walk of all files in the repo (if there is none, it walks itself as before).
    if args.stdin {
        doeff_linter::project::fsmonitor::enable();
    }

    if args.hook {
        return run_as_hook(&args);
    }

    match OutputFormat::parse(&args.output_format) {
        OutputFormat::EditorJson => run_editor(&args),
        OutputFormat::Text | OutputFormat::Json if args.baseline_report.is_some() => {
            eprintln!("doeff-linter: --baseline-report can be used only with --output-format editor-json");
            ExitCode::from(2)
        }
        OutputFormat::Text | OutputFormat::Json => run_normal(&args),
    }
}

/// `--commit-hook` -- decides the git working tree root (`--root` wins) and the config, and fires the body (doeff_linter::commit_hook). `--split-rules` likewise outputs only the split of rules to apply to the changed paths
/// from the same root and config (the gate and the hook use the same judgment).
/// Exit code 0 = pass, 1 = block (including when it was cut off by the limit and could not measure), 2 = config, git, or linter error.
fn run_commit_hook(args: &Args) -> ExitCode {
    let fail = |reason: String| {
        eprintln!("doeff-linter commit-hook: {}", reason);
        ExitCode::from(2)
    };
    let cwd = match std::env::current_dir() {
        Ok(cwd) => cwd,
        Err(error) => return fail(format!("cannot read the current dir: {}", error)),
    };
    let root = match &args.root {
        Some(root) => root.clone(),
        None => match doeff_linter::commit_hook::git_toplevel(&cwd) {
            Ok(root) => root,
            Err(reason) => return fail(reason),
        },
    };
    let root = match root.canonicalize() {
        Ok(root) => root,
        Err(error) => return fail(format!("cannot read repo root {}: {}", root.display(), error)),
    };
    let loaded = if args.no_config {
        None
    } else {
        match config::load_config_checked(args.config.as_deref(), &root) {
            Ok(loaded) => loaded,
            Err(reason) => return fail(reason),
        }
    };
    let linter = match std::env::current_exe() {
        Ok(linter) => linter,
        Err(error) => return fail(format!("cannot read the current binary's path: {}", error)),
    };
    let config = loaded.as_ref().map(|l| (&l.config, l.path.canonicalize().unwrap_or_else(|_| l.path.clone())));
    let mut options = doeff_linter::commit_hook::CommitHookOptions::new(root, config, &args.enable, &args.disable, args.commit_hook_timeout_s, linter);
    if let Some(file) = &args.commit_message {
        let message = match std::fs::read_to_string(file) {
            Ok(message) => message,
            Err(error) => return fail(format!("cannot read commit body {}: {}", file.display(), error)),
        };
        options.overlay = doeff_linter::commit_hook::baseline_overlay(&message, &options.declarations);
        options.message = Some(cwd.join(file));
    }
    if args.split_rules {
        // The gate's interface: treat the path arguments as changed paths and output the same split of judgments as the hook (the default . is not a changed path).
        let changed: Vec<String> = args.paths.iter().filter(|p| p.as_str() != ".").cloned().collect();
        let split = doeff_linter::commit_hook::split_for_change(&options.enabled, &changed, &options.declarations);
        let changed_declaration = doeff_linter::commit_hook::touches_declaration(&changed, &options.declarations);
        println!(
            "{}",
            serde_json::json!({ "quick": split.quick, "whole": split.whole, "declaration_changed": changed_declaration, "baseline_overlay": options.overlay })
        );
        return ExitCode::SUCCESS;
    }
    ExitCode::from(doeff_linter::commit_hook::run(&options, args.report_out.as_deref()))
}

/// `--affected-tests` -- walks reverse dependencies from the files that git knows in the repo root (`--root` wins; otherwise the root of the git working tree of the current dir),
/// and outputs the answer JSON (project::affected_tests::AffectedReport) in one line. Exit code 0 = answered, 2 = argument or git error.
fn run_affected_tests(args: &Args, changed: &[String]) -> ExitCode {
    let fail = |reason: String| {
        eprintln!("doeff-linter affected-tests: {}", reason);
        ExitCode::from(2)
    };
    if args.test_pattern.is_empty() {
        return fail("no rule for test file names -- pass the caller's collector rule with --test-pattern (there is no default list)".to_string());
    }
    let root = match &args.root {
        Some(root) => root.clone(),
        None => match std::env::current_dir().map_err(|e| format!("cannot read the current dir: {}", e)).and_then(|cwd| doeff_linter::commit_hook::git_toplevel(&cwd)) {
            Ok(root) => root,
            Err(reason) => return fail(reason),
        },
    };
    let root = match root.canonicalize() {
        Ok(root) => root,
        Err(error) => return fail(format!("cannot read repo root {}: {}", root.display(), error)),
    };
    let files = match project::affected_tests::known_files(&root) {
        Ok(files) => files,
        Err(reason) => return fail(reason),
    };
    let query = project::affected_tests::Query {
        changed: changed.to_vec(),
        test_patterns: args.test_pattern.clone(),
        max_dependents: args.max_dependents.unwrap_or(project::affected_tests::DEFAULT_MAX_DEPENDENTS),
    };
    let report = project::affected_tests::affected_tests(&root, &files, &query);
    match serde_json::to_string(&report) {
        Ok(json) => {
            println!("{}", json);
            ExitCode::SUCCESS
        }
        Err(error) => fail(format!("cannot turn the answer into JSON: {}", error)),
    }
}

/// The exit code when there is a new critical in the comparison with the baseline (spec section 1).
const NEW_CRITICAL_EXIT: u8 = 4;

/// Reads the `--baseline-report` file and returns the set of identifiers of the baseline's criticals (None if absent). A reason if unreadable or the shape is wrong.
fn baseline_identities(args: &Args) -> Result<Option<std::collections::BTreeSet<String>>, String> {
    let Some(path) = &args.baseline_report else {
        return Ok(None);
    };
    let text = std::fs::read_to_string(path).map_err(|e| format!("cannot read --baseline-report {}: {}", path.display(), e))?;
    doeff_linter::baseline::read_baseline(&text).map(Some).map_err(|reason| format!("--baseline-report {}: {}", path.display(), reason))
}

/// `--output-format editor-json` -- outputs one JSON for editors. Exit code 0 = no new breaches (errors), 1 = some, 2 = argument or config error,
/// 4 = there are criticals not in the `--baseline-report` baseline (the table in spec section 1 is the source of truth).
fn run_editor(args: &Args) -> ExitCode {
    if let Some(reason) = missing_semantic_targets(args) {
        eprintln!("doeff-linter: {}", reason);
        return ExitCode::from(2);
    }
    let baseline = match baseline_identities(args) {
        Ok(baseline) => baseline,
        Err(reason) => {
            eprintln!("doeff-linter: {}", reason);
            return ExitCode::from(2);
        }
    };
    let setup = match doeff_linter::timing::timed("prepare", || prepare(args)) {
        Ok(setup) => setup,
        Err(reason) => {
            eprintln!("doeff-linter: {}", reason);
            return ExitCode::from(2);
        }
    };
    let python_rules = rules::get_enabled_rules(setup.enabled_rules.as_deref());
    let python_ids: Vec<String> = python_rules.iter().map(|r| r.rule_id().to_string()).collect();
    let project_rules = setup.project_rules();
    let project_wired = setup.project_wired();

    let (python_results, project_report, stdin_file, only) = if args.stdin {
        let Some(path) = &args.path else {
            eprintln!("doeff-linter: --stdin requires --path");
            return ExitCode::from(2);
        };
        let mut source = String::new();
        if let Err(error) = io::stdin().read_to_string(&mut source) {
            eprintln!("doeff-linter: cannot read stdin: {}", error);
            return ExitCode::from(2);
        }
        let path = editor::normalize_path(path);
        let is_python = path.extension().is_some_and(|e| e == "py");
        let python_results = if is_python && !python_rules.is_empty() && !should_exclude(&path, &setup.exclude_patterns) {
            vec![lint_source_at(&path.to_string_lossy(), &source, &python_rules)]
        } else {
            Vec::new()
        };
        let project_report = if setup.has_project_rules() {
            let mode = doeff_linter::timing::timed("semantic-mode", || semantic_mode(args, &setup.root, Some(&path)));
            doeff_linter::timing::timed("project(single)", || {
                project::run_with(&setup.root, &setup.settings, &project_rules, Target::Single { path: path.clone(), source: &source }, &mode)
            })
        } else {
            ProjectReport::default()
        };
        (python_results, project_report, Some((path, source)), None)
    } else {
        if args.path.is_some() {
            eprintln!("doeff-linter: --path is used together with --stdin");
            return ExitCode::from(2);
        }
        let python_results = if python_rules.is_empty() {
            Vec::new()
        } else {
            let files = collect_python_files_with_options(&args.paths, &setup.exclude_patterns, args.force_exclude);
            lint_files_parallel(&files, &python_rules)
        };
        let only = only_paths(&args.paths);
        let project_report = if setup.has_project_rules() {
            doeff_linter::timing::timed("project", || {
                project::run_with(&setup.root, &setup.settings, &project_rules, Target::Whole { focus: only.as_deref() }, &semantic_mode(args, &setup.root, None))
            })
        } else {
            ProjectReport::default()
        };
        (python_results, project_report, None, only)
    };

    let mut project_report = project_report;
    project_report.findings.extend(setup.notice_findings());
    // Unknown config keys (DOEFF100) come from the same config in both whole runs and single-file runs -- both runs declare it as a judged rule (#2163).
    project_report.judged.insert(ProjectRule::UnknownConfigKey);
    // The headings and bindings of a pre-save Hy file (version 2) -- on top of the repo's Hy table, only this file is read from stdin's contents.
    let signatures = stdin_file.as_ref().and_then(|(path, source)| {
        let is_hy = path.extension().is_some_and(|e| e == "hy" || e == "hyk" || e == "hyp");
        let rel = project::relative_path(&setup.root, path).filter(|_| is_hy)?;
        // If there is a table that the project run built with the same overlay, reuse it (build one only when none exists = only when the rules did not need the table; #1033).
        let built;
        let world = match project_report.world.as_ref() {
            Some(world) => world,
            None => {
                built = project::signatures::World::build_for_file(&setup.root, &rel, source);
                &built
            }
        };
        Some(doeff_linter::timing::timed("signatures", || project::file_view::file_signatures(world, &setup.root, &rel, source)))
    });
    let mut report = doeff_linter::timing::timed("editor-build", || editor::build(&EditorInput {
        root: &setup.root,
        python: &python_results,
        stdin: stdin_file.as_ref().map(|(p, s)| (p.as_path(), s.as_str())),
        project: &project_report,
        settings: &setup.settings,
        python_rules: &python_ids,
        project_rules: &project_rules,
        project_wired: &project_wired,
        only: only.as_deref(),
        signatures: signatures.as_ref(),
    }));
    report.new_critical =
        baseline.map(|baseline| doeff_linter::baseline::blocking_criticals(&baseline, &report.violations, only.is_some()));
    // Of the named Hy files, those outside the range the linter walks (not judged by the layer rules -- spec section 1 "outside the named range").
    report.out_of_scope = match (&only, stdin_file.is_none() && setup.has_project_rules()) {
        (Some(named), true) => {
            Some(
                project::hy_files::outside_walk(&setup.root, &setup.settings.include, named, setup.declaration.as_deref())
                    .iter()
                    .map(|p| p.to_string_lossy().into_owned())
                    .collect(),
            )
        }
        _ => None,
    };
    match doeff_linter::timing::timed("editor-serialize", || serde_json::to_string(&report)) {
        Ok(text) => println!("{}", text),
        Err(error) => {
            eprintln!("doeff-linter: cannot turn the output into JSON: {}", error);
            return ExitCode::from(2);
        }
    }
    if report.new_critical.as_ref().is_some_and(|fresh| !fresh.is_empty()) {
        ExitCode::from(NEW_CRITICAL_EXIT)
    } else if report.has_errors() {
        ExitCode::from(1)
    } else if project_report.semantic.as_ref().is_some_and(|s| s.unmeasured > 0)
        || report.out_of_scope.as_ref().is_some_and(|outside| !outside.is_empty())
    {
        ExitCode::from(UNMEASURED_EXIT)
    } else {
        ExitCode::SUCCESS
    }
}

/// The exit code when there are no breaches but an answer could not be obtained for a definition that the semantic rules should have asked about (could not measure).
const UNMEASURED_EXIT: u8 = 3;

fn run_as_hook(args: &Args) -> ExitCode {
    // Read JSON from stdin
    let mut input = String::new();
    if let Err(e) = io::stdin().read_to_string(&mut input) {
        eprintln!("Failed to read stdin: {}", e);
        // Output empty response and exit
        println!("{}", serde_json::json!({}));
        return ExitCode::SUCCESS;
    }

    // Parse hook input
    let hook_input: HookInput = match serde_json::from_str(&input) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("Failed to parse hook input: {}", e);
            println!("{}", serde_json::json!({}));
            return ExitCode::SUCCESS;
        }
    };

    // Get paths to lint from workspace_roots or use current directory
    let paths: Vec<String> = hook_input
        .workspace_roots
        .unwrap_or_else(|| vec![".".to_string()]);

    // Load config (the hook does not stop the agent, so when the config cannot be read, print the reason to stderr and continue without a config)
    let setup = match prepare(args) {
        Ok(setup) => setup,
        Err(reason) => {
            eprintln!("doeff-linter: {} (continuing without a config)", reason);
            let (enabled_rules, exclude_patterns) = config::merge_config(None, &args.enable, &args.disable, &args.exclude);
            Setup {
                config: None,
                root: std::env::current_dir().unwrap_or_default(),
                enabled_rules,
                exclude_patterns,
                settings: ProjectSettings::default(),
                notices: Vec::new(),
                declaration: None,
            }
        }
    };
    let config = setup.config.clone();
    let (enabled_rules, exclude_patterns) = (setup.enabled_rules.clone(), setup.exclude_patterns.clone());

    // Get rules
    let all_rules = rules::get_enabled_rules(enabled_rules.as_deref());

    // Collect files (hook mode always respects exclusions)
    let files = collect_python_files_with_options(&paths, &exclude_patterns, true);

    // Lint files (the layer rules' violations are added in the same form)
    let mut results = lint_files_parallel(&files, &all_rules);
    if setup.has_project_rules() {
        // The hook (the watcher for the worker's stop) is a whole run -- if a proxy exists, it asks "only when remembered".
        let only = only_paths(&paths);
        let mut report =
            project::run_with(&setup.root, &setup.settings, &setup.project_rules(), Target::Whole { focus: only.as_deref() }, &project::semantic::SemanticMode::Peek);
        report.findings.extend(setup.notice_findings());
        results.extend(project_results(&report, only.as_deref()));
    } else if !setup.notices.is_empty() {
        let report = ProjectReport { findings: setup.notice_findings(), ..ProjectReport::default() };
        results.extend(project_results(&report, only_paths(&paths).as_deref()));
    }

    if results.iter().all(|r| r.violations.is_empty()) {
        // No violations, output empty response
        println!("{}", serde_json::json!({}));
        return ExitCode::SUCCESS;
    }

    // Group and count violations
    let mut grouped: BTreeMap<String, Vec<ViolationSummary>> = BTreeMap::new();
    let mut error_count = 0;

    for result in &results {
        for v in &result.violations {
            if v.severity == Severity::Error {
                error_count += 1;
            }
            let line = get_line_from_offset(&result.file_path, v.offset);
            let source_line = read_source_line(&v.file_path, line);
            grouped
                .entry(v.rule_id.clone())
                .or_default()
                .push(ViolationSummary {
                    file_path: v.file_path.clone(),
                    line,
                    source_line,
                    detail: ProjectRule::parse(&v.rule_id).map(|_| v.message.clone()),
                });
        }
    }

    // Log results (enabled by default, use --no-log to disable)
    if !args.no_log {
        let log_file = args.log_file.clone().or_else(|| config.as_ref().and_then(|c| c.log_file.clone()));
        if let Some(log_path) = log_file {
            let enabled_rule_ids: Vec<String> = all_rules.iter().map(|r| r.rule_id().to_string()).collect();
            let log_entry = LintLogEntry::from_results(&results, "hook", Some(enabled_rule_ids));
            match LintLogger::new(&log_path) {
                Ok(mut logger) => {
                    if let Err(e) = logger.log(&log_entry) {
                        eprintln!("Warning: Failed to write to log file: {}", e);
                    }
                }
                Err(e) => {
                    eprintln!("Warning: Failed to create log file: {}", e);
                }
            }
        }
    }

    // If there are errors, create a followup message
    let output = if error_count > 0 {
        let message = build_followup_message(&grouped);
        HookOutput {
            followup_message: Some(message),
        }
    } else {
        HookOutput {
            followup_message: None,
        }
    };

    // Output hook response
    println!("{}", serde_json::to_string(&output).unwrap_or_else(|_| "{}".to_string()));
    ExitCode::SUCCESS
}

struct ViolationSummary {
    file_path: String,
    line: usize,
    source_line: String,
    /// The sentence for a layer rule's violation (this is, why, fix -- so that an agent can read the reason and fix it).
    detail: Option<String>,
}

fn build_followup_message(grouped: &BTreeMap<String, Vec<ViolationSummary>>) -> String {
    let mut message = String::from("The doeff-linter found code quality issues that need to be fixed:\n\n");

    for (rule_id, violations) in grouped {
        let rule_info = get_rule_info(rule_id);
        message.push_str(&format!("## {} - {}\n", rule_id, rule_info.name));
        message.push_str(&format!("**Problem:** {}\n", rule_info.description));
        message.push_str(&format!("**How to fix:** {}\n\n", rule_info.fix));

        // Show up to 5 examples per rule
        let examples: Vec<_> = violations.iter().take(5).collect();
        for v in &examples {
            message.push_str(&format!("- `{}:{}`", v.file_path, v.line));
            if !v.source_line.is_empty() {
                message.push_str(&format!(" → `{}`", v.source_line));
            }
            message.push('\n');
            if let Some(detail) = &v.detail {
                for line in detail.lines() {
                    message.push_str(&format!("  {}\n", line));
                }
            }
        }
        if violations.len() > 5 {
            message.push_str(&format!("- ... and {} more\n", violations.len() - 5));
        }
        message.push('\n');
    }

    message.push_str("Please fix these issues following the suggestions above.");
    message
}

/// The reason to refuse, among the paths named by `--semantic` / `--semantic-changed`, those that are not on disk (None if all exist). The named paths are the key of the set of targets to ask
/// (SemanticMode's targets), so a nonexistent path hit no definition and the run silently ended with 0 asked, calibration not-run, and exit code 0
/// (in the real case where 3 people read this as "silently 0 when many are asked", zsh did not split the unquoted `$FILES`, and 11 paths arrived joined by spaces as
/// one nonexistent path). So that a run that named something to ask does not claim green having asked nothing, it stops with exit code 2 as an argument error.
fn missing_semantic_targets(args: &Args) -> Option<String> {
    if !(args.semantic || args.semantic_changed) || args.stdin {
        return None;
    }
    let missing: Vec<&str> = args.paths.iter().filter(|p| p.as_str() != "." && !editor::normalize_path(Path::new(p)).exists()).map(String::as_str).collect();
    if missing.is_empty() {
        return None;
    }
    Some(format!(
        "the path named by --semantic does not exist -- cannot decide what to ask, so stopping (is it joined into one argument containing spaces? zsh does not split an unquoted $VAR into words): {}",
        missing.iter().map(|p| format!("{:?}", p)).collect::<Vec<_>>().join(", ")
    ))
}

fn run_normal(args: &Args) -> ExitCode {
    if args.stdin || args.path.is_some() {
        eprintln!("doeff-linter: --stdin and --path are used only with --output-format editor-json");
        return ExitCode::from(2);
    }
    if let Some(reason) = missing_semantic_targets(args) {
        eprintln!("doeff-linter: {}", reason);
        return ExitCode::from(2);
    }
    // Load config (an unreadable config is not silently discarded; print the reason and exit with code 2)
    let setup = match prepare(args) {
        Ok(setup) => setup,
        Err(reason) => {
            eprintln!("doeff-linter: {}", reason);
            return ExitCode::from(2);
        }
    };
    let config = setup.config.clone();
    let (enabled_rules, exclude_patterns) = (setup.enabled_rules.clone(), setup.exclude_patterns.clone());

    if args.verbose {
        eprintln!("Enabled rules: {:?}", enabled_rules);
        eprintln!("Exclude patterns: {:?}", exclude_patterns);
        eprintln!("Force exclude: {}", args.force_exclude);
    }

    // Get rules
    let all_rules = rules::get_enabled_rules(enabled_rules.as_deref());

    if args.verbose {
        eprintln!(
            "Active rules: {}",
            all_rules
                .iter()
                .map(|r| r.rule_id())
                .collect::<Vec<_>>()
                .join(", ")
        );
    }

    // Collect files
    let files = if args.modified {
        // Get git-modified files
        let base_path = args.paths.first().map(|s| s.as_str()).unwrap_or(".");
        let modified_files = get_git_modified_files(base_path);

        if args.verbose {
            eprintln!("Git modified files: {:?}", modified_files);
        }

        // Filter by exclude patterns and convert to PathBuf
        // Modified mode always applies exclusions (like force_exclude)
        // Merging symlinks and the real thing into one is the same single point as dir traversal and named files (#2905)
        one_path_per_file(
            modified_files
                .into_iter()
                .filter(|f| !exclude_patterns.iter().any(|pat| f.contains(pat)))
                .map(std::path::PathBuf::from)
                .collect(),
        )
    } else {
        collect_python_files_with_options(&args.paths, &exclude_patterns, args.force_exclude)
    };

    if args.verbose {
        eprintln!("Found {} Python files", files.len());
    }

    if files.is_empty() && (args.modified || !setup.has_project_rules()) {
        eprintln!("No Python files found");
        return ExitCode::SUCCESS;
    }

    // Lint files (the layer rules' violations are added in the same form)
    let mut results = lint_files_parallel(&files, &all_rules);
    // The number of definitions the semantic rules should have asked about but got no answer for (if not 0, even with no breaches, it is separated from green with exit code 3).
    let mut unmeasured = 0;
    // Of the Hy files named on the command line, those outside the range the linter walks (not judged by the layer rules -- even with no breaches, separated from green with exit code 3).
    // --modified is not naming, so it is not counted.
    let mut outside: Vec<PathBuf> = Vec::new();
    if setup.has_project_rules() {
        // With --modified, only the changed files' violations (do not stop on known violations in unchanged files). Rules that can be judged by one file
        // read only under this path (the focus of Target::Whole).
        let only: Option<Vec<PathBuf>> = if args.modified { Some(project::record_stubs::with_sibling_stubs(files.iter().map(|f| editor::normalize_path(f)).collect())) } else { only_paths(&args.paths) };
        if let (Some(named), false) = (&only, args.modified) {
            outside = project::hy_files::outside_walk(&setup.root, &setup.settings.include, named, setup.declaration.as_deref());
            for path in &outside {
                eprintln!("{}", outside_line(path, &setup.root));
            }
        }
        let mut report =
            project::run_with(&setup.root, &setup.settings, &setup.project_rules(), Target::Whole { focus: only.as_deref() }, &semantic_mode(args, &setup.root, None));
        report.findings.extend(setup.notice_findings());
        for error in &report.errors {
            eprintln!("doeff-linter: {}", error);
        }
        for note in &report.notes {
            eprintln!("doeff-linter: notice -- {}", note);
        }
        if let Some(semantic) = &report.semantic {
            eprintln!(
                "doeff-linter: semantic rules (Jev {}, {}) -- judged {}, unjudged {}, fired this time {}, unmeasurable {}, from the proxy's memory {}, input tokens {}, calibration {}",
                semantic.model,
                semantic.wire,
                semantic.judged,
                semantic.unjudged,
                semantic.asked,
                semantic.unmeasured,
                semantic.peeked,
                semantic.input_tokens,
                semantic.calibration
            );
            unmeasured = semantic.unmeasured;
            let labeled = &semantic.labeled;
            eprintln!(
                "doeff-linter: {} semantic rule misjudgments (listed, removed from violations), comparison with human judgment -- of {} positive examples answered {}, hits {} / of {} counterexamples answered {}, hits {}",
                semantic.false_positives,
                labeled.positives.listed,
                labeled.positives.judged,
                labeled.positives.flagged,
                labeled.negatives.listed,
                labeled.negatives.judged,
                labeled.negatives.flagged
            );
        }
        results.extend(project_results(&report, only.as_deref()));
    } else if !setup.notices.is_empty() {
        // Even with a config that has no layer-rule section, unknown keys are reported (not silently skipped).
        let report = ProjectReport { findings: setup.notice_findings(), ..ProjectReport::default() };
        results.extend(project_results(&report, only_paths(&args.paths).as_deref()));
    }

    // Count violations
    let mut error_count = 0;
    let mut warning_count = 0;
    let mut info_count = 0;

    for result in &results {
        for v in &result.violations {
            match v.severity {
                Severity::Error => error_count += 1,
                Severity::Warning => warning_count += 1,
                Severity::Info => info_count += 1,
            }
        }
    }

    // Output results
    match OutputFormat::parse(&args.output_format) {
        OutputFormat::Json => {
            print_json(&results);
        }
        OutputFormat::Text | OutputFormat::EditorJson => {
            print_text_grouped(&results);
        }
    }

    // Log results (enabled by default, use --no-log to disable)
    if !args.no_log {
        let log_file = args.log_file.clone().or_else(|| config.as_ref().and_then(|c| c.log_file.clone()));
        if let Some(log_path) = log_file {
            let run_mode = if args.modified { "modified" } else { "normal" };
            let enabled_rule_ids: Vec<String> = all_rules.iter().map(|r| r.rule_id().to_string()).collect();
            let log_entry = LintLogEntry::from_results(&results, run_mode, Some(enabled_rule_ids));
            match LintLogger::new(&log_path) {
                Ok(mut logger) => {
                    if let Err(e) = logger.log(&log_entry) {
                        eprintln!("Warning: Failed to write to log file: {}", e);
                    } else if args.verbose {
                        eprintln!("Logged {} violations to {}", log_entry.total_violations, log_path);
                    }
                }
                Err(e) => {
                    eprintln!("Warning: Failed to create log file: {}", e);
                }
            }
        }
    }

    // Print summary
    let total = error_count + warning_count + info_count;
    if total > 0 {
        eprintln!(
            "\nFound {} issue(s): {} error(s), {} warning(s), {} info",
            total, error_count, warning_count, info_count
        );
    } else if args.verbose {
        eprintln!("\nNo issues found.");
    }

    // Return exit code (0 = no breaches, 1 = breaches, 2 = argument or config error, 3 = no breaches but something could not be measured -- a definition for which the semantic rules
    // could not be measured, or a named Hy file is outside the range the linter walks)
    if error_count > 0 {
        ExitCode::from(1)
    } else if unmeasured > 0 || !outside.is_empty() {
        if unmeasured > 0 {
            eprintln!("doeff-linter: {} definitions could not be measured by the semantic rules (cannot ask Jev, or the bundle that reads the proxy's memory did not return) -- not green", unmeasured);
        }
        if !outside.is_empty() {
            eprintln!("doeff-linter: {} of the named Hy files are out of scope (lines above) — not measured, so not green", outside.len());
        }
        ExitCode::from(UNMEASURED_EXIT)
    } else {
        ExitCode::SUCCESS
    }
}

/// One line that names that a named Hy file is outside the range the linter walks (stderr of the normal entry).
fn outside_line(path: &Path, root: &Path) -> String {
    format!(
        "doeff-linter: out of scope — {}(outside the range the linter walks = Hy files under root {} — layer rules do not judge this file)",
        path.display(),
        root.display()
    )
}

/// Violation info for grouping
struct ViolationInfo {
    file_path: String,
    line: usize,
    severity: Severity,
    #[allow(dead_code)]
    message: String,
    source_line: String,
}

/// Read a specific line from a file
fn read_source_line(file_path: &str, line_num: usize) -> String {
    if let Ok(content) = std::fs::read_to_string(file_path) {
        content
            .lines()
            .nth(line_num.saturating_sub(1))
            .map(|s| s.trim().to_string())
            .unwrap_or_default()
    } else {
        String::new()
    }
}

fn print_text_grouped(results: &[doeff_linter::models::LintResult]) {
    // Group violations by rule ID
    let mut grouped: BTreeMap<String, Vec<ViolationInfo>> = BTreeMap::new();

    for result in results {
        if let Some(error) = &result.error {
            eprintln!("{}: {}", result.file_path.red(), error);
            continue;
        }

        for v in &result.violations {
            let line = get_line_from_offset(&result.file_path, v.offset);
            let source_line = read_source_line(&v.file_path, line);
            grouped
                .entry(v.rule_id.clone())
                .or_default()
                .push(ViolationInfo {
                    file_path: v.file_path.clone(),
                    line,
                    severity: v.severity,
                    message: v.message.clone(),
                    source_line,
                });
        }
    }

    // Print grouped output
    for (rule_id, violations) in &grouped {
        let rule_info = get_rule_info(rule_id);
        let count = violations.len();
        
        // Determine severity color for header
        let severity = violations.first().map(|v| v.severity).unwrap_or(Severity::Warning);
        let header_color = match severity {
            Severity::Error => "error".red().bold(),
            Severity::Warning => "warning".yellow().bold(),
            Severity::Info => "info".blue().bold(),
        };

        println!(
            "\n{} {} - {} ({} occurrence{})",
            header_color,
            rule_id.cyan().bold(),
            rule_info.name.white().bold(),
            count,
            if count == 1 { "" } else { "s" }
        );
        println!("{}", "─".repeat(80).dimmed());
        println!("  {} {}", "What:".bright_white(), rule_info.description);
        println!("  {}  {}", "Fix:".bright_green(), rule_info.fix);
        println!();

        for v in violations {
            println!(
                "    {}:{}",
                v.file_path.dimmed(),
                v.line.to_string().yellow()
            );
            if !v.source_line.is_empty() {
                println!("      {}", v.source_line.bright_white());
            }
            // Layer rules have a different sentence per violation (this is, why, law, fix, key), so emit them one at a time.
            if ProjectRule::parse(rule_id).is_some() {
                for line in v.message.lines() {
                    println!("      {}", line);
                }
            }
        }
    }
}

/// Violation location info for JSON output
struct ViolationLocation {
    file: String,
    line: usize,
    severity: Severity,
    source: String,
    /// Optional case-specific detail (for future extensibility)
    /// Can contain variable-specific info extracted from the violation message
    detail: Option<String>,
}

fn print_json(results: &[doeff_linter::models::LintResult]) {
    // Group by rule for JSON output
    // Message is now per-rule, not per-violation
    let mut grouped: BTreeMap<String, Vec<ViolationLocation>> = BTreeMap::new();

    for result in results {
        for v in &result.violations {
            let line = get_line_from_offset(&result.file_path, v.offset);
            let source_line = read_source_line(&v.file_path, line);
            
            // Extract case-specific detail if the message contains variable-specific info
            // For now, we don't include detail (keeping it simple per user request)
            // Future: parse v.message to extract variable names or other context
            let detail = extract_violation_detail(&v.message);
            
            grouped
                .entry(v.rule_id.clone())
                .or_default()
                .push(ViolationLocation {
                    file: v.file_path.clone(),
                    line,
                    severity: v.severity,
                    source: source_line,
                    detail,
                });
        }
    }

    let output: Vec<serde_json::Value> = grouped
        .into_iter()
        .map(|(rule_id, violations)| {
            let rule_info = get_rule_info(&rule_id);
            let severity = violations.first().map(|v| v.severity).unwrap_or(Severity::Warning);
            
            // Build violation entries - only include detail if present
            let violation_entries: Vec<serde_json::Value> = violations
                .iter()
                .map(|v| {
                    let mut entry = serde_json::json!({
                        "file": v.file,
                        "line": v.line,
                        "source": v.source,
                    });
                    // Only include detail if it has meaningful content
                    if let Some(ref detail) = v.detail {
                        entry.as_object_mut().unwrap().insert(
                            "detail".to_string(),
                            serde_json::Value::String(detail.clone()),
                        );
                    }
                    entry
                })
                .collect();
            
            serde_json::json!({
                "rule": rule_id,
                "name": rule_info.name,
                "severity": format!("{}", severity),
                "message": rule_info.description,
                "fix": rule_info.fix,
                "count": violations.len(),
                "violations": violation_entries,
            })
        })
        .collect();

    println!("{}", serde_json::to_string_pretty(&output).unwrap_or_default());
}

/// Extract case-specific detail from a violation message
/// Returns Some(detail) if there's meaningful context-specific info,
/// None if the message is just a generic rule description
fn extract_violation_detail(message: &str) -> Option<String> {
    // For now, we return None to keep the output simple
    // Future: parse messages to extract variable names, etc.
    // Examples of patterns we could extract:
    // - "Consider refactoring: 'data' is initialized..." -> extract "data"
    // - "Parameter 'list' shadows builtin" -> extract "list"
    
    // Currently disabled per user request - message is at rule level only
    // To enable, uncomment and implement pattern matching:
    // if message.contains("'") {
    //     // Extract quoted variable/parameter names
    //     ...
    // }
    
    let _ = message; // silence unused warning
    None
}

fn get_line_from_offset(file_path: &str, offset: usize) -> usize {
    if let Ok(content) = std::fs::read_to_string(file_path) {
        doeff_linter::noqa::offset_to_line(&content, offset)
    } else {
        1
    }
}

/// Get list of git-modified Python files (both tracked and untracked)
fn get_git_modified_files(base_path: &str) -> Vec<String> {
    let mut files = Vec::new();

    // Get modified tracked files (staged and unstaged)
    // git diff --name-only HEAD (shows all changes vs HEAD)
    // git diff --name-only (shows unstaged changes)
    // git diff --name-only --cached (shows staged changes)
    // We use git status --porcelain to get both
    if let Ok(output) = Command::new("git")
        .args(["status", "--porcelain", "-uall"])
        .current_dir(base_path)
        .output()
    {
        if output.status.success() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            for line in stdout.lines() {
                // Format: XY filename or XY orig -> renamed
                // X = staged status, Y = unstaged status
                // ?? = untracked, M = modified, A = added, etc.
                if line.len() > 3 {
                    let file_part = &line[3..];
                    // Handle renamed files (take the new name after "->")
                    let filename = if let Some(pos) = file_part.find(" -> ") {
                        &file_part[pos + 4..]
                    } else {
                        file_part
                    };
                    
                    // Only include Python files
                    if filename.ends_with(".py") {
                        let full_path = Path::new(base_path).join(filename);
                        if full_path.exists() {
                            files.push(full_path.to_string_lossy().to_string());
                        }
                    }
                }
            }
        }
    }

    files
}
