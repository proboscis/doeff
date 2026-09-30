//! 層の規則(DOEFF101〜108)— repo の module の一覧と設定を見て判じる規則。Hy と Python の両方の file を読む。
//!
//! 規則の中身は設定(`[tool.doeff-linter.layers]` ほか・`settings.rs`)から受け取り、層の名前・role・環境の語を
//! Rust に書き込まない。Hy の事実は doeff-indexer の hy-index の解析(関数として呼ぶ)と読み取り器から取る。
//!
//! 流れ: 母集団の file を集める → file ごとに事実を読む(`facts.rs`)→ 規則ごとに違反の下書きを作る →
//! law と登録簿の鍵を当てて重さを決める(`finish`)。

pub mod architecture;
pub mod explain;
pub mod facts_cache;
pub mod facts;
pub mod hy_files;
pub mod names;
pub mod param_calls;
pub mod registry;
pub mod notice;
pub mod rule;
pub mod bare_calls;
pub mod defn_to_defk;
pub mod semantic;
pub mod smells;
pub mod unreadable;
pub mod settings;
pub mod signatures;
pub mod call_view;
pub mod body_view;
pub mod world_catalog;
pub mod test_forms;
pub mod single_point_vocabulary;
pub mod spelling_scope;
pub mod confined_spellings;
pub mod counted_spellings;
pub mod effect_census;
pub mod field_holders;
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
pub mod intent_coverage;
pub mod assembly_shape;
pub mod invariants;
pub mod clause_coverage;

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::{self, Definition, DefinitionKind, HyFileIndex, RawCatalog, RawSettings, RawStrength};
use rayon::prelude::*;
use walkdir::WalkDir;

use crate::models::Severity;
use crate::position::{first_line_range, LineIndex, Position, Range};
use explain::{Explain, Explanation, NameSubject, Narrator, Placement};
use facts::{read_facts, ByteSpan, Language, ModuleFacts};
use names::{environment_words_of, hy_mangle, is_upper_name, module_of};
use registry::Registry;
use rule::ProjectRule;
use settings::{EnvironmentSettings, LawSpec, LayerId, LayerSettings, ProjectSettings};

/// 違反 1 件(law と登録簿を当てた後)。
#[derive(Debug, Clone)]
pub struct Finding {
    pub rule: ProjectRule,
    pub law: Option<String>,
    pub adr: Option<String>,
    pub severity: Severity,
    /// file の絶対の path。
    pub path: PathBuf,
    /// repo の根からの path(区切り `/`)。
    pub rel: String,
    pub range: Range,
    pub message: String,
    pub hint: String,
    /// 登録簿の鍵 `<path>::<law か規則の ID>[::<細目>]`。
    pub key: String,
    /// 登録簿に鍵が載っているか(照合中の規則でも載っていれば真)。
    pub registered: bool,
    /// 登録簿と照合中を当てる前の、規則そのものの重さ(`severity` はこれを下げた後の重さ)。
    pub base_severity: Severity,
    /// 新しい破れか・登録簿に載った既知の破れか・照合中で下げたか。
    pub standing: Standing,
    /// これは何か・なぜ違反か・law の文(explain.rs が作る)。
    pub explanation: Explanation,
    /// 判定の出どころ(決定的な規則か Jev か)。
    pub origin: FindingOrigin,
    /// Jev の判定の確率(Jev の違反だけ)。
    pub probability: Option<f64>,
}

/// 違反の立場 — 重さを下げた理由の閉じた集合(エディタが「手つかずの重い破れ」を数えるため)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Standing {
    /// 登録簿に無い新しい破れ(重さは規則そのもの)。
    New,
    /// 登録簿に載った既知の破れ(重さは下げてある)。
    Registered,
    /// 照合中の規則の破れ(`registry.reconciling` — 登録簿の有無によらず info に下げてある)。
    Reconciling,
}

impl Standing {
    /// 登録簿の有無と照合中かから立場を決める(照合中が先 — 重さを info に下げた理由はそちら)。
    pub fn of(registered: bool, reconciling: bool) -> Standing {
        match (reconciling, registered) {
            (true, _) => Standing::Reconciling,
            (false, true) => Standing::Registered,
            (false, false) => Standing::New,
        }
    }
}

/// 違反の判定の出どころ。
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum FindingOrigin {
    /// 決定的な規則。
    Linter,
    /// Jev の意味の判定。
    Jev,
}

/// 地図の材料 — 層の規則が読んだ module 1 つ。
#[derive(Debug, Clone)]
pub struct ModuleSummary {
    pub path: PathBuf,
    pub rel: String,
    pub layer: Option<String>,
    pub context: Option<String>,
    pub role: Option<String>,
    /// 層を何で決めたか(path の置き場所・タグ・両方の食い違い)。
    pub layer_reason: Option<String>,
    /// 置き場の `*` の段に当たった service の名(無ければ None)。
    pub service: Option<String>,
}

/// 層の規則の結果の全部。
#[derive(Debug, Clone, Default)]
pub struct ProjectReport {
    pub findings: Vec<Finding>,
    pub modules: Vec<ModuleSummary>,
    /// 読めなかった file・登録簿・目録の理由。
    pub errors: Vec<String>,
    /// 誤りではない知らせ(無い登録簿の dir を空として読んだ — agora-redesign #1732)。
    pub notes: Vec<String>,
    /// 意味の規則の要約(設定が無ければ None)。
    pub semantic: Option<semantic::SemanticSummary>,
    /// 1 file の実行で組んだ effect の推論の表(組んだ時だけ・書いた file の中身を overlay にした物)。
    pub world: Option<signatures::World>,
}

/// 何を判じるか — repo 全体か、保存前の内容の 1 file。
pub enum Target<'a> {
    /// repo 全体。focus は命令の行で名指した path(None = 全部)— file 1 つで判じられる規則(DOEFF150・151)は、名指しが在れば
    /// その下の file だけを読み、repo 全体を読まない(agora-redesign #1193 — 全体を読む規則が commit を止めた DOEFF133 の再発を防ぐ)。
    /// ほかの規則は今までどおり全体を読み、出力を名指しの path で絞る。
    Whole { focus: Option<&'a [PathBuf]> },
    Single { path: PathBuf, source: &'a str },
}

/// 違反の下書き(law と登録簿を当てる前)。
struct Draft {
    rule: ProjectRule,
    layer: Option<LayerId>,
    rel: String,
    path: PathBuf,
    range: Range,
    message: String,
    /// 鍵の `<規則>` の後ろの細目(無ければ None)。
    detail: Option<String>,
    base: Severity,
    /// 説明の材料。
    explain: Explain,
}

/// 母集団の file 1 つ。
#[derive(Debug, Clone)]
struct SourceFile {
    rel: String,
    path: PathBuf,
    language: Language,
}

/// 層の母集団の file 1 つ。
#[derive(Debug, Clone)]
struct LayerFile {
    file: SourceFile,
    module: String,
    site: ModuleSite,
}

/// module の置き場 — 層と、当たった置き場の実際の dir と、`*` の段に当たった service の名。
#[derive(Debug, Clone, PartialEq, Eq)]
struct ModuleSite {
    layer: LayerId,
    dir: String,
    service: Option<String>,
    /// 層を path ではなくタグの role から推したか(architecture.hy の宣言した置き場所の外の module)。
    by_tags: bool,
}

/// 層の規則を走らせる。root は正規化した repo の根、enabled は有効な規則。
pub fn run(root: &Path, settings: &ProjectSettings, enabled: &BTreeSet<ProjectRule>, target: Target) -> ProjectReport {
    run_with(root, settings, enabled, target, &semantic::SemanticMode::CacheOnly)
}

/// 層の規則を走らせる(意味の規則をどう扱うかを選べる — 既定の `run` は cache を読むだけ)。
pub fn run_with(root: &Path, settings: &ProjectSettings, enabled: &BTreeSet<ProjectRule>, target: Target, semantic_mode: &semantic::SemanticMode) -> ProjectReport {
    let semantic_files: Vec<(LayerFile, Option<String>)>;
    let plain_files: Vec<(SourceFile, Option<String>)>;
    // hy-index の file(生の副作用の証拠つき)— DOEFF119 と、DOEFF119 が何も出さない class だけを問う DOEFF204 が同じ物を読む。
    let mut indexes: HashMap<String, HyFileIndex> = HashMap::new();
    let mut report = ProjectReport::default();
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
    let raw = raw_settings(root, settings, &mut report.errors);
    let mut semantic_probes: Vec<SemanticProbe> = Vec::new();
    let wants_raw = enabled.contains(&ProjectRule::RawSideEffectDirect)
        || enabled.contains(&ProjectRule::RawSideEffectVia)
        || enabled.contains(&ProjectRule::WorldHandlerNamedOutsideList)
        || enabled.contains(&ProjectRule::WorldHandlerMisplaced);
    let mut drafts = Vec::new();

    // 読めない Hy の file の知らせ(DOEFF128)の材料 — 全体なら repo の Hy の file の全部、1 file ならその保存前の中身。
    // 登録簿の当たらない行(DOEFF166)は repo 全体を当てた時だけ判じる(名指しの file だけ・1 file の実行では、当たる所見が範囲の外に在りうる)。
    let whole_repo = matches!(&target, Target::Whole { focus: None });
    let unreadable_target: Option<(PathBuf, String)> = match &target {
        Target::Whole { .. } => None,
        Target::Single { path, source } => Some((path.clone(), source.to_string())),
    };
    match target {
        Target::Whole { focus } => {
            if let Some(architecture) = &settings.architecture {
                let (found, errors) = crate::timing::timed("retired", || judge_retired_files(root, architecture, enabled, focus));
                drafts.extend(found);
                report.errors.extend(errors);
                // DOEFF141 は宣言した定義の module と、届いた先の module の file だけを読む(名指しに関わらず小さい — repo 全体は読まない)。
                if enabled.contains(&ProjectRule::BlindDefinitionReads) && !architecture.blind_definitions.is_empty() {
                    let architecture_rel = relative_path(root, &architecture.path).unwrap_or_else(|| "architecture.hy".to_string());
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
                                    format!("{} から届く定義 {} が決めた材料の外の語 {} を読む", found.declared, reached, word)
                                }
                                blind::BlindProblem::Imports { module } => format!("{} の module が {} を import する", found.declared, module),
                                blind::BlindProblem::Missing { reason } => format!("宣言した定義 {} が無い — {}", found.declared, reason),
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
                // DOEFF147 は宣言した定義の module の file だけを読む(名指しに関わらず小さい — repo 全体は読まない)。
                if enabled.contains(&ProjectRule::DefinitionCallsUnlistedHead) && !architecture.allowed_heads.is_empty() {
                    let architecture_rel = relative_path(root, &architecture.path).unwrap_or_else(|| "architecture.hy".to_string());
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
                                    format!("{} の中で呼んでよい頭の一覧の外の ({} …) を呼ぶ", found.declared, head)
                                }
                                allowed_heads::HeadProblem::Missing { reason } => format!("宣言した定義 {} が無い — {}", found.declared, reason),
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
                // DOEFF148 は群の :files の glob の頭の dir だけを歩く(repo 全体は読まない)。
                if enabled.contains(&ProjectRule::SpellingOutsideItsFiles) && !architecture.confined_spellings.is_empty() {
                    let architecture_rel = relative_path(root, &architecture.path).unwrap_or_else(|| "architecture.hy".to_string());
                    let (found, errors) = crate::timing::timed("confined-spellings", || {
                        confined_spellings::find(root, &architecture.confined_spellings, &architecture_rel, focus)
                    });
                    drafts.extend(found.into_iter().map(|found| {
                        let (message, detail) = match &found.problem {
                            confined_spellings::ConfinedProblem::Outside { count } => (
                                format!("{} — 綴りの群 {} を書いてよい file の外に {} か所", found.rel, found.group, count),
                                found.group.clone(),
                            ),
                            confined_spellings::ConfinedProblem::Missing => (
                                format!("{} — 綴りの群 {} の :files に当たる file が無い", found.rel, found.group),
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
                            explain: Explain::ConfinedSpelling { group: found.group, why: found.why, problem: found.problem },
                            range: found.range,
                            rel: found.rel,
                        }
                    }));
                    report.errors.extend(errors);
                }
                // DOEFF161 も宣言の :files の glob の頭の dir だけを歩く(repo 全体は読まない)。
                if enabled.contains(&ProjectRule::SpellingCountDiffers) && !architecture.counted_spellings.is_empty() {
                    let architecture_rel = relative_path(root, &architecture.path).unwrap_or_else(|| "architecture.hy".to_string());
                    let (found, errors) = crate::timing::timed("counted-spellings", || {
                        counted_spellings::find(root, &architecture.counted_spellings, &architecture_rel)
                    });
                    drafts.extend(found.into_iter().map(|found| {
                        let message = match &found.problem {
                            counted_spellings::CountProblem::Mismatch { found: count, wanted, within: Some(name) } => format!(
                                "{} — 数を決めた綴り {} が定義 {} の中に {} か所({} のはず)",
                                found.rel,
                                found.group,
                                name,
                                count,
                                wanted.spelling()
                            ),
                            counted_spellings::CountProblem::Mismatch { found: count, wanted, within: None } => {
                                format!("{} — 数を決めた綴り {} が {} か所({} のはず)", found.rel, found.group, count, wanted.spelling())
                            }
                            counted_spellings::CountProblem::Missing { reason } => {
                                format!("{} — 数を決めた綴り {} の数える所が無い — {}", found.rel, found.group, reason)
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
                // DOEFF162 は宣言の :files の file だけを読む(repo 全体は読まない)。
                if enabled.contains(&ProjectRule::EffectOutsideCensus) && !architecture.effect_census.is_empty() {
                    let architecture_rel = relative_path(root, &architecture.path).unwrap_or_else(|| "architecture.hy".to_string());
                    let (found, errors) =
                        crate::timing::timed("effect-census", || effect_census::find(root, &architecture.effect_census, &architecture_rel));
                    drafts.extend(found.into_iter().map(|found| {
                        let what = match &found.problem {
                            effect_census::CensusProblem::Unlisted { effect } => format!("effect {} が一覧 {} の外で宣言されている", effect, found.group),
                            effect_census::CensusProblem::Twice { effect } => format!("effect {} が 2 度宣言されている(一覧 {})", effect, found.group),
                            effect_census::CensusProblem::Undeclared { effect } => format!("一覧 {} の effect {} の宣言が無い", found.group, effect),
                            effect_census::CensusProblem::NoFiles => format!("一覧 {} の :files に当たる file が無い", found.group),
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
                // DOEFF149 も宣言の :files の file だけを読む(repo 全体は読まない)。
                if enabled.contains(&ProjectRule::FieldHoldersDiffer) && !architecture.field_holders.is_empty() {
                    let architecture_rel = relative_path(root, &architecture.path).unwrap_or_else(|| "architecture.hy".to_string());
                    let (found, errors) =
                        crate::timing::timed("field-holders", || field_holders::find(root, &architecture.field_holders, &architecture_rel));
                    drafts.extend(found.into_iter().map(|found| {
                        let what = match &found.problem {
                            field_holders::HolderProblem::Unlisted { class } => {
                                format!("class {} が {} の欄を持つ(持ち手の一覧 {} の外)", class, found.type_name, found.group)
                            }
                            field_holders::HolderProblem::Absent { class } => {
                                format!("持ち手の一覧 {} の class {} が {} の欄を持たない", found.group, class, found.type_name)
                            }
                            field_holders::HolderProblem::NoClass { class } => format!("持ち手の一覧 {} が名指す class {} が無い", found.group, class),
                            field_holders::HolderProblem::NoFiles => format!("持ち手の一覧 {} の :files に当たる Python の file が無い", found.group),
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
                // DOEFF159 は :files の glob の字義どおりの頭の dir だけを歩く(名指しに関わらず小さい — repo 全体は歩かない)。
                if enabled.contains(&ProjectRule::CallOutsideDeclaredSites) && !architecture.call_sites.is_empty() {
                    let architecture_rel = relative_path(root, &architecture.path).unwrap_or_else(|| "architecture.hy".to_string());
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
                // DOEFF160 も :files の glob の頭の dir だけを歩く(repo 全体は歩かない)。
                if enabled.contains(&ProjectRule::BroadCatchOutsideCarrier) && !architecture.broad_catches.is_empty() {
                    let architecture_rel = relative_path(root, &architecture.path).unwrap_or_else(|| "architecture.hy".to_string());
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
                // DOEFF144・145 も file 1 つで判じる(名指しが在ればその下だけを読む — repo 全体の索引を組まない)。
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
            let env_files = settings.environment.as_ref().map(|env| collect_environment_files(root, env)).unwrap_or_default();
            indexes = crate::timing::timed("hy-index", || whole_hy_index(root, settings, enabled, &raw, &layer_files, &env_files, wants_raw));
            let hy = &indexes;
            let effect_world = crate::timing::timed("effect-world", || effect_world_for(root, settings, enabled, None));
            if let Some(layers) = &settings.layers {
                let index = module_index(&layer_files);
                let judged: Vec<LayerJudgement> = crate::timing::timed("layer-judge", || layer_files
                    .par_iter()
                    .map(|file| match std::fs::read_to_string(&file.file.path) {
                        Ok(source) => judge_layer_file(file, &source, layers, settings, enabled, &index, hy.get(&file.file.rel)),
                        Err(error) => LayerJudgement { errors: vec![format!("{}: 読めない: {}", file.file.rel, error)], ..LayerJudgement::default() },
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
                    if enabled.contains(&ProjectRule::TranslationEmitsIntent) {
                        let judged: Vec<Result<Vec<Draft>, String>> = layer_files
                            .par_iter()
                            .filter(|file| is_translation_file(file, translation))
                            .map(|file| {
                                std::fs::read_to_string(&file.file.path)
                                    .map(|source| judge_translation_intents(file, &source, layers, translation, &index, world))
                                    .map_err(|error| format!("{}: 読めない: {}", file.file.rel, error))
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
                        drafts.extend(judge_test_kinds(root, architecture, hy));
                    }
                    if enabled.contains(&ProjectRule::WorldHandlerWithoutContractTest) && !architecture.world_handlers.is_empty() {
                        drafts.extend(crate::timing::timed("contract-tests", || judge_contract_tests(root, architecture, hy)));
                    }
                    let fake_rules = [
                        ProjectRule::BusinessEffectFake,
                        ProjectRule::TestOnlyFake,
                        ProjectRule::IntentAnswererNotTranslation,
                        ProjectRule::ServiceWithoutCounterexample,
                        ProjectRule::ClauseWithoutCounterexample,
                        ProjectRule::IntentEffectUncovered,
                    ];
                    if let Some(decl) = architecture.business_fakes.as_ref().filter(|_| fake_rules.iter().any(|r| enabled.contains(r))) {
                        let (found, problems) = crate::timing::timed("business-fakes", || {
                            judge_business_fakes(root, architecture, Some(layers), decl, architecture.assembly_shape.as_ref(), hy, enabled)
                        });
                        drafts.extend(found);
                        report.errors.extend(problems);
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
                        drafts.extend(judge_untested_services(architecture, hy).into_iter().map(|d| Draft { path: root.join(&d.rel), ..d }));
                    }
                    if enabled.contains(&ProjectRule::ServiceInvariantsMissing) {
                        drafts.extend(judge_service_invariants(root, architecture, hy));
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
                                message: format!("{} — テストの形 {}({})— テストは deftest だけ", found.rel, found.form, found.detail),
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
                                message: format!("{} — 語彙 {} が判定の 1 点の外に {} 行(#146) — {}", hit.rel, hit.group, hit.count, hit.instead),
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
                    let files: Vec<SourceFile> = hy_files::collect(root)
                        .into_iter()
                        .filter_map(|path| {
                            let rel = relative_path(root, &path)?;
                            (is_definition_file(&rel, definitions) || is_test_file(&rel, definitions))
                                .then_some(SourceFile { rel, path, language: Language::Hy })
                        })
                        // 名指しが在れば、その下の file だけを判じる — 判定は file 1 つ(と全体の表)で決まり、出力は名指しの path で絞られる
                        // (main の project_results と同じ述語)。全部を判じると、1 file の commit の hook で CPU 2.4 秒を使っていた
                        // (agora-redesign #1418)。
                        .filter(|file| focus.is_none_or(|only| only.iter().any(|p| file.path.starts_with(p))))
                        .collect();
                    let judged: Vec<Result<Vec<Draft>, String>> = crate::timing::timed("definitions-judge", || files
                        .par_iter()
                        .map(|file| {
                            std::fs::read_to_string(&file.path)
                                .map(|source| {
                                    let mut found = judge_definitions(file, &source, definitions, enabled, plain_callable_reasons(settings), hy.get(&file.rel));
                                    found.extend(judge_smells(file, &source, settings, definitions, enabled, &failure));
                                    found.extend(judge_bare_calls(file, &source, definitions, enabled, &defks, &program_params));
                                    if enabled.contains(&ProjectRule::EffectsDisagreeWithInference) {
                                        found.extend(judge_effect_mismatches(file, &source, definitions, effect_world.as_ref()));
                                    }
                                    found
                                })
                                .map_err(|error| format!("{}: 読めない: {}", file.rel, error))
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
                                .map_err(|error| format!("{}: 読めない: {}", file.rel, error))
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
                            .map_err(|error| format!("{}: 読めない: {}", rel, error))
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
                            Err(error) => report.errors.push(format!("{}: 読めない: {}", file.rel, error)),
                        }
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
            // 根の中の file は、全体の実行と同じく「根 + 根からの path」を出す(エディタが結果を差し替える鍵を揃えるため)。
            let path = rel.as_ref().map(|r| root.join(r)).unwrap_or(path);
            let wants_index = wants_raw
                || enabled.contains(&ProjectRule::EnvironmentName)
                || enabled.contains(&ProjectRule::ClassWithBehaviour)
                || enabled.contains(&ProjectRule::SemanticClassRole);
            let hy_file = match (language_of(&path), wants_index) {
                (Some(Language::Hy), true) => hy_index::index_stdin_source(root, &path, source, &raw).files.into_iter().next(),
                _ => None,
            };
            if let (Some(index), Some(rel)) = (&hy_file, &rel) {
                indexes.insert(rel.clone(), index.clone());
            }
            let effect_world = crate::timing::timed("effect-world", || rel.as_ref().and_then(|rel| effect_world_for(root, settings, enabled, Some((rel.as_str(), source)))));
            if let (Some(layers), Some(rel)) = (&settings.layers, &rel) {
                if let Some((site, language)) = classify_layer_file(rel, layers).or_else(|| infer_layer_site(rel, source, layers)) {
                    let file = LayerFile { file: SourceFile { rel: rel.clone(), path: path.clone(), language }, module: module_of(rel), site };
                    let mut layer_files = crate::timing::timed("layer-files", || collect_layer_files(root, layers));
                    layer_files.push(file.clone());
                    let index = module_index(&layer_files);
                    let judged = judge_layer_file(&file, source, layers, settings, enabled, &index, hy_file.as_ref());
                    drafts.extend(judged.drafts);
                    if let (Some(translation), Some(world)) = (&settings.translation, &effect_world) {
                        if enabled.contains(&ProjectRule::TranslationEmitsIntent) && is_translation_file(&file, translation) {
                            drafts.extend(judge_translation_intents(&file, source, layers, translation, &index, world));
                        }
                    }
                    report.modules.extend(judged.summary);
                    report.errors.extend(judged.errors);
                }
            }
            if let (Some(architecture), Some(layers), Some(rel)) = (&settings.architecture, &settings.layers, &rel) {
                if is_architecture_file(rel, architecture, layers) {
                    let file = SourceFile { rel: rel.clone(), path: path.clone(), language: language_of(&path).unwrap_or(Language::Hy) };
                    drafts.extend(judge_places(root, architecture, layers, &[file], enabled, PlaceScope::Single, Some(source)));
                }
            }
            if let (Some(definitions), Some(rel)) = (&settings.definitions, &rel) {
                if wants_definitions(enabled)
                    && language_of(&path) == Some(Language::Hy)
                    && (is_definition_file(rel, definitions) || is_test_file(rel, definitions))
                {
                    let file = SourceFile { rel: rel.clone(), path: path.clone(), language: Language::Hy };
                    drafts.extend(judge_definitions(&file, source, definitions, enabled, plain_callable_reasons(settings), hy_file.as_ref()));
                    let failure = crate::timing::timed("failure-types", || failure_types_for(root, enabled, &definitions.tags));
                    drafts.extend(judge_smells(&file, source, settings, definitions, enabled, &failure));
                    let defks = crate::timing::timed("defk-names", || defk_names_for(root, enabled));
                    let program_params = crate::timing::timed("program-params", || program_params_for(root, enabled, &defks));
                    drafts.extend(judge_bare_calls(&file, source, definitions, enabled, &defks, &program_params));
                    if enabled.contains(&ProjectRule::EffectsDisagreeWithInference) {
                        drafts.extend(judge_effect_mismatches(&file, source, definitions, effect_world.as_ref()));
                    }
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
                let (word_hits, call_hits) = retired::judge(rel, source, words, calls);
                drafts.extend(retired_drafts(&path, source, word_hits, call_hits));
                if enabled.contains(&ProjectRule::UntypedStructuredValue) && architecture.typed_values.as_ref().is_some_and(|s| typed_values::wants(rel, s)) {
                    match typed_values::judge(rel, source) {
                        Ok(hits) => drafts.extend(typed_value_drafts(rel, &path, source, hits)),
                        Err(reason) => report.errors.push(format!("{}: DOEFF144 の判定が読めない({})", rel, reason)),
                    }
                }
                if let Some(selection) = architecture.record_stubs.as_ref().filter(|_| enabled.contains(&ProjectRule::RecordStubNotKwOnly)) {
                    if record_stubs::wants(rel, &path, selection) {
                        match record_stubs::judge_file(rel, &path, source) {
                            Ok(found) => drafts.extend(record_stub_drafts(rel, &path, source, found)),
                            Err(reason) => report.errors.push(format!("{}: DOEFF145 の判定が読めない({})", rel, reason)),
                        }
                    } else if let Some((stub_rel, stub_path)) = record_stubs::stub_of_hy(rel, &path, selection) {
                        // 保存前の .hy は stdin の中身で、隣の型の宣言(.pyi)は disk から読み、当たりは .pyi の path で出す。
                        let judged = std::fs::read_to_string(&stub_path)
                            .map_err(|error| error.to_string())
                            .and_then(|stub| record_stubs::judge(source, &stub, &stub_rel).map(|found| (stub, found)));
                        match judged {
                            Ok((stub, found)) => drafts.extend(record_stub_drafts(&stub_rel, &stub_path, &stub, found)),
                            Err(reason) => report.errors.push(format!("{}: DOEFF145 の判定が読めない({})", stub_rel, reason)),
                        }
                    }
                }
            }
            if let (Some(env), Some(rel)) = (&settings.environment, &rel) {
                if enabled.contains(&ProjectRule::EnvironmentName) && is_environment_file(rel, env) {
                    if let Some(language) = language_of(&path) {
                        let file = SourceFile { rel: rel.clone(), path: path.clone(), language };
                        drafts.extend(judge_environment_names(&file, source, env, hy_file.as_ref()));
                    }
                }
            }
            if let (Some(decl), Some(rel)) = (settings.architecture.as_ref().and_then(|a| a.handler_arguments.as_ref()), &rel) {
                if enabled.contains(&ProjectRule::HandlerArgumentHoldsState) && handler_arguments::in_population(rel, decl) {
                    let classes = crate::timing::timed("handler-argument-classes", || handler_arguments::class_index(root, Some((rel.as_str(), source))));
                    drafts.extend(judge_handler_arguments(root, rel, source, decl, &classes));
                }
            }
            // 同じ根・同じ overlay で組んだ表を返す — editor の見出しと束縛(版 2)が作り直さずに使う(1 回で 2 度組んでいた・#1033)。
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
                marker: settings.definitions.as_ref().map(|d| d.deff_reason_marker.clone()).unwrap_or_else(|| "defk にできない:".to_string()),
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
    // 索引(repo 全体の Hy・数百 MB)の後片づけは別の thread で — 実行の終わりを待たせない(1 file の commit の hook で 0.07 秒・
    // agora-redesign #1418)。process が先に終われば片づけは OS が持つ。
    crate::timing::timed("drop-index", || {
        std::thread::spawn(move || drop(indexes));
    });
    if whole_repo && enabled.contains(&ProjectRule::RegistryEntryStale) {
        let stale = crate::timing::timed("stale-registry", || stale_registry_drafts(root, settings, enabled, &registry, &drafts));
        drafts.extend(stale);
    }
    let labels = crate::timing::timed("labels", || judged_labels(root, settings, &mut report.errors));
    let (findings, dropped) = crate::timing::timed("finish", || finish(drafts, settings, &registry, &labels.false_positives));
    report.findings = findings;
    if let Some(summary) = report.semantic.as_mut() {
        summary.false_positives = dropped;
        summary.labeled = labeled_summary(settings, &labels, &semantic_probes);
    }
    // 読めない Hy の file は、有効な規則の一覧に関わらず知らせる(違反が欠けているのを黙らせない — DOEFF128)。
    report.findings.extend(unreadable_findings(root, settings, &unreadable_target));
    report
}

/// 有効な規則の分の群(DOEFF150 が無効なら語の群は空・DOEFF151 が無効なら呼びの群は空)。
fn retired_groups<'a>(architecture: &'a architecture::Architecture, enabled: &BTreeSet<ProjectRule>) -> (&'a [architecture::RetiredWords], &'a [architecture::RetiredCalls]) {
    let words: &[architecture::RetiredWords] = if enabled.contains(&ProjectRule::RetiredWord) { &architecture.retired_words } else { &[] };
    let calls: &[architecture::RetiredCalls] = if enabled.contains(&ProjectRule::RetiredCall) { &architecture.retired_calls } else { &[] };
    (words, calls)
}

/// DOEFF150・151 を全体の実行で判じる(focus が在ればその下の file だけを読む)。
fn judge_retired_files(root: &Path, architecture: &architecture::Architecture, enabled: &BTreeSet<ProjectRule>, focus: Option<&[PathBuf]>) -> (Vec<Draft>, Vec<String>) {
    let (words, calls) = retired_groups(architecture, enabled);
    if words.is_empty() && calls.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let (found, errors) = retired::find(root, words, calls, focus);
    let drafts = found.into_iter().flat_map(|file| retired_drafts(&file.path, &file.source, file.words, file.calls)).collect();
    (drafts, errors)
}

/// DOEFF144 の当たりを違反の下書きにする(file 1 つ分 — 鍵の細目は `<種類>:<名>`)。
fn typed_value_drafts(rel: &str, path: &Path, source: &str, hits: Vec<typed_values::TypedHit>) -> Vec<Draft> {
    let lines = LineIndex::new(source);
    hits.into_iter()
        .map(|hit| Draft {
            rule: ProjectRule::UntypedStructuredValue,
            layer: None,
            path: path.to_path_buf(),
            range: lines.range(hit.start, hit.end),
            message: format!("{} — {} {} の型が {}(欄の名前と型を持つ型で表す)", rel, hit.what.label(), hit.name, hit.problem),
            detail: Some(hit.detail()),
            base: Severity::Error,
            explain: Explain::UntypedStructuredValue { what: hit.what.label().to_string(), name: hit.name, problem: hit.problem },
            rel: rel.to_string(),
        })
        .collect()
}

/// DOEFF145 の食い違いを違反の下書きにする(.pyi 1 つ分 — 鍵の細目は class の名)。
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
                "{} — class {} は同じ名の .hy で {}(実行時は欄を名でしか受けない)なのに、型の宣言の @dataclass に kw_only=True が無い",
                rel, m.name, m.form
            ),
            detail: Some(m.name.clone()),
            base: Severity::Error,
            explain: Explain::RecordStubNotKwOnly { class: m.name, form: m.form.to_string() },
            rel: rel.to_string(),
        })
        .collect()
}

/// 当たりを違反の下書きにする(file 1 つ分)。
fn retired_drafts(path: &Path, source: &str, words: Vec<retired::WordHit>, calls: Vec<retired::CallHit>) -> Vec<Draft> {
    let lines = LineIndex::new(source);
    let mut out: Vec<Draft> = words
        .into_iter()
        .map(|hit| {
            let what = match (&hit.place, &hit.name) {
                (architecture::WordPlace::Paths, _) => format!("file の名に使わないと決めた綴り {}", hit.spelling),
                (_, Some(name)) => format!("定義の名 {} に使わないと決めた綴り {}", name, hit.spelling),
                (_, None) => format!("使わないと決めた綴り {}", hit.spelling),
            };
            Draft {
                rule: ProjectRule::RetiredWord,
                layer: None,
                path: path.to_path_buf(),
                range: lines.range(hit.start, hit.end),
                message: format!("{} — {}(群 {}・代わり: {})", hit.rel, what, hit.group, hit.instead),
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
        message: format!("{} — 使わないと決めた呼び ({} …)(群 {}・代わり: {})", hit.rel, hit.call, hit.group, hit.instead),
        detail: Some(hit.call.clone()),
        base: Severity::Error,
        explain: Explain::RetiredCall { group: hit.group, call: hit.call, instead: hit.instead },
        rel: hit.rel,
    }));
    out
}

/// 目録(同梱の物と、設定の追加)を読む。追加が読めなければ理由を積んで同梱の物だけで判じる。
fn raw_settings(root: &Path, settings: &ProjectSettings, errors: &mut Vec<String>) -> RawSettings {
    let bundled = match RawCatalog::bundled() {
        Ok(catalog) => catalog,
        Err(reason) => {
            errors.push(format!("生の副作用の目録を読めない: {}", reason));
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
                    errors.push(format!("raw_side_effects.catalog_extra {} を読めない: {}", extra, reason));
                    RawSettings { catalog: bundled, problems: Vec::new() }
                }
            }
        }
    }
}

/// 全体の実行の Hy の索引(repo の根からの path → file の索引)。経由の証拠(DOEFF107)が要る時は根の全体を、
/// そうでなければ判じる file だけを索引する。
fn whole_hy_index(
    root: &Path,
    settings: &ProjectSettings,
    enabled: &BTreeSet<ProjectRule>,
    raw: &RawSettings,
    layer_files: &[LayerFile],
    env_files: &[SourceFile],
    wants_raw: bool,
) -> HashMap<String, HyFileIndex> {
    let wants_env = enabled.contains(&ProjectRule::EnvironmentName) && settings.environment.is_some();
    // DOEFF119 は業務の file の class の生の副作用(経由も)を見るので、全体の索引を作る。
    let wants_classes = (enabled.contains(&ProjectRule::ClassWithBehaviour) || enabled.contains(&ProjectRule::SemanticClassRole)) && settings.definitions.is_some();
    // DOEFF133 はテストの file を含む全体の索引で、テストから定義を辿る。
    // 許可名簿を書いた repo は、層の置き場の外の file にも DOEFF106・131 を当てる(#1147)ので、全体の索引を作る。
    // DOEFF136 は模擬の環境の deftest から service の entry の層の定義へ届くかを見る。
    let wants_tests = ((enabled.contains(&ProjectRule::BusinessEffectFake)
        || enabled.contains(&ProjectRule::TestOnlyFake)
        || enabled.contains(&ProjectRule::IntentAnswererNotTranslation)
        || enabled.contains(&ProjectRule::ServiceWithoutCounterexample)
        || enabled.contains(&ProjectRule::ClauseWithoutCounterexample)
        || enabled.contains(&ProjectRule::IntentEffectUncovered)
        || enabled.contains(&ProjectRule::AssemblyShapeBroken)
        || enabled.contains(&ProjectRule::AssemblyAnswerMisplaced))
        && settings.architecture.as_ref().is_some_and(|a| a.business_fakes.is_some()))
        || ((enabled.contains(&ProjectRule::TestKindMismatch) || enabled.contains(&ProjectRule::WorldHandlerWithoutContractTest))
            && settings.architecture.as_ref().is_some_and(|a| a.edge_mark.is_some()))
        || (enabled.contains(&ProjectRule::ServiceUntestedOnSim) && settings.architecture.as_ref().is_some_and(|a| a.verification_environment.is_some()))
        || (enabled.contains(&ProjectRule::ServiceInvariantsMissing) && settings.architecture.is_some())
        || (settings.raw.as_ref().is_some_and(|r| r.world_modules.is_some())
            && (enabled.contains(&ProjectRule::RawSideEffectDirect) || enabled.contains(&ProjectRule::WorldHandlerNamedOutsideList)));
    if !(wants_raw && settings.raw.is_some()) && !wants_env && !wants_classes && !wants_tests {
        return HashMap::new();
    }
    // hy_index::index_root / index_paths と同じ組み方(集めた順に file ごとに読み、生の副作用を注記する)— ただし file ごとの読みを
    // cache から引く(cached_hy_files)。経由の辿りは他の file の中身と目録で答えが変わるので毎回組む。
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
    let mut files = crate::timing::timed("hy-index.read", || cached_hy_files(root, &paths));
    crate::timing::timed("hy-index.annotate", || hy_index::annotate_raw(&mut files, &raw.catalog, via));
    files.into_iter().filter_map(|file| relative_path(root, Path::new(&file.path)).map(|rel| (rel, file))).collect()
}

/// Hy の file の索引(生の副作用の注記の前)を、file ごとの cache(facts_cache の種類 "hy-index"・binary の形)を通して読むため
/// (agora-redesign #1364 — 1 file の実行でも repo 全体の索引を組むので、変わった file だけ読み直す)。答えは cache の無い
/// hy_index::index_file と同じ — cache の形が契約の JSON の形では運べない欄も持つ(hy_index::CachedHyFile)。欄の数が合わない
/// 壊れた cache はその file を読み直す。鍵は facts_cache と同じ(file の大きさと更新時刻・linter の binary の印と版)で、file の path を
/// 鍵の名にする。
fn cached_hy_files(root: &Path, paths: &[PathBuf]) -> Vec<HyFileIndex> {
    let keyed: Vec<(String, PathBuf)> = paths.iter().map(|path| (path.to_string_lossy().into_owned(), path.clone())).collect();
    facts_cache::per_file_compact(root, "hy-index", &keyed, |_key, path| Some(hy_index::CachedHyFile::of(hy_index::index_file(root, path))))
        .into_iter()
        .map(|cached| cached.into_file().unwrap_or_else(|broken| hy_index::index_file(root, Path::new(&broken.path))))
        .collect()
}

/// path の拡張子から言語を決める(Hy でも Python でもなければ None)。
fn language_of(path: &Path) -> Option<Language> {
    match path.extension().and_then(|e| e.to_str()) {
        Some("py") => Some(Language::Python),
        Some("hy" | "hyk" | "hyp") => Some(Language::Hy),
        _ => None,
    }
}

/// path の repo の根からの相対の綴り(区切り `/`)。根の外なら None。symlink の違いは正規化してもう一度試す。
pub fn relative_path(root: &Path, path: &Path) -> Option<String> {
    let joined = |rel: &Path| rel.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect::<Vec<_>>().join("/");
    if let Ok(rel) = path.strip_prefix(root) {
        return Some(joined(rel));
    }
    let canonical = match path.canonicalize() {
        Ok(p) => p,
        Err(_) => path.parent()?.canonicalize().ok()?.join(path.file_name()?),
    };
    let root = root.canonicalize().ok()?;
    canonical.strip_prefix(&root).ok().map(joined)
}

/// rel が dir の下(または dir そのもの)か。
fn under(rel: &str, dir: &str) -> bool {
    dir.is_empty() || rel == dir || rel.starts_with(&format!("{}/", dir))
}

/// rel が置き場の書き方(dir、または末尾 `*` の前方一致)に合うか。
fn matches_place(rel: &str, place: &str) -> bool {
    match place.strip_suffix('*') {
        Some(prefix) => rel.starts_with(prefix),
        None => under(rel, place),
    }
}

/// 層の母集団の file か — どれかの層の置き場の下で、拡張子が合い、除く区切りを含まない。当たる置き場が 2 つ以上なら
/// 段の多い方(より細かい置き場)を採り、同じなら層の順の先の方。
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

/// 宣言した置き場所の外(architecture.hy の root の下)の module の層を、:role のタグから推す。role を許す層が 1 つならその層、
/// 2 つ以上なら path の段に同じ名の層(層が先の dir)があればそれ。推せなければ None(層の規則の母集団に入らない)。
fn infer_layer_site(rel: &str, source: &str, layers: &LayerSettings) -> Option<(ModuleSite, Language)> {
    let infer_root = layers.infer_root.as_ref()?;
    let language = language_of(Path::new(rel))?;
    let extension = Path::new(rel).extension()?.to_str()?;
    if !rel.starts_with(&format!("{}/", infer_root)) || !layers.extensions.contains(extension) || rel.split('/').any(|part| layers.exclude.contains(part)) {
        return None;
    }
    // 推すのは層が先の dir(root の下の段に層の名がある — controllers/core/… など)の module だけ。旧い機能の dir は層の規則の母集団に入れない
    // (前から入っていなかった物を増やさない)。
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
    // role を許す層が 1 つならその層、2 つ以上なら path の段の層を選ぶ。タグで推せない(role が無い・どの層の役でもない)物は path の段の層に置き、
    // タグの規則(DOEFF104・105)がその理由を出す。
    let (layer, by_tags) = match candidates.as_slice() {
        [only] => (*only, true),
        [] => (by_path, false),
        many => (many.iter().copied().find(|id| *id == by_path).unwrap_or(many[0]), true),
    };
    Some((ModuleSite { layer, dir, service: None, by_tags }, language))
}

/// 層の置き場の下の module を全部集める(層の順、層の中は path の順)。
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
    // 宣言した置き場所の外の module は、タグの role から層を推して母集団に入れる(置き場所の違反は DOEFF114・115 が別に出す)。
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

/// dir の下の file を全部集める(読めない枝は飛ばす・symlink は辿らない)。
fn walk_files(dir: &Path) -> Vec<PathBuf> {
    WalkDir::new(dir).follow_links(false).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()).map(|e| e.into_path()).collect()
}

/// module の綴り → 置き場 の索引(import の先を層と service へ解くため)。
fn module_index(files: &[LayerFile]) -> HashMap<String, ModuleSite> {
    files.iter().map(|f| (f.module.clone(), f.site.clone())).collect()
}

/// 層の module 1 つを判じた結果(違反の下書き・地図の 1 行・読めなかった理由・別の service を読んだ組)。
#[derive(Default)]
struct LayerJudgement {
    drafts: Vec<Draft>,
    summary: Option<ModuleSummary>,
    errors: Vec<String>,
    /// (この file の service, import した先の service) — 宣言したのに使っていない依存(DOEFF117)を見るため。
    crossings: Vec<(String, String)>,
}

/// 層の module 1 つを判じる(違反の下書き・地図の 1 行・読めなかった理由)。
fn judge_layer_file(
    file: &LayerFile,
    source: &str,
    layers: &LayerSettings,
    settings: &ProjectSettings,
    enabled: &BTreeSet<ProjectRule>,
    index: &HashMap<String, ModuleSite>,
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
            let declared = judge.placement.service.as_deref().is_some_and(|s| architecture.service_by_dir(s).is_some());
            let shared = settings::ServiceSettings {
                shared: architecture.shared.iter().cloned().collect(),
                guarded: BTreeSet::new(),
                open: BTreeSet::new(),
                exceptions: BTreeSet::new(),
                check_context: true,
            };
            if let Some(mut draft) = judge.context_matches_service(&facts, &contexts, &shared) {
                if declared {
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
            drafts.extend(judge.context_matches_service(&facts, &contexts, services));
        }
    }
    if let (Some(raw), Some(hy_file)) = (&settings.raw, hy_file) {
        // 許可名簿を書いた repo では名簿の定義の module だけに許す(層では許さない — agora-redesign #1140)。
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

/// 層の module 1 つの判定に要る物をまとめた道具。
struct LayerJudge<'a> {
    file: &'a LayerFile,
    source: &'a str,
    lines: &'a LineIndex<'a>,
    layers: &'a LayerSettings,
    layer: LayerId,
    /// この file の層と、タグで名乗った役(説明の主体)。
    placement: Placement,
}

impl<'a> LayerJudge<'a> {
    /// 層の名前。
    fn layer_name(&self, id: LayerId) -> &'a str {
        &self.layers.layers[id.0].name
    }

    /// byte の範囲をエディタの範囲へ。
    fn range(&self, span: ByteSpan) -> Range {
        self.lines.range(span.start, span.end)
    }

    /// 下書きを 1 つ作る。
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

    /// DOEFF104: タグの無い定義(module の頭のタグも無い)と、何も名乗らない module。
    fn declares_tags(&self, facts: &ModuleFacts) -> Option<Draft> {
        let rel = &self.file.file.rel;
        match (facts.untagged.first(), facts.module_tags.is_some(), facts.tagged.is_empty()) {
            (Some(first), false, _) => {
                let names: Vec<&str> = facts.untagged.iter().map(|n| n.name.as_str()).collect();
                Some(self.draft(
                    ProjectRule::ModuleDeclaresTags,
                    self.range(first.span),
                    format!("{} の定義 {} にタグが無い — 契約の辞書の :tags {{:context … :role …}} か、module の頭のタグで文脈と役を名乗る", rel, names.join("・")),
                    None,
                    Explain::UntaggedDefinitions { placement: self.placement.clone(), names: names.iter().map(|n| n.to_string()).collect() },
                ))
            }
            (None, false, true) => Some(self.draft(
                ProjectRule::ModuleDeclaresTags,
                first_line_range(self.source),
                format!("{} にタグ(:context と :role)が無い — 層の dir の下の module は文脈と役をタグで名乗る", rel),
                None,
                Explain::NoTags { placement: self.placement.clone() },
            )),
            _ => None,
        }
    }

    /// DOEFF105: 実効のタグごとに、role と context があり role がこの層で許される物か。
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
                        "{} の role {} は層 {} に置けない(:context と :role が要る・許す role = {})",
                        self.file.file.rel, role, spec.name, allowed_text
                    ),
                    Some(role),
                    Explain::RoleMismatch { placement: self.placement.clone(), role: t.role.clone(), context: t.context.clone() },
                )
            })
            .collect()
    }

    /// DOEFF103: 型だけの層に関数と handler を置かない。
    fn types_only(&self, facts: &ModuleFacts) -> Option<Draft> {
        let first = facts.functions.first()?;
        let names: Vec<&str> = facts.functions.iter().map(|n| n.name.as_str()).collect();
        Some(self.draft(
            ProjectRule::LayerTypesOnly,
            self.range(first.span),
            format!(
                "{}({})が関数 / handler {} を定める — {} は型だけの層",
                self.file.file.rel,
                self.layer_name(self.layer),
                names.join("・"),
                self.layer_name(self.layer)
            ),
            Some("definitions".to_string()),
            Explain::TypesOnly { placement: self.placement.clone(), functions: names.iter().map(|n| n.to_string()).collect() },
        ))
    }

    /// DOEFF102: この層に禁じた module を直に import しない。import の綴りの前方一致で照らす(同じ綴りか、その下位の module / 名 —
    /// `urllib.request` は `urllib.request.urlopen` に当たり、`urllib.parse` には当たらない)。当たった module ごとに 1 件、位置は最初の import。
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
                    format!("{}({})が module {} を import する — この層では直に import しない(I/O は許された層の handler が持つ)", self.file.file.rel, spec.name, top),
                    Some(top.to_string()),
                    Explain::ForbiddenModule { placement: self.placement.clone(), module: top.to_string() },
                )
            })
            .collect()
    }

    /// DOEFF101 が破れと判じる import の先か — 母集団の module で、この file 自身でなく、層の許した層の外。
    /// DOEFF116 は同じ import を二重に数えないため、これが真の import を判じない(層の向きの直しが先 — #1799)。
    fn breaks_direction(&self, target: &str, index: &HashMap<String, ModuleSite>) -> bool {
        let Some(allowed) = &self.layers.layers[self.layer.0].allowed else { return false };
        resolve_target(target, index).is_some_and(|(owner, site)| owner != self.file.module && !allowed.contains(&site.layer))
    }

    /// DOEFF101: import の先が母集団の module(かその中の名)で、許された層の外なら破れ。
    fn import_direction(&self, facts: &ModuleFacts, index: &HashMap<String, ModuleSite>) -> Vec<Draft> {
        let spec = &self.layers.layers[self.layer.0];
        let Some(allowed) = &spec.allowed else { return Vec::new() };
        let mut first: BTreeMap<&str, ByteSpan> = BTreeMap::new();
        for import in &facts.imports {
            first.entry(import.target.as_str()).or_insert(import.span);
        }
        let allowed_text = allowed.iter().map(|id| self.layer_name(*id)).collect::<BTreeSet<_>>().into_iter().collect::<Vec<_>>().join("・");
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
                        "{}({})が {} の {} を import する — 層 {} が import してよい層は {}",
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

    /// DOEFF109: service の境界 — この file(service A の守る層)が、別の service B の守る層の module を読んだら破れ。
    /// B の開いた層(intent など)と共有の置き場と例外は読んでよい。service を持たない置き場(層が先の形)は判じない。
    fn service_boundary(&self, facts: &ModuleFacts, index: &HashMap<String, ModuleSite>, services: &settings::ServiceSettings) -> Vec<Draft> {
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
                            "{}(service {} の層 {})が service {} の層 {} の {} を import する — 別の service は intent を通して頼む",
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

    /// DOEFF116: service の依存 — この file の service A が別の service B の module を読む時、B は A の :depends-on に在り、
    /// 読む先は、この file の層が依存先で読んでよい層(層の :dependency-layers — 組み立ての entry は intent と protocol —、
    /// 無ければ :open-layers の intent)であること。共有の置き場と foundation は service ではないので見ない。
    /// 別の service を読んだ組は、宣言したのに使っていない依存(DOEFF117)のために返す。
    fn service_dependencies(
        &self,
        facts: &ModuleFacts,
        index: &HashMap<String, ModuleSite>,
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
            // 依存先が既にこの service に依存していれば、:depends-on に足すと service の間の依存が輪になる。
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
                        "{}(service {}・層 {})が依存先 {} の層 {} の {} を読む — 層 {} が依存先で読めるのは {} だけ",
                        self.file.file.rel,
                        own_name,
                        self.layer_name(self.placement.layer),
                        other_name,
                        self.layer_name(site.layer),
                        target,
                        self.layer_name(self.placement.layer),
                        readable.join("・")
                    )
                } else {
                    format!("{}(service {})が :depends-on に無い service {} の {} を読む", self.file.file.rel, own_name, other_name, target)
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

    /// DOEFF140: architecture.hy の :placed-dependencies の層の module(service と shared — 層が先の旧い dir と foundation は外)が、
    /// root の下の層の置き場の外の module を import する。読む先は import の綴りから file を引いて決める(その file か親の module が
    /// root の下に在り、層の索引に無く、package の印でない物)— 渡された file の import と置き場だけで判じ、repo 全体の索引は読まない。
    /// 同じ module を何度読んでも 1 件(最初の import の所)。
    fn placed_dependencies(&self, facts: &ModuleFacts, index: &HashMap<String, ModuleSite>, architecture: &architecture::Architecture) -> Vec<Draft> {
        if self.placement.service.is_none() || !architecture.placed_dependencies.iter().any(|l| l == self.layer_name(self.placement.layer)) {
            return Vec::new();
        }
        let mut repo = self.file.file.path.clone();
        for _ in self.file.file.rel.split('/') {
            repo.pop();
        }
        let root_module = settings::normalize_dir(&architecture.root).replace('/', ".");
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
                        "{}(層 {})が層の置き場の外の module {}({})を import する — 層の置き場へ移した module だけに依存する",
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

    /// DOEFF113: タグの :context と dir の service が食い違う(info)。名の `-` と `_` は同じに見る。共有の置き場は見ない。
    fn context_matches_service(&self, facts: &ModuleFacts, contexts: &[String], services: &settings::ServiceSettings) -> Option<Draft> {
        let own = self.placement.service.as_deref()?;
        if services.shared.contains(own) {
            return None;
        }
        let norm = |text: &str| text.replace('-', "_");
        let mut foreign: Vec<String> = contexts.iter().filter(|c| norm(c) != norm(own)).cloned().collect();
        foreign.dedup();
        let first = foreign.first()?;
        let span = facts.tag_sets().into_iter().find(|t| t.context.as_deref() == Some(first.as_str())).map(|t| t.span);
        let range = span.map(|s| self.range(s)).unwrap_or_else(|| first_line_range(self.source));
        let mut draft = self.draft(
            ProjectRule::ContextMatchesService,
            range,
            format!("{} のタグの :context {} が、置き場の service {} と食い違う", self.file.file.rel, foreign.join("・"), own),
            Some(foreign.join("+")),
            Explain::ContextMismatch { placement: self.placement.clone(), contexts: foreign.clone() },
        );
        draft.base = Severity::Info;
        Some(draft)
    }

    /// DOEFF106: 定義の中の生の副作用の直接の証拠(強い証拠は error、弱い証拠は warning)。入れ子の定義と外の定義で
    /// 同じ証拠が重なる時は、いちばん内側の定義に 1 度だけ数える。
    fn raw_direct(&self, hy_file: &HyFileIndex, raw: &settings::RawSettingsSpec, module: &str) -> Vec<Draft> {
        let definitions = &hy_file.definitions;
        let allowed = match &raw.world_modules {
            Some(_) => "生の副作用に触ってよいのは architecture.hy の :world-handlers(外の世界に触れてよい定義の許可名簿)の定義の module だけ".to_string(),
            None => format!("生の副作用に触ってよい層は {}", raw.allowed.iter().map(|id| self.layer_name(*id)).collect::<Vec<_>>().join("・")),
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
                        "定義 {}({})が生の副作用 {}({})に直に触る — {}",
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

    /// DOEFF131: :wraps に挙げた doeff の実 I/O の handler を、名簿の定義(とその中の入れ子の定義)の外で名指す所。
    /// 値として渡す参照(with-handlers の列)と呼び出しの両方を数え、同じ定義の同じ handler は 1 件にまとめる。
    fn world_handler_named(&self, hy_file: &HyFileIndex, architecture: &architecture::Architecture) -> Vec<Draft> {
        let definitions = &hy_file.definitions;
        world_handler_spots(hy_file, architecture)
            .into_iter()
            .map(|spot| {
                let WorldSpot { owner, spelling, allowed, range } = spot;
                let definition = owner.map(|i| definition_label(&definitions[i])).unwrap_or_else(|| "module の top level".to_string());
                let shown = owner.map(|i| definitions[i].name.clone()).unwrap_or_else(|| "module の top level".to_string());
                self.draft(
                    ProjectRule::WorldHandlerNamedOutsideList,
                    range,
                    format!(
                        "{} が doeff の実 I/O の handler {} を名指す — 名指してよいのは architecture.hy の :world-handlers の定義({})だけ",
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

    /// DOEFF107: 呼ぶ定義を通した生の副作用(info・経路つき)。全体の実行だけが経由の証拠を持つ。
    /// 同じ経路と同じ証拠を外の定義と入れ子の定義の両方が持つ時は内側だけに数え、1 つの定義で経路と証拠の名が同じ物
    /// (同じ関数の中の 2 か所の `.stat` など)は 1 件にまとめる。
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
                        "定義 {} は {} を通して生の副作用 {}({})に届く({})",
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

/// 証拠の位置(file・範囲・名前)— 入れ子の定義と外の定義で重なる証拠を 1 つに見分けるため。
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct EvidenceSpot {
    path: String,
    start: Position,
    end: Position,
    name: String,
}

/// 経由の証拠の位置(経路の定義の名の列と、行き着いた証拠)。
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct ViaSpot {
    through: Vec<String>,
    evidence: EvidenceSpot,
}

/// 経由の知らせ 1 件の単位(定義・経路・証拠の名 — 同じ関数の中の同じ名の証拠は 1 件)。
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct ViaReport {
    definition: usize,
    through: Vec<String>,
    name: String,
}

/// file の索引の中の証拠の在り処(定義の添字と、その定義の証拠の列の添字)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct EvidenceRef {
    definition: usize,
    evidence: usize,
}

/// 鍵に使う定義の綴り(入れ子は `外.内`・mangle 済み)。
fn definition_label(definition: &Definition) -> String {
    match &definition.container {
        Some(container) => format!("{}.{}", hy_mangle(container), hy_mangle(&definition.name)),
        None => hy_mangle(&definition.name),
    }
}

/// import の先を母集団の module へ解く(module そのもの、または `module.名`)。母集団の外なら None。
fn resolve_target<'i, 'x>(target: &'i str, index: &'x HashMap<String, ModuleSite>) -> Option<(&'i str, &'x ModuleSite)> {
    if let Some(site) = index.get(target) {
        return Some((target, site));
    }
    let (owner, _) = target.rsplit_once('.')?;
    index.get(owner).map(|site| (owner, site))
}

/// import の綴りが指す root の下の module(その物か親の module)の綴りと file の path — 無い・root の外・package の印なら None。
/// Hy の綴りの `-` は file の名の `_` に読む。
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

// --- architecture.hy の置き場(DOEFF114・115)と使っていない依存(DOEFF117)---------------

/// 全体か 1 file か(DOEFF115 の dir の知らせを、全体では dir ごとに 1 度、1 file ではその file に出す)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PlaceScope {
    Whole,
    Single,
}

/// root の下の module か(拡張子が層の設定に在り、除く区切りを含まない)。
fn is_architecture_file(rel: &str, architecture: &architecture::Architecture, layers: &LayerSettings) -> bool {
    let root = settings::normalize_dir(&architecture.root);
    let extension_ok = Path::new(rel).extension().and_then(|e| e.to_str()).is_some_and(|e| layers.extensions.contains(e));
    extension_ok && rel.starts_with(&format!("{}/", root)) && !rel.split('/').any(|part| layers.exclude.contains(part))
}

/// root の下の module を全部集める(path の順)。
fn collect_architecture_files(root: &Path, architecture: &architecture::Architecture, layers: &LayerSettings) -> Vec<SourceFile> {
    let mut files: Vec<SourceFile> = walk_files(&root.join(settings::normalize_dir(&architecture.root)))
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

/// file の置き場の宣言との照らし(宣言どおり・宣言されていない置き場所の file・宣言に無い dir)。
enum PlaceVerdict {
    Declared,
    UndeclaredPlace(explain::PlaceProblem),
    UndeclaredDirectory { dir: String, problem: explain::DirectoryProblem },
}

/// file 1 つを宣言に照らす。
fn place_verdict(rel: &str, architecture: &architecture::Architecture) -> PlaceVerdict {
    let root = settings::normalize_dir(&architecture.root);
    let rest = rel.strip_prefix(&format!("{}/", root)).unwrap_or(rel);
    // package の印(`__init__.py`・`__init__.hy`)は置き場の module ではない。
    if Path::new(rel).file_stem().is_some_and(|stem| stem == "__init__") {
        return PlaceVerdict::Declared;
    }
    let parts: Vec<&str> = rest.split('/').collect();
    // 模擬の環境の置き場(:verification-environment)の下は宣言どおり — service ではない置き場。
    if let (Some(place), [first, _, ..]) = (architecture.verification_environment.as_deref(), parts.as_slice()) {
        if *first == place {
            return PlaceVerdict::Declared;
        }
    }
    let service_layers: BTreeSet<&str> =
        architecture.layers.iter().map(|l| l.name.as_str()).filter(|l| architecture.foundation.as_deref() != Some(*l)).collect();
    match parts.as_slice() {
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

/// DOEFF114・115: root の下の module を architecture.hy の宣言に照らす。115 は全体では dir ごとに最初の file に 1 度だけ出す。
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
    // 移し先の案: service は :context のタグ(`-` は `_`)、層は :role のタグから推した層か、推せなければ今の path の段の層の名。
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
        // :role のタグをすべて許す層がちょうど 1 つの時にその層。
        let roles: Vec<String> = facts.tag_sets().iter().filter_map(|t| t.role.clone()).filter(|r| !r.is_empty()).collect();
        let by_roles = || {
            let fitting: Vec<&settings::LayerSpec> = layers
                .layers
                .iter()
                .filter(|l| !roles.is_empty() && roles.iter().all(|role| l.roles.as_ref().is_some_and(|allowed| allowed.contains(role))))
                .collect();
            match fitting.as_slice() {
                [only] => Some(only.name.clone()),
                _ => None,
            }
        };
        // 層は定義の :role から推し(置き場所が誤っているから DOEFF114 が出る — 今の path の段は案にならない)、推せない時だけ path の段の層の名。
        let layer = by_roles().or(by_path).unwrap_or_else(|| "<層>".to_string());
        let name = parts.last().copied().unwrap_or("");
        let root_dir = settings::normalize_dir(&architecture.root);
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
                        format!("{} は宣言されていない置き場所に在る", file.rel),
                        None,
                        Explain::UndeclaredPlace { rel: file.rel.clone(), problem, root: architecture.root.clone(), destination: destination(file) },
                        file.rel.clone(),
                    ));
                }
            }
            PlaceVerdict::UndeclaredDirectory { dir, problem } => {
                // 宣言の外の dir の module も file ごとに出す(エディタで file ごとに見え、移し先の案が file ごとに付く)。
                if enabled.contains(&ProjectRule::UndeclaredPlace) {
                    drafts.push(draft(
                        ProjectRule::UndeclaredPlace,
                        zero_range(),
                        format!("{} は宣言されていない置き場所に在る", file.rel),
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
                        format!("dir {} は architecture.hy に宣言されていない", dir),
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

/// file の頭の長さ 0 の範囲(置き場の違反は file まるごとに掛かる)。
fn zero_range() -> Range {
    let start = Position { line: 0, character: 0 };
    Range { start, end: start }
}

/// DOEFF117(info): 宣言した依存を、その service のどの module も読んでいない。位置は architecture.hy の defservice の名。
fn judge_unused_dependencies(root: &Path, architecture: &architecture::Architecture, crossings: &BTreeSet<(String, String)>) -> Vec<Draft> {
    let rel = relative_path(root, &architecture.path).unwrap_or_else(|| architecture.path.to_string_lossy().into_owned());
    let mut drafts = Vec::new();
    for service in &architecture.services {
        for dependency in &service.depends_on {
            if crossings.contains(&(service.name.clone(), dependency.clone())) {
                continue;
            }
            drafts.push(Draft {
                rule: ProjectRule::UnusedDependency,
                layer: None,
                rel: rel.clone(),
                path: architecture.path.clone(),
                range: service.range,
                message: format!("service {} の :depends-on の {} を、{} のどの module も読んでいない", service.name, dependency, service.name),
                detail: Some(format!("{}>{}", service.name, dependency)),
                base: Severity::Info,
                explain: Explain::UnusedDependency { service: service.name.clone(), dependency: dependency.clone() },
            });
        }
    }
    drafts
}

/// DOEFF132: 許可名簿の定義 1 本ずつ — module が層の置き場に在るか・その層が foundation か・Hy の module なら定義が在るか。
/// 位置は architecture.hy の名簿の要素(全体の実行だけ)。
fn judge_world_handler_places(
    root: &Path,
    architecture: &architecture::Architecture,
    layers: &settings::LayerSettings,
    layer_files: &[LayerFile],
    hy: &HashMap<String, HyFileIndex>,
) -> Vec<Draft> {
    let rel = relative_path(root, &architecture.path).unwrap_or_else(|| architecture.path.to_string_lossy().into_owned());
    let foundation = architecture.foundation.as_deref();
    let mut drafts = Vec::new();
    for handler in &architecture.world_handlers {
        let module = handler.definition.mangled_module();
        let file = layer_files.iter().find(|f| architecture::mangle_dotted(&f.module) == module);
        let problem = match file {
            None => Some(format!("module {} が層の置き場に無い(無い module か、層の外の置き場)", handler.definition.module)),
            Some(file) => {
                let layer = &layers.layers[file.site.layer.0].name;
                if Some(layer.as_str()) != foundation {
                    Some(format!("{} は層 {} に在る — foundation の層にだけ置く", file.file.rel, layer))
                } else {
                    let target = handler.definition.target();
                    match hy.get(&file.file.rel) {
                        Some(index) if !index.definitions.iter().any(|d| d.qualified_name == target) => {
                            Some(format!("{} に定義 {} が無い", file.file.rel, handler.definition.name))
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
                message: format!("許可名簿の定義 {} — {}", spelling, problem),
                detail: Some(spelling.clone()),
                base: Severity::Error,
                explain: Explain::WorldHandlerMisplaced { definition: spelling, problem },
            });
        }
    }
    drafts
}

/// 定義の範囲 inner が outer の中に在るか(同じ範囲は入れ子ではない)。
fn range_inside(inner: &Range, outer: &Range) -> bool {
    outer.start <= inner.start && inner.end <= outer.end && inner != outer
}

/// 位置 spot を含む、いちばん内側の定義の添字(無ければ None)。
fn innermost_definition(definitions: &[Definition], spot: &Range) -> Option<usize> {
    definitions
        .iter()
        .enumerate()
        .filter(|(_, d)| d.full_range.start <= spot.start && spot.end <= d.full_range.end)
        .max_by_key(|(_, d)| d.full_range.start)
        .map(|(index, _)| index)
}

/// 1 つの test file の印の読み(file ごとに Hy の reader で 1 度だけ読む — deftest ごとに読み直さない・agora-redesign #1352)。
/// module の印 = 最上位の `(val pytestmark 値)` / `(setv pytestmark 値)` の値の綴り。deftest の印 = deftest の始まりの行 → :marks の並びの
/// 綴り(doeff-hy の deftest と同じ読み方 — 名の後の fixture の並び `[..]` を 1 つ外し、先頭の文字列を飛ばした最初の dict の :marks)。
/// 註・検の本体の文字列の中の綴りは印ではない(agora-redesign #1279)。defadr などの中に入れ子の deftest も読む。
struct FileMarks {
    module: Vec<String>,
    deftests: HashMap<usize, String>,
    /// 空でない `:interpreters` を持つ deftest の始まりの行(DOEFF137)。
    interpreters: HashSet<usize>,
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
        // 行の頭の byte の位置(form の始まりの行を二分探索で引く)。
        let starts: Vec<usize> = std::iter::once(0).chain(source.match_indices('\n').map(|(at, _)| at + 1)).collect();
        let line_of = |form: &Form| starts.partition_point(|start| *start <= form.span.start).saturating_sub(1);
        let mut deftests = HashMap::new();
        let mut interpreters = HashSet::new();
        let mut stack: Vec<&Form> = forms.iter().collect();
        while let Some(form) = stack.pop() {
            // 入れ子はどの括弧の中にも在る(defadr の :tests [(deftest …) …] の並びの中など)。
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
            // :interpreters の要素(file の外の定数の記号)は読み解かない — 空でない列かだけを見る(DOEFF137)。
            if option(":interpreters").is_some_and(|value| matches!(&value.node, Node::Seq { items, .. } if !items.is_empty())) {
                interpreters.insert(line_of(form));
            }
        }
        FileMarks { module, deftests, interpreters }
    }

    /// テストの定義が空でない `:interpreters` を持つか(縁の検 — 本物と模擬の解釈器に同じ検を通す・DOEFF137)。
    fn runs_interpreters(&self, test: &Definition) -> bool {
        self.interpreters.contains(&(test.full_range.start.line as usize))
    }

    /// テストの定義か module の頭が、縁の印 mark を持つか。印の名は Python の綴り(`real_world`)と Hy の綴り(`real-world`)の両方で読む。
    fn carries(&self, test: &Definition, mark: &str) -> bool {
        let spellings = [mark.to_string(), mark.replace('_', "-")];
        let named = |text: &str| spellings.iter().any(|s| text.contains(&format!("\"{}\"", s)) || text.contains(&format!("mark.{}", s)));
        self.deftests.get(&(test.full_range.start.line as usize)).is_some_and(|marks| named(marks)) || self.module.iter().any(|value| named(value))
    }
}

/// 定義の間の辺(呼び出し・参照・入れ子)の図 — 全体の索引から 1 度だけ組む(DOEFF133・136 が使う)。
struct DefinitionGraph<'h> {
    rels: Vec<&'h String>,
    /// 定義 1 つ = 節 1 つ(file の順・file の中の添字の順)。
    nodes: Vec<(&'h str, usize)>,
    base: HashMap<&'h str, usize>,
    /// world[n] = その定義そのものが外の世界に触れる理由(名簿の定義の綴り・:wraps の handler の綴り・生の I/O の証拠の名)。
    world: Vec<Option<String>>,
    /// callers[n] = n に届く定義(辺の逆向き)。
    callers: Vec<Vec<usize>>,
    /// world_carried[n] = 系の値の中(defsystem の本体・:systems の :carriers の引数)で外の世界に触れる理由(agora-redesign #1390)。
    world_carried: Vec<Option<String>>,
    /// carried[n] = 系の値の中の辺で n に届く定義(系の値として運ぶだけで、この場では回らない辺)。
    carried: Vec<Vec<usize>>,
    /// runs[n] = その定義が系を回す入口(:systems の :runners)を名指す。
    runs: Vec<bool>,
}

fn definition_graph<'h>(architecture: &architecture::Architecture, hy: &'h HashMap<String, HyFileIndex>) -> DefinitionGraph<'h> {
    let listed: HashMap<String, (String, Vec<architecture::WorldTouch>)> =
        architecture.world_handlers.iter().map(|h| (h.definition.target(), (h.definition.spelling(), h.touches.clone()))).collect();
    let catalog = world_catalog::WorldCatalog::bundled();
    let wrapped = architecture.world_targets(catalog);
    let static_readers: BTreeSet<String> = architecture.static_readers.iter().map(|r| r.target()).collect();
    // 系の値を組む物 = :carriers と、索引の defsystem の定義(その呼び出しの引数の土台は系の値として運ばれる)。
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
    // 定義 1 つ = 節 1 つ(file の順・file の中の添字の順)。
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
    // world[n] = その定義そのものが外の世界に触れる理由(名簿の定義の綴り・:wraps の handler の綴り・生の I/O の証拠の名)。
    let mut world: Vec<Option<String>> = vec![None; nodes.len()];
    // callers[n] = n に届く定義(辺の逆向き)。
    let mut callers: Vec<Vec<usize>> = vec![Vec::new(); nodes.len()];
    let mut world_carried: Vec<Option<String>> = vec![None; nodes.len()];
    let mut carried: Vec<Vec<usize>> = vec![Vec::new(); nodes.len()];
    let mut runs: Vec<bool> = vec![false; nodes.len()];
    for rel in &rels {
        let file = &hy[*rel];
        let first = base[rel.as_str()];
        let definitions = &file.definitions;
        for (index, definition) in definitions.iter().enumerate() {
            // 縁に数えるのは :edge-touches の触れる先だけ(名簿の定義はその :touches・生の I/O は証拠の分類で判じる)。
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
        // 入れ子: 外の定義は内の定義の届く先に届く(始まりの順に並べ、開いている定義の stack で親を引く)。
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
        // 読むだけの受け手(:static-readers)の呼び出しの引数の中の参照は、値として読まれるだけで実行されない — 辺にしない。
        let read_only: Vec<&Range> =
            file.calls.iter().filter(|c| c.target.as_deref().is_some_and(|t| static_readers.contains(t))).map(|c| &c.form_range).collect();
        let only_read = |range: &Range| read_only.iter().any(|form| range_inside(range, form));
        // 系の値の中(:carriers の呼び出しの引数・defsystem の本体)の名は、系の値として運ばれるだけの辺(#1390)。
        let carrier_forms: Vec<&Range> =
            file.calls.iter().filter(|c| c.target.as_deref().is_some_and(|t| carriers.contains(t))).map(|c| &c.form_range).collect();
        let in_carrier = |range: &Range| carrier_forms.iter().any(|form| range_inside(range, form));
        // 辺になる名(索引の定義か :wraps の handler)だけを見る — ほかの名の持ち主の定義は引かない。
        let interesting = |t: &str| by_name.contains_key(t) || wrapped.contains_key(t) || runners.contains(t);
        // 値を検めるだけの名指し(比べの form と assert の被演算子 — 索引の `Reference::inspected`)は、名指した値を呼ばず・被せず・渡さないので
        // 辺にしない(agora-redesign #1581)。呼ぶ・with-handlers の列に置く・他の定義の引数に渡す・比べの外で属性を読む名指しは今までどおり辺。
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
            // :systems を宣言しない repo は今までどおり(defsystem の中の辺もふつうの辺 — 入口を知らないので、系の中を別に数えない)。
            let is_carried = architecture.systems.is_some() && (inside_carrier || definitions[owner_index].kind == DefinitionKind::Defsystem);
            if runners.contains(target) {
                runs[owner] = true;
                continue;
            }
            if let Some((spelling, _, touches)) = wrapped.get(target) {
                // 目録の数えない行(:wraps に書けるだけの移行の間の行 — agora-redesign #1318)は外の世界に触れない。
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
    DefinitionGraph { rels, nodes, base, world, callers, world_carried, carried, runs }
}

/// 定義の辺の図の上で、種の節へ届く節(DOEFF133・137 が使う)。reaches[n] = n から種へ届く・toward[n] = n から種へ向かう次の節
/// (None なら n 自身が種)。
struct Reach {
    reaches: Vec<bool>,
    toward: Vec<Option<usize>>,
}

/// 系を回す入口(:systems の :runners)に届く定義(入口を名指す定義から、ふつうの辺を逆向きに辿る)。
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

/// 種の節から逆向きに辿る(DOEFF133 の縁の数えと同じ辿り方)。seeds[n] = n そのものが種・carried_seeds[n] = 系の値の中で n が種に触れる。
/// 2 段: direct = 系の値の中の辺を通らずに届く・via_system = 系の値の中の辺を 1 度でも通って届く(#1390)。届く = 直に届くか、系の値の
/// 中から届き、しかも系を回す入口にも届く(running)。
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

/// DOEFF133: テストの種類を届く先から導く。定義の間の辺(呼び出し・参照・入れ子)を全体の索引から 1 度だけ組み、外の世界の側
/// (名簿の定義・:wraps の handler を名指す定義・強い生の I/O の証拠を持つ定義)から逆向きに辿って「外の世界に届く定義」の集合を
/// 求める。deftest がその集合に在れば縁(:edge-mark の印が要る)、無ければ手元(印を持たない)。Python の検は数えない(R6 で deftest へ)。
fn judge_test_kinds(root: &Path, architecture: &architecture::Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<Draft> {
    let Some(mark) = architecture.edge_mark.as_deref() else { return Vec::new() };
    let graph = definition_graph(architecture, hy);
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
        let Ok(source) = std::fs::read_to_string(root.join(rel.as_str())) else { continue };
        let marks = FileMarks::read(&source);
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
                format!("テスト {} は外の世界に届く(縁 — {})のに印 {} が無い", test.name, reached.join(" → "), mark)
            } else {
                format!("テスト {} は外の世界に届かない(手元)のに印 {} が在る", test.name, mark)
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
    drafts
}

/// DOEFF137: 許可名簿の handler ごとに縁の検が在るかを判じる。縁の検 = 空でない `:interpreters` を持つ deftest のうち、DOEFF133 と
/// 同じ定義の辺の図を辿ってその handler の定義に届く物(`:interpreters` の要素は file の外の定数の記号なので読み解かない)。
/// 理由つきの `:contract-test (none …)` の handler は判じない・理由の無い `none` は縁の検が在っても鳴る。当たりの位置は architecture.hy の
/// 名簿の要素・細目は名簿の綴り(agora-redesign #1363・#1796)。
fn judge_contract_tests(root: &Path, architecture: &architecture::Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<Draft> {
    let judged: Vec<&architecture::WorldHandler> =
        architecture.world_handlers.iter().filter(|h| !matches!(h.contract_test, architecture::ContractTest::Waived(_))).collect();
    if judged.is_empty() {
        return Vec::new();
    }
    let graph = definition_graph(architecture, hy);
    let running = running_nodes(&graph);
    // 縁の検の節(空でない :interpreters を持つ deftest)— file ごとに 1 度だけ読む。
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
    let rel = relative_path(root, &architecture.path).unwrap_or_else(|| architecture.path.to_string_lossy().into_owned());
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
            ContractTestBreach::NoEdgeTest => format!("許可名簿の handler {} に縁の検(空でない :interpreters を持ち、この handler に届く deftest)が無い", spelling),
            ContractTestBreach::NoneWithoutReason => {
                format!("許可名簿の handler {} の :contract-test none に理由が無い(縁の検を求めない理由のテストの名を書く)", spelling)
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
    drafts
}

/// DOEFF137 の当たりの種類。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContractTestBreach {
    /// 縁の検を求める handler に、届く縁の検が無い。
    NoEdgeTest,
    /// 理由の無い `:contract-test none`。
    NoneWithoutReason,
}

/// handler 1 つの縁の検の宣言と、縁の検が届くか(求める時だけ問う)から、DOEFF137 の当たりを決める(agora-redesign #1796)。
fn contract_test_breach(declared: &architecture::ContractTest, covered: impl FnOnce() -> bool) -> Option<ContractTestBreach> {
    match declared {
        architecture::ContractTest::Waived(_) => None,
        architecture::ContractTest::NoneWithoutReason => Some(ContractTestBreach::NoneWithoutReason),
        architecture::ContractTest::Required => (!covered()).then_some(ContractTestBreach::NoEdgeTest),
    }
}

/// 定義の辺の図の前向きの辺(callees[n] = n から届く定義)。系の値の中の辺(defsystem の本体・:carriers の引数)も本番では系が回すので
/// 届く先に数える(DOEFF133 の縁の数えとは別 — #1390)。DOEFF143・155・156 が使う。
fn forward_edges(graph: &DefinitionGraph) -> Vec<Vec<usize>> {
    let mut callees: Vec<Vec<usize>> = vec![Vec::new(); graph.nodes.len()];
    for (callee, (callers, carried)) in graph.callers.iter().zip(&graph.carried).enumerate() {
        for &caller in callers.iter().chain(carried) {
            callees[caller].push(callee);
        }
    }
    callees
}

/// 図の中の effect の節の全部と、その tap(読まない file の節は数えない)。DOEFF143・155・156 が使う。
fn effect_clauses(root: &Path, graph: &DefinitionGraph, hy: &HashMap<String, HyFileIndex>, decl: &architecture::BusinessFakes) -> Vec<business_fakes::Clause> {
    use business_fakes::FileRole;
    let mut clauses: Vec<business_fakes::Clause> = Vec::new();
    let mut taps: HashMap<&str, HashMap<(String, String), bool>> = HashMap::new();
    for (node, &(rel, index)) in graph.nodes.iter().enumerate() {
        let d = &hy[rel].definitions[index];
        let role = business_fakes::role_of(rel, decl);
        if d.kind != DefinitionKind::EffectClause || role == FileRole::Skipped {
            continue;
        }
        let Some(effect) = d.handles.as_ref().and_then(|h| h.target.clone()) else { continue };
        let handler = d.container.clone().unwrap_or_default();
        let file_taps = taps.entry(rel).or_insert_with(|| std::fs::read_to_string(root.join(rel)).map(|s| business_fakes::taps_in(&s)).unwrap_or_default());
        let head = d.handles.as_ref().map(|h| h.name.clone()).unwrap_or_default();
        let tap = file_taps.get(&(handler.clone(), head)).copied().unwrap_or(false);
        clauses.push(business_fakes::Clause { node, rel: rel.to_string(), handler, effect, tap });
    }
    clauses
}

/// DOEFF155・156: 組み立ての形の破れを知らせにする(判定は assembly_shape — agora-redesign #1376)。全体の索引が要る(全体の実行だけ)。
fn judge_assembly_shape(
    root: &Path,
    architecture: &architecture::Architecture,
    layers: Option<&LayerSettings>,
    decl: &architecture::BusinessFakes,
    shape: &architecture::AssemblyShape,
    hy: &HashMap<String, HyFileIndex>,
    enabled: &BTreeSet<ProjectRule>,
) -> (Vec<Draft>, Vec<String>) {
    let (breaches, problems) = assembly_shape::find(root, architecture, layers, decl, shape, hy);
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

/// DOEFF143: 模擬の根と本番の入口から定義の辺の図を前向きに辿り、模擬の根からだけ届く effect の節(偽物)が業務の効果に tap でなく
/// 答える所と、外の世界の表・反例の表の腐りを出す(agora-redesign #1375)。全体の索引が要る(全体の実行だけ)。読めない表の理由は 2 つ目に返す。
fn judge_business_fakes(
    root: &Path,
    architecture: &architecture::Architecture,
    layers: Option<&LayerSettings>,
    decl: &architecture::BusinessFakes,
    shape: Option<&architecture::AssemblyShape>,
    hy: &HashMap<String, HyFileIndex>,
    enabled: &BTreeSet<ProjectRule>,
) -> (Vec<Draft>, Vec<String>) {
    use business_fakes::{FileRole, Verdict};
    let mut problems = Vec::new();
    let mut table = |dir: &Option<String>| -> BTreeMap<String, String> {
        let Some(dir) = dir else { return BTreeMap::new() };
        let judged = registry::JudgedKeys::load(root, std::slice::from_ref(dir));
        problems.extend(judged.problems);
        judged.reasons
    };
    let external = table(&decl.external_effects);
    let counterexamples = table(&decl.counterexamples);
    let unserved = table(&decl.unserved);
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
    // 模擬の根: 模擬の環境と組み立ての層の定義・組の file の模擬の組の関数。
    let simulation_roots: Vec<usize> = (0..count)
        .filter(|&n| {
            let (rel, _) = graph.nodes[n];
            matches!(role(n), FileRole::Simulation | FileRole::Assembly)
                || (role(n) == FileRole::Production && business_fakes::set_member(rel, &definition(n).name, decl.simulation_prefix.as_deref(), decl))
        })
        .collect();
    // 本番の入口: 本番の code の組の file の本番の組の関数・defsystem・__main__ の節が名指す定義・入口の文字列が指す定義。
    let production_code = |rel: &str| business_fakes::production_code(rel, decl);
    let mut production_roots: Vec<usize> = (0..count)
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
    // 文字列の綴りは定義の完全名まで縮める(属性の綴りは定義へ)。
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
    let reach = |roots: &[usize]| -> Vec<bool> {
        let mut seen = vec![false; count];
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
    };
    let simulated_nodes = reach(&simulation_roots);
    let produced_nodes = reach(&production_roots);
    // 検の根: 検の file の定義の全部(検だけが使う本番の code の置き場の業務の写しも、ここから届く)。
    let test_roots: Vec<usize> = (0..count).filter(|&n| role(n) == FileRole::Test).collect();
    let tested_nodes = reach(&test_roots);
    let clauses = effect_clauses(root, &graph, hy, decl);
    let simulated: Vec<bool> = clauses.iter().map(|c| simulated_nodes[c.node]).collect();
    let produced: Vec<bool> = clauses.iter().map(|c| produced_nodes[c.node]).collect();
    let tested: Vec<bool> = clauses.iter().map(|c| tested_nodes[c.node]).collect();
    // 層の名(intent と翻訳)は :assembly-shape から読む。効果の層は定義元の module の置き場で決める。
    let layer_of = |rel: &str| layers.and_then(|l| classify_layer_file(rel, l).map(|(site, _)| l.layers[site.layer.0].name.clone()));
    let intent_effect: Vec<bool> = clauses
        .iter()
        .map(|c| {
            let base = business_fakes::module_of_effect(&c.effect).replace('.', "/");
            let layer = layer_of(&format!("{}.hy", base)).or_else(|| layer_of(&format!("{}.py", base)));
            shape.is_some_and(|s| layer.as_deref() == Some(s.intent_layer.as_str()))
        })
        .collect();
    let translation_file: Vec<bool> =
        clauses.iter().map(|c| shape.is_some_and(|s| layer_of(&c.rel).as_deref() == Some(s.translation_layer.as_str()))).collect();
    // 本番の code の Python の handler(索引の図の外)が isinstance で答える効果。
    let python_answered: BTreeSet<String> = WalkDir::new(root)
        .follow_links(false)
        .into_iter()
        .filter_entry(|e| !e.file_name().to_string_lossy().starts_with('.') && e.file_name() != "__pycache__")
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file() && e.path().extension().is_some_and(|x| x == "py"))
        .filter_map(|e| relative_path(root, e.path()).map(|rel| (rel, e.path().to_path_buf())))
        .filter(|(rel, _)| decl.entry_string_modules.iter().any(|m| rel.starts_with(&format!("{}/", m.replace('.', "/")))) && production_code(rel))
        .filter_map(|(rel, path)| std::fs::read_to_string(path).ok().map(|source| (module_of(&rel), source)))
        .flat_map(|(module, source)| business_fakes::python_isinstance_effects(&source, &module))
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
    let table_draft = |dir: &Option<String>, detail: String, subject: String, reason: &str| {
        let rel = dir.clone().unwrap_or_else(|| "architecture.hy".to_string());
        Draft {
            rule: ProjectRule::BusinessEffectFake,
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
    // 節 1 つの知らせ(鍵の細目 = `[<種類>:]<handler>::<効果>`)。
    let clause_draft = |rule: ProjectRule, i: usize, kind: Option<&str>, what: String, reason: &str| {
        let clause = &clauses[i];
        let explain = Explain::BusinessEffectFake { subject: format!("handler {} の節 {}", clause.handler, clause.effect), reason: reason.to_string() };
        Draft {
            rule,
            layer: None,
            path: root.join(&clause.rel),
            rel: clause.rel.clone(),
            range: definition(clause.node).range,
            message: format!("{} の {} が{}", clause.rel, clause.handler, what),
            detail: Some(match kind {
                Some(kind) => format!("{}:{}::{}", kind, clause.handler, clause.effect),
                None => format!("{}::{}", clause.handler, clause.effect),
            }),
            base: Severity::Error,
            explain,
        }
    };
    // DOEFF165(#1561 K3): intent の層の効果ごとの網羅の表 — 同じ到達(模擬の根・本番の入口)と、deftest から前向きに届く定義から 3 列を読む。
    let coverage: Vec<Draft> = match shape.filter(|_| enabled.contains(&ProjectRule::IntentEffectUncovered)) {
        None => Vec::new(),
        Some(shape) => {
            // 出す側の根は検の file の deftest だけ(検の helper から届くだけの定義を「テストした」に数えない — DOEFF157 の検の根とは別)。
            let deftest_roots: Vec<usize> = test_roots.iter().copied().filter(|&n| definition(n).kind == DefinitionKind::Deftest).collect();
            let deftested = reach(&deftest_roots);
            let service_root = settings::normalize_dir(&architecture.root);
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
                    // 出す側 = この効果を名指す定義のうち、答え手(効果の節とその handler)と宣言そのものを除いた物で、deftest から届く物。
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
                        message: format!("intent の効果 {} の網羅の欠け: {} — {}", row.facts.effect, row.gap_words(), row.columns()),
                        detail: Some(row.detail()),
                        // 欠けは失敗(#1562 K4)。今ある欠けは repo の登録簿に載せ、載った欠けは finish が warning に下げる。
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
    let mut drafts: Vec<Draft> = business_fakes::judge(&inputs, decl)
        .into_iter()
        .map(|verdict| match verdict {
            Verdict::Fake(i) => clause_draft(
                ProjectRule::BusinessEffectFake,
                i,
                None,
                format!("業務の効果 {} に答える偽物(模擬の根からだけ届く)", clauses[i].effect),
                "模擬の根から届き本番の入口から届かない定義が業務の効果に答えを作っている。業務の操作は下の層の効果を出す defk で書き、検査は外の世界の handler だけを差し替える。",
            ),
            Verdict::LowerLayerFake(i) => clause_draft(
                ProjectRule::BusinessEffectFake,
                i,
                Some("lower"),
                format!("下の層の効果 {} に答える第 2 の偽物(模擬の根からだけ届く)", clauses[i].effect),
                "下の層の効果に答える偽物は下の層が持つ正典 1 つだけ。模擬は外の世界の handler と正典の偽物で組む。",
            ),
            Verdict::TestOnlyFake(i) => clause_draft(
                ProjectRule::TestOnlyFake,
                i,
                None,
                format!("{} に答える検だけの偽物(検の定義からだけ届く)", clauses[i].effect),
                "検だけから届く定義が業務の効果か下の層の効果に答えを作っている。業務の handler は本番の 1 つだけで、検は土台(記録の効果・時計・外の相手)の handler の差し替えで組む。わざと壊した反例なら反例の表に載せる。",
            ),
            Verdict::IntentAnsweredOutside(i) => clause_draft(
                ProjectRule::IntentAnswererNotTranslation,
                i,
                Some("outside"),
                format!("intent の効果 {} に翻訳の層の外で答える(本番の入口から届く)", clauses[i].effect),
                "intent の効果に答えるのは翻訳の層の handler 1 つだけ(本番と模擬で同じ)。環境ごとの別の答え手や土台の handler で答えず、模擬は土台を差し替える。",
            ),
            Verdict::IntentAnsweredTwice(i, n) => clause_draft(
                ProjectRule::IntentAnswererNotTranslation,
                i,
                Some("shared"),
                format!("intent の効果 {} に答える翻訳の handler {} 個の 1 つ", clauses[i].effect, n),
                "intent の効果 1 つに答える翻訳の handler は 1 つだけ。答えを 1 つの handler にまとめる。",
            ),
            Verdict::StaleCounterexample(key) => table_draft(
                &decl.counterexamples,
                format!("counterexample-unused::{}", key),
                format!("反例の表の {} はもう当たらない", key),
                "反例の表の行は、わざと壊した反例の handler が今も在る間だけ置く — 表から外す。",
            ),
            Verdict::UnusedExternal(effect) => table_draft(
                &decl.external_effects,
                format!("external-unused::{}", effect),
                format!("外の世界の表の {} にどの偽物も答えない", effect),
                "外の世界の表は偽物が答える外の世界の効果の宣言 — 答える偽物が無い行は表から外す(表を腐らせない)。",
            ),
            Verdict::UnservedExternal(effect) => table_draft(
                &decl.external_effects,
                format!("external-unserved::{}", effect),
                format!("外の世界の表の {} に本番の入口から届く答え手が無い", effect),
                "外と名乗った効果には本番の答え手が要る(偽物 1 つで通さない)— 本番の handler を書くか、業務の効果なら下の層の効果で書く。今の不足は :unserved の表に理由と担い手つきで載せる。",
            ),
        })
        .filter(|draft| enabled.contains(&draft.rule))
        .chain(coverage)
        .collect();
    if enabled.contains(&ProjectRule::ServiceWithoutCounterexample) {
        drafts.extend(
            judge_counterexample_coverage(architecture, &graph, hy, &clauses, &produced, &counterexamples)
                .into_iter()
                .map(|d| Draft { path: root.join(&d.rel), ..d }),
        );
    }
    if enabled.contains(&ProjectRule::ClauseWithoutCounterexample) {
        let claims = match decl.counterexamples.as_deref() {
            Some(dir) => clause_coverage::ClauseClaims::load(root, dir),
            None => clause_coverage::ClauseClaims::default(),
        };
        let (found, claim_problems) = judge_clause_coverage(architecture, &graph, hy, &clauses, &produced, &counterexamples, &claims);
        drafts.extend(found.into_iter().map(|d| Draft { path: root.join(&d.rel), ..d }));
        problems.extend(claims.problems);
        problems.extend(claim_problems);
    }
    (drafts, problems)
}

/// DOEFF136: service ごとに、entry の層の定義から呼び手を逆向きに辿り、模擬の環境(:verification-environment)の下の deftest に
/// 1 本も届かなければ、その defservice を出す(agora-redesign #1106 の R5)。entry の層を持たない service は数えない。
fn judge_untested_services(architecture: &architecture::Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<Draft> {
    let Some(place) = architecture.verification_environment.as_deref() else { return Vec::new() };
    let root = settings::normalize_dir(&architecture.root);
    let sim = format!("{}/{}", root, place);
    let graph = definition_graph(architecture, hy);
    let definition = |node: usize| {
        let (rel, index) = graph.nodes[node];
        &hy[rel].definitions[index]
    };
    let on_sim = |node: usize| under(graph.nodes[node].0, &sim) && definition(node).kind == DefinitionKind::Deftest;
    let mut drafts = Vec::new();
    for service in architecture.services.iter().filter(|s| s.layers.iter().any(|l| l == "entry")) {
        let entry = format!("{}/{}/entry", root, service.dir);
        let seeds: Vec<usize> = (0..graph.nodes.len()).filter(|n| under(graph.nodes[*n].0, &entry)).collect();
        // entry の層を宣言しても定義が 0 本なら、回す組み立てが無い(数えない)。
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
            // 系の値の中の辺も数える(模擬の環境の検が系を組むだけでも、組み立ての entry に届いている)。
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
            rel: "architecture.hy".to_string(),
            path: PathBuf::from("architecture.hy"),
            range: service.range,
            message: format!("service {} の entry の層({} 本の定義)に、模擬の環境 {} の deftest が 1 本も届かない", service.name, seeds.len(), sim),
            detail: Some(service.name.clone()),
            base: Severity::Error,
            explain: Explain::ServiceUntestedOnSim { service: service.name.clone(), entry, definitions: seeds.len(), sim: sim.clone() },
        });
    }
    drafts
}

/// DOEFF163: code を持つ service の不変条件の宣言の欠けを、defservice の位置の下書きにする(鍵の細目 = service の名・関数の欠けは `::` と名指し)。
fn judge_service_invariants(root: &Path, architecture: &architecture::Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<Draft> {
    let rel = relative_path(root, &architecture.path).unwrap_or_else(|| architecture.path.to_string_lossy().into_owned());
    invariants::gaps(architecture, hy)
        .into_iter()
        .map(|(service, gap)| {
            let message = gap.describe(&service.name);
            Draft {
                rule: ProjectRule::ServiceInvariantsMissing,
                layer: None,
                rel: rel.clone(),
                path: architecture.path.clone(),
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

/// seeds から呼び手を逆向きに辿って届く deftest(DOEFF136 と同じ辺 — 呼び出し・参照・入れ子と、系の値の中の辺)。
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

/// DOEFF164: service ごとの壊した handler の反例の有無(agora-redesign #1560)。反例の節 = 反例の表の鍵に当たり本番の入口から届かない節。
/// 節の効果の定義元の file を含む service の dir が持ち主(どの service の下にも無ければ土台の効果)。反例の節に届く deftest の 1 本でも
/// その service の entry の層の定義に(DOEFF136 と同じ図を逆向きに)届けば有り。母集団は DOEFF136 と同じ(entry の層に定義を持つ service)。
fn judge_counterexample_coverage(
    architecture: &architecture::Architecture,
    graph: &DefinitionGraph,
    hy: &HashMap<String, HyFileIndex>,
    clauses: &[business_fakes::Clause],
    produced: &[bool],
    counterexamples: &BTreeMap<String, String>,
) -> Vec<Draft> {
    let root = settings::normalize_dir(&architecture.root);
    let service_dir = |service: &architecture::ArchService| format!("{}/{}", root, service.dir);
    let mut keys = BTreeSet::new();
    let mut cases = Vec::new();
    for (i, clause) in clauses.iter().enumerate() {
        if clause.tap || produced[i] || !counterexamples.contains_key(&clause.key()) || !keys.insert(clause.key()) {
            continue;
        }
        let base = business_fakes::module_of_effect(&clause.effect).replace('.', "/");
        let owner = architecture.services.iter().find(|s| under(&base, &service_dir(s))).map(|s| s.name.clone());
        cases.push(business_fakes::CounterexampleCase { owner, tests: deftests_reaching(graph, hy, &[clause.node]) });
    }
    let mut services = Vec::new();
    let mut entries = Vec::new();
    for service in architecture.services.iter().filter(|s| s.layers.iter().any(|l| l == "entry")) {
        let entry = format!("{}/entry", service_dir(service));
        let seeds: Vec<usize> = (0..graph.nodes.len()).filter(|n| under(graph.nodes[*n].0, &entry)).collect();
        // entry の層を宣言しても定義が 0 本なら、回す組み立てが無い(DOEFF136 と同じく数えない)。
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
                rel: "architecture.hy".to_string(),
                path: PathBuf::from("architecture.hy"),
                range: service.range,
                message: format!(
                    "service {} に壊した handler の反例が無い(反例の表 {} 節のうち候補 {} 節 — どれに届く deftest も {} に届かない)",
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

/// DOEFF167: service ごと・条ごとの反例の網羅(agora-redesign #1713)。効く反例の節と母集団は DOEFF164 と同じ(反例の表に在り本番の入口から
/// 届かない節・entry の層に定義を持つ service)。節が名乗る条は反例の表の行の `breaks:`(claims)から読み、節に届く deftest の 1 本でも
/// service の entry の層に届けば、その条は有り。返りの 2 つ目 = 宣言に無い service か条を名乗る `breaks:` の理由(表の読めない行)。
fn judge_clause_coverage(
    architecture: &architecture::Architecture,
    graph: &DefinitionGraph,
    hy: &HashMap<String, HyFileIndex>,
    clauses: &[business_fakes::Clause],
    produced: &[bool],
    counterexamples: &BTreeMap<String, String>,
    claims: &clause_coverage::ClauseClaims,
) -> (Vec<Draft>, Vec<String>) {
    let root = settings::normalize_dir(&architecture.root);
    let mut keys = BTreeSet::new();
    let mut cases = Vec::new();
    for (i, clause) in clauses.iter().enumerate() {
        if clause.tap || produced[i] || !counterexamples.contains_key(&clause.key()) || !keys.insert(clause.key()) {
            continue;
        }
        let breaks = claims.by_key.get(&clause.key()).cloned().unwrap_or_default();
        if !breaks.is_empty() {
            cases.push(clause_coverage::ClauseCase { breaks, tests: deftests_reaching(graph, hy, &[clause.node]) });
        }
    }
    let mut services = Vec::new();
    let mut declared_services = Vec::new();
    for service in architecture.services.iter().filter(|s| s.layers.iter().any(|l| l == "entry")) {
        let entry = format!("{}/{}/entry", root, service.dir);
        let seeds: Vec<usize> = (0..graph.nodes.len()).filter(|n| under(graph.nodes[*n].0, &entry)).collect();
        // entry の層を宣言しても定義が 0 本なら、回す組み立てが無い(DOEFF164 と同じく数えない)。
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
                rel: "architecture.hy".to_string(),
                path: PathBuf::from("architecture.hy"),
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

/// 生の副作用の直接の証拠のうち、入れ子で重なる物はいちばん内側の定義に 1 度だけ(DOEFF106 の母集団)。
/// agora-redesign #1797: 境目の部品(architecture.hy の :boundary-parts)の module の中で、触れる先が宣言に入る生の副作用の証拠か
/// (DOEFF106 が当たりにしない)。種類の外の証拠と、宣言の無い module の証拠は今どおり当たる。
fn boundary_allows(raw: &settings::RawSettingsSpec, module: &str, category: hy_index::RawCategory) -> bool {
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

/// DOEFF131 の当たり 1 つ(持ち主の定義の添字 — top level の式なら None・handler の綴り・名指してよい名簿の定義の文・位置)。
struct WorldSpot {
    owner: Option<usize>,
    spelling: String,
    allowed: String,
    range: Range,
}

/// 目録の実 I/O の handler を、名簿の定義(とその中の入れ子の定義)の外で名指す所(参照と呼び出し — import の行は数えない)。
/// 同じ定義の同じ handler は 1 件。
fn world_handler_spots(hy_file: &HyFileIndex, architecture: &architecture::Architecture) -> Vec<WorldSpot> {
    let wrapped = architecture.world_targets(world_catalog::WorldCatalog::bundled());
    let listed = architecture.world_definition_targets();
    let definitions = &hy_file.definitions;
    let in_import = |range: &Range| hy_file.imports.iter().any(|imp| imp.range.start <= range.start && range.end <= imp.range.end);
    let spots = hy_file
        .references
        .iter()
        .filter(|r| !in_import(&r.range))
        .filter_map(|r| r.target.as_ref().map(|t| (t, r.range)))
        .chain(hy_file.calls.iter().filter_map(|c| c.target.as_ref().map(|t| (t, c.range))));
    let catalog = world_catalog::WorldCatalog::bundled();
    let mut chosen: BTreeMap<(Option<usize>, String), Range> = BTreeMap::new();
    for (target, range) in spots {
        // 目録の数えない行(:wraps に書けるだけの移行の間の行 — agora-redesign #1318)は数えない。
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
            let allowed = if by.is_empty() { "名簿のどの定義もこの handler を :wraps に挙げていない".to_string() } else { by.join("・") };
            WorldSpot { owner, spelling: spelling.clone(), allowed, range }
        })
        .collect()
}

/// agora-redesign #1147: 層の置き場の外の Hy の file(層の外の dir・architecture の :root の外の :raw-io-roots)にも、許可名簿の規則
/// DOEFF106・131 を当てる — 外の世界に触れてよいのは名簿の定義だけで、置き場の破れ(DOEFF114)の file も例外ではない。検の file
/// (path の段に tests・名が test_ か conftest)と :exclude の段は外す(縁のテストは DOEFF133 が持つ)。
fn judge_unplaced_world(
    root: &Path,
    architecture: &architecture::Architecture,
    raw: &settings::RawSettingsSpec,
    placed: &BTreeSet<&str>,
    hy: &HashMap<String, HyFileIndex>,
    enabled: &BTreeSet<ProjectRule>,
) -> Vec<Draft> {
    let roots: Vec<String> = match &architecture.raw_io_roots {
        Some(roots) => roots.iter().map(|r| settings::normalize_dir(r)).collect(),
        None => vec![settings::normalize_dir(&architecture.root)],
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
        // 実行できる ADR の冊(defadr を持つ file)は pytest が集めるテストの冊 — 検の file と同じく外す。
        if definitions.iter().any(|d| d.kind == DefinitionKind::Defadr) {
            continue;
        }
        // deftest(ADR の冊の law の検を含む)の中の実 I/O はテストの持ち分(縁のテスト — DOEFF133)で、ここでは数えない。
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
                        "定義 {}(層の置き場の外)が生の副作用 {}({})に直に触る — 生の副作用に触ってよいのは architecture.hy の :world-handlers の定義の module だけ",
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
                let shown = spot.owner.map(|i| definitions[i].name.clone()).unwrap_or_else(|| "module の top level".to_string());
                drafts.push(draft(
                    ProjectRule::WorldHandlerNamedOutsideList,
                    spot.range,
                    format!(
                        "{}(層の置き場の外)が doeff の実 I/O の handler {} を名指す — 名指してよいのは architecture.hy の :world-handlers の定義({})だけ",
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

// --- 意味の規則(DOEFF201・202 — Jev)---------------------------------------------------

/// 意味の問いを当てる定義の kind(型と関数と handler — 値の束縛・検・macro は問わない)。
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

/// 較正の例の kind の綴りを 'static にするための一覧(semantic_kind と同じ kind)。
const SEMANTIC_KIND_NAMES: &[&str] = &["defn", "defn/a", "defk", "deff", "defp", "defpp", "defhandler", "defeffect", "defclass", "defrecord", "defenum"];

/// DOEFF203・204 の入力 — 業務の Hy の file・architecture.hy の受け入れる理由と受け入れない型(名・説明・直し方)・註の目印・
/// 定義の規則のタグの読み方(class の形を読むため)・hy-index の file(DOEFF119 と同じ証拠で問う class を選ぶため)。
struct PlainCallableInput<'a> {
    files: Vec<(SourceFile, Option<String>)>,
    accepted: Vec<architecture::ReasonKind>,
    rejected: Vec<architecture::ReasonKind>,
    marker: String,
    tags: Option<settings::TagReading>,
    indexes: &'a HashMap<String, HyFileIndex>,
}

/// 規則と問いの対応。
fn semantic_rule(question: semantic::SemanticQuestion) -> ProjectRule {
    match question {
        semantic::SemanticQuestion::BusinessDecision => ProjectRule::SemanticBusinessDecision,
        semantic::SemanticQuestion::TransportKnowledge => ProjectRule::SemanticTransportKnowledge,
        semantic::SemanticQuestion::PlainCallable => ProjectRule::SemanticPlainCallable,
        semantic::SemanticQuestion::ClassRole => ProjectRule::SemanticClassRole,
        semantic::SemanticQuestion::MixedConcerns => ProjectRule::SemanticMixedConcerns,
    }
}

/// Jev の答えのある定義 1 つ — 人の判定との突き合わせの材料(閾値に届かず違反にならなかった定義も含む)。
struct SemanticProbe {
    rule: ProjectRule,
    /// 鍵の law を引く層(層を問う規則 DOEFF201・202 だけ)。
    layer: Option<LayerId>,
    rel: String,
    /// 鍵の細目(mangle した定義の名)。
    detail: String,
    probability: f64,
    /// 今の閾値で違反になるか。
    flagged: bool,
}

/// DOEFF201・202: 設定した層の Hy の最上位の定義を Jev の問いにし、cache を読むか(既定)撃つ(--semantic)。答えの無い定義は未判定の数。
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
        // 覚えている時だけの問いは代理にだけ撃つ(代理でない宛先では cache だけ — 本物の Jev を呼ばない)。token が無ければ黙って cache だけ。
        semantic::SemanticMode::Peek => match (target.proxy, settings.proxy.as_ref()) {
            (true, Some(proxy)) => semantic::HttpGateway::new(target, proxy.peek_timeout).ok(),
            _ => None,
        },
        _ => match semantic::HttpGateway::new(target, settings.timeout) {
            Ok(gateway) => Some(gateway),
            Err(reason) => {
                errors.push(format!("意味の規則: {}", reason));
                None
            }
        },
    };
    let mut items = Vec::new();
    // file ごとの Hy の索引は、下の問いの節(層の問い・DOEFF203・DOEFF205)で共有する — 同じ file を節ごとに索引し直すと、1 file の実行で
    // 34KB の file に約 0.02 秒ずつ 3 回かかっていた(agora-redesign #1632)。どの節も同じ file を同じ中身で読む(1 file の実行は stdin の
    // 中身・全体の実行は disk)ので、答えは変わらない。
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
                    errors.push(format!("{}: 読めない: {}", file.file.rel, error));
                    continue;
                }
            },
        };
        let facts = read_facts(Language::Hy, &text, &file.module, &layers.tags);
        let mut roles: Vec<String> = Vec::new();
        for tags in facts.tag_sets() {
            if let Some(role) = tags.role.clone().filter(|r| !r.is_empty() && !roles.contains(r)) {
                roles.push(role);
            }
        }
        roles_of.insert(file.file.rel.clone(), roles);
        let index = &*indexed.entry(file.file.rel.clone()).or_insert_with(|| hy_index::index_source(root, &file.file.path, &text));
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
    // DOEFF203: 種類を名乗った deff ごとに、種類の一覧 + none から理由を選ばせる。
    let pairs = |reasons: &[architecture::ReasonKind]| reasons.iter().map(|r| (r.name.clone(), r.description.clone())).collect::<Vec<_>>();
    let (accepted, rejected) = (pairs(&plain.accepted), pairs(&plain.rejected));
    if settings.plain_callable.is_some() && enabled.contains(&ProjectRule::SemanticPlainCallable) && !plain.accepted.is_empty() {
        for (file, source) in &plain.files {
            let text = match source {
                Some(text) => text.clone(),
                None => match std::fs::read_to_string(&file.path) {
                    Ok(text) => text,
                    Err(error) => {
                        errors.push(format!("{}: 読めない: {}", file.rel, error));
                        continue;
                    }
                },
            };
            let index = &*indexed.entry(file.rel.clone()).or_insert_with(|| hy_index::index_source(root, &file.path, &text));
            for definition in index.definitions.iter().filter(|d| d.container.is_none() && d.kind == DefinitionKind::Deff) {
                let start = crate::position::offset_of(&text, definition.full_range.start);
                let (line, previous) = line_and_previous(&text, start);
                let comment = parse_reason_comment(&line, &plain.marker).or_else(|| {
                    previous.trim_start().starts_with(';').then(|| parse_reason_comment(&previous, &plain.marker)).flatten()
                });
                // 理由の註が在り、空でも「同上」でもない deff だけを問う(それ以外は DOEFF111 が決定的に出す)。
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
    // DOEFF204: DOEFF119 が何も出さず、処理を持つ method のある class だけを、value / external-world / stateful / other から選ばせる。
    if settings.class_role.is_some() && enabled.contains(&ProjectRule::SemanticClassRole) {
        if let Some(reading) = &plain.tags {
            for (file, source) in &plain.files {
                let text = match source {
                    Some(text) => text.clone(),
                    None => match std::fs::read_to_string(&file.path) {
                        Ok(text) => text,
                        Err(error) => {
                            errors.push(format!("{}: 読めない: {}", file.rel, error));
                            continue;
                        }
                    },
                };
                let facts = read_facts(Language::Hy, &text, &module_of(&file.rel), reading);
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
    // DOEFF205: 役が judgment / program の定義(定義の :tags か module の頭のタグ)に、形の検めと判断が混ざっているかを問う。
    if let (Some(mixed), Some(reading)) = (&settings.mixed_concerns, &plain.tags) {
        if enabled.contains(&ProjectRule::SemanticMixedConcerns) {
            let spec = &layers.layers[mixed.layer.0];
            for (file, source) in &plain.files {
                let text = match source {
                    Some(text) => text.clone(),
                    None => match std::fs::read_to_string(&file.path) {
                        Ok(text) => text,
                        Err(error) => {
                            errors.push(format!("{}: 読めない: {}", file.rel, error));
                            continue;
                        }
                    },
                };
                let module_role = read_facts(Language::Hy, &text, &module_of(&file.rel), reading).module_tags.and_then(|t| t.role);
                let index = &*indexed.entry(file.rel.clone()).or_insert_with(|| hy_index::index_source(root, &file.path, &text));
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
    // 較正の見張りの例(同梱の正例と反例)を、その問いを当てる最初の層の説明で組む。
    let calibration: Vec<(semantic::SemanticItem, bool)> = semantic::calibration_examples()
        .into_iter()
        .filter_map(|example| {
            let rule = ProjectRule::parse(&example.rule)?;
            // 較正は有効な規則の問いだけ(DOEFF203 だけの実行で DOEFF201・202 の例を撃たない)。
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
    let outcome = semantic::evaluate(root, settings, items, mode, gateway.as_ref().map(|g| g as &dyn semantic::Gateway), &calibration);
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
                    message: format!("{} の {} は Jev の判定で形の検めと判断が混ざっている(p={:.2})", item.rel, item.name, probability),
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
                    message: format!("{} の defclass {} は Jev の判定で {}(p={:.2})", item.rel, item.name, chosen, probability),
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
                let verdict = if accepted_kind.is_some() { "受け入れる理由に近いが疑わしい" } else { "受け入れられない" };
                return Some(Draft {
                    rule: ProjectRule::SemanticPlainCallable,
                    layer: None,
                    rel: item.rel.clone(),
                    path: item.path.clone(),
                    range: item.range,
                    message: format!("{} の deff {} の理由は{}(Jev の選択 = {} p={:.2})", item.rel, item.name, verdict, chosen, probability),
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
                    "{} の {}({})— Jev の判定 p={:.2}: {}",
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
            probability: answer.probability,
            flagged: draft.is_some(),
        });
        drafts.extend(draft);
    }
    (drafts, summary, errors, probes)
}

// --- 定義の書き方(DOEFF110〜112)------------------------------------------------------

/// 定義の書き方の規則のどれかが有効か。
fn wants_definitions(enabled: &BTreeSet<ProjectRule>) -> bool {
    if enabled.iter().any(|rule| rule.is_smell())
        || enabled.contains(&ProjectRule::DefkCalledBare)
        || enabled.contains(&ProjectRule::EffectsDisagreeWithInference)
    {
        return true;
    }
    [ProjectRule::DefnForbidden, ProjectRule::DeffNeedsReason, ProjectRule::DefinitionTagsRequired, ProjectRule::TestIsDeftest, ProjectRule::ClassWithBehaviour]
        .iter()
        .any(|rule| enabled.contains(rule))
}

/// 検の置き場(DOEFF118 の母集団)の Hy の file か。
fn is_test_file(rel: &str, definitions: &settings::DefinitionSettings) -> bool {
    language_of(Path::new(rel)) == Some(Language::Hy) && definitions.test_paths.iter().any(|pattern| glob_matches(pattern, rel))
}

/// path の glob の照合 — `**` は 0 個以上の段、`*` は段の中の任意の綴り(`/` を越えない)。`/` を含まない綴りは file の名に当てる。
pub fn glob_matches(pattern: &str, rel: &str) -> bool {
    let path: Vec<&str> = rel.split('/').collect();
    if !pattern.contains('/') {
        return path.last().is_some_and(|name| segment_matches(pattern, name));
    }
    let parts: Vec<&str> = pattern.split('/').filter(|p| !p.is_empty()).collect();
    segments_match(&parts, &path)
}

/// glob の段の列と path の段の列の照合(`**` は 0 個以上の段)。
fn segments_match(pattern: &[&str], path: &[&str]) -> bool {
    match pattern.split_first() {
        None => path.is_empty(),
        Some((&"**", rest)) => (0..=path.len()).any(|skip| segments_match(rest, &path[skip..])),
        Some((first, rest)) => path.split_first().is_some_and(|(name, tail)| segment_matches(first, name) && segments_match(rest, tail)),
    }
}

/// 1 段の照合(`*` は任意の綴り)。
fn segment_matches(pattern: &str, name: &str) -> bool {
    match pattern.split_once('*') {
        None => pattern == name,
        Some((head, tail)) => {
            name.starts_with(head)
                && (head.len()..=name.len()).any(|at| name.is_char_boundary(at) && segment_matches(tail, &name[at..]))
        }
    }
}

/// 定義の規則の母集団の file か(置き場の 1 つの下 — 置き場が空なら全部 — で、除く置き場・区切りに当たらない Hy の file)。
fn is_definition_file(rel: &str, definitions: &settings::DefinitionSettings) -> bool {
    language_of(Path::new(rel)) == Some(Language::Hy)
        && (definitions.paths.is_empty() || definitions.paths.iter().any(|place| matches_place(rel, place)))
        && !definitions.exclude.iter().any(|place| matches_place(rel, place))
        && !rel.split('/').any(|part| definitions.exclude_parts.contains(part))
}

/// architecture.hy が宣言した、素の関数を許す理由の種類(無ければ空)。
fn plain_callable_reasons(settings: &ProjectSettings) -> &[architecture::ReasonKind] {
    settings.architecture.as_ref().map(|a| a.plain_callable_reasons.as_slice()).unwrap_or(&[])
}

/// file の path と根からの path から repo の根を出す(基底の module が repo の中かを見るため)。
fn repo_root_of(file: &SourceFile) -> Option<PathBuf> {
    let depth = file.rel.split('/').count();
    file.path.ancestors().nth(depth).map(Path::to_path_buf)
}

/// 例外・Enum・Protocol のような、名で分かる許す基底(repo の中で定めた例外の子も含めるため、名の終わりで見る)。
fn is_allowed_base_name(base: &str) -> bool {
    let last = base.rsplit('.').next().unwrap_or(base);
    ["Error", "Exception", "Warning", "Enum", "Flag", "Protocol", "NamedTuple", "TypedDict"].iter().any(|suffix| last.ends_with(suffix))
}

/// 基底が repo の外(外の library・組み込み)の class か。束縛の module の先頭の段が repo の根に在れば repo の中。
fn is_external_base(base: &str, bindings: &BTreeMap<String, String>, local: &BTreeSet<&str>, root: Option<&Path>) -> bool {
    let head = base.split('.').next().unwrap_or(base);
    if local.contains(base) {
        return false;
    }
    let module = match bindings.get(base).or_else(|| bindings.get(head)) {
        Some(module) => module.clone(),
        // 束縛の無い裸の名は組み込み(Exception・object …)。
        None if !base.contains('.') => return true,
        None => base.to_string(),
    };
    let top = module.split('.').next().unwrap_or(&module);
    match root {
        Some(root) => !(root.join(top).is_dir() || root.join(format!("{}.hy", top)).is_file() || root.join(format!("{}.py", top)).is_file()),
        None => true,
    }
}

/// DOEFF119 の class 1 つの判定(閉じた集合)。
#[derive(Debug, Clone, PartialEq)]
enum ClassJudgement {
    /// 例外・Enum・Protocol・外の library の基底を継ぐ class、または欄だけでも処理を持つ method も無い物 — 何も出さない。
    Allowed,
    /// 決定的に分かった区分(外の世界 / 状態 / 欄だけ)。
    Found(explain::ClassShapeFacts, explain::ClassVerdict),
    /// 処理を持つ method があるが、証拠も書き換えも見えない — 値の class か窓口かを Jev(DOEFF204)に問う。
    AskJev,
}

/// DOEFF119: defclass を中身の証拠で分ける — method か欄の初期値の生の副作用(強い証拠・直接か経由)は外の世界、
/// __init__ 等の外で self の欄を書き換える method は状態、処理を持つ method が無ければ欄だけ。名前では判じない。
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
    // 生の副作用の証拠 — class の定義そのもの(欄の初期値の式を含む)と、class の中の定義(method・欄)。
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
    // 作る時(__init__・__post_init__・__new__)に欄を置くのは書き換えに数えない。
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

/// 理由の註 `; <目印>(<種類>): <詳細>` を読んだ結果(種類の括弧は半角でも全角でもよい。種類の無い旧い形は kind = None)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReasonComment {
    pub kind: Option<String>,
    pub detail: String,
}

/// 行の註(`;` の後)から理由の註を読む。目印は設定の deff_reason_marker(末尾の `:` は外して比べる)。
pub fn parse_reason_comment(line: &str, marker: &str) -> Option<ReasonComment> {
    let base = marker.trim_end_matches([':', '：']).trim();
    let (_, comment) = line.split_once(';')?;
    let at = comment.find(base)?;
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

/// 理由の文が、その定義に固有の理由になっていないか(空・「同上」とその変形)。
fn is_not_a_reason(detail: &str) -> bool {
    let text = detail.trim();
    text.is_empty() || ["同上", "上と同じ", "上に同じ", "前と同じ", "同前"].iter().any(|word| text.starts_with(word))
}

/// 行の頭の byte の位置(offset を含む行と、その前の行)の本文を返す。
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

/// DOEFF122 の失敗の型の宣言を repo の Hy の file から集める(規則が有効な時だけ — 無ければ空)。`:failure` / `:absent` の綴りを
/// 含む file だけを読み、宣言の型は file の import と module で module まで含めた名に解く。読めない file は飛ばす。
fn failure_types_for(root: &Path, enabled: &BTreeSet<ProjectRule>, reading: &settings::TagReading) -> smells::FailureTypes {
    let mut all = smells::FailureTypes::default();
    if !enabled.contains(&ProjectRule::FailureRethrow) {
        return all;
    }
    // file ごとの失敗の型は、その file の中身とタグの読み方(設定)で決まる — 読み方の指紋を印に含めて file ごとに覚え、
    // 変わった file だけ読み直す(#1033)。集合の和なので、束ねる順は答えを変えない。
    let files: Vec<(String, PathBuf)> = hy_files::collect(root)
        .into_iter()
        .filter_map(|path| relative_path(root, &path).map(|rel| (rel, path)))
        .collect();
    let key = {
        use sha2::{Digest, Sha256};
        format!("{:x}", Sha256::digest(format!("{reading:?}").as_bytes()))
    };
    let found: Vec<smells::FailureTypes> = facts_cache::per_file_keyed(root, "failure-types", &key, &files, |rel, path| {
        let source = std::fs::read_to_string(path).ok()?;
        if !(source.contains(":failure") || source.contains(":absent")) {
            return None;
        }
        let module = module_of(rel);
        let facts = read_facts(Language::Hy, &source, &module, reading);
        let found = smells::failure_types_in(&source, smells::Scope { module: &module, bindings: &facts.bindings });
        (!found.is_empty()).then_some(found)
    });
    for one in found {
        all.extend(one);
    }
    all
}

/// 読めない Hy の file を DOEFF128 の違反にする(全体 = 規則が読む Hy の file を並べて読む・1 file = 保存前の中身だけ)。
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
        None => hy_files::collect(root)
            .par_iter()
            .filter_map(|path| {
                let rel = relative_path(root, path)?;
                let source = std::fs::read_to_string(path).ok()?;
                is_judged_file(&rel, &source, settings).then(|| unreadable::finding(&rel, path, &source)).flatten()
            })
            .collect(),
    }
}

/// どれかの規則が読む file か(層の置き場・定義の規則の母集団・検の置き場・業務の名の母集団)— 規則が読まない file(文書の中の
/// 抜き書きなど)は、読めなくても違反が欠けないので知らせない。
fn is_judged_file(rel: &str, source: &str, settings: &ProjectSettings) -> bool {
    let layered = settings.layers.as_ref().is_some_and(|layers| classify_layer_file(rel, layers).or_else(|| infer_layer_site(rel, source, layers)).is_some());
    let defined = settings.definitions.as_ref().is_some_and(|d| is_definition_file(rel, d) || is_test_file(rel, d));
    let named = settings.environment.as_ref().is_some_and(|env| is_environment_file(rel, env));
    layered || defined || named
}

/// DOEFF126 の defk の集合を repo の Hy の file から集める(規則が有効な時だけ — 無ければ空)。`(defk` の綴りを含む file だけを読み、
/// 最上位の defk の名を module まで含めた名にする。読めない file は飛ばす。
fn defk_names_for(root: &Path, enabled: &BTreeSet<ProjectRule>) -> bare_calls::DefkNames {
    let mut all = bare_calls::DefkNames::default();
    if !enabled.contains(&ProjectRule::DefkCalledBare) {
        return all;
    }
    // file ごとの defk の名はその file の中身だけで決まるので、変わった file だけ読み直す(保存ごとの 1 file の実行でも
    // repo 全体の名が要るため — #1025)。
    let files: Vec<(String, PathBuf)> = hy_files::collect(root)
        .into_iter()
        .filter_map(|path| relative_path(root, &path).map(|rel| (rel, path)))
        .collect();
    let found: Vec<bare_calls::DefkNames> = facts_cache::per_file(root, "defk-names", &files, |rel, path| {
        let source = std::fs::read_to_string(path).ok()?;
        source.contains("(defk").then(|| bare_calls::defk_names_in(&source, &module_of(rel)))
    });
    for names in found {
        all.extend(names);
    }
    all
}

/// DOEFF127・130 の表(repo の Hy の file 全部の型・effect・defk と推論)— どれかの規則が有効な時だけ 1 度作る。1 file の実行はその file を
/// stdin の中身で読む。推論の読み方は defk の見出し(editor-json の signatures)と同じ `signatures::World` の 1 か所。
fn effect_world_for(root: &Path, settings: &ProjectSettings, enabled: &BTreeSet<ProjectRule>, overlay: Option<(&str, &str)>) -> Option<signatures::World> {
    let definitions = settings.definitions.is_some() && enabled.contains(&ProjectRule::EffectsDisagreeWithInference);
    let translation = settings.translation.is_some() && settings.layers.is_some() && enabled.contains(&ProjectRule::TranslationEmitsIntent);
    (definitions || translation).then(|| signatures::World::build(root, overlay))
}

/// DOEFF142: 1 file の defhandler の引数の当たり → 下書き(critical — 責務の境界)。
fn judge_handler_arguments(root: &Path, rel: &str, source: &str, decl: &architecture::HandlerArguments, classes: &handler_arguments::ClassIndex) -> Vec<Draft> {
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
                message: format!("{} の {} が引数 {}({}{})を取る — 接続先と設定は Ask、client は (session val …)、状態は (session var …) で持つ", rel, found.handler, found.param, found.kind.word(), type_part),
                detail: Some(found.detail()),
                base: Severity::Error,
                explain: Explain::HandlerArgumentHoldsState { handler: found.handler.clone(), param: found.param.clone(), kind: found.kind.word(), type_text: found.type_text.clone() },
            }
        })
        .collect()
}

/// DOEFF130 の母集団 — 翻訳の層(設定の handler_layers)の Hy の module。
fn is_translation_file(file: &LayerFile, translation: &settings::TranslationSettings) -> bool {
    file.file.language == Language::Hy && translation.handler_layers.contains(&file.site.layer)
}

/// DOEFF130: 翻訳の層の handler が業務の intent を出す所を判じる(error — 責務の境界の違反)。業務の intent = 撃った呼びの頭が、
/// 設定の intent_layers の module の大文字の名(型)で、handler と同じ service の物(他の service の公開の intent は数えない)。撃った呼びの頭が repo の defk なら、その先を max_depth 段まで辿る —
/// 定義の本文の名だけでは、import した関数を経由した intent がすり抜ける(agora-redesign #956・#942 の独立レビュー)。
fn judge_translation_intents(
    file: &LayerFile,
    source: &str,
    layers: &LayerSettings,
    translation: &settings::TranslationSettings,
    index: &HashMap<String, ModuleSite>,
    world: &signatures::World,
) -> Vec<Draft> {
    let intent_layer_of = |qualified: &str| -> Option<LayerId> {
        let (module, name) = qualified.rsplit_once('.')?;
        // 型の名(頭が大文字)だけ — intent の層は型だけを置く
        if !name.starts_with(|c: char| c.is_ascii_uppercase()) {
            return None;
        }
        let site = index.get(module).or_else(|| index.get(&format!("{}.__init__", module)))?;
        // 他の service の公開の intent を出すのは翻訳の仕事(DOEFF156 の「他の service の公開の効果」と同じ読み — agora-redesign #1134 の決め)。
        // 止めるのは handler と同じ service の intent だけ。どちらかの service が決まらない(層が先の置き場)時は、今までどおり数える。
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
                    "{} の handler {}(層 {})が {} を経由して、層 {} の業務の intent {} を出す — 翻訳の handler は doeff の汎用の effect だけを出す",
                    file.file.rel, intent.handler, handler_layer, via, intent_layer, intent.effect()
                ),
                None => format!(
                    "{} の handler {}(層 {})が層 {} の業務の intent {} を出す — 翻訳の handler は doeff の汎用の effect だけを出す",
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

/// DOEFF127: 業務の Hy の file の defk のうち `:effects` を宣言した物で、宣言と推論が合わない所を判じる。違反の場所 = 宣言に無い
/// effect に至る撃った呼びの頭 / 起こさない effect の `:effects` の中の名(見出しに数を出さず、違反している所に出すため — #849)。
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
                    format!("{} の defk {} が {} を経由して、:effects に無い effect {} を起こす", file.rel, definition, via, effect)
                }
                signatures::EffectMismatch::Undeclared { definition, effect, via: None, .. } => {
                    format!("{} の defk {} が :effects に無い effect {} を撃つ", file.rel, definition, effect)
                }
                signatures::EffectMismatch::Unused { definition, effect, .. } => {
                    format!("{} の defk {} は :effects に {} を書いているが、起こしていない", file.rel, definition, effect)
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

/// DOEFF126: 業務の Hy の file(検の置き場も)で、defk の定義を素で呼んでいる所を判じる(error — 静かな誤りなので)。
fn judge_bare_calls(
    file: &SourceFile,
    source: &str,
    definitions: &settings::DefinitionSettings,
    enabled: &BTreeSet<ProjectRule>,
    defks: &bare_calls::DefkNames,
    program_params: &param_calls::ProgramParams,
) -> Vec<Draft> {
    let in_population = is_definition_file(&file.rel, definitions) || is_test_file(&file.rel, definitions);
    if !enabled.contains(&ProjectRule::DefkCalledBare) || !in_population || defks.is_empty() {
        return Vec::new();
    }
    let lines = LineIndex::new(source);
    let module = module_of(&file.rel);
    let facts = read_facts(Language::Hy, source, &module, &definitions.tags);
    let scope = smells::Scope { module: &module, bindings: &facts.bindings };
    let mut drafts: Vec<Draft> = bare_calls::bare_calls_in(source, scope, defks)
        .into_iter()
        .map(|call| Draft {
            rule: ProjectRule::DefkCalledBare,
            layer: None,
            rel: file.rel.clone(),
            path: file.path.clone(),
            range: lines.range(call.span.start, call.span.end),
            message: format!("{} の {} が defk {} を素で呼ぶ — 答えではなく Program が返る", file.rel, call.definition, call.callee),
            detail: Some(call.detail()),
            base: Severity::Error,
            explain: Explain::BareDefkCall { call },
        })
        .collect();
    // 引数で受けた関数を素で呼ぶ形 — 呼び手がその引数に defk か fnk を渡している時だけ。
    drafts.extend(param_calls::param_calls_in(source, scope, defks, program_params).into_iter().map(|call| Draft {
        rule: ProjectRule::DefkCalledBare,
        layer: None,
        rel: file.rel.clone(),
        path: file.path.clone(),
        range: lines.range(call.span.start, call.span.end),
        message: format!(
            "{} の {} が引数 {} を素で呼ぶ — 呼び手({})が {} を渡すので、答えではなく Program が返る",
            file.rel, call.definition, call.param, call.caller, call.passed
        ),
        detail: Some(call.detail()),
        base: Severity::Error,
        explain: Explain::ParamCalledBare { call },
    }));
    drafts
}

/// DOEFF126 の 2 つ目の形の材料 — repo の全部の呼びのうち、引数に defk か fnk を渡している物(規則が有効な時だけ)。
fn program_params_for(
    root: &Path,
    enabled: &BTreeSet<ProjectRule>,
    defks: &bare_calls::DefkNames,
) -> param_calls::ProgramParams {
    let mut params = param_calls::ProgramParams::default();
    if !enabled.contains(&ProjectRule::DefkCalledBare) || defks.is_empty() {
        return params;
    }
    // file ごとの結果は、その file の中身と repo 全体の defk の名(defks)で決まる。defks の指紋を印に含めて file ごとに覚え、
    // defks が変わらない間は変わった file だけ解析し直す(defks が変われば全部を作り直す・#1026)。
    let files: Vec<(String, PathBuf)> = hy_files::collect(root)
        .into_iter()
        .filter_map(|path| relative_path(root, &path).map(|rel| (rel, path)))
        .collect();
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

/// DOEFF121 の問いを当てる定義の種類(関数と handler)。
fn is_judgment_kind(kind: DefinitionKind) -> bool {
    matches!(kind, DefinitionKind::Defk | DefinitionKind::Deff | DefinitionKind::Defn | DefinitionKind::DefnAsync | DefinitionKind::Defp | DefinitionKind::Defpp)
}

/// DOEFF121〜125: 業務の Hy の file の臭いを判じる(重さの既定は warning — Absent / Raise が本線に入ったので info から上げた(ADR-DOE-HY-007 R9)。
/// 設定の rules.<ID>.severity で info に下げられる。登録簿に載った warning は info)。
fn judge_smells(
    file: &SourceFile,
    source: &str,
    settings: &ProjectSettings,
    definitions: &settings::DefinitionSettings,
    enabled: &BTreeSet<ProjectRule>,
    failure: &smells::FailureTypes,
) -> Vec<Draft> {
    if !is_definition_file(&file.rel, definitions) || !enabled.iter().any(|rule| rule.is_smell()) {
        return Vec::new();
    }
    // DOEFF121 は判断の層(smells.shape_check_layers)の file だけ — 層は path の置き場所か、層が先の dir ならタグから推す。
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
                smells::SmellKind::ShapeCheck { field, .. } => format!("{} の {} が欄 \"{}\" の形を isinstance で検める", file.rel, smell.definition, field),
                smells::SmellKind::FailureRethrow { failure_type, subject } => {
                    format!("{} の {} が失敗 {}({})を return し直す", file.rel, smell.definition, failure_type, subject)
                }
                smells::SmellKind::BindThenReturn { name } => format!("{} の {} が {} を束ねてすぐ返す", file.rel, smell.definition, name),
                smells::SmellKind::FieldsJoined { value, fields } => {
                    format!("{} の {} が {} の欄 {} を文字列につなぐ", file.rel, smell.definition, value, fields.join("・"))
                }
                smells::SmellKind::RebuiltAccumulator { name } => format!("{} の {} がループの中で {} を作り直す", file.rel, smell.definition, name),
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

/// DOEFF110〜112: file の定義の書き方を判じる(defn の禁止・deff の理由の註・定義のタグ必須)。
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
                    (format!("{} の defclass {} は外の世界に触る({})— 土台の handler にする", file.rel, name, evidence.join("・")), Severity::Error)
                }
                explain::ClassVerdict::Stateful { mutations } => {
                    (format!("{} の defclass {} は変わる状態を持つ({})— 状態は handler の (session var …) へ", file.rel, name, mutations.join("・")), Severity::Warning)
                }
                explain::ClassVerdict::DataOnly => (format!("{} の defclass {} は欄だけ — defrecord にできる", file.rel, name), Severity::Info),
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
                format!("{} の {} は {} で書かれた検 — deftest で書く", file.rel, name, head),
                name.clone(),
                Explain::TestNotDeftest { name: name.clone(), head: head.to_string() },
            ));
        }
        if !in_scope || head == "fn" {
            continue;
        }
        let (line, previous) = line_and_previous(source, definition.start);
        let same_line = parse_reason_comment(&line, &definitions.deff_reason_marker);
        // 直前の行は、註だけの行の時に限って読む(前の定義の行末の註を取り違えない)。
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
                format!("{} の {} は {} で書かれている — defk で書く", file.rel, name, head),
                name.clone(),
                Explain::DefnForbidden { name: name.clone(), head: head.to_string(), declared_kind: declared_kind.clone() },
            ));
        }
        if enabled.contains(&ProjectRule::DeffNeedsReason) && head == "deff" {
            // 決定的に判じるのは「註が無い・理由が空・同上とその変形」だけ。受け入れるかは Jev(DOEFF203)が決める。
            let problem = match &comment {
                None => Some(explain::DeffReasonProblem::Missing),
                Some(ReasonComment { detail, .. }) if is_not_a_reason(detail) => Some(explain::DeffReasonProblem::NoDetail { detail: detail.clone() }),
                Some(_) => None,
            };
            if let Some(problem) = problem {
                let found = draft(
                    ProjectRule::DeffNeedsReason,
                    definition.name.span,
                    format!("{} の deff {} の理由の註 — {}", file.rel, name, problem.short()),
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
                    format!("{} の {}({})の :tags に {} が無い", file.rel, name, head, missing.join("・")),
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

// --- JsonValue の使い場所(DOEFF120)---------------------------------------------------

/// DOEFF120 が数える名 — 素の dict・list・str … を名で包んだだけの型(JsonObject = dict[str, JsonValue] も同じ逃げ道)。
const JSON_VALUE_NAMES: &[&str] = &["JsonValue", "JSONValue", "JsonObject", "JSONObject"];

/// どの repo でも JsonValue を使ってよい汎用の解き手の module(doeff-hy の defwire の実行時・doeff-records の wire)。
/// module の綴りの末尾の段で照らす(doeff の repo の `packages/doeff-hy/src/doeff_hy/wire.hy` = `packages.doeff-hy.src.doeff_hy.wire` も当たる)。
const BUILTIN_WIRE_MODULES: &[&str] = &["doeff_hy.wire", "doeff_records.wire"];

/// DOEFF120 の母集団で降りない dir(`.` で始まる隠し dir も降りない — `.venv`・`.git`・`.worktrees` …)。
const JSON_VALUE_SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// DOEFF120 の許しの判定(閉じた集合)。
enum JsonValueAllowance {
    /// JsonValue を使ってよい。
    Allowed,
    /// 使えない(訳は説明に出す)。
    Refused(explain::JsonValueRefusal),
}

/// DOEFF120 の許しの方針 — module が JsonValue を使ってよいかを、この 1 か所だけで決める。
///
/// 方針は差し替えられる形にしてある: 許す場所の決め方を変える時(例: defwire の macro が生む解き手の中だけを許す形へ移る時)は、
/// この関数の中身だけを替える。呼び手(母集団・名の数え方・鍵・説明の形)は変わらない。
///
/// 今の方針(agora-redesign #840・operator 2026-09-28「JsonValue に触ってよいのは、汎用の解き手と、送受信そのものを行う foundation だけ」):
/// 1. 組み込みの汎用の解き手(`BUILTIN_WIRE_MODULES`)は許す。
/// 2. architecture.hy の `:wire-modules` の pattern に当たり、かつ foundation の層(`:foundation` の層の置き場)に在る module は許す。
/// 3. `:wire-modules` に当たっても foundation の外なら許さない(訳を説明に出す)。当たらなければ許さない。
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
            foundation_dir: architecture.foundation.as_ref().map(|f| format!("{}/{}", settings::normalize_dir(&architecture.root), f)),
        }),
    }
}

/// module の綴りの pattern の照合(`.` 区切り — `*` は段の中の任意の綴り・`**` は 0 個以上の段。test_paths の glob と同じ照らし方)。
fn module_pattern_matches(pattern: &str, module: &str) -> bool {
    let pattern: Vec<&str> = pattern.split('.').collect();
    let module: Vec<&str> = module.split('.').collect();
    segments_match(&pattern, &module)
}

/// DOEFF120 の母集団の file か — Hy か Python で、隠し dir と `JSON_VALUE_SKIPPED_DIRS` の下でない。
fn is_json_value_file(rel: &str) -> bool {
    let parts: Vec<&str> = rel.split('/').collect();
    let dirs = &parts[..parts.len().saturating_sub(1)];
    language_of(Path::new(rel)).is_some() && !dirs.iter().any(|dir| dir.starts_with('.') || JSON_VALUE_SKIPPED_DIRS.contains(dir))
}

/// DOEFF120 の母集団(repo の Hy と Python の file の全部・path の順)。
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

/// DOEFF120: module 1 つの JsonValue の使用を数え、許されない module なら 1 件出す(位置は最初の使用・数とほかの行は説明に)。
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
            "{} が {} を {} か所で使う — JsonValue に触ってよいのは汎用の解き手と :wire-modules に挙げた foundation の送受信の module だけ",
            file.rel,
            names.join("・"),
            uses.count
        ),
        detail: None,
        base: Severity::Error,
        explain: Explain::JsonValueUse { module, uses, refusal },
    })
}

// --- 環境の語(DOEFF108)---------------------------------------------------------------

/// 業務の file か(置き場の 1 つの下で、除く置き場・区切りに当たらず、拡張子が合う)。
fn is_environment_file(rel: &str, env: &EnvironmentSettings) -> bool {
    let extension_ok = Path::new(rel).extension().and_then(|e| e.to_str()).is_some_and(|e| env.extensions.contains(e));
    extension_ok
        && env.paths.iter().any(|place| matches_place(rel, place))
        && !env.exclude.iter().any(|place| matches_place(rel, place))
        && !rel.split('/').any(|part| env.exclude_parts.contains(part))
}

/// 業務の file を全部集める(path の順)。
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

/// 定義が名を見る対象か — handler(defhandler と、`[effect k]` を受ける do の関数)と、組み立ての file の最上位の定義。
fn names_business_handler(definition: &Definition, assembly_file: bool) -> bool {
    if definition.container.is_some() {
        return false;
    }
    let dispatch = matches!(definition.kind, DefinitionKind::Defn | DefinitionKind::Defk)
        && definition.params.len() == 2
        && definition.params.first().is_some_and(|p| p == "effect");
    assembly_file || definition.kind == DefinitionKind::Defhandler || dispatch
}

/// DOEFF108: 業務の file の名と、handler・組み立ての関数の名に環境の語が無いか(名前だけで判じる — 到達の解析はしない)。
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
            format!("{} の file の名が環境の語({})を含む — 業務の file と handler は環境を知らない。環境で差し替えるのは土台の汎用の handler だけ", file.rel, words.join("・")),
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
            format!("{} の {} が環境の語({})を含む — 業務の file と handler は環境を知らない。環境で差し替えるのは土台の汎用の handler だけ", file.rel, name, words.join("・")),
            Some(name.clone()),
            Explain::EnvironmentName { subject: NameSubject::Definition { name, kind: definition.kind.as_str() }, words },
        ));
    }
    drafts
}

// --- 仕上げ -------------------------------------------------------------------------------

/// 違反の鍵 `<repo の根からの path>::<law の名か規則の ID>[::<細目>]`(登録簿・誤判定の一覧が照らす綴り — 組むのはここ 1 か所)。
fn finding_key(law: Option<&LawSpec>, rule: ProjectRule, rel: &str, detail: Option<&str>) -> String {
    let segment = law.map(|l| l.name.as_str()).unwrap_or_else(|| rule.id());
    match detail {
        Some(detail) => format!("{}::{}::{}", rel, segment, detail),
        None => format!("{}::{}", rel, segment),
    }
}

/// 鍵の区切り(`<path>::<law の名か規則の ID>[::<細目>]` の 2 つ目)が指す規則のうち、この実行で判じた物(有効・意味の規則でない・
/// 照合中でない)。区切りから規則を引けない鍵(登録簿の dir を共用する他の検の鍵)と、判じていない規則の鍵は空。
fn judged_rules_of(key: &str, settings: &ProjectSettings, enabled: &BTreeSet<ProjectRule>) -> Vec<ProjectRule> {
    let Some(segment) = key.split("::").nth(1) else { return Vec::new() };
    let named: Vec<ProjectRule> = match ProjectRule::parse(segment) {
        Some(rule) => vec![rule],
        None => settings
            .laws
            .iter()
            .filter(|law| law.name == segment)
            .flat_map(|law| law.rules.iter())
            .filter_map(|rule| match rule {
                settings::ProjectRuleOrExternal::Project(rule) => Some(*rule),
                settings::ProjectRuleOrExternal::External(_) => None,
            })
            .collect(),
    };
    named
        .into_iter()
        .filter(|rule| enabled.contains(rule) && !rule.is_semantic() && !settings.registry.reconciling.contains(rule))
        .collect()
}

/// DOEFF166: 登録簿の鍵のうち、この実行で判じた規則の鍵で、どの下書きの鍵にも当たらない物を下書きにする(鍵の順・鍵の細目 = 登録簿の鍵)。
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
            let source = registry.origins.get(key).cloned().unwrap_or_else(|| "登録簿".to_string());
            let ids: Vec<String> = rules.iter().map(|rule| rule.id().to_string()).collect();
            Some(Draft {
                rule: ProjectRule::RegistryEntryStale,
                layer: None,
                path: root.join(&source),
                rel: source.clone(),
                range: zero_range(),
                message: format!("登録簿 {} の鍵 {} はもう当たらない({} を repo 全体に当てた)— 行を消す", source, key, ids.join("・")),
                detail: Some(key.clone()),
                base: Severity::Error,
                explain: Explain::RegistryEntryStale { key: key.clone(), source, rules: ids },
            })
        })
        .collect()
}

/// 人の判定の一覧(意味の規則の誤判定と正例 — 鍵 → 理由)。
#[derive(Debug, Default)]
struct JudgedLabels {
    false_positives: BTreeMap<String, String>,
    true_positives: BTreeMap<String, String>,
}

/// 意味の規則の設定の誤判定の一覧と正例の一覧を読む。両方に載った鍵は食い違いとして理由を積み、どちらとしても読まない。
fn judged_labels(root: &Path, settings: &ProjectSettings, errors: &mut Vec<String>) -> JudgedLabels {
    let Some(semantic) = &settings.semantic else { return JudgedLabels::default() };
    let negatives = registry::JudgedKeys::load(root, &semantic.false_positives);
    let positives = registry::JudgedKeys::load(root, &semantic.true_positives);
    errors.extend(negatives.problems);
    errors.extend(positives.problems);
    let mut labels = JudgedLabels { false_positives: negatives.reasons, true_positives: positives.reasons };
    let both: Vec<String> = labels.false_positives.keys().filter(|k| labels.true_positives.contains_key(*k)).cloned().collect();
    for key in both {
        errors.push(format!("判定の一覧の食い違い: {} が誤判定の一覧と正例の一覧の両方に在る — どちらとしても読まない", key));
        labels.false_positives.remove(&key);
        labels.true_positives.remove(&key);
    }
    labels
}

/// 人の判定と Jev の答えを突き合わせる(鍵の順)。一覧に載っても答えの無い(未判定の・今は無い)定義は listed にだけ数える。
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

/// 下書きに law・鍵・登録簿・照合中を当てて違反にする(path と位置の順)。意味の規則の当たりで鍵が誤判定の一覧に載った物は出さない
/// (返す数 = 外した数)。
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
            let registered = registry.keys.contains(&key);
            let base = settings.severity.get(&draft.rule).copied().unwrap_or(draft.base);
            let reconciling = settings.registry.reconciling.contains(&draft.rule);
            let severity = if reconciling {
                Severity::Info
            } else {
                match (registered, base) {
                    (true, Severity::Error) => settings.registered_severity.get(&draft.rule).copied().unwrap_or(Severity::Warning),
                    // 登録簿に載った warning(移行の間の旧い形など)は info に下げる。
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

/// 判じる規則の集合(`enable`・`disable` を展開した ID の列から。None は全部)。
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
        // DOEFF137(agora-redesign #1796): 理由(doeff 側の契約テストの名)つきの none は、縁の検が無くても鳴らない。
        let waived = ContractTest::Waived(ContractTestWaiver::DoeffContractTest(node("packages/doeff-core-effects/tests/test_http.hy::test-contract")));
        assert_eq!(contract_test_breach(&waived, || false), None);
        let covered_here = ContractTest::Waived(ContractTestWaiver::CoveredByRepoTest(node("tests/test_main.hy::test-main")));
        assert_eq!(contract_test_breach(&covered_here, || false), None);
    }

    #[test]
    fn none_without_reason_rings_even_when_an_edge_test_exists() {
        // 理由の無い none は鳴る — 縁の検が届いていても宣言の誤りとして鳴らす(黙って通さない・#1796)。
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
}
