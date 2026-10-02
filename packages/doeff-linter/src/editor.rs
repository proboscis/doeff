//! エディタ向けの出力 `--output-format editor-json`(契約 = docs/SPECIFICATION.md 2 節・版 2)の形と、その組み立て。
//!
//! linter が規則の判定の唯一の正本で、エディタ(doeff-runner)はこの JSON を表示するだけ。Python の文ごとの規則
//! (DOEFF001〜031)と層の規則(DOEFF101〜108)の違反を 1 つの形に揃える。位置は 0 始まりの行・UTF-16 の列。
//!
//! `rules` の各項目には短い日本語の名(`title`)と規則の家族(`family`)を持たせる — エディタが規則ごとの行に
//! 名を添え、家族で行の絵を変えるため。名と家族の正本は linter 側(層の規則は `project::rule::ProjectRule`・
//! Python の規則は `rule_info::RuleInfo`)にあり、エディタは写しを持たない。版は上げない(欄の追加だけ — 契約の更新 6)。
//!
//! 一番上の `judged_rules` は、この実行で判じた規則の ID(契約の更新 8・agora-redesign #2163)。1 file の実行(`--stdin`)は repo 全体で
//! だけ判じる規則(DOEFF166・141 など — `ProjectRule::judged_on_one_file` が偽の規則)を走らせないので、エディタは 1 file の結果で
//! この列の規則の違反だけを差し替え、ほかの規則の違反は全体の実行の結果のまま残す。版は上げない(欄の追加 — 欄の無い古い出力を読む
//! エディタは今までどおり全部差し替え、版を上げると版 2 だけを読む古いエディタが出力の全体を捨てる)。

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

/// 契約の版。形を変える時は契約と一緒に上げる。版 2 = defk / deff の見出し `signatures` と束縛の型 `bindings` を足した
/// (agora-redesign #849 — エディタが型・effect・tags を読むだけの表示で描くため)。
pub const EDITOR_CONTRACT_VERSION: u32 = 2;

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
    /// 登録簿と照合中で下げる前の、規則そのものの重さ(`severity` はこれを下げた後)。エディタが「手つかずの重い破れ」を数える材料。
    pub base_severity: EditorSeverity,
    /// 新しい破れ(new)・登録簿に載った既知の破れ(registered)・照合中で下げた(reconciling)。
    pub standing: crate::project::report::Standing,
    /// 規則の重大さ(repo の宣言 `rules.<ID>.level`、無ければ base_severity から)。登録簿で下げない。
    pub level: crate::project::rule::RuleLevel,
    /// これは何か・なぜ違反か・law の文(層の規則だけ。Python の文ごとの規則は null)。
    pub explanation: Option<Explanation>,
    /// 判定の出どころ(linter = 決定的な規則・jev = Jev の意味の判定)。
    pub source: crate::project::report::FindingOrigin,
    /// Jev の判定の確率(Jev の違反だけ・他は null)。
    pub probability: Option<f64>,
}

/// 地図の材料の module 1 つ。
#[derive(Debug, Clone, Serialize)]
pub struct EditorModule {
    pub path: String,
    pub layer: Option<String>,
    pub context: Option<String>,
    pub role: Option<String>,
    pub violations: usize,
    /// 層を何で決めたか(path の置き場所・タグ・両方の食い違い)。
    pub layer_reason: Option<String>,
    /// 置き場の `*` の段に当たった service の名(層が先の形なら null)。
    pub service: Option<String>,
}

/// 層の説明 1 件(設定 `[tool.doeff-linter.layers.describe.<層>]` から。設定に無い欄は null)。
#[derive(Debug, Clone, Serialize)]
pub struct EditorLayer {
    pub name: String,
    pub summary: Option<String>,
    pub knows: Option<String>,
    pub does_not_know: Option<String>,
    pub question: Option<String>,
}

/// 有効な規則(と、ADR に在って針の無い law)の 1 件。この実行で判じたかは `EditorReport::judged_rules` が名乗る(#2163)。
#[derive(Debug, Clone, Serialize)]
pub struct EditorRule {
    pub rule: String,
    pub adr: Option<String>,
    pub statement: String,
    pub wired: bool,
    /// 短い日本語の名(違反の形。エディタが規則ごとの見出しに使う)。契約の更新 6。
    pub title: String,
    /// 規則の家族(エディタが違反の欄の行の絵を選ぶ閉じた集合)。契約の更新 6。
    pub family: RuleFamily,
}

/// この出力を作った binary(版と、組んだ doeff の commit — 置き場の binary が古いかを外から見分けるため・agora-redesign #848)。
#[derive(Debug, Clone, Serialize)]
pub struct EditorLinter {
    pub version: String,
    pub commit: String,
}

/// 出力の全体。
#[derive(Debug, Clone, Serialize)]
pub struct EditorReport {
    pub version: u32,
    /// 出力を作った binary(契約の版 1 への追加の欄 — 拡張の読み込みは知らない一番上の欄を読み飛ばす)。
    pub linter: EditorLinter,
    pub root: String,
    /// 層の順(外の世界から遠い順)と説明。
    pub layers: Vec<EditorLayer>,
    /// 意味の規則(Jev)の要約 — model・通信の形・判定済み / 未判定の数・今回撃った数と費用・較正の結果。設定が無ければ null。
    pub semantic: Option<crate::project::semantic::SemanticSummary>,
    /// architecture.hy の宣言(service の一覧 — name・dir・description・depends_on・layers と、層の宣言)。無ければ null。
    pub architecture: Option<crate::project::architecture::Architecture>,
    pub violations: Vec<EditorViolation>,
    /// `--stdin` の file の defk / deff の見出し(版 2・全体の実行では空)。
    pub signatures: Vec<crate::project::signatures::Signature>,
    /// `--stdin` の file の束縛(`<-`・val・var・setv・:=)の型(版 2・全体の実行では空)。
    pub bindings: Vec<crate::project::signatures::Binding>,
    /// `--stdin` の file の定義の本体の呼びを `f(a, b)` の形で見せる表示の置き換え(版 2 への欄の追加・全体の実行では空・17 節)。
    pub rewrites: Vec<crate::project::call_view::Rewrite>,
    /// `--stdin` の file の定義ごとの本体の文字の行(読む面が描く — 版 2 への欄の追加・全体の実行では空・20 節)。
    pub bodies: Vec<crate::project::body_view::Body>,
    pub modules: Vec<EditorModule>,
    pub rules: Vec<EditorRule>,
    /// この実行で判じた規則の ID(辞書順・重ねない — 契約の更新 8・agora-redesign #2163)。当たりを出し切った規則で、0 件の規則も載る。
    /// `rules[]` の欄(`judged`)にしないのは、`rules[]` が有効な規則の一覧で、その外で出る違反の規則(noqa の知らせ NOQA001・enable に
    /// 無くても出す DOEFF100 / DOEFF128)を持たないため — 判じたかは実行の性質で、report の一番上で名乗る。`violations` の規則はどれも
    /// この列に入る。
    pub judged_rules: Vec<String>,
    pub errors: Vec<String>,
    /// `--baseline-report` の時: 基点に無い critical の識別子(`<path>::<規則>::<名>`・辞書順)。基点と比べない時は null
    /// (仕様 1 節「基点との比べ」— 版は上げない欄の追加・agora-redesign #1803)。
    pub new_critical: Option<Vec<String>>,
    /// path を名指した全体の実行の時: 名指した Hy の file のうち linter が歩く範囲(根の下)の外の物(名指しの綴りの下の path・辞書順)。
    /// 名指しの無い実行と `--stdin` は null(仕様 1 節「名指しの範囲の外」— 版は上げない欄の追加・agora-redesign #2821)。
    pub out_of_scope: Option<Vec<String>>,
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
    /// `--stdin` の Hy の file の見出しと束縛(版 2)。
    pub signatures: Option<&'a crate::project::file_view::FileSignatures>,
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

/// この実行で判じた規則の ID(辞書順)。Python の文ごとの規則は file 1 つで決まるので、全体の実行でも 1 file の実行でも有効な物の全部と、
/// それを走らせる時に必ず出しうる noqa の知らせ(NOQA001)。層の規則は project の実行が判じた物(`ProjectReport::judged`)。
fn judged_rule_ids(input: &EditorInput) -> Vec<String> {
    let noqa = (!input.python_rules.is_empty()).then(|| crate::noqa::NOQA_RULE_ID.to_string());
    let project = input.project.judged.iter().map(|rule| rule.id().to_string());
    let ids: BTreeSet<String> = input.python_rules.iter().cloned().chain(noqa).chain(project).collect();
    ids.into_iter().collect()
}

/// 層の順と説明(層の設定が無ければ空)。
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

/// 走らせた規則の一覧(law の文があれば law ごと)と、針の無い law。
fn rule_list(input: &EditorInput) -> Vec<EditorRule> {
    let mut rules = Vec::new();
    for id in input.python_rules {
        // Python の文ごとの規則(DOEFF001〜031)。層の規則の ID がここに紛れることは無いので家族は python 固定。
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
            title: "自動の判定がまだ無い決まり".to_string(),
            family: RuleFamily::Law,
        });
    }
    rules
}

/// law の文(statement が空なら law の名)。
fn law_statement(law: &crate::project::law::LawSpec) -> String {
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::project::law::LawSpec;
    use crate::project::report::ProjectReport;

    /// `rule_list` を呼ぶための最小の `EditorInput` を組み立てる。
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
        // agora-redesign #2163: judged_rules は Python の規則(と NOQA001)と project の実行が判じた規則だけ — 1 file の実行で判じない
        // DOEFF166 は有効でも載らない(rules[] には有効な規則として載ったまま)。
        let python_rules = vec!["DOEFF016".to_string()];
        let project_rules: BTreeSet<ProjectRule> = [ProjectRule::DefnForbidden, ProjectRule::RegistryEntryStale].into_iter().collect();
        let project_wired = project_rules.clone();
        let project = ProjectReport { judged: [ProjectRule::DefnForbidden, ProjectRule::UnreadableFile].into_iter().collect(), ..ProjectReport::default() };
        let settings = ProjectSettings::default();
        let input = build_input(&python_rules, &project_rules, &project_wired, &project, &settings);

        let report = build(&input);

        assert_eq!(report.judged_rules, vec!["DOEFF016", "DOEFF110", "DOEFF128", "NOQA001"]);
        assert!(report.rules.iter().any(|r| r.rule == "DOEFF166"), "rules[] は有効な規則の一覧のまま");
        // Python の規則が無ければ NOQA001 も出ない(lint_source を走らせない)。
        let input = build_input(&[], &project_rules, &project_wired, &project, &settings);
        assert_eq!(build(&input).judged_rules, vec!["DOEFF110", "DOEFF128"]);
        // JSON では一番上の欄 judged_rules。
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
            name: "no-針-law".to_string(),
            adr: None,
            statement: "".to_string(),
            rules: Vec::new(), // 規則を持たない law
            layers: BTreeSet::new(),
        });
        let input = build_input(&python_rules, &project_rules, &project_wired, &project, &settings);

        let rules = rule_list(&input);

        let python_rule = rules.iter().find(|r| r.rule == "DOEFF001").expect("DOEFF001 が rules に無い");
        assert_eq!(python_rule.title, "変数名が builtin を隠す");
        assert_eq!(python_rule.family, RuleFamily::Python);

        let layer_rule = rules.iter().find(|r| r.rule == "DOEFF110").expect("DOEFF110 が rules に無い");
        assert_eq!(layer_rule.title, "defn を使っている");
        assert_eq!(layer_rule.family, RuleFamily::Definition);

        let law_rule = rules.iter().find(|r| r.rule == "no-針-law").expect("針の無い law が rules に無い");
        assert_eq!(law_rule.title, "自動の判定がまだ無い決まり");
        assert_eq!(law_rule.family, RuleFamily::Law);

        // JSON でも title・family が欄として出る(3 種類とも)。
        let json = serde_json::to_value(&rules).unwrap();
        for id in ["DOEFF001", "DOEFF110", "no-針-law"] {
            let entry = json.as_array().unwrap().iter().find(|v| v["rule"] == id).unwrap();
            assert!(entry.get("title").is_some(), "{} に title が無い", id);
            assert!(entry.get("family").is_some(), "{} に family が無い", id);
        }
        assert_eq!(json.as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF110").unwrap()["family"], "definition");
        assert_eq!(json.as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF001").unwrap()["family"], "python");
        assert_eq!(json.as_array().unwrap().iter().find(|v| v["rule"] == "no-針-law").unwrap()["family"], "law");
    }
}
