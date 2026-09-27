//! 層の規則(DOEFF101〜108)— repo の module の一覧と設定を見て判じる規則。Hy と Python の両方の file を読む。
//!
//! 規則の中身は設定(`[tool.doeff-linter.layers]` ほか・`settings.rs`)から受け取り、層の名前・role・環境の語を
//! Rust に書き込まない。Hy の事実は doeff-indexer の hy-index の解析(関数として呼ぶ)と読み取り器から取る。
//!
//! 流れ: 母集団の file を集める → file ごとに事実を読む(`facts.rs`)→ 規則ごとに違反の下書きを作る →
//! law と登録簿の鍵を当てて重さを決める(`finish`)。

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
}

/// 地図の材料 — 層の規則が読んだ module 1 つ。
#[derive(Debug, Clone)]
pub struct ModuleSummary {
    pub path: PathBuf,
    pub rel: String,
    pub layer: Option<String>,
    pub context: Option<String>,
    pub role: Option<String>,
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
    layer: LayerId,
}

/// 層の規則を走らせる。root は正規化した repo の根、enabled は有効な規則。
pub fn run(root: &Path, settings: &ProjectSettings, enabled: &BTreeSet<ProjectRule>, target: Target) -> ProjectReport {
    let mut report = ProjectReport::default();
    let registry = Registry::load(root, &settings.registry.dirs, &settings.registry.files);
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
                let judged: Vec<(Vec<Draft>, Option<ModuleSummary>, Vec<String>)> = layer_files
                    .par_iter()
                    .map(|file| match std::fs::read_to_string(&file.file.path) {
                        Ok(source) => judge_layer_file(file, &source, layers, settings, enabled, &index, hy.get(&file.file.rel)),
                        Err(error) => (Vec::new(), None, vec![format!("{}: 読めない: {}", file.file.rel, error)]),
                    })
                    .collect();
                for (found, summary, errors) in judged {
                    drafts.extend(found);
                    report.modules.extend(summary);
                    report.errors.extend(errors);
                }
            }
            if let Some(env) = &settings.environment {
                if enabled.contains(&ProjectRule::EnvironmentName) {
                    for file in &env_files {
                        let source = std::fs::read_to_string(&file.path).unwrap_or_default();
                        drafts.extend(judge_environment_names(file, &source, env, hy.get(&file.rel)));
                    }
                }
            }
        }
        Target::Single { path, source } => {
            let rel = relative_path(root, &path);
            let hy_file = match (language_of(&path), wants_raw || enabled.contains(&ProjectRule::EnvironmentName)) {
                (Some(Language::Hy), true) => hy_index::index_stdin_source(root, &path, source, &raw).files.into_iter().next(),
                _ => None,
            };
            if let (Some(layers), Some(rel)) = (&settings.layers, &rel) {
                if let Some((layer, language)) = classify_layer_file(rel, layers) {
                    let file = LayerFile { file: SourceFile { rel: rel.clone(), path: path.clone(), language }, module: module_of(rel), layer };
                    let mut layer_files = collect_layer_files(root, layers);
                    layer_files.push(file.clone());
                    let index = module_index(&layer_files);
                    let (found, summary, errors) = judge_layer_file(&file, source, layers, settings, enabled, &index, hy_file.as_ref());
                    drafts.extend(found);
                    report.modules.extend(summary);
                    report.errors.extend(errors);
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

/// 層の母集団の file か — 層の dir の下で、拡張子が合い、除く区切りを含まない。
fn classify_layer_file(rel: &str, layers: &LayerSettings) -> Option<(LayerId, Language)> {
    let language = language_of(Path::new(rel))?;
    let extension = Path::new(rel).extension()?.to_str()?;
    if !layers.extensions.contains(extension) || rel.split('/').any(|part| layers.exclude.contains(part)) {
        return None;
    }
    layers.layers.iter().position(|layer| !layer.dir.is_empty() && under(rel, &layer.dir)).map(|index| (LayerId(index), language))
}

/// 層の dir の下の module を全部集める(層の順、層の中は path の順)。
fn collect_layer_files(root: &Path, layers: &LayerSettings) -> Vec<LayerFile> {
    let mut out = Vec::new();
    for (index, layer) in layers.layers.iter().enumerate() {
        if layer.dir.is_empty() {
            continue;
        }
        let mut files: Vec<LayerFile> = walk_files(&root.join(&layer.dir))
            .into_iter()
            .filter_map(|path| {
                let rel = relative_path(root, &path)?;
                let (found, language) = classify_layer_file(&rel, layers)?;
                (found == LayerId(index)).then(|| LayerFile { module: module_of(&rel), layer: found, file: SourceFile { rel, path, language } })
            })
            .collect();
        files.sort_by(|a, b| a.file.rel.cmp(&b.file.rel));
        out.extend(files);
    }
    out
}

/// dir の下の file を全部集める(読めない枝は飛ばす・symlink は辿らない)。
fn walk_files(dir: &Path) -> Vec<PathBuf> {
    WalkDir::new(dir).follow_links(false).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()).map(|e| e.into_path()).collect()
}

/// module の綴り → 層 の索引(import の先を層へ解くため)。
fn module_index(files: &[LayerFile]) -> HashMap<String, LayerId> {
    files.iter().map(|f| (f.module.clone(), f.layer)).collect()
}

/// 層の module 1 つを判じる(違反の下書き・地図の 1 行・読めなかった理由)。
fn judge_layer_file(
    file: &LayerFile,
    source: &str,
    layers: &LayerSettings,
    settings: &ProjectSettings,
    enabled: &BTreeSet<ProjectRule>,
    index: &HashMap<String, LayerId>,
    hy_file: Option<&HyFileIndex>,
) -> (Vec<Draft>, Option<ModuleSummary>, Vec<String>) {
    let facts = read_facts(file.file.language, source, &file.module, &layers.tags);
    let errors = facts.errors.iter().map(|e| format!("{}: {}", file.file.rel, e)).collect();
    let lines = LineIndex::new(source);
    let spec = &layers.layers[file.layer.0];
    let judge = LayerJudge { file, source, lines: &lines, layers, layer: file.layer };
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
    if let (Some(raw), Some(hy_file)) = (&settings.raw, hy_file) {
        if !raw.allowed.contains(&file.layer) {
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
    };
    (drafts, Some(summary), errors)
}

/// 層の module 1 つの判定に要る物をまとめた道具。
struct LayerJudge<'a> {
    file: &'a LayerFile,
    source: &'a str,
    lines: &'a LineIndex<'a>,
    layers: &'a LayerSettings,
    layer: LayerId,
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
    fn draft(&self, rule: ProjectRule, range: Range, message: String, detail: Option<String>) -> Draft {
        Draft {
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
                ))
            }
            (None, false, true) => Some(self.draft(
                ProjectRule::ModuleDeclaresTags,
                first_line_range(self.source),
                format!("{} にタグ(:context と :role)が無い — 層の dir の下の module は文脈と役をタグで名乗る", rel),
                None,
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
        ))
    }

    /// DOEFF102: この層に禁じた module を直に import しない(一番上の綴りごとに 1 件、位置は最初の import)。
    fn forbidden_modules(&self, facts: &ModuleFacts) -> Vec<Draft> {
        let spec = &self.layers.layers[self.layer.0];
        let mut first: BTreeMap<&str, ByteSpan> = BTreeMap::new();
        for import in &facts.imports {
            let top = import.target.split('.').next().unwrap_or("");
            if spec.forbid_modules.contains(top) {
                first.entry(top).or_insert(import.span);
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
                )
            })
            .collect()
    }

    /// DOEFF101: import の先が母集団の module(かその中の名)で、許された層の外なら破れ。
    fn import_direction(&self, facts: &ModuleFacts, index: &HashMap<String, LayerId>) -> Vec<Draft> {
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
                let (owner, owner_layer) = resolve_target(target, index)?;
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
                ))
            })
            .collect()
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
fn resolve_target<'i>(target: &'i str, index: &HashMap<String, LayerId>) -> Option<(&'i str, LayerId)> {
    if let Some(layer) = index.get(target) {
        return Some((target, *layer));
    }
    let (owner, _) = target.rsplit_once('.')?;
    index.get(owner).map(|layer| (owner, *layer))
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
    let draft = |range: Range, message: String, detail: Option<String>| Draft {
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
            Some(name),
        ));
    }
    drafts
}

// --- 仕上げ -------------------------------------------------------------------------------

/// 下書きに law・鍵・登録簿・照合中を当てて違反にする(path と位置の順)。
fn finish(drafts: Vec<Draft>, settings: &ProjectSettings, registry: &Registry) -> Vec<Finding> {
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
                    (true, Severity::Error) => Severity::Warning,
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
