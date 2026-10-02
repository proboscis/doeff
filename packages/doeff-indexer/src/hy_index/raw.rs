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
//! - 呼び手が「止める」と渡した定義(実 I/O を担うと宣言した定義)には入らない — その先の生の副作用はその定義の責務なので、
//!   呼び手の経由の証拠に数えない(agora-redesign #1902)。止める定義を通らない別の経路で届く証拠は今までどおり数える。

use std::collections::{BTreeSet, HashMap, HashSet};

use super::model::{
    ArgumentValue, HyFileIndex, RawEvidence, RawEvidenceKind, RawMark, RawStep, RawStrength, RawVia, Range, Reference,
};
use super::position::Position;
use super::raw_catalog::{RawCatalog, RawCategory};

/// 経由の伝播の深さの上限。
pub const RAW_VIA_MAX_DEPTH: usize = 4;
/// 1 つの定義に持つ経由の証拠の上限。
pub const RAW_VIA_MAX_ITEMS: usize = 50;

/// 経由の証拠を辿るか。
#[derive(Debug, Clone, Copy)]
pub enum ViaTrace<'a> {
    /// 経由を計算しない(1 file の実行 — 経由は file をまたぐ)。
    Skip,
    /// 経由を辿る。`stops`(完全修飾名 — 定義の `qualified_name` と同じ綴り)の定義には入らない。空なら全部を辿る。
    Through { stops: &'a BTreeSet<String> },
}

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
    /// method の名・数える条件の module・受け手を除く引数の上限(目録の RawMethod)。
    methods: Vec<(String, Vec<String>, Option<usize>)>,
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
                    methods: entry.methods.iter().map(|m| (match_name(&m.name), m.context.clone(), m.max_args)).collect(),
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
    // `.random rng` や `(. rng (random))` の method 名を、同名の import random と取り違えない。
    if reference.member && reference.qualifier.is_none() {
        return Expanded::Member(match_name(&reference.name));
    }
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
    let seeded_constructors: HashSet<(u32, u32)> = file.calls.iter()
        .filter(|call| call.target.as_deref() == Some("random.Random"))
        .filter(|call| call.arguments.iter().find(|arg| arg.keyword.is_none() || arg.keyword.as_deref() == Some("x"))
            .is_some_and(|arg| arg.value == ArgumentValue::Explicit))
        .map(|call| (call.range.start.line, call.range.start.character))
        .collect();
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
            if entry.category == RawCategory::Random {
                // import と型の参照は乱数を引かない。種を明示した Random も OS の entropy に触れない。
                let imported = file.imports.iter().any(|import| contains(&import.range, &reference.range.start));
                let seeded = seeded_constructors.contains(&(reference.range.start.line, reference.range.start.character));
                if reference.type_only || imported || seeded {
                    continue;
                }
            }
            if let Some((found, context)) = match_entry(entry, &expanded, skip_import, at_call_head, reference.call_arity, &file.path, reference.range) {
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
    call_arity: Option<usize>,
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
        Expanded::Member(name) => method_match(entry, name, call_arity).map(|context| {
            (evidence(format!(".{}", name), RawEvidenceKind::Method, RawStrength::Weak), Some(context))
        }),
    }
}

/// method 名の一致と、それに要る context。呼びの引数(受け手を除く)が目録の上限より多い呼びは同じ名の別の型の method
/// として数えない(agora-redesign #3014 — 文字列の `.replace`)。引数の分からない参照(呼びの頭でない)は数える。
fn method_match(entry: &CompiledEntry, name: &str, call_arity: Option<usize>) -> Option<Vec<String>> {
    entry
        .methods
        .iter()
        .find(|(m, _, max_args)| m == name && !matches!((call_arity, max_args), (Some(arity), Some(max)) if arity > *max))
        .map(|(_, context, _)| context.clone())
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

/// 経由の計算のための、索引全体の引き表と覚え書き。
struct ViaWorld<'a> {
    files: &'a [HyFileIndex],
    /// 完全修飾名 → 定義(呼び出しの `target` の行き先 — 名前の解決は qualify.rs の 1 か所)
    by_qualified: HashMap<&'a str, Vec<DefId>>,
    direct: Vec<Vec<Vec<RawEvidence>>>,
    callees: HashMap<DefId, Vec<DefId>>,
    /// 入らない定義の完全修飾名(`ViaTrace::Through` の `stops`)
    stops: &'a BTreeSet<String>,
    /// (定義, 残りの段数) → その段数の中で直接の証拠を持つ定義に届き得るか(訪問中の定義を除かない上限の見積もり)
    reachable: HashMap<(DefId, usize), bool>,
}

impl<'a> ViaWorld<'a> {
    /// 索引全体の引き表を作り、全定義の直接の証拠を計算する。
    fn new(files: &'a [HyFileIndex], direct: Vec<Vec<Vec<RawEvidence>>>, stops: &'a BTreeSet<String>) -> ViaWorld<'a> {
        let mut by_qualified: HashMap<&'a str, Vec<DefId>> = HashMap::new();
        for (fi, file) in files.iter().enumerate() {
            for (di, definition) in file.definitions.iter().enumerate() {
                by_qualified.entry(definition.qualified_name.as_str()).or_default().push((fi, di));
            }
        }
        ViaWorld { files, by_qualified, direct, callees: HashMap::new(), stops, reachable: HashMap::new() }
    }

    /// 定義の範囲の中の呼び出しが行き着く定義(自分の中の入れ子と、入らない定義 `stops` は除く・書いた順・重ねない)。
    fn callees_of(&mut self, id: DefId) -> Vec<DefId> {
        if let Some(known) = self.callees.get(&id) {
            return known.clone();
        }
        let file = &self.files[id.0];
        let range = file.definitions[id.1].full_range;
        let mut found: Vec<DefId> = Vec::new();
        for call in &file.calls {
            if !contains(&range, &call.range.start) {
                continue;
            }
            let Some(targets) = call.target.as_deref().and_then(|name| self.by_qualified.get(name)) else {
                continue;
            };
            for &target in targets {
                let target_def = &self.files[target.0].definitions[target.1];
                let inside = target.0 == id.0 && contains(&range, &target_def.range.start);
                let stopped = self.stops.contains(&target_def.qualified_name);
                if !inside && !stopped && !found.contains(&target) {
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
/// `--root` の全体の実行だけが計算する(1 file や `--file` の実行では `ViaTrace::Skip` で空)。
pub fn annotate(files: &mut [HyFileIndex], catalog: &RawCatalog, trace: ViaTrace) {
    let compiled = CompiledCatalog::new(catalog);
    let direct = direct_in_parallel(files, &compiled);
    let via: Vec<Vec<Vec<RawVia>>> = match trace {
        ViaTrace::Through { stops } => {
        let mut world = ViaWorld::new(files, direct.clone(), stops);
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
        }
        ViaTrace::Skip => files.iter().map(|f| vec![Vec::new(); f.definitions.len()]).collect(),
    };
    for ((file, direct_per_def), via_per_def) in files.iter_mut().zip(direct).zip(via) {
        for ((definition, direct), via) in file.definitions.iter_mut().zip(direct_per_def).zip(via_per_def) {
            definition.raw = RawMark { direct, via };
        }
    }
}
