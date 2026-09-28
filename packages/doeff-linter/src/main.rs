//! doeff-linter CLI

use clap::Parser;
use colored::*;
use doeff_linter::{
    collect_python_files_with_options, config,
    editor::{self, EditorInput},
    lint_files_parallel, lint_source,
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

EXAMPLES:
  doeff-linter .                         Lint current directory
  doeff-linter --enable DOEFF001         Enable only specific rule
  doeff-linter --disable DOEFF001        Disable specific rule
  doeff-linter --modified                Lint only git-modified files
  doeff-linter --output-format json      Output in JSON format
  doeff-linter --output-format editor-json            エディタ向けの JSON(repo 全体)
  doeff-linter --output-format editor-json --stdin --path <file>   保存前の内容の 1 file
  doeff-linter --config <file> --root <dir> ...       設定 file と repo の根を指定
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

    /// 設定 file(`[tool.doeff-linter]` を持つ pyproject.toml の形か、節の中身だけの TOML)。無ければ上へ探す
    #[arg(long)]
    config: Option<PathBuf>,

    /// repo の根(層の置き場・登録簿・鍵の path の基準)。既定は見つけた pyproject.toml の dir、--config の時は今の dir
    #[arg(long)]
    root: Option<PathBuf>,

    /// 保存前の内容を stdin から読む(--path が要る・editor-json の時だけ)
    #[arg(long)]
    stdin: bool,

    /// --stdin の内容をどの file として判じるか
    #[arg(long)]
    path: Option<PathBuf>,

    /// Show verbose output
    #[arg(short, long)]
    verbose: bool,

    /// Run as Cursor stop hook (reads JSON from stdin, outputs hook response)
    #[arg(long)]
    hook: bool,

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

    /// 意味の規則(DOEFF201・202・203)で Jev に問う — 対象は path の引数の file、無ければ git で変わった file。これが無い実行は cache を読むだけ
    #[arg(long)]
    semantic: bool,

    /// 意味の規則で、設定した層の全部の定義を Jev に問う
    #[arg(long)]
    semantic_all: bool,

    /// 意味の規則で、対象の file(--semantic と同じ選び方)のうち手元の cache に答えの無い定義(中身が変わった定義)だけを Jev に問う
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

/// 出力の形(閉じた集合)。知らない綴りは今までどおり text として扱う。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum OutputFormat {
    Text,
    Json,
    EditorJson,
}

impl OutputFormat {
    /// `--output-format` の綴りを読む。
    fn parse(text: &str) -> OutputFormat {
        match text {
            "json" => OutputFormat::Json,
            "editor-json" => OutputFormat::EditorJson,
            _ => OutputFormat::Text,
        }
    }
}

/// 設定を読んで決めた実行の前提(設定・repo の根・有効な規則・除く pattern・層の規則の設定)。
struct Setup {
    config: Option<config::Config>,
    root: PathBuf,
    enabled_rules: Option<Vec<String>>,
    exclude_patterns: Vec<String>,
    settings: ProjectSettings,
    /// 設定の知らない鍵と規則の ID(読まずに残りを読んだ — DOEFF100 で知らせる・agora-redesign #848)。
    notices: Vec<project::notice::ConfigNotice>,
}

impl Setup {
    /// 知らせ(DOEFF100)の違反。`enable` の一覧に載っていなくても出す(一覧は DOEFF100 より古い設定にも在り、そこで黙ると
    /// 知らない鍵を黙って読み飛ばす形に戻る)。止めるのは `disable` に DOEFF100 を名指した時だけ(prepare が notices を空にする)。
    fn notice_findings(&self) -> Vec<project::Finding> {
        project::notice::findings(&self.notices, &self.root)
    }

    /// 有効な層の規則(`enable`・`disable` を当てた後)。
    fn project_rules(&self) -> BTreeSet<ProjectRule> {
        project::enabled_rules(self.enabled_rules.as_deref())
    }

    /// 層の規則のうち、設定の節が在って判定がつながっている物。
    fn project_wired(&self) -> BTreeSet<ProjectRule> {
        self.project_rules()
            .into_iter()
            .filter(|rule| match rule {
                ProjectRule::UnknownConfigKey => true,
                ProjectRule::LayerImportDirection
                | ProjectRule::LayerForbiddenModule
                | ProjectRule::LayerTypesOnly
                | ProjectRule::ModuleDeclaresTags
                | ProjectRule::RoleMatchesLayer => self.settings.layers.is_some(),
                ProjectRule::RawSideEffectDirect | ProjectRule::RawSideEffectVia => self.settings.raw.is_some(),
                ProjectRule::EnvironmentName => self.settings.environment.is_some(),
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
                ProjectRule::JsonValueOutsideWire => self.settings.architecture.is_some(),
                ProjectRule::ShapeCheckInJudgment => {
                    self.settings.definitions.is_some() && self.settings.smells.as_ref().is_some_and(|s| !s.shape_check_layers.is_empty())
                }
                ProjectRule::FailureRethrow | ProjectRule::BindThenReturn | ProjectRule::FieldsJoinedIntoText | ProjectRule::RebuiltAccumulator => {
                    self.settings.definitions.is_some()
                }
                ProjectRule::SemanticMixedConcerns => self.settings.semantic.as_ref().is_some_and(|s| s.mixed_concerns.is_some()),
                ProjectRule::SemanticClassRole => self.settings.semantic.as_ref().is_some_and(|s| s.class_role.is_some()),
            })
            .collect()
    }

    /// 層の規則の設定が 1 つでも在るか(無ければ層の規則を走らせない)。
    fn has_project_rules(&self) -> bool {
        self.settings.layers.is_some() || self.settings.architecture.is_some() || self.settings.environment.is_some() || self.settings.raw.is_some() || self.settings.services.is_some()
            || self.settings.definitions.is_some()
    }
}

/// 意味の規則の扱いを引数から決める(--semantic-all = 全部・--semantic = 指定の file か git で変わった file・--semantic-changed = その
/// うち答えの無い定義だけ・どれも無ければ cache だけ)。問わない実行のうち全体の実行(hook を含む)は、代理が在れば「覚えている時だけ」
/// 問う(Peek)。編集中の 1 file(--stdin)は cache だけ(エディタは保存と打つのが止まった時に --semantic で新しく問う)。
fn semantic_mode(args: &Args, root: &Path, stdin_path: Option<&Path>) -> project::semantic::SemanticMode {
    use project::semantic::SemanticMode;
    if args.semantic_all {
        return SemanticMode::AskAll;
    }
    if !args.semantic && !args.semantic_changed {
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
    if args.semantic_changed {
        SemanticMode::AskChanged(targets)
    } else {
        SemanticMode::Ask(targets)
    }
}

/// git で変わった file(追跡していない file を含む・repo の根からの path)。
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

/// 設定を探して読み、repo の根と有効な規則を決める。設定が読めない・名前が食い違う時は理由の文(終了コード 2)。
fn prepare(args: &Args) -> Result<Setup, String> {
    let cwd = std::env::current_dir().map_err(|e| format!("今の dir を読めない: {}", e))?;
    let loaded = if args.no_config {
        None
    } else {
        config::load_config_checked(args.config.as_deref(), &cwd)?
    };
    let root = match (&args.root, &loaded, &args.config) {
        (Some(root), _, _) => root.clone(),
        (None, Some(found), None) => found.path.parent().map(Path::to_path_buf).unwrap_or_else(|| cwd.clone()),
        _ => cwd.clone(),
    };
    let root = root.canonicalize().map_err(|e| format!("repo の根 {} を読めない: {}", root.display(), e))?;
    let config_dir = loaded.as_ref().and_then(|l| l.path.canonicalize().ok()).and_then(|p| p.parent().map(Path::to_path_buf));
    let mut notices: Vec<project::notice::ConfigNotice> = loaded.as_ref().map(|l| l.notices.clone()).unwrap_or_default();
    let config_file = loaded.as_ref().map(|l| (l.path.clone(), l.text.clone()));
    let config = loaded.map(|l| l.config);
    // service と層の宣言: 設定の architecture(設定 file の dir から)か、repo の根の architecture.hy。
    let architecture_path = match config.as_ref().and_then(|c| c.architecture.clone()) {
        Some(path) => Some(config_dir.clone().unwrap_or_else(|| root.clone()).join(path)),
        None => Some(root.join("architecture.hy")).filter(|p| p.is_file()),
    };
    let architecture = match architecture_path {
        Some(path) => Some(
            project::architecture::Architecture::load(&path).map_err(|problems| format!("architecture.hy の誤り:\n  {}", problems.join("\n  ")))?,
        ),
        None => None,
    };
    notices.extend(architecture.iter().flat_map(|a| a.notices.iter().cloned()));
    let mut settings = match (&config, architecture) {
        (Some(config), architecture) => {
            config.project_settings_with(architecture).map_err(|problems| format!("設定の誤り:\n  {}", problems.join("\n  ")))?
        }
        (None, Some(architecture)) => config::Config::default()
            .project_settings_with(Some(architecture))
            .map_err(|problems| format!("設定の誤り:\n  {}", problems.join("\n  ")))?,
        (None, None) => ProjectSettings::default(),
    };
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
    Ok(Setup { config, root, enabled_rules, exclude_patterns, settings, notices })
}

/// 違反を出す file を path の引数で絞る時の path の列(既定の "." なら None = 全部)。
fn only_paths(paths: &[String]) -> Option<Vec<PathBuf>> {
    if paths.iter().all(|p| p == ".") {
        return None;
    }
    Some(paths.iter().map(|p| editor::normalize_path(Path::new(p))).collect())
}

/// 層の規則の違反を、今までの出力(text・json・hook)が読む形(Python の規則の違反と同じ Violation)に写す。
/// only があれば、その path の下の file の違反だけにする。
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
                    eprintln!("doeff-linter: {}: 位置を求めるために読めない: {}", path_text, error);
                    String::new()
                }
            };
            let mut result = LintResult::new(path_text.clone());
            result.violations = findings
                .into_iter()
                .map(|f| {
                    // 人と agent が読む形 — 短い 1 行の後に「これは」(主体)・「なぜ」(理由)・law の文・直し方・鍵を並べる。
                    let head = match &f.law {
                        Some(law) => format!("[{}] {}", law, f.message),
                        None => f.message.clone(),
                    };
                    let mut lines = vec![head, format!("これは: {}", f.explanation.subject), format!("なぜ: {}", f.explanation.reason)];
                    if let Some(statement) = &f.explanation.law_statement {
                        lines.push(format!("law: {}", statement));
                    }
                    lines.push(format!("直し方: {}", f.hint));
                    lines.push(format!("鍵: {}", f.key));
                    let message = lines.join("\n");
                    Violation::new(f.rule.id().to_string(), message, offset_of(&source, f.range.start), path_text.clone(), f.severity)
                })
                .collect();
            result
        })
        .collect()
}

fn main() -> ExitCode {
    let args = Args::parse();

    if args.hook {
        return run_as_hook(&args);
    }

    match OutputFormat::parse(&args.output_format) {
        OutputFormat::EditorJson => run_editor(&args),
        OutputFormat::Text | OutputFormat::Json => run_normal(&args),
    }
}

/// `--output-format editor-json` — エディタ向けの JSON を 1 つ出す。終了コード 0 = 新しい破れ(error)なし、1 = あり、2 = 引数・設定の誤り。
fn run_editor(args: &Args) -> ExitCode {
    let setup = match prepare(args) {
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
            eprintln!("doeff-linter: --stdin には --path が要る");
            return ExitCode::from(2);
        };
        let mut source = String::new();
        if let Err(error) = io::stdin().read_to_string(&mut source) {
            eprintln!("doeff-linter: stdin を読めない: {}", error);
            return ExitCode::from(2);
        }
        let path = editor::normalize_path(path);
        let is_python = path.extension().is_some_and(|e| e == "py");
        let python_results = if is_python && !python_rules.is_empty() && !should_exclude(&path, &setup.exclude_patterns) {
            vec![lint_source(&path.to_string_lossy(), &source, &python_rules)]
        } else {
            Vec::new()
        };
        let project_report = if setup.has_project_rules() {
            let mode = semantic_mode(args, &setup.root, Some(&path));
            project::run_with(&setup.root, &setup.settings, &project_rules, Target::Single { path: path.clone(), source: &source }, &mode)
        } else {
            ProjectReport::default()
        };
        (python_results, project_report, Some((path, source)), None)
    } else {
        if args.path.is_some() {
            eprintln!("doeff-linter: --path は --stdin と一緒に使う");
            return ExitCode::from(2);
        }
        let python_results = if python_rules.is_empty() {
            Vec::new()
        } else {
            let files = collect_python_files_with_options(&args.paths, &setup.exclude_patterns, args.force_exclude);
            lint_files_parallel(&files, &python_rules)
        };
        let project_report = if setup.has_project_rules() {
            project::run_with(&setup.root, &setup.settings, &project_rules, Target::Whole, &semantic_mode(args, &setup.root, None))
        } else {
            ProjectReport::default()
        };
        (python_results, project_report, None, only_paths(&args.paths))
    };

    let mut project_report = project_report;
    project_report.findings.extend(setup.notice_findings());
    // 保存前の Hy の file の見出しと束縛(版 2)— repo の Hy の表の上で、この file だけは stdin の中身で読む。
    let signatures = stdin_file.as_ref().and_then(|(path, source)| {
        let is_hy = path.extension().is_some_and(|e| e == "hy" || e == "hyk" || e == "hyp");
        let rel = project::relative_path(&setup.root, path).filter(|_| is_hy)?;
        let world = project::signatures::World::build(&setup.root, Some((&rel, source)));
        Some(project::signatures::file_signatures(&world, &setup.root, &rel, source))
    });
    let report = editor::build(&EditorInput {
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
    });
    match serde_json::to_string(&report) {
        Ok(text) => println!("{}", text),
        Err(error) => {
            eprintln!("doeff-linter: 出力を JSON にできない: {}", error);
            return ExitCode::from(2);
        }
    }
    if report.has_errors() {
        ExitCode::from(1)
    } else {
        ExitCode::SUCCESS
    }
}

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

    // Load config(hook は agent を止めないので、設定が読めない時は理由を stderr に出して設定なしで続ける)
    let setup = match prepare(args) {
        Ok(setup) => setup,
        Err(reason) => {
            eprintln!("doeff-linter: {}(設定なしで続ける)", reason);
            let (enabled_rules, exclude_patterns) = config::merge_config(None, &args.enable, &args.disable, &args.exclude);
            Setup {
                config: None,
                root: std::env::current_dir().unwrap_or_default(),
                enabled_rules,
                exclude_patterns,
                settings: ProjectSettings::default(),
                notices: Vec::new(),
            }
        }
    };
    let config = setup.config.clone();
    let (enabled_rules, exclude_patterns) = (setup.enabled_rules.clone(), setup.exclude_patterns.clone());

    // Get rules
    let all_rules = rules::get_enabled_rules(enabled_rules.as_deref());

    // Collect files (hook mode always respects exclusions)
    let files = collect_python_files_with_options(&paths, &exclude_patterns, true);

    // Lint files(層の規則の違反も同じ形で足す)
    let mut results = lint_files_parallel(&files, &all_rules);
    if setup.has_project_rules() {
        // hook(作業係の停止の見張り)は全体の実行 — 代理が在れば「覚えている時だけ」問う。
        let mut report = project::run_with(&setup.root, &setup.settings, &setup.project_rules(), Target::Whole, &project::semantic::SemanticMode::Peek);
        report.findings.extend(setup.notice_findings());
        results.extend(project_results(&report, only_paths(&paths).as_deref()));
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
    /// 層の規則の違反の文(これは・なぜ・直し方 — agent が理由を読んで直せるように)。
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

fn run_normal(args: &Args) -> ExitCode {
    if args.stdin || args.path.is_some() {
        eprintln!("doeff-linter: --stdin と --path は --output-format editor-json の時だけ使う");
        return ExitCode::from(2);
    }
    // Load config(読めない設定は黙って捨てず、理由を出して終了コード 2)
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
        modified_files
            .into_iter()
            .filter(|f| {
                !exclude_patterns.iter().any(|pat| f.contains(pat))
            })
            .map(std::path::PathBuf::from)
            .collect()
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

    // Lint files(層の規則の違反も同じ形で足す)
    let mut results = lint_files_parallel(&files, &all_rules);
    if setup.has_project_rules() {
        let mut report = project::run_with(&setup.root, &setup.settings, &setup.project_rules(), Target::Whole, &semantic_mode(args, &setup.root, None));
        report.findings.extend(setup.notice_findings());
        for error in &report.errors {
            eprintln!("doeff-linter: {}", error);
        }
        if let Some(semantic) = &report.semantic {
            eprintln!(
                "doeff-linter: 意味の規則(Jev {}・{}) — 判定済み {}・未判定 {}・今回撃った {}・代理の覚えから {}・入力のトークン {}・較正 {}",
                semantic.model, semantic.wire, semantic.judged, semantic.unjudged, semantic.asked, semantic.peeked, semantic.input_tokens, semantic.calibration
            );
        }
        // --modified の時は、変更した file の違反だけにする(変更していない file の既知の違反で止めない)。
        let only = if args.modified { Some(files.iter().map(|f| editor::normalize_path(f)).collect()) } else { only_paths(&args.paths) };
        results.extend(project_results(&report, only.as_deref()));
    } else if !setup.notices.is_empty() {
        // 層の規則の節が無い設定でも、知らない鍵は知らせる(黙って読み飛ばさない)。
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

    // Return exit code
    if error_count > 0 {
        ExitCode::from(1)
    } else {
        ExitCode::SUCCESS
    }
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
            // 層の規則は違反ごとに文が違う(これは・なぜ・law・直し方・鍵)ので、1 件ずつ出す。
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
