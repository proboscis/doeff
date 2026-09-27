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
pub mod registry;
pub mod rule;
pub mod settings;

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
}

/// 層の規則を走らせる。root は正規化した repo の根、enabled は有効な規則。
pub fn run(root: &Path, settings: &ProjectSettings, enabled: &BTreeSet<ProjectRule>, target: Target) -> ProjectReport {
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

    match target {
        Target::Whole => {
            let layer_files = settings.layers.as_ref().map(|layers| collect_layer_files(root, layers)).unwrap_or_default();
            let env_files = settings.environment.as_ref().map(|env| collect_environment_files(root, env)).unwrap_or_default();
            let hy = whole_hy_index(root, settings, enabled, &raw, &layer_files, &env_files, wants_raw);
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
                    drafts.extend(judge_places(root, architecture, &files, enabled, PlaceScope::Whole));
                }
            }
            if let Some(definitions) = &settings.definitions {
                if wants_definitions(enabled) {
                    let files: Vec<SourceFile> = hy_index::collect_hy_files(root)
                        .into_iter()
                        .filter_map(|path| {
                            let rel = relative_path(root, &path)?;
                            is_definition_file(&rel, definitions).then_some(SourceFile { rel, path, language: Language::Hy })
                        })
                        .collect();
                    let judged: Vec<Result<Vec<Draft>, String>> = files
                        .par_iter()
                        .map(|file| {
                            std::fs::read_to_string(&file.path)
                                .map(|source| judge_definitions(file, &source, definitions, enabled))
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
            // 根の中の file は、全体の実行と同じく「根 + 根からの path」を出す(エディタが結果を差し替える鍵を揃えるため)。
            let path = rel.as_ref().map(|r| root.join(r)).unwrap_or(path);
            let hy_file = match (language_of(&path), wants_raw || enabled.contains(&ProjectRule::EnvironmentName)) {
                (Some(Language::Hy), true) => hy_index::index_stdin_source(root, &path, source, &raw).files.into_iter().next(),
                _ => None,
            };
            if let (Some(layers), Some(rel)) = (&settings.layers, &rel) {
                if let Some((site, language)) = classify_layer_file(rel, layers) {
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
                    drafts.extend(judge_places(root, architecture, &[file], enabled, PlaceScope::Single));
                }
            }
            if let (Some(definitions), Some(rel)) = (&settings.definitions, &rel) {
                if wants_definitions(enabled) && language_of(&path) == Some(Language::Hy) && is_definition_file(rel, definitions) {
                    let file = SourceFile { rel: rel.clone(), path: path.clone(), language: Language::Hy };
                    drafts.extend(judge_definitions(&file, source, definitions, enabled));
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
    report.findings = finish(drafts, settings, &registry);
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
    if !(wants_raw && settings.raw.is_some()) && !wants_env {
        return HashMap::new();
    }
    let index = if enabled.contains(&ProjectRule::RawSideEffectVia) && settings.raw.is_some() {
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
                    best = Some((place.depth(), ModuleSite { layer: LayerId(index), dir: found.dir, service: found.service }));
                }
            }
        }
    }
    best.map(|(_, site)| (site, language))
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
    let placement = Placement { layer: file.site.layer, dir: file.site.dir.clone(), service: file.site.service.clone(), roles };
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
    /// 読む先は B の open-layers の層(intent)であること。共有の置き場と foundation は service ではないので見ない。
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
            let open = architecture.open_layers.iter().any(|l| *l == self.layer_name(site.layer));
            if declared && open {
                continue;
            }
            let depends_on = own.map(|s| s.depends_on.clone()).unwrap_or_default();
            drafts.push(self.draft(
                ProjectRule::ServiceDependency,
                self.range(span),
                if declared {
                    format!("{}(service {})が依存先 {} の層 {} の {} を読む — 依存先で読めるのは {} だけ", self.file.file.rel, own_name, other_name, self.layer_name(site.layer), target, architecture.open_layers.join("・"))
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
                    open_layers: architecture.open_layers.clone(),
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
    if architecture.legacy.iter().any(|l| rel == l.dir || rel.starts_with(&format!("{}/", l.dir))) {
        return PlaceVerdict::Declared;
    }
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
fn judge_places(root: &Path, architecture: &architecture::Architecture, files: &[SourceFile], enabled: &BTreeSet<ProjectRule>, scope: PlaceScope) -> Vec<Draft> {
    let _ = root;
    let mut drafts = Vec::new();
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
                        Explain::UndeclaredPlace { rel: file.rel.clone(), problem, root: architecture.root.clone() },
                        file.rel.clone(),
                    ));
                }
            }
            PlaceVerdict::UndeclaredDirectory { dir, problem } => {
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

// --- 定義の書き方(DOEFF110〜112)------------------------------------------------------

/// 定義の書き方の規則のどれかが有効か。
fn wants_definitions(enabled: &BTreeSet<ProjectRule>) -> bool {
    [ProjectRule::DefnForbidden, ProjectRule::DeffNeedsReason, ProjectRule::DefinitionTagsRequired].iter().any(|rule| enabled.contains(rule))
}

/// 定義の規則の母集団の file か(置き場の 1 つの下 — 置き場が空なら全部 — で、除く置き場・区切りに当たらない Hy の file)。
fn is_definition_file(rel: &str, definitions: &settings::DefinitionSettings) -> bool {
    language_of(Path::new(rel)) == Some(Language::Hy)
        && (definitions.paths.is_empty() || definitions.paths.iter().any(|place| matches_place(rel, place)))
        && !definitions.exclude.iter().any(|place| matches_place(rel, place))
        && !rel.split('/').any(|part| definitions.exclude_parts.contains(part))
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

/// DOEFF110〜112: file の定義の書き方を判じる(defn の禁止・deff の理由の註・定義のタグ必須)。
fn judge_definitions(file: &SourceFile, source: &str, definitions: &settings::DefinitionSettings, enabled: &BTreeSet<ProjectRule>) -> Vec<Draft> {
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
    for definition in &facts.definitions {
        let name = definition.name.name.clone();
        let head = definition.head.as_str();
        if enabled.contains(&ProjectRule::DefnForbidden) && matches!(head, "defn" | "defn/a") && !definition.compile_time {
            drafts.push(draft(
                ProjectRule::DefnForbidden,
                definition.name.span,
                format!("{} の {} は {} で書かれている — defk で書く", file.rel, name, head),
                name.clone(),
                Explain::DefnForbidden { name: name.clone(), head: head.to_string() },
            ));
        }
        if enabled.contains(&ProjectRule::DeffNeedsReason) && head == "deff" {
            let (line, previous) = line_and_previous(source, definition.start);
            let has_reason = [line, previous].iter().any(|text| text.split_once(';').is_some_and(|(_, comment)| comment.contains(&definitions.deff_reason_marker)));
            if !has_reason {
                drafts.push(draft(
                    ProjectRule::DeffNeedsReason,
                    definition.name.span,
                    format!("{} の deff {} に理由の註 `; {} …` が無い", file.rel, name, definitions.deff_reason_marker),
                    name.clone(),
                    Explain::DeffWithoutReason { name: name.clone(), marker: definitions.deff_reason_marker.clone() },
                ));
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
            let severity = if settings.registry.reconciling.contains(&draft.rule) {
                Severity::Info
            } else {
                match (registered, draft.base) {
                    (true, Severity::Error) => settings.registered_severity.get(&draft.rule).copied().unwrap_or(Severity::Warning),
                    (_, base) => base,
                }
            };
            Finding {
                rule: draft.rule,
                law: law.map(|l| l.name.clone()),
                adr: law.and_then(|l| l.adr.clone()),
                severity,
                path: draft.path,
                rel: draft.rel,
                range: draft.range,
                message: draft.message,
                hint: draft.rule.hint().to_string(),
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
