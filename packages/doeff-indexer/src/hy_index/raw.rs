//! 生の副作用の証拠 — 目録(`raw_catalog.rs`)と索引(imports・references・calls・full_range)だけから、
//! 定義ごとの直接の証拠と経由の証拠(経路つき)を集める。これは事実であって規則の判定ではない(層の規則の判定の
//! 正本は agora-controllers の linter)。VS Code の拡張はこの事実を表示するだけで、目録を持たない。
//!
//! 規則(doeff-runner の TypeScript の判定から移したもの — 実データで結果が一致することを確かめた):
//! - 参照は dotted の終端の区切りだけを数え、file の import で完全な名前に直して目録と比べる。
//! - 例外の型(名前の終わりが Error / Exception / Timeout / Warning)と ignored の名前は数えない。
//! - 組み込みは import されていない修飾の無い名前を、呼び出しの頭の位置でだけ数える(局所の変数 `open` を拾わない)。
//! - method 名は弱い証拠で、context の module が file の import か定義の中の参照に見える時だけ数える。数えるのは値の上の
//!   属性・method として書かれた区切り(`(.m x)`・`x.m`・`(. x m)`)だけで、名前の引き(局所の束縛・引数・定義の名)は
//!   method の名と同じ綴りでも数えない(agora-redesign #798)。
//! - 経由は、同じ file か import で行き先が決まった呼び出しだけを、深さ 4 まで辿る(循環は止め、同じ証拠は 1 度)。

use std::collections::{HashMap, HashSet};

use super::model::{
    HyFileIndex, Import, RawEvidence, RawEvidenceKind, RawMark, RawStep, RawStrength, RawVia, Range, Reference,
};
use super::position::Position;
use super::raw_catalog::RawCatalog;

/// 経由の伝播の深さの上限。
pub const RAW_VIA_MAX_DEPTH: usize = 4;
/// 1 つの定義に持つ経由の証拠の上限。
pub const RAW_VIA_MAX_ITEMS: usize = 50;

/// 照合用の名前の規則(doeff-runner の mangle と同じ)— `-` を `_` にし、先頭に続く `-` は残す。
fn match_name(name: &str) -> String {
    let trimmed = name.trim_start_matches('-');
    let leading = &name[..name.len() - trimmed.len()];
    format!("{}{}", leading, trimmed.replace('-', "_"))
}

/// dotted の名前を区切りごとに照合用の形にする。
fn match_dotted(dotted: &str) -> String {
    dotted.split('.').map(match_name).collect::<Vec<_>>().join(".")
}

/// dotted の名前が pattern に合うか(区切りの境目での前方一致・末尾 `*` は区切りの中の前方一致)。
pub fn matches_pattern(full: &str, pattern: &str) -> bool {
    let name = match_dotted(full);
    if let Some(prefix) = pattern.strip_suffix('*') {
        return name.starts_with(&match_dotted(prefix));
    }
    let p = match_dotted(pattern);
    name == p || name.starts_with(&format!("{}.", p))
}

/// 範囲が位置を含むか(両端を含む)。
fn contains(range: &Range, pos: &Position) -> bool {
    let after_start =
        pos.line > range.start.line || (pos.line == range.start.line && pos.character >= range.start.character);
    let before_end = pos.line < range.end.line || (pos.line == range.end.line && pos.character <= range.end.character);
    after_start && before_end
}

/// 照合用の形に直した pattern(末尾 `*` は区切りの中の前方一致)。
struct CompiledPattern {
    text: String,
    prefix_only: bool,
}

impl CompiledPattern {
    /// pattern を照合用の形にする。
    fn new(pattern: &str) -> CompiledPattern {
        match pattern.strip_suffix('*') {
            Some(prefix) => CompiledPattern { text: match_dotted(prefix), prefix_only: true },
            None => CompiledPattern { text: match_dotted(pattern), prefix_only: false },
        }
    }

    /// 照合用の形の名前が合うか(matches_pattern と同じ規則)。
    fn matches(&self, name: &str) -> bool {
        if self.prefix_only {
            return name.starts_with(&self.text);
        }
        name.len() >= self.text.len()
            && name.starts_with(&self.text)
            && (name.len() == self.text.len() || name.as_bytes()[self.text.len()] == b'.')
    }
}

/// 分類 1 つの目録を照合用の形にした物。
struct CompiledEntry {
    category: super::raw_catalog::RawCategory,
    patterns: Vec<CompiledPattern>,
    builtins: Vec<String>,
    methods: Vec<(String, Vec<String>)>,
}

/// 目録の全体を照合用の形にした物(file の走査の前に 1 度だけ作る)。
struct CompiledCatalog {
    entries: Vec<CompiledEntry>,
    ignored: Vec<CompiledPattern>,
    exception_suffixes: Vec<String>,
    context_modules: Vec<(String, CompiledPattern)>,
}

impl CompiledCatalog {
    /// 目録を照合用の形にする。
    fn new(catalog: &RawCatalog) -> CompiledCatalog {
        let mut context_modules: Vec<(String, CompiledPattern)> = Vec::new();
        for entry in &catalog.categories {
            for method in &entry.methods {
                for module in &method.context {
                    if !context_modules.iter().any(|(m, _)| m == module) {
                        context_modules.push((module.clone(), CompiledPattern::new(module)));
                    }
                }
            }
        }
        CompiledCatalog {
            entries: catalog
                .categories
                .iter()
                .map(|entry| CompiledEntry {
                    category: entry.category,
                    patterns: entry.patterns.iter().map(|p| CompiledPattern::new(p)).collect(),
                    builtins: entry.builtins.iter().map(|b| match_name(b)).collect(),
                    methods: entry.methods.iter().map(|m| (match_name(&m.name), m.context.clone())).collect(),
                })
                .collect(),
            ignored: catalog.ignored.iter().map(|n| CompiledPattern::new(n)).collect(),
            exception_suffixes: catalog.exception_suffixes.clone(),
            context_modules,
        }
    }
}

/// file の import 1 つを照合用の形にした物。
struct CompiledImport {
    bound: Vec<String>,
    head: String,
}

/// file の import を照合用の形にする(相対 import は目録の外なので除く)。
fn compile_imports(file: &HyFileIndex) -> Vec<CompiledImport> {
    file.imports
        .iter()
        .filter(|imp| !imp.module.starts_with('.'))
        .map(|imp| {
            let module = match_dotted(&imp.module);
            let bound = match_dotted(imp.alias.as_deref().or(imp.name.as_deref()).unwrap_or(&imp.module))
                .split('.')
                .map(str::to_string)
                .collect();
            let head = match &imp.name {
                None => module,
                Some(name) => format!("{}.{}", module, match_name(name)),
            };
            CompiledImport { bound, head }
        })
        .collect()
}

/// 参照の書かれた dotted(修飾 + 名前)を照合用の形にする。
fn chain_of(reference: &Reference) -> String {
    match &reference.qualifier {
        Some(qualifier) => match_dotted(&format!("{}.{}", qualifier, reference.name)),
        None => match_dotted(&reference.name),
    }
}

/// 参照を import で完全な名前に直した結果。
enum Expanded {
    /// import を通した名前(`time.sleep`)
    Import(String),
    /// import されていない修飾の無い名前の引き(組み込みか、外で決まる名前か、局所の束縛の名)
    Unbound(String),
    /// file の中の定義の名前の引き
    Local(String),
    /// import で決まらない値の上の属性・method の名前(`(.stat p)`・`p.stat`・`(. p stat)`)— method の証拠はここだけ
    Member(String),
}

/// 参照を file の import で完全な名前に直す(最も長く一致する import を使う・先に見つけた同じ長さの物が勝つ)。
fn expand(imports: &[CompiledImport], reference: &Reference, local_names: &HashSet<String>) -> Expanded {
    let chain = chain_of(reference);
    let segments: Vec<&str> = chain.split('.').collect();
    let mut best: Option<(usize, &CompiledImport)> = None;
    for import in imports {
        let matched = import.bound.iter().enumerate().all(|(i, seg)| segments.get(i) == Some(&seg.as_str()));
        if !matched || best.is_some_and(|(length, _)| length >= import.bound.len()) {
            continue;
        }
        best = Some((import.bound.len(), import));
    }
    if let Some((length, import)) = best {
        let mut full = import.head.clone();
        for segment in segments.iter().skip(length) {
            full.push('.');
            full.push_str(segment);
        }
        return Expanded::Import(full);
    }
    let name = match_name(&reference.name);
    if reference.member {
        Expanded::Member(name)
    } else if local_names.contains(&name) {
        Expanded::Local(name)
    } else {
        Expanded::Unbound(name)
    }
}

/// file の走査の結果 — 証拠の候補と、method の証拠を数える条件を決める材料。
pub struct FileScan {
    evidence: Vec<RawEvidence>,
    /// evidence と同じ添字の、method の証拠に要る context の module(method でなければ None)
    method_contexts: Vec<Option<Vec<String>>>,
    imported_contexts: HashSet<String>,
    context_refs: HashMap<String, Vec<Range>>,
}

/// file の参照を 1 度だけ走査して、生の副作用の証拠の候補を集める(目録は照合用の形に前処理した物を使う)。
fn scan_compiled(file: &HyFileIndex, catalog: &CompiledCatalog) -> FileScan {
    let local_names: HashSet<String> =
        file.definitions.iter().filter(|d| d.container.is_none()).map(|d| d.mangled.clone()).collect();
    let call_heads: HashSet<(u32, u32)> = file
        .calls
        .iter()
        .filter(|c| c.qualifier.is_none())
        .map(|c| (c.range.start.line, c.range.start.character))
        .collect();
    // 後ろに `.x` が続く参照(dotted の途中)を見分けるための表
    let continued: HashSet<(u32, i64, String)> = file
        .references
        .iter()
        .filter_map(|r| {
            r.qualifier
                .as_ref()
                .map(|q| (r.range.start.line, i64::from(r.range.start.character) - 1, match_dotted(q)))
        })
        .collect();
    let imported_contexts: HashSet<String> = catalog
        .context_modules
        .iter()
        .filter(|(_, pattern)| file.imports.iter().any(|imp| pattern.matches(&match_dotted(&imp.module))))
        .map(|(module, _)| module.clone())
        .collect();
    let imports = compile_imports(file);
    let mut context_refs: HashMap<String, Vec<Range>> = HashMap::new();
    let mut evidence = Vec::new();
    let mut method_contexts = Vec::new();
    for reference in &file.references {
        let expanded = expand(&imports, reference, &local_names);
        if let Expanded::Import(full) = &expanded {
            for (module, pattern) in &catalog.context_modules {
                if pattern.matches(full) {
                    context_refs.entry(module.clone()).or_default().push(reference.range);
                }
            }
        }
        let end_key = (reference.range.end.line, i64::from(reference.range.end.character), chain_of(reference));
        if continued.contains(&end_key) {
            continue; // dotted の途中の区切り
        }
        let at_call_head = call_heads.contains(&(reference.range.start.line, reference.range.start.character));
        let skip_import = match &expanded {
            Expanded::Import(full) => {
                let last = full.rsplit('.').next().unwrap_or("");
                catalog.exception_suffixes.iter().any(|suffix| last.ends_with(suffix.as_str()))
                    || catalog.ignored.iter().any(|pattern| pattern.matches(full))
            }
            _ => false,
        };
        for entry in &catalog.entries {
            if let Some((found, context)) = match_entry(entry, &expanded, skip_import, at_call_head, &file.path, reference.range) {
                evidence.push(found);
                method_contexts.push(context);
            }
        }
    }
    FileScan { evidence, method_contexts, imported_contexts, context_refs }
}

/// 参照 1 つを目録の分類 1 つと比べる(method は弱い証拠で、要る context を添える)。
fn match_entry(
    entry: &CompiledEntry,
    expanded: &Expanded,
    skip_import: bool,
    at_call_head: bool,
    path: &str,
    range: Range,
) -> Option<(RawEvidence, Option<Vec<String>>)> {
    let evidence = |name: String, kind: RawEvidenceKind, strength: RawStrength| RawEvidence {
        category: entry.category,
        name,
        kind,
        strength,
        path: path.to_string(),
        range,
    };
    match expanded {
        Expanded::Import(full) => (!skip_import && entry.patterns.iter().any(|p| p.matches(full)))
            .then(|| (evidence(full.clone(), RawEvidenceKind::Name, RawStrength::Strong), None)),
        // 名前の引きは method の証拠にしない(局所の束縛の名 stat は .stat ではない — agora-redesign #798)。
        Expanded::Unbound(name) => (at_call_head && entry.builtins.iter().any(|b| b == name))
            .then(|| (evidence(name.clone(), RawEvidenceKind::Builtin, RawStrength::Strong), None)),
        Expanded::Local(_) => None,
        Expanded::Member(name) => method_match(entry, name).map(|context| {
            (evidence(format!(".{}", name), RawEvidenceKind::Method, RawStrength::Weak), Some(context))
        }),
    }
}

/// method 名だけの一致と、それに要る context。
fn method_match(entry: &CompiledEntry, name: &str) -> Option<Vec<String>> {
    entry.methods.iter().find(|(m, _)| m == name).map(|(_, context)| context.clone())
}

/// 定義の範囲の中の直接の証拠(method の証拠は context の module が見える時だけ)。
pub fn direct_evidence(scan: &FileScan, full_range: &Range) -> Vec<RawEvidence> {
    let context_visible = |module: &String| {
        scan.imported_contexts.contains(module)
            || scan.context_refs.get(module).is_some_and(|ranges| ranges.iter().any(|r| contains(full_range, &r.start)))
    };
    scan.evidence
        .iter()
        .zip(&scan.method_contexts)
        .filter(|(e, context)| {
            contains(full_range, &e.range.start)
                && match context {
                    None => true,
                    Some(modules) => modules.is_empty() || modules.iter().any(context_visible),
                }
        })
        .map(|(e, _)| e.clone())
        .collect()
}

/// 定義を一意に指す番号(file の添字と定義の添字)。
type DefId = (usize, usize);

/// import が束ねる先。
struct Binding {
    module: String,
    symbol: Option<String>,
    container: Option<String>,
}

/// 経由の計算のための、索引全体の引き表と覚え書き。
struct ViaWorld<'a> {
    files: &'a [HyFileIndex],
    by_module: HashMap<String, Vec<usize>>,
    defined_names: HashSet<String>,
    direct: Vec<Vec<Vec<RawEvidence>>>,
    callees: HashMap<DefId, Vec<DefId>>,
    /// (定義, 残りの段数) → その段数の中で直接の証拠を持つ定義に届き得るか(訪問中の定義を除かない上限の見積もり)
    reachable: HashMap<(DefId, usize), bool>,
}

/// file が package の `__init__` か(相対 import の基準が変わる)。
fn is_package_init(path: &str) -> bool {
    let base = std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    matches!(base.as_str(), "__init__.hy" | "__init__.hyk" | "__init__.hyp")
}

/// 相対 import を書いた file の module を基準に絶対の dotted 名へ直す。
fn absolute_module(file: &HyFileIndex, module: &str) -> String {
    let dots = module.len() - module.trim_start_matches('.').len();
    if dots == 0 {
        return module.to_string();
    }
    let base: Vec<&str> = file.module.split('.').filter(|p| !p.is_empty()).collect();
    let package: Vec<&str> = if is_package_init(&file.path) { base } else { base[..base.len().saturating_sub(1)].to_vec() };
    let keep = package.len().saturating_sub(dots - 1);
    let mut parts: Vec<String> = package[..keep].iter().map(|s| s.to_string()).collect();
    let rest = &module[dots..];
    if !rest.is_empty() {
        parts.extend(rest.split('.').map(str::to_string));
    }
    parts.join(".")
}

/// import が file の中に作る名前(別名 > 名前 > module)。
fn bound_name(import: &Import) -> &str {
    import.alias.as_deref().or(import.name.as_deref()).unwrap_or(&import.module)
}

impl<'a> ViaWorld<'a> {
    /// 索引全体の引き表を作り、全定義の直接の証拠を計算する。
    fn new(files: &'a [HyFileIndex], direct: Vec<Vec<Vec<RawEvidence>>>) -> ViaWorld<'a> {
        let mut by_module: HashMap<String, Vec<usize>> = HashMap::new();
        let mut defined_names = HashSet::new();
        for (i, file) in files.iter().enumerate() {
            by_module.entry(match_dotted(&file.module)).or_default().push(i);
            for definition in &file.definitions {
                defined_names.insert(definition.mangled.clone());
            }
        }
        ViaWorld { files, by_module, defined_names, direct, callees: HashMap::new(), reachable: HashMap::new() }
    }

    /// 修飾の無い名前を束ねる import(最初の 1 つ)。
    fn binding_for_name(file: &HyFileIndex, mangled: &str) -> Option<Binding> {
        file.imports.iter().find(|imp| match_dotted(bound_name(imp)) == mangled).map(|imp| {
            let module = absolute_module(file, &imp.module);
            Binding { module, symbol: imp.name.as_ref().map(|n| match_name(n)), container: None }
        })
    }

    /// `q.m` の q を import で解き、m の在る module(と入れ物)を返す。
    fn bindings_for_qualified(file: &HyFileIndex, qualifier: &str, mangled: &str) -> Vec<Binding> {
        let q = match_dotted(qualifier);
        let mut found = Vec::new();
        for imp in &file.imports {
            let bound = match_dotted(bound_name(imp));
            let module = absolute_module(file, &imp.module);
            match &imp.name {
                None => {
                    if q == bound {
                        found.push(Binding { module, symbol: Some(mangled.to_string()), container: None });
                    } else if q.starts_with(&format!("{}.", bound)) {
                        let rest = &q[bound.len()..];
                        found.push(Binding { module: format!("{}{}", module, rest), symbol: Some(mangled.to_string()), container: None });
                    }
                }
                Some(name) => {
                    if q == bound {
                        found.push(Binding { module: format!("{}.{}", module, name), symbol: Some(mangled.to_string()), container: None });
                        found.push(Binding { module, symbol: Some(mangled.to_string()), container: Some(match_name(name)) });
                    }
                }
            }
        }
        found
    }

    /// 定義が「入れ物 container の中の名前 symbol」か。
    fn is_member(definition: &super::model::Definition, symbol: &str, container: Option<&str>) -> bool {
        let container_matches = match container {
            None => definition.container.is_none(),
            Some(c) => definition.container.as_deref().is_some_and(|dc| match_name(dc) == c),
        };
        definition.mangled == symbol && container_matches
    }

    /// 呼び出しの行き先を、同じ file か import で決まる Hy の定義に解く(workspace 全体の同名当ては使わない)。
    fn resolve(&self, file_index: usize, name: &str, qualifier: Option<&str>) -> Vec<DefId> {
        let file = &self.files[file_index];
        let mangled = match_name(name);
        let local: Vec<DefId> = file
            .definitions
            .iter()
            .enumerate()
            .filter(|(_, d)| {
                d.mangled == mangled
                    && match qualifier {
                        None => d.container.is_none(),
                        Some(q) => d.container.as_deref().is_some_and(|c| match_name(c) == match_dotted(q)),
                    }
            })
            .map(|(i, _)| (file_index, i))
            .collect();
        if !local.is_empty() {
            return local;
        }
        let bindings = match qualifier {
            None => Self::binding_for_name(file, &mangled).into_iter().collect(),
            Some(q) => Self::bindings_for_qualified(file, q, &mangled),
        };
        for binding in bindings {
            let Some(module_files) = self.by_module.get(&match_dotted(&binding.module)) else {
                continue;
            };
            let Some(symbol) = &binding.symbol else {
                return Vec::new(); // module そのもの(呼び出しの行き先の定義は無い)
            };
            let found: Vec<DefId> = module_files
                .iter()
                .flat_map(|&fi| {
                    self.files[fi]
                        .definitions
                        .iter()
                        .enumerate()
                        .filter(|(_, d)| Self::is_member(d, symbol, binding.container.as_deref()))
                        .map(move |(di, _)| (fi, di))
                })
                .collect();
            if !found.is_empty() {
                return found;
            }
        }
        Vec::new()
    }

    /// 定義の範囲の中の呼び出しが行き着く定義(自分の中の入れ子は除く・書いた順・重ねない)。
    fn callees_of(&mut self, id: DefId) -> Vec<DefId> {
        if let Some(known) = self.callees.get(&id) {
            return known.clone();
        }
        let file = &self.files[id.0];
        let range = file.definitions[id.1].full_range;
        let mut found: Vec<DefId> = Vec::new();
        for call in &file.calls {
            if !contains(&range, &call.range.start) || !self.defined_names.contains(&call.mangled) {
                continue;
            }
            for target in self.resolve(id.0, &call.callee, call.qualifier.as_deref()) {
                let target_def = &self.files[target.0].definitions[target.1];
                let inside = target.0 == id.0 && contains(&range, &target_def.range.start);
                if !inside && !found.contains(&target) {
                    found.push(target);
                }
            }
        }
        self.callees.insert(id, found.clone());
        found
    }

    /// 定義から `steps` 段以内の呼び出しで、直接の証拠を持つ定義に届き得るか。訪問中の定義を除かない分だけ広く
    /// 見積もるので、偽なら本当に届かない — 経由の探索はこれが偽の枝を飛ばしても結果が変わらない。
    fn can_reach(&mut self, id: DefId, steps: usize) -> bool {
        if steps == 0 {
            return false;
        }
        if let Some(&known) = self.reachable.get(&(id, steps)) {
            return known;
        }
        // 問いは 1 段ごとに段数が減るので、同じ (定義, 段数) の問いに戻ることは無い(循環でも止まる)
        let mut found = false;
        for callee in self.callees_of(id) {
            if !self.direct[callee.0][callee.1].is_empty() || self.can_reach(callee, steps - 1) {
                found = true;
                break;
            }
        }
        self.reachable.insert((id, steps), found);
        found
    }

    /// 経由の証拠を深さ優先で集める(循環は経路の中の定義で止め、同じ証拠の位置は 1 度だけ)。
    fn collect_via(
        &mut self,
        id: DefId,
        through: &mut Vec<DefId>,
        visiting: &mut Vec<DefId>,
        out: &mut Vec<RawVia>,
        seen: &mut HashSet<(String, u32, u32)>,
    ) {
        if through.len() >= RAW_VIA_MAX_DEPTH || out.len() >= RAW_VIA_MAX_ITEMS {
            return;
        }
        for callee in self.callees_of(id) {
            if visiting.contains(&callee) {
                continue;
            }
            // この先の段数で証拠に届き得ない呼び出し先は、訪れても何も足さない
            let remaining = RAW_VIA_MAX_DEPTH - (through.len() + 1);
            if self.direct[callee.0][callee.1].is_empty() && !self.can_reach(callee, remaining) {
                continue;
            }
            through.push(callee);
            for evidence in &self.direct[callee.0][callee.1] {
                let at = (evidence.path.clone(), evidence.range.start.line, evidence.range.start.character);
                if !seen.contains(&at) && out.len() < RAW_VIA_MAX_ITEMS {
                    seen.insert(at);
                    let steps = through
                        .iter()
                        .map(|&(fi, di)| RawStep {
                            path: self.files[fi].path.clone(),
                            index: di,
                            name: self.files[fi].definitions[di].name.clone(),
                        })
                        .collect();
                    out.push(RawVia { through: steps, evidence: evidence.clone() });
                }
            }
            visiting.push(callee);
            self.collect_via(callee, through, visiting, out, seen);
            visiting.pop();
            through.pop();
        }
    }
}

/// 全 file の定義の直接の証拠を、file を分けて並列に求める(file ごとに独立なので順は変わらない)。
fn direct_in_parallel(files: &[HyFileIndex], compiled: &CompiledCatalog) -> Vec<Vec<Vec<RawEvidence>>> {
    let per_file = |file: &HyFileIndex| -> Vec<Vec<RawEvidence>> {
        let scan = scan_compiled(file, compiled);
        file.definitions.iter().map(|d| direct_evidence(&scan, &d.full_range)).collect()
    };
    let workers = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1).max(1);
    let chunk = files.len().div_ceil(workers).max(1);
    std::thread::scope(|scope| {
        let handles: Vec<_> =
            files.chunks(chunk).map(|part| scope.spawn(move || part.iter().map(per_file).collect::<Vec<_>>())).collect();
        handles
            .into_iter()
            .zip(files.chunks(chunk))
            .flat_map(|(handle, part)| {
                // 走査が panic しても全体は止めず、その file の定義は証拠なしとして進む
                handle.join().unwrap_or_else(|_| part.iter().map(|f| vec![Vec::new(); f.definitions.len()]).collect())
            })
            .collect()
    })
}

/// 索引の全 file の定義に、直接の証拠と(`with_via` なら)経由の証拠を埋める。経由は file をまたぐので、
/// `--root` の全体の実行だけが計算する(1 file や `--file` の実行では空)。
pub fn annotate(files: &mut [HyFileIndex], catalog: &RawCatalog, with_via: bool) {
    let compiled = CompiledCatalog::new(catalog);
    let direct = direct_in_parallel(files, &compiled);
    let via: Vec<Vec<Vec<RawVia>>> = if with_via {
        let mut world = ViaWorld::new(files, direct.clone());
        let mut all = Vec::with_capacity(files.len());
        for fi in 0..files.len() {
            let mut per_file = Vec::with_capacity(files[fi].definitions.len());
            for di in 0..files[fi].definitions.len() {
                let mut out = Vec::new();
                world.collect_via((fi, di), &mut Vec::new(), &mut vec![(fi, di)], &mut out, &mut HashSet::new());
                per_file.push(out);
            }
            all.push(per_file);
        }
        all
    } else {
        files.iter().map(|f| vec![Vec::new(); f.definitions.len()]).collect()
    };
    for ((file, direct_per_def), via_per_def) in files.iter_mut().zip(direct).zip(via) {
        for ((definition, direct), via) in file.definitions.iter_mut().zip(direct_per_def).zip(via_per_def) {
            definition.raw = RawMark { direct, via };
        }
    }
}
