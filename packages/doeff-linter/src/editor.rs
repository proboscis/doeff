//! エディタ向けの出力 `--output-format editor-json`(契約 lint-contract-v1.md 版 1)の形と、その組み立て。
//!
//! linter が規則の判定の唯一の正本で、エディタ(doeff-runner)はこの JSON を表示するだけ。Python の文ごとの規則
//! (DOEFF001〜031)と層の規則(DOEFF101〜108)の違反を 1 つの形に揃える。位置は 0 始まりの行・UTF-16 の列。

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::models::{LintResult, Severity};
use crate::position::{line_range, Range};
use crate::project::rule::ProjectRule;
use crate::project::settings::{ProjectRuleOrExternal, ProjectSettings};
use crate::project::ProjectReport;
use crate::rule_info::get_rule_info;

/// 契約の版。形を変える時は契約と一緒に上げる。
pub const EDITOR_CONTRACT_VERSION: u32 = 1;

/// 違反の重さ(契約の閉じた集合)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum EditorSeverity {
    Error,
    Warning,
    Info,
}

impl From<Severity> for EditorSeverity {
    /// linter の重さを契約の綴りへ写す。
    fn from(severity: Severity) -> Self {
        match severity {
            Severity::Error => EditorSeverity::Error,
            Severity::Warning => EditorSeverity::Warning,
            Severity::Info => EditorSeverity::Info,
        }
    }
}

/// 違反 1 件。
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
}

/// 地図の材料の module 1 つ。
#[derive(Debug, Clone, Serialize)]
pub struct EditorModule {
    pub path: String,
    pub layer: Option<String>,
    pub context: Option<String>,
    pub role: Option<String>,
    pub violations: usize,
}

/// 走らせた規則(と、ADR に在って針の無い law)の 1 件。
#[derive(Debug, Clone, Serialize)]
pub struct EditorRule {
    pub rule: String,
    pub adr: Option<String>,
    pub statement: String,
    pub wired: bool,
}

/// 出力の全体。
#[derive(Debug, Clone, Serialize)]
pub struct EditorReport {
    pub version: u32,
    pub root: String,
    pub violations: Vec<EditorViolation>,
    pub modules: Vec<EditorModule>,
    pub rules: Vec<EditorRule>,
    pub errors: Vec<String>,
}

impl EditorReport {
    /// 新しい破れ(error)があるか — 終了コード 1 の条件。登録簿に載った破れ(warning)と info は数えない。
    pub fn has_errors(&self) -> bool {
        self.violations.iter().any(|v| v.severity == EditorSeverity::Error)
    }
}

/// 組み立てに要る入力。
pub struct EditorInput<'a> {
    pub root: &'a Path,
    /// Python の規則の結果(file の path は cwd からの相対か絶対)。
    pub python: &'a [LintResult],
    /// 保存前の内容(`--stdin` の時の 1 file — path は絶対)。
    pub stdin: Option<(&'a Path, &'a str)>,
    pub project: &'a ProjectReport,
    pub settings: &'a ProjectSettings,
    pub python_rules: &'a [String],
    pub project_rules: &'a BTreeSet<ProjectRule>,
    /// 規則ごとに節が在って判定がつながっているか(層の規則だけ)。
    pub project_wired: &'a BTreeSet<ProjectRule>,
    /// 違反を出す file を絞る(None なら全部)。
    pub only: Option<&'a [PathBuf]>,
}

/// 出力を組み立てる。
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
                    errors.push(format!("{}: 位置を求めるために読めない: {}", path.display(), error));
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
            }
        })
        .collect();
    EditorReport {
        version: EDITOR_CONTRACT_VERSION,
        root: input.root.to_string_lossy().into_owned(),
        violations,
        modules,
        rules: rule_list(input),
        errors,
    }
}

/// 走らせた規則の一覧(law の文があれば law ごと)と、針の無い law。
fn rule_list(input: &EditorInput) -> Vec<EditorRule> {
    let mut rules = Vec::new();
    for id in input.python_rules {
        match input.settings.law_for_external(id) {
            Some(law) => rules.push(EditorRule { rule: id.clone(), adr: law.adr.clone(), statement: law_statement(law), wired: true }),
            None => rules.push(EditorRule { rule: id.clone(), adr: None, statement: get_rule_info(id).description.to_string(), wired: true }),
        }
    }
    for rule in input.project_rules {
        let wired = input.project_wired.contains(rule);
        let laws: Vec<_> =
            input.settings.laws.iter().filter(|law| law.rules.contains(&ProjectRuleOrExternal::Project(*rule))).collect();
        if laws.is_empty() {
            rules.push(EditorRule { rule: rule.id().to_string(), adr: None, statement: rule.statement().to_string(), wired });
        }
        for law in laws {
            rules.push(EditorRule { rule: rule.id().to_string(), adr: law.adr.clone(), statement: law_statement(law), wired });
        }
    }
    for law in input.settings.laws.iter().filter(|law| law.rules.is_empty()) {
        rules.push(EditorRule { rule: law.name.clone(), adr: law.adr.clone(), statement: law_statement(law), wired: false });
    }
    rules
}

/// law の文(statement が空なら law の名)。
fn law_statement(law: &crate::project::settings::LawSpec) -> String {
    if law.statement.is_empty() {
        law.name.clone()
    } else {
        format!("{}: {}", law.name, law.statement)
    }
}

/// path を出力と照合の唯一の形にする — 絶対にしてから正規化する(symlink と `..` を解く)。file がまだ無ければ
/// 親の dir を正規化して名前をつなぐ。どちらも失敗したら絶対にしただけの path。
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

/// path を絶対にする(cwd からの相対なら cwd を前に付け、`./` を外す)。
pub fn absolute(path: &Path) -> PathBuf {
    let joined = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().map(|cwd| cwd.join(path)).unwrap_or_else(|_| path.to_path_buf())
    };
    joined.components().filter(|c| !matches!(c, std::path::Component::CurDir)).collect()
}
