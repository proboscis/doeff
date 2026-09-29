//! DOEFF141: 決めた材料だけで判じる定義(agora-redesign #1368 — agora-controllers の入口の保証の判定
//! controllers/screen/tests/entrance_guarantee_rules.hy の 9 点のうち、一般の形で持てる 2 点の移し先)。
//!
//! architecture.hy の `:blind-definitions [(blind "module:名" :forbid-words [..] :no-imports True :allow-requires [..] :why "…")]` の
//! 定義ごとに:
//!   * 定義から呼び出しと名指し(値として渡す所)で推移的に届く repo の Hy の定義の本体(入れ子の定義を含む・註は除く)に、
//!     :forbid-words の綴りが部分一致で現れたら、届いた定義と語ごとに 1 件(`<定義の名>:<語>`)。helper へ逃がしてもすり抜けない。
//!   * :no-imports なら、定義の module の import と require(:allow-requires に挙げた module の require は macro の読み込みなので除く)
//!     ごとに 1 件(`import:<module>`)— 推移閉包は「呼ぶ」側しか辿らないので、材料を引き込む口はここで塞ぐ。
//!   * 宣言した定義が見つからない(module の Hy の file が無い・定義が無い)なら architecture.hy の位置で 1 件(`missing`)。
//! 読むのは宣言した module と、届いた先の module の file だけ(索引はその file ごとに 1 度 — repo 全体は読まない)。
//! 届く先は索引が名前を解いた先(`target`)で、repo の中に Hy の file が在る module だけを辿る(外の package と Python は辿らない)。

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;

use doeff_indexer::hy_index::{self, HyFileIndex, Range, RawSettings};

use super::architecture::BlindDefinition;
use crate::position::{offset_of, LineIndex};

/// 当たりの種類(閉じた 3 つ)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BlindProblem {
    /// 届いた定義 reached の本体に語 word が在る。
    ReadsWord { reached: String, word: String },
    /// 定義の module が import / require を持つ。
    Imports { module: String },
    /// 宣言した定義が見つからない。
    Missing { reason: String },
}

/// 当たり 1 つ。
#[derive(Debug, Clone)]
pub struct BlindFinding {
    /// 宣言の綴り(`module:名`)。
    pub declared: String,
    pub why: String,
    /// 当たりの file(根からの path)と、その中の位置。Missing は architecture.hy。
    pub rel: String,
    pub range: Range,
    pub problem: BlindProblem,
    /// 登録簿の鍵の細目。
    pub detail: String,
}

/// 読んだ module 1 つ(索引と中身)。
struct Loaded {
    rel: String,
    source: String,
    index: HyFileIndex,
}

/// module の索引を file ごとに 1 度だけ作る置き場。
struct Modules<'a> {
    root: &'a Path,
    raw: &'a RawSettings,
    loaded: HashMap<String, Option<Loaded>>,
    errors: Vec<String>,
}

impl<'a> Modules<'a> {
    /// mangle した dotted の module の綴り → repo の中の Hy の file(無ければ None)。
    fn load(&mut self, module: &str) -> Option<&Loaded> {
        if !self.loaded.contains_key(module) {
            let rel = format!("{}.hy", module.replace('.', "/"));
            let path = self.root.join(&rel);
            let loaded = match std::fs::read_to_string(&path) {
                Ok(source) => hy_index::index_paths(self.root, &[path.clone()], self.raw).files.into_iter().next().map(|index| Loaded { rel, source, index }),
                Err(error) if path.exists() => {
                    self.errors.push(format!("{}: 読めない: {}", rel, error));
                    None
                }
                Err(_) => None,
            };
            self.loaded.insert(module.to_string(), loaded);
        }
        self.loaded.get(module).and_then(Option::as_ref)
    }

    /// 完全修飾名 target の定義の在りか(module と、file の定義の添字)— module は target の頭の段のうち、repo に Hy の file の在る最も長い物。
    fn locate(&mut self, target: &str) -> Option<(String, usize)> {
        let parts: Vec<&str> = target.split('.').collect();
        for cut in (1..parts.len()).rev() {
            let module = parts[..cut].join(".");
            if let Some(loaded) = self.load(&module) {
                return loaded.index.definitions.iter().position(|d| d.qualified_name == target).map(|index| (module, index));
            }
        }
        None
    }
}

fn inside(inner: &Range, outer: &Range) -> bool {
    outer.start <= inner.start && inner.end <= outer.end
}

/// Hy の source の註(`;` から行末 — 文字列の中の `;` は除く)を空白に潰す。byte の長さを変えないので、位置はそのまま元の source の位置。
fn without_comments(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let (mut in_string, mut escaped, mut in_comment) = (false, false, false);
    for ch in text.chars() {
        let keep = match ch {
            '\n' => {
                in_comment = false;
                true
            }
            _ if in_comment => false,
            _ if escaped => {
                escaped = false;
                true
            }
            '\\' if in_string => {
                escaped = true;
                true
            }
            '"' => {
                in_string = !in_string;
                true
            }
            ';' if !in_string => {
                in_comment = true;
                false
            }
            _ => true,
        };
        if keep {
            out.push(ch);
        } else {
            out.extend(std::iter::repeat_n(' ', ch.len_utf8()));
        }
    }
    out
}

/// 宣言 1 つを判じる。
fn judge_one(blind: &BlindDefinition, modules: &mut Modules, architecture_rel: &str) -> Vec<BlindFinding> {
    let declared = blind.definition.spelling();
    let finding = |rel: String, range: Range, problem: BlindProblem, detail: String| BlindFinding { declared: declared.clone(), why: blind.why.clone(), rel, range, problem, detail };
    let module = blind.definition.mangled_module();
    let root_target = blind.definition.target();
    let (rel, has_root, imports) = match modules.load(&module) {
        Some(loaded) => (
            loaded.rel.clone(),
            loaded.index.definitions.iter().any(|d| d.qualified_name == root_target),
            loaded.index.imports.iter().map(|i| (i.module.clone(), i.is_require, i.range)).collect::<Vec<_>>(),
        ),
        None => {
            let reason = format!("module {} の Hy の file({}.hy)が repo に無い", blind.definition.module, module.replace('.', "/"));
            return vec![finding(architecture_rel.to_string(), blind.range, BlindProblem::Missing { reason }, "missing".to_string())];
        }
    };
    if !has_root {
        let reason = format!("{} に定義 {} が無い", rel, blind.definition.name);
        return vec![finding(architecture_rel.to_string(), blind.range, BlindProblem::Missing { reason }, "missing".to_string())];
    }
    let mut out = Vec::new();
    if blind.no_imports {
        for (imported, is_require, range) in imports {
            let allowed = is_require && blind.allow_requires.iter().any(|m| m == &imported);
            if !allowed {
                let detail = format!("import:{}", imported);
                out.push(finding(rel.clone(), range, BlindProblem::Imports { module: imported }, detail));
            }
        }
        // 同じ module を 2 つの形で読む時(import と require)も、鍵は module ごとに 1 つ。
        let mut seen: BTreeSet<String> = BTreeSet::new();
        out.retain(|f| seen.insert(f.detail.clone()));
    }
    // 届く定義の推移閉包(完全修飾名)— 入れ子の定義は外の定義の範囲の中に在るので、外の定義の本体と辺に含まれる。
    let mut reached: BTreeMap<String, (String, usize)> = BTreeMap::new();
    let mut queue: Vec<String> = vec![root_target.clone()];
    while let Some(target) = queue.pop() {
        if reached.contains_key(&target) {
            continue;
        }
        let Some((module, index)) = modules.locate(&target) else { continue };
        reached.insert(target.clone(), (module.clone(), index));
        let Some(file) = modules.load(&module) else { continue };
        let full = file.index.definitions[index].full_range;
        let in_import = |range: &Range| file.index.imports.iter().any(|imp| inside(range, &imp.range));
        let targets: Vec<String> = file
            .index
            .calls
            .iter()
            .filter(|c| inside(&c.form_range, &full))
            .filter_map(|c| c.target.clone())
            .chain(file.index.references.iter().filter(|r| inside(&r.range, &full) && !in_import(&r.range)).filter_map(|r| r.target.clone()))
            .filter(|t| !reached.contains_key(t))
            .collect();
        queue.extend(targets);
    }
    for (module, index) in reached.values() {
        let Some(file) = modules.load(module) else { continue };
        let definition = &file.index.definitions[*index];
        let start = offset_of(&file.source, definition.full_range.start);
        let end = offset_of(&file.source, definition.full_range.end).max(start);
        let body = without_comments(&file.source[start..end]);
        let lines = LineIndex::new(&file.source);
        for word in &blind.forbid_words {
            if let Some(at) = body.find(word.as_str()) {
                let range = lines.range(start + at, start + at + word.len());
                let reached_name = definition.name.clone();
                let detail = format!("{}:{}", reached_name, word);
                out.push(finding(file.rel.clone(), range, BlindProblem::ReadsWord { reached: reached_name, word: word.clone() }, detail));
            }
        }
    }
    out
}

/// 宣言の全部を判じる。architecture_rel は architecture.hy の根からの path(見つからない宣言の位置)。
pub fn find(root: &Path, blinds: &[BlindDefinition], raw: &RawSettings, architecture_rel: &str) -> (Vec<BlindFinding>, Vec<String>) {
    let mut modules = Modules { root, raw, loaded: HashMap::new(), errors: Vec::new() };
    let found: Vec<BlindFinding> = blinds.iter().flat_map(|blind| judge_one(blind, &mut modules, architecture_rel)).collect();
    (found, modules.errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn comments_become_spaces_of_the_same_length() {
        let text = "(defk f [x] ; view.policy は読まない\n  \"a;b\" x) ;; 末尾";
        let stripped = without_comments(text);
        assert_eq!(stripped.len(), text.len());
        assert!(!stripped.contains("view.policy"));
        assert!(stripped.contains("\"a;b\""), "文字列の中の ; は註でない");
        assert!(stripped.contains("\n  \"a;b\" x)"));
    }
}
