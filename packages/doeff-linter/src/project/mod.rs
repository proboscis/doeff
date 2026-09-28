//! 層の規則(DOEFF101〜108)— repo の module の一覧と設定を見て判じる規則。Hy と Python の両方の file を読む。
//!
//! 規則の中身は設定(`[tool.doeff-linter.layers]` ほか・`settings.rs`)から受け取り、層の名前・role・環境の語を
//! Rust に書き込まない。Hy の事実は doeff-indexer の hy-index の解析(関数として呼ぶ)と読み取り器から取る。
//!
//! 流れ: 母集団の file を集める → file ごとに事実を読む(`facts.rs`)→ 規則ごとに違反の下書きを作る →
//! law と登録簿の鍵を当てて重さを決める(`finish`)。

pub mod architecture;
pub mod explain;
pub mod facts;
pub mod names;
pub mod param_calls;
pub mod registry;
pub mod notice;
pub mod rule;
pub mod bare_calls;
pub mod semantic;
pub mod smells;
pub mod unreadable;
pub mod settings;
pub mod signatures;
pub mod call_view;

use std::collections::{BTreeMap, BTreeSet, HashMap};
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
use settings::{EnvironmentSettings, LayerId, LayerSettings, ProjectSettings};

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
    pub registered: bool,
    /// これは何か・なぜ違反か・law の文(explain.rs が作る)。
    pub explanation: Explanation,
    /// 判定の出どころ(決定的な規則か Jev か)。
    pub origin: FindingOrigin,
    /// Jev の判定の確率(Jev の違反だけ)。
    pub probability: Option<f64>,
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
    /// 意味の規則の要約(設定が無ければ None)。
    pub semantic: Option<semantic::SemanticSummary>,
}

/// 何を判じるか — repo 全体か、保存前の内容の 1 file。
pub enum Target<'a> {
    Whole,
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
    let mut registry = Registry::load(root, &settings.registry.dirs, &settings.registry.files);
    if !settings.registry.config_files.is_empty() {
        let base = settings.config_dir.clone().unwrap_or_else(|| root.to_path_buf());
        let extra = Registry::load(&base, &[], &settings.registry.config_files);
        registry.keys.extend(extra.keys);
        registry.problems.extend(extra.problems);
    }
    report.errors.extend(registry.problems.iter().cloned());
    let raw = raw_settings(root, settings, &mut report.errors);
    let wants_raw = enabled.contains(&ProjectRule::RawSideEffectDirect) || enabled.contains(&ProjectRule::RawSideEffectVia);
    let mut drafts = Vec::new();

    // 読めない Hy の file の知らせ(DOEFF128)の材料 — 全体なら repo の Hy の file の全部、1 file ならその保存前の中身。
    let unreadable_target: Option<(PathBuf, String)> = match &target {
        Target::Whole => None,
        Target::Single { path, source } => Some((path.clone(), source.to_string())),
    };
    match target {
        Target::Whole => {
            let layer_files = settings.layers.as_ref().map(|layers| collect_layer_files(root, layers)).unwrap_or_default();
            semantic_files = layer_files.iter().map(|f| (f.clone(), None)).collect();
            let wants_plain = settings.semantic.as_ref().is_some_and(|s| s.plain_callable.is_some() || s.class_role.is_some() || s.mixed_concerns.is_some());
            plain_files = match (&settings.definitions, wants_plain) {
                (Some(definitions), true) => hy_index::collect_hy_files(root)
                    .into_iter()
                    .filter_map(|path| {
                        let rel = relative_path(root, &path)?;
                        is_definition_file(&rel, definitions).then_some((SourceFile { rel, path, language: Language::Hy }, None))
                    })
                    .collect(),
                _ => Vec::new(),
            };
            let env_files = settings.environment.as_ref().map(|env| collect_environment_files(root, env)).unwrap_or_default();
            indexes = whole_hy_index(root, settings, enabled, &raw, &layer_files, &env_files, wants_raw);
            let hy = &indexes;
            if let Some(layers) = &settings.layers {
                let index = module_index(&layer_files);
                let judged: Vec<LayerJudgement> = layer_files
                    .par_iter()
                    .map(|file| match std::fs::read_to_string(&file.file.path) {
                        Ok(source) => judge_layer_file(file, &source, layers, settings, enabled, &index, hy.get(&file.file.rel)),
                        Err(error) => LayerJudgement { errors: vec![format!("{}: 読めない: {}", file.file.rel, error)], ..LayerJudgement::default() },
                    })
                    .collect();
                let mut crossings: BTreeSet<(String, String)> = BTreeSet::new();
                for judged in judged {
                    drafts.extend(judged.drafts);
                    report.modules.extend(judged.summary);
                    report.errors.extend(judged.errors);
                    crossings.extend(judged.crossings);
                }
                if let Some(architecture) = &settings.architecture {
                    if enabled.contains(&ProjectRule::UnusedDependency) {
                        drafts.extend(judge_unused_dependencies(root, architecture, &crossings));
                    }
                }
            }
            if let (Some(architecture), Some(layers)) = (&settings.architecture, &settings.layers) {
                if enabled.contains(&ProjectRule::UndeclaredPlace) || enabled.contains(&ProjectRule::UndeclaredDirectory) {
                    let files = collect_architecture_files(root, architecture, layers);
                    drafts.extend(judge_places(root, architecture, layers, &files, enabled, PlaceScope::Whole, None));
                }
            }
            if let Some(definitions) = &settings.definitions {
                if wants_definitions(enabled) {
                    let failure = failure_types_for(root, enabled, &definitions.tags);
                    let defks = defk_names_for(root, enabled);
                    let program_params = program_params_for(root, enabled, &defks);
                    let effect_world = effect_world_for(root, enabled, None);
                    let files: Vec<SourceFile> = hy_index::collect_hy_files(root)
                        .into_iter()
                        .filter_map(|path| {
                            let rel = relative_path(root, &path)?;
                            (is_definition_file(&rel, definitions) || is_test_file(&rel, definitions))
                                .then_some(SourceFile { rel, path, language: Language::Hy })
                        })
                        .collect();
                    let judged: Vec<Result<Vec<Draft>, String>> = files
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
                                    if enabled.contains(&ProjectRule::JudgmentPerformsEffect) {
                                        found.extend(judge_judgment_effects(file, &source, definitions, effect_world.as_ref()));
                                    }
                                    found
                                })
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
            if let (Some(layers), Some(rel)) = (&settings.layers, &rel) {
                if let Some((site, language)) = classify_layer_file(rel, layers).or_else(|| infer_layer_site(rel, source, layers)) {
                    let file = LayerFile { file: SourceFile { rel: rel.clone(), path: path.clone(), language }, module: module_of(rel), site };
                    let mut layer_files = collect_layer_files(root, layers);
                    layer_files.push(file.clone());
                    let index = module_index(&layer_files);
                    let judged = judge_layer_file(&file, source, layers, settings, enabled, &index, hy_file.as_ref());
                    drafts.extend(judged.drafts);
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
                    drafts.extend(judge_smells(&file, source, settings, definitions, enabled, &failure_types_for(root, enabled, &definitions.tags)));
                    let defks = defk_names_for(root, enabled);
                    let program_params = program_params_for(root, enabled, &defks);
                    drafts.extend(judge_bare_calls(&file, source, definitions, enabled, &defks, &program_params));
                    let effect_world = effect_world_for(root, enabled, Some((rel.as_str(), source)));
                    if enabled.contains(&ProjectRule::EffectsDisagreeWithInference) {
                        drafts.extend(judge_effect_mismatches(&file, source, definitions, effect_world.as_ref()));
                    }
                    if enabled.contains(&ProjectRule::JudgmentPerformsEffect) {
                        drafts.extend(judge_judgment_effects(&file, source, definitions, effect_world.as_ref()));
                    }
                }
            }
            if let (Some(architecture), Some(rel), Some(language)) = (&settings.architecture, &rel, language_of(&path)) {
                if enabled.contains(&ProjectRule::JsonValueOutsideWire) && is_json_value_file(rel) {
                    let file = SourceFile { rel: rel.clone(), path: path.clone(), language };
                    drafts.extend(judge_json_value(&file, source, architecture, settings.layers.as_ref()));
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
            let (found, summary, errors) = judge_semantic(root, semantic, layers, enabled, &semantic_files, semantic_mode, &plain);
            drafts.extend(found);
            report.errors.extend(errors);
            report.semantic = Some(summary);
        }
    }
    report.findings = finish(drafts, settings, &registry);
    // 読めない Hy の file は、有効な規則の一覧に関わらず知らせる(違反が欠けているのを黙らせない — DOEFF128)。
    report.findings.extend(unreadable_findings(root, settings, &unreadable_target));
    report
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
    if !(wants_raw && settings.raw.is_some()) && !wants_env && !wants_classes {
        return HashMap::new();
    }
    let index = if (enabled.contains(&ProjectRule::RawSideEffectVia) && settings.raw.is_some()) || wants_classes {
        hy_index::index_root(root, raw)
    } else {
        let paths: BTreeSet<PathBuf> = layer_files
            .iter()
            .map(|f| &f.file)
            .chain(env_files.iter())
            .filter(|f| f.language == Language::Hy)
            .map(|f| f.path.clone())
            .collect();
        hy_index::index_paths(root, &paths.into_iter().collect::<Vec<_>>(), raw)
    };
    index
        .files
        .into_iter()
        .filter_map(|file| relative_path(root, Path::new(&file.path)).map(|rel| (rel, file)))
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
        let (found, crossed) = judge.service_dependencies(&facts, index, architecture, enabled.contains(&ProjectRule::ServiceDependency));
        drafts.extend(found);
        crossings = crossed;
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
        if !raw.allowed.contains(&file.site.layer) {
            if enabled.contains(&ProjectRule::RawSideEffectDirect) {
                drafts.extend(judge.raw_direct(hy_file, raw));
            }
            if enabled.contains(&ProjectRule::RawSideEffectVia) {
                drafts.extend(judge.raw_via(hy_file));
            }
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
            if !judge {
                continue;
            }
            let declared = own.is_some_and(|s| s.depends_on.contains(&other_name));
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
                    depends_on,
                    open_layers: readable.to_vec(),
                    widened: readable != architecture.open_layers.as_slice(),
                },
            ));
        }
        (drafts, crossings)
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
    fn raw_direct(&self, hy_file: &HyFileIndex, raw: &settings::RawSettingsSpec) -> Vec<Draft> {
        let definitions = &hy_file.definitions;
        let allowed = raw.allowed.iter().map(|id| self.layer_name(*id)).collect::<Vec<_>>().join("・");
        let mut chosen: BTreeMap<EvidenceSpot, EvidenceRef> = BTreeMap::new();
        for (definition_index, definition) in definitions.iter().enumerate() {
            for (evidence_index, evidence) in definition.raw.direct.iter().enumerate() {
                let spot = EvidenceSpot { path: evidence.path.clone(), start: evidence.range.start, end: evidence.range.end, name: evidence.name.clone() };
                let candidate = EvidenceRef { definition: definition_index, evidence: evidence_index };
                let keep_current = chosen
                    .get(&spot)
                    .is_some_and(|current| definitions[current.definition].full_range.start >= definition.full_range.start);
                if !keep_current {
                    chosen.insert(spot, candidate);
                }
            }
        }
        chosen
            .into_values()
            .map(|found| {
                let definition = &definitions[found.definition];
                let evidence = &definition.raw.direct[found.evidence];
                let mut draft = self.draft(
                    ProjectRule::RawSideEffectDirect,
                    evidence.range,
                    format!(
                        "定義 {}({})が生の副作用 {}({})に直に触る — 生の副作用に触ってよい層は {}",
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
    // 移し先の案: service は :context のタグ(`-` は `_`)、層は今の path の段の層の名か :role のタグから推した層。
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
        // path の段に層の名が無ければ、:role のタグをすべて許す層がちょうど 1 つの時にその層。
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
        let layer = by_path.or_else(by_roles).unwrap_or_else(|| "<層>".to_string());
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

/// DOEFF201・202: 設定した層の Hy の最上位の定義を Jev の問いにし、cache を読むか(既定)撃つ(--semantic)。答えの無い定義は未判定の数。
fn judge_semantic(
    root: &Path,
    settings: &semantic::SemanticSettings,
    layers: &LayerSettings,
    enabled: &BTreeSet<ProjectRule>,
    files: &[(LayerFile, Option<String>)],
    mode: &semantic::SemanticMode,
    plain: &PlainCallableInput<'_>,
) -> (Vec<Draft>, semantic::SemanticSummary, Vec<String>) {
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
        let index = hy_index::index_source(root, &file.file.path, &text);
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
            let index = hy_index::index_source(root, &file.path, &text);
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
                let index = hy_index::index_source(root, &file.path, &text);
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
    let drafts = outcome
        .answered
        .into_iter()
        .filter_map(|(item, answer)| {
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
        })
        .collect();
    (drafts, summary, errors)
}

// --- 定義の書き方(DOEFF110〜112)------------------------------------------------------

/// 定義の書き方の規則のどれかが有効か。
fn wants_definitions(enabled: &BTreeSet<ProjectRule>) -> bool {
    if enabled.iter().any(|rule| rule.is_smell())
        || enabled.contains(&ProjectRule::DefkCalledBare)
        || enabled.contains(&ProjectRule::EffectsDisagreeWithInference)
        || enabled.contains(&ProjectRule::JudgmentPerformsEffect)
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
    for path in hy_index::collect_hy_files(root) {
        let Some(rel) = relative_path(root, &path) else { continue };
        let Ok(source) = std::fs::read_to_string(&path) else { continue };
        if !(source.contains(":failure") || source.contains(":absent")) {
            continue;
        }
        let module = module_of(&rel);
        let facts = read_facts(Language::Hy, &source, &module, reading);
        all.extend(smells::failure_types_in(&source, smells::Scope { module: &module, bindings: &facts.bindings }));
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
        None => hy_index::collect_hy_files(root)
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
    // file ごとに並べて読む(保存ごとの 1 file の実行でも repo 全体を読むので)。
    let found: Vec<bare_calls::DefkNames> = hy_index::collect_hy_files(root)
        .par_iter()
        .filter_map(|path| {
            let rel = relative_path(root, path)?;
            let source = std::fs::read_to_string(path).ok()?;
            source.contains("(defk").then(|| bare_calls::defk_names_in(&source, &module_of(&rel)))
        })
        .collect();
    for names in found {
        all.extend(names);
    }
    all
}

/// DOEFF127・129 の表(repo の Hy の file 全部の型・effect・defk と推論)— どちらかの規則が有効な時だけ 1 度作る。1 file の実行はその file を
/// stdin の中身で読む。推論の読み方は defk の見出し(editor-json の signatures)と同じ `signatures::World` の 1 か所。
fn effect_world_for(root: &Path, enabled: &BTreeSet<ProjectRule>, overlay: Option<(&str, &str)>) -> Option<signatures::World> {
    (enabled.contains(&ProjectRule::EffectsDisagreeWithInference) || enabled.contains(&ProjectRule::JudgmentPerformsEffect))
        .then(|| signatures::World::build(root, overlay))
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

/// DOEFF129: `:tags` で役 judgment を名乗った defk が effect を起こす所を判じる(判断は値から値を決める純粋な定義 — #800 段階 4)。
/// 推論は DOEFF127 と同じ上からの見積もり(handler で受けた effect を引かない)なので重さは warning。追えない呼びの先は数えない。
fn judge_judgment_effects(
    file: &SourceFile,
    source: &str,
    definitions: &settings::DefinitionSettings,
    world: Option<&signatures::World>,
) -> Vec<Draft> {
    let Some(world) = world else { return Vec::new() };
    if !is_definition_file(&file.rel, definitions) || !source.contains("\"judgment\"") {
        return Vec::new();
    }
    let lines = LineIndex::new(source);
    signatures::judgment_effects(world, &file.rel, source)
        .into_iter()
        .map(|effect| {
            let message = match &effect.via {
                Some(via) => format!("{} の defk {}(役 judgment)が {} を経由して effect {} を起こす — 判断は effect を起こさない", file.rel, effect.definition, via, effect.effect()),
                None => format!("{} の defk {}(役 judgment)が effect {} を撃つ — 判断は effect を起こさない", file.rel, effect.definition, effect.effect()),
            };
            Draft {
                rule: ProjectRule::JudgmentPerformsEffect,
                layer: None,
                rel: file.rel.clone(),
                path: file.path.clone(),
                range: lines.range(effect.start, effect.end),
                message,
                detail: Some(format!("{}::{}", hy_mangle(&effect.definition), effect.effect())),
                base: Severity::Warning,
                explain: Explain::JudgmentEffect { effect },
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
    // file ごとに並べて読み、最後に束ねる。
    let found: Vec<param_calls::ProgramParams> = hy_index::collect_hy_files(root)
        .par_iter()
        .filter_map(|path| {
            let rel = relative_path(root, path)?;
            let source = std::fs::read_to_string(path).ok()?;
            let module = module_of(&rel);
            let bindings = facts::hy_bindings(&source, &module);
            let mut one = param_calls::ProgramParams::default();
            one.collect(&source, &rel, smells::Scope { module: &module, bindings: &bindings }, defks);
            Some(one)
        })
        .collect();
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

/// 下書きに law・鍵・登録簿・照合中を当てて違反にする(path と位置の順)。
fn finish(drafts: Vec<Draft>, settings: &ProjectSettings, registry: &Registry) -> Vec<Finding> {
    let narrator = Narrator { layers: settings.layers.as_ref(), raw: settings.raw.as_ref() };
    let mut findings: Vec<Finding> = drafts
        .into_iter()
        .map(|draft| {
            let law = settings.law_for(draft.rule, draft.layer);
            let segment = law.map(|l| l.name.clone()).unwrap_or_else(|| draft.rule.id().to_string());
            let key = match &draft.detail {
                Some(detail) => format!("{}::{}::{}", draft.rel, segment, detail),
                None => format!("{}::{}", draft.rel, segment),
            };
            let registered = registry.keys.contains(&key);
            let base = settings.severity.get(&draft.rule).copied().unwrap_or(draft.base);
            let severity = if settings.registry.reconciling.contains(&draft.rule) {
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
            Finding {
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
                registered,
                explanation: narrator.explain(&draft.explain, law),
            }
        })
        .collect();
    findings.sort_by(|a, b| (&a.rel, a.range.start, a.rule).cmp(&(&b.rel, b.range.start, b.rule)));
    findings
}

/// 判じる規則の集合(`enable`・`disable` を展開した ID の列から。None は全部)。
pub fn enabled_rules(enabled_ids: Option<&[String]>) -> BTreeSet<ProjectRule> {
    match enabled_ids {
        None => ProjectRule::ALL.into_iter().collect(),
        Some(ids) => ids.iter().filter_map(|id| ProjectRule::parse(id)).collect(),
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
