//! The shape of the editor-facing output `--output-format editor-json` (contract = docs/SPECIFICATION.md section 2, version 2) and how it is assembled.
//!
//! The linter is the sole source of truth for rule judgments; the editor (doeff-runner) only displays this JSON. It unifies violations of the Python per-statement rules
//! (DOEFF001 to 031) and the layer rules (DOEFF101 to 108) into one shape. Positions are 0-based lines and UTF-16 columns.
//!
//! Each item of `rules` carries a short name (`title`) and a rule family (`family`) — so the editor can attach
//! the name to each rule's row and change the row's icon by family. The source of truth for names and families is on the linter side (`project::rule::ProjectRule` for layer rules,
//! `rule_info::RuleInfo` for Python rules), and the editor keeps no copy. The version is not bumped (only a field is added — contract update 6).
//!
//! The top-level `judged_rules` lists the IDs of the rules judged in this run (contract update 8). A single-file run (`--stdin`) does not run rules that are judged only
//! over the whole repo (DOEFF166, 141, and so on — rules for which `ProjectRule::judged_on_one_file` is false), so the editor replaces only the violations of the rules in this list
//! with the single-file result and leaves the violations of other rules as in the whole run's result. The version is not bumped (a field is added — an editor reading old output without the field
//! replaces everything as before, and bumping the version would make an old editor that reads only version 2 discard the entire output).

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::models::{LintResult, Severity};
use crate::position::{line_range, Range};
use crate::project::explain::Explanation;
use crate::project::rule::{ProjectRule, RuleFamily};
use crate::project::law::ProjectRuleOrExternal;
use crate::project::settings::ProjectSettings;
use crate::project::report::ProjectReport;
use crate::rule_info::get_rule_info;

/// The contract version. Bump it together with the contract when the shape changes. Version 2 = added the defk / deff headings `signatures` and the binding types `bindings`
/// (so the editor can draw types, effects, and tags in a display that only reads).
pub const EDITOR_CONTRACT_VERSION: u32 = 2;

/// The severity of a violation (a closed set in the contract).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum EditorSeverity {
    Error,
    Warning,
    Info,
}

impl From<Severity> for EditorSeverity {
    /// Map the linter's severity to the contract's spelling.
    fn from(severity: Severity) -> Self {
        match severity {
            Severity::Error => EditorSeverity::Error,
            Severity::Warning => EditorSeverity::Warning,
            Severity::Info => EditorSeverity::Info,
        }
    }
}

/// One violation.
#[derive(Debug, Clone, Serialize)]
pub struct EditorViolation {
    pub rule: String,
    pub law: Option<String>,
    pub adr: Option<String>,
    pub severity: EditorSeverity,
    pub path: String,
    pub range: Range,
    pub message: String,
    pub hint: Option<String>,
    pub key: Option<String>,
    pub registered: bool,
    /// The rule's own severity before being lowered by registry reconciliation (`severity` is the value after lowering). Material for the editor to count "untouched heavy violations".
    pub base_severity: EditorSeverity,
    /// New violation (new), known violation listed in the registry (registered), or lowered during reconciliation (reconciling).
    pub standing: crate::project::report::Standing,
    /// The rule's gravity (the repo's declaration `rules.<ID>.level`, otherwise from base_severity). Not lowered by the registry.
    pub level: crate::project::rule::RuleLevel,
    /// What this is, why it is a violation, and the law statement (layer rules only. null for Python per-statement rules).
    pub explanation: Option<Explanation>,
    /// Where the judgment came from (linter = a deterministic rule, jev = a Jev semantic judgment).
    pub source: crate::project::report::FindingOrigin,
    /// The probability of the Jev judgment (Jev violations only; null for others).
    pub probability: Option<f64>,
}

/// One module of the map's material.
#[derive(Debug, Clone, Serialize)]
pub struct EditorModule {
    pub path: String,
    pub layer: Option<String>,
    pub context: Option<String>,
    pub role: Option<String>,
    pub violations: usize,
    /// What decided the layer (path location, tag, or a mismatch between the two).
    pub layer_reason: Option<String>,
    /// The name of the service that matched the `*` segment of the location (null if the layer is fixed in advance).
    pub service: Option<String>,
}

/// One layer description (from the setting `[tool.doeff-linter.layers.describe.<layer>]`. Fields absent from the setting are null).
#[derive(Debug, Clone, Serialize)]
pub struct EditorLayer {
    pub name: String,
    pub summary: Option<String>,
    pub knows: Option<String>,
    pub does_not_know: Option<String>,
    pub question: Option<String>,
}

/// One enabled rule (or a law that exists in an ADR but has no detector). Whether it was judged in this run is declared by `EditorReport::judged_rules` (#2163).
#[derive(Debug, Clone, Serialize)]
pub struct EditorRule {
    pub rule: String,
    pub adr: Option<String>,
    pub statement: String,
    pub wired: bool,
    /// A short name (the shape of the violation; the editor uses it as a heading for each rule). Contract update 6.
    pub title: String,
    /// The rule's family (a closed set from which the editor chooses the icon of a violation's field row). Contract update 6.
    pub family: RuleFamily,
}

/// The binary that produced this output (its version and the doeff commit it was built from — so that one can tell from outside whether the binary in storage is stale).
#[derive(Debug, Clone, Serialize)]
pub struct EditorLinter {
    pub version: String,
    pub commit: String,
}

/// The whole output.
#[derive(Debug, Clone, Serialize)]
pub struct EditorReport {
    pub version: u32,
    /// The binary that produced the output (a field added to contract version 1 — the extension's loader skips top-level fields it does not know).
    pub linter: EditorLinter,
    pub root: String,
    /// The order of layers (farthest from the outside world first) and their descriptions.
    pub layers: Vec<EditorLayer>,
    /// A summary of the semantic rules (Jev) — model, communication shape, counts of judged / unjudged, the number fired this time and its cost, and calibration results. null if there is no setting.
    pub semantic: Option<crate::project::semantic::SemanticSummary>,
    /// The declaration of architecture.hy (the list of services — name, dir, description, depends_on, layers — and the layer declarations). null if absent.
    pub architecture: Option<crate::project::architecture::Architecture>,
    pub violations: Vec<EditorViolation>,
    /// The defk / deff headings of the `--stdin` file (version 2; empty in a whole run).
    pub signatures: Vec<crate::project::signatures::Signature>,
    /// The types of the bindings (`<-`, val, var, setv, :=) of the `--stdin` file (version 2; empty in a whole run).
    pub bindings: Vec<crate::project::signatures::Binding>,
    /// A display replacement showing the calls in a definition's body of the `--stdin` file in the shape `f(a, b)` (a field added to version 2; empty in a whole run; section 17).
    pub rewrites: Vec<crate::project::call_view::Rewrite>,
    /// The lines of body text for each definition of the `--stdin` file (drawn by the reading surface — a field added to version 2; empty in a whole run; section 20).
    pub bodies: Vec<crate::project::body_view::Body>,
    pub modules: Vec<EditorModule>,
    pub rules: Vec<EditorRule>,
    /// The IDs of the rules judged in this run (in dictionary order, no duplicates — contract update 8). Rules that finished emitting their findings, including rules with 0 findings.
    /// It is not made a field (`judged`) of `rules[]` because `rules[]` is the list of enabled rules and lacks the rules of violations emitted outside it (the noqa notice NOQA001, and DOEFF100 / DOEFF128 which are emitted even when not in
    /// enable) — whether a rule was judged is a property of the run, so it is declared at the top of the report. Every rule of `violations` is in
    /// this list.
    pub judged_rules: Vec<String>,
    pub errors: Vec<String>,
    /// When `--baseline-report`: the identifiers of critical violations not in the baseline (`<path>::<rule>::<name>`, in dictionary order). null when not compared with a baseline
    /// (spec section 1, "comparison with the baseline" — a field added without bumping the version).
    pub new_critical: Option<Vec<String>>,
    /// When it is a whole run with paths named: of the named Hy files, those outside the range the linter walks (under the root plus the settings' include) (normalized absolute
    /// paths — the same shape as violation paths, in dictionary order).
    /// null for a run with no named paths and for `--stdin` (spec section 1, "outside the named scope" — a field added without bumping the version).
    pub out_of_scope: Option<Vec<String>>,
}

impl EditorReport {
    /// Whether there is a new violation (error) — the condition for exit code 1. Violations listed in the registry (warning) and info are not counted.
    pub fn has_errors(&self) -> bool {
        self.violations.iter().any(|v| v.severity == EditorSeverity::Error)
    }
}

/// The inputs needed for assembly.
pub struct EditorInput<'a> {
    pub root: &'a Path,
    /// The results of the Python rules (a file's path is relative to cwd or absolute).
    pub python: &'a [LintResult],
    /// The content before saving (one file for `--stdin` — the path is absolute).
    pub stdin: Option<(&'a Path, &'a str)>,
    pub project: &'a ProjectReport,
    pub settings: &'a ProjectSettings,
    pub python_rules: &'a [String],
    pub project_rules: &'a BTreeSet<ProjectRule>,
    /// Whether each rule has a clause and its judgment is wired (layer rules only).
    pub project_wired: &'a BTreeSet<ProjectRule>,
    /// Narrow the files that emit violations (all if None).
    pub only: Option<&'a [PathBuf]>,
    /// The headings and bindings of the `--stdin` Hy file (version 2).
    pub signatures: Option<&'a crate::project::file_view::FileSignatures>,
}

/// Assemble the output.
pub fn build(input: &EditorInput) -> EditorReport {
    let mut violations = Vec::new();
    let mut errors: Vec<String> = input.project.errors.clone();
    for result in input.python {
        let path = normalize_path(&PathBuf::from(&result.file_path));
        if let Some(error) = &result.error {
            errors.push(format!("{}: {}", path.display(), error));
            continue;
        }
        if result.violations.is_empty() {
            continue;
        }
        let source = match input.stdin {
            Some((stdin_path, text)) if stdin_path == path => text.to_string(),
            _ => match std::fs::read_to_string(&path) {
                Ok(text) => text,
                Err(error) => {
                    errors.push(format!("{}: cannot be read to determine positions: {}", path.display(), error));
                    String::new()
                }
            },
        };
        for violation in &result.violations {
            let law = input.settings.law_for_external(&violation.rule_id);
            let info = get_rule_info(&violation.rule_id);
            violations.push(EditorViolation {
                rule: violation.rule_id.clone(),
                law: law.map(|l| l.name.clone()),
                adr: law.and_then(|l| l.adr.clone()),
                severity: violation.severity.into(),
                path: path.to_string_lossy().into_owned(),
                range: line_range(&source, violation.offset),
                message: violation.message.clone(),
                hint: Some(info.fix.to_string()),
                key: None,
                registered: false,
                base_severity: violation.severity.into(),
                standing: crate::project::report::Standing::New,
                level: input.settings.level_of(&violation.rule_id, violation.severity),
                explanation: None,
                source: crate::project::report::FindingOrigin::Linter,
                probability: None,
            });
        }
    }
    for finding in &input.project.findings {
        violations.push(EditorViolation {
            rule: finding.rule.id().to_string(),
            law: finding.law.clone(),
            adr: finding.adr.clone(),
            severity: finding.severity.into(),
            path: finding.path.to_string_lossy().into_owned(),
            range: finding.range,
            message: finding.message.clone(),
            hint: Some(finding.hint.clone()),
            key: Some(finding.key.clone()),
            registered: finding.registered,
            base_severity: finding.base_severity.into(),
            standing: finding.standing,
            level: input.settings.level_of(finding.rule.id(), finding.base_severity),
            explanation: Some(finding.explanation.clone()),
            source: finding.origin,
            probability: finding.probability,
        });
    }
    if let Some(only) = input.only {
        violations.retain(|v| only.iter().any(|p| Path::new(&v.path).starts_with(p)));
    }
    let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
    for v in &violations {
        *counts.entry(v.path.as_str()).or_default() += 1;
    }
    let modules = input
        .project
        .modules
        .iter()
        .filter(|m| input.only.is_none_or(|only| only.iter().any(|p| m.path.starts_with(p))))
        .map(|m| {
            let path = m.path.to_string_lossy().into_owned();
            EditorModule {
                violations: counts.get(path.as_str()).copied().unwrap_or(0),
                path,
                layer: m.layer.clone(),
                context: m.context.clone(),
                role: m.role.clone(),
                layer_reason: m.layer_reason.clone(),
                service: m.service.clone(),
            }
        })
        .collect();
    EditorReport {
        version: EDITOR_CONTRACT_VERSION,
        linter: EditorLinter { version: env!("CARGO_PKG_VERSION").to_string(), commit: crate::build_info::BUILD_COMMIT.to_string() },
        root: input.root.to_string_lossy().into_owned(),
        layers: layer_list(input.settings),
        architecture: input.settings.architecture.clone(),
        semantic: input.project.semantic.clone(),
        violations,
        signatures: input.signatures.map(|s| s.signatures.clone()).unwrap_or_default(),
        bindings: input.signatures.map(|s| s.bindings.clone()).unwrap_or_default(),
        rewrites: input.signatures.map(|s| s.rewrites.clone()).unwrap_or_default(),
        bodies: input.signatures.map(|s| s.bodies.clone()).unwrap_or_default(),
        modules,
        rules: rule_list(input),
        judged_rules: judged_rule_ids(input),
        errors,
        new_critical: None,
        out_of_scope: None,
    }
}

/// The IDs of the rules judged in this run (in dictionary order). Python per-statement rules are decided per file, so in both a whole run and a single-file run it is all the enabled ones, plus
/// the noqa notice (NOQA001) that can always be emitted when they run. Layer rules are those judged by the project run (`ProjectReport::judged`).
fn judged_rule_ids(input: &EditorInput) -> Vec<String> {
    let noqa = (!input.python_rules.is_empty()).then(|| crate::noqa::NOQA_RULE_ID.to_string());
    let project = input.project.judged.iter().map(|rule| rule.id().to_string());
    let ids: BTreeSet<String> = input.python_rules.iter().cloned().chain(noqa).chain(project).collect();
    ids.into_iter().collect()
}

/// The layer order and descriptions (empty if there are no layer settings).
fn layer_list(settings: &ProjectSettings) -> Vec<EditorLayer> {
    settings
        .layers
        .iter()
        .flat_map(|layers| layers.layers.iter())
        .map(|layer| EditorLayer {
            name: layer.name.clone(),
            summary: layer.description.summary.clone(),
            knows: layer.description.knows.clone(),
            does_not_know: layer.description.does_not_know.clone(),
            question: layer.description.question.clone(),
        })
        .collect()
}

/// The list of rules that ran (per law if there is a law statement), and laws with no detector.
fn rule_list(input: &EditorInput) -> Vec<EditorRule> {
    let mut rules = Vec::new();
    for id in input.python_rules {
        // Python per-statement rules (DOEFF001 to 031). Layer rule IDs never get mixed in here, so the family is fixed to python.
        let info = get_rule_info(id);
        match input.settings.law_for_external(id) {
            Some(law) => rules.push(EditorRule {
                rule: id.clone(),
                adr: law.adr.clone(),
                statement: law_statement(law),
                wired: true,
                title: info.label.to_string(),
                family: RuleFamily::Python,
            }),
            None => rules.push(EditorRule {
                rule: id.clone(),
                adr: None,
                statement: info.description.to_string(),
                wired: true,
                title: info.label.to_string(),
                family: RuleFamily::Python,
            }),
        }
    }
    for rule in input.project_rules {
        let wired = input.project_wired.contains(rule);
        let title = rule.label().to_string();
        let family = rule.family();
        let laws: Vec<_> =
            input.settings.laws.iter().filter(|law| law.rules.contains(&ProjectRuleOrExternal::Project(*rule))).collect();
        if laws.is_empty() {
            rules.push(EditorRule {
                rule: rule.id().to_string(),
                adr: None,
                statement: rule.statement().to_string(),
                wired,
                title: title.clone(),
                family,
            });
        }
        for law in laws {
            rules.push(EditorRule {
                rule: rule.id().to_string(),
                adr: law.adr.clone(),
                statement: law_statement(law),
                wired,
                title: title.clone(),
                family,
            });
        }
    }
    for law in input.settings.laws.iter().filter(|law| law.rules.is_empty()) {
        rules.push(EditorRule {
            rule: law.name.clone(),
            adr: law.adr.clone(),
            statement: law_statement(law),
            wired: false,
            title: "A rule with no automatic check yet".to_string(),
            family: RuleFamily::Law,
        });
    }
    rules
}

/// The law statement (the law's name if statement is empty).
fn law_statement(law: &crate::project::law::LawSpec) -> String {
    if law.statement.is_empty() {
        law.name.clone()
    } else {
        format!("{}: {}", law.name, law.statement)
    }
}

/// Make a path the sole form used for output and reconciliation — make it absolute and then normalize it (resolving symlinks and `..`). If the file does not exist yet,
/// normalize the parent dir and join the name. If both fail, the path is just made absolute.
pub fn normalize_path(path: &Path) -> PathBuf {
    let absolute = absolute(path);
    if let Ok(real) = absolute.canonicalize() {
        return real;
    }
    match (absolute.parent().and_then(|parent| parent.canonicalize().ok()), absolute.file_name()) {
        (Some(parent), Some(name)) => parent.join(name),
        _ => absolute,
    }
}

/// Make a path absolute (if relative to cwd, put cwd in front and remove `./`).
pub fn absolute(path: &Path) -> PathBuf {
    let joined = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().map(|cwd| cwd.join(path)).unwrap_or_else(|_| path.to_path_buf())
    };
    joined.components().filter(|c| !matches!(c, std::path::Component::CurDir)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::project::law::LawSpec;
    use crate::project::report::ProjectReport;

    /// Assemble the minimal `EditorInput` needed to call `rule_list`.
    fn build_input<'a>(
        python_rules: &'a [String],
        project_rules: &'a BTreeSet<ProjectRule>,
        project_wired: &'a BTreeSet<ProjectRule>,
        project: &'a ProjectReport,
        settings: &'a ProjectSettings,
    ) -> EditorInput<'a> {
        EditorInput {
            root: Path::new("/repo"),
            python: &[],
            stdin: None,
            project,
            settings,
            python_rules,
            project_rules,
            project_wired,
            only: None,
            signatures: None,
        }
    }

    #[test]
    fn judged_rules_name_python_rules_noqa_and_the_project_judged_rules_only() {
        // judged_rules contains only the Python rules (and NOQA001) and the rules judged by the project run — DOEFF166, which is not judged in a single-file run,
        // is not listed even when enabled (it stays in rules[] as an enabled rule).
        let python_rules = vec!["DOEFF016".to_string()];
        let project_rules: BTreeSet<ProjectRule> = [ProjectRule::DefnForbidden, ProjectRule::RegistryEntryStale].into_iter().collect();
        let project_wired = project_rules.clone();
        let project = ProjectReport { judged: [ProjectRule::DefnForbidden, ProjectRule::UnreadableFile].into_iter().collect(), ..ProjectReport::default() };
        let settings = ProjectSettings::default();
        let input = build_input(&python_rules, &project_rules, &project_wired, &project, &settings);

        let report = build(&input);

        assert_eq!(report.judged_rules, vec!["DOEFF016", "DOEFF110", "DOEFF128", "NOQA001"]);
        assert!(report.rules.iter().any(|r| r.rule == "DOEFF166"), "rules[] stays the list of enabled rules");
        // Without Python rules, NOQA001 is not emitted either (lint_source is not run).
        let input = build_input(&[], &project_rules, &project_wired, &project, &settings);
        assert_eq!(build(&input).judged_rules, vec!["DOEFF110", "DOEFF128"]);
        // In JSON it is the top-level field judged_rules.
        let json = serde_json::to_value(build(&input)).unwrap();
        assert_eq!(json["judged_rules"], serde_json::json!(["DOEFF110", "DOEFF128"]));
    }

    #[test]
    fn rule_list_has_title_and_family_for_layer_python_and_law_entries() {
        let python_rules = vec!["DOEFF001".to_string()];
        let mut project_rules = BTreeSet::new();
        project_rules.insert(ProjectRule::DefnForbidden); // DOEFF110
        let project_wired = BTreeSet::new();
        let project = ProjectReport::default();
        let mut settings = ProjectSettings::default();
        settings.laws.push(LawSpec {
            name: "no-detector-law".to_string(),
            adr: None,
            statement: "".to_string(),
            rules: Vec::new(), // a law with no rules
            layers: BTreeSet::new(),
        });
        let input = build_input(&python_rules, &project_rules, &project_wired, &project, &settings);

        let rules = rule_list(&input);

        let python_rule = rules.iter().find(|r| r.rule == "DOEFF001").expect("DOEFF001 is not in rules");
        assert_eq!(python_rule.title, "Variable name shadows a builtin");
        assert_eq!(python_rule.family, RuleFamily::Python);

        let layer_rule = rules.iter().find(|r| r.rule == "DOEFF110").expect("DOEFF110 is not in rules");
        assert_eq!(layer_rule.title, "uses defn");
        assert_eq!(layer_rule.family, RuleFamily::Definition);

        let law_rule = rules.iter().find(|r| r.rule == "no-detector-law").expect("the law with no detector is not in rules");
        assert_eq!(law_rule.title, "A rule with no automatic check yet");
        assert_eq!(law_rule.family, RuleFamily::Law);

        // In JSON, title and family also appear as fields (for all three kinds).
        let json = serde_json::to_value(&rules).unwrap();
        for id in ["DOEFF001", "DOEFF110", "no-detector-law"] {
            let entry = json.as_array().unwrap().iter().find(|v| v["rule"] == id).unwrap();
            assert!(entry.get("title").is_some(), "{} has no title", id);
            assert!(entry.get("family").is_some(), "{} has no family", id);
        }
        assert_eq!(json.as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF110").unwrap()["family"], "definition");
        assert_eq!(json.as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF001").unwrap()["family"], "python");
        assert_eq!(json.as_array().unwrap().iter().find(|v| v["rule"] == "no-detector-law").unwrap()["family"], "law");
    }
}
