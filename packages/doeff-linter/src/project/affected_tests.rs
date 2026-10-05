//! 変えた file を直接・間接に import(Hy は require も)する検の file を、距離つきで選ぶ — 入口 `--affected-tests`
//! (agora-redesign #2605 の 2 本目・2 便目)。登記の前の入口(doeff の scripts/run_changed_tests.py・agora の land の前の検)が、
//! 契約の検の組と変えた検の後ろに、近い順で足す。
//!
//! 読み方:
//! - 母集団 = repo の git が知る file(`git ls-files --cached --others --exclude-standard` — 追跡外の新しい file を含み、
//!   無視した file は含まない)のうち、Python(`.py`)と Hy(`.hy`・`.hyk`・`.hyp`)。依存の読みは層の規則と同じ
//!   (`facts::module_dependencies` — import は `collect_imports`・`python_imports`、Hy の `(require …)` の module を足す)。
//! - module の名 = file の path を、source の根から点で綴った物。source の根 = file から上へ歩いて最初に当たる dir のうち、
//!   名が `src`・名が Python の識別子でない(`doeff-core-effects`)・親の pyproject.toml の `[tool.maturin] python-source` が
//!   名指す dir のどれか(repo の根も根)。名が識別子の dir は、自分の `pyproject.toml` を持っていても根にしない — agora-controllers の
//!   `controllers/scheduling` は pyproject.toml を持つが、使い手は全部 repo の根からの名(`controllers.scheduling.protocol.…`)で
//!   import する。根にすると module が `protocol.…` と名付けられて使い手と結ばず、逆依存の検が黙って 0 本になった(2026-10-05・
//!   agora-redesign #3605 の後 — 入れ子の側の名で import する file は、linter を使う 4 つの repo で 0 件と数えてから外した)。
//!   `__init__` は package の名。`.pyi` は読まないが、
//!   変えた path としては隣の module と同じ名になる(stub を変えれば、その module の使い手を選ぶ)。
//! - import の先 `a.b.名` は、知っている module の名のうち最も長い接頭辞へ縮める(`from pkg import name` は `pkg/name.py` が
//!   在ればそれ、無ければ `pkg/__init__.py`)。どの接頭辞も知らなければ repo の外(標準・第三者の library)。親の package の
//!   `__init__` は辺に数えない(数えると、`__init__` が何でも読む package では全部が選ばれる)。
//! - module は source の根と名の組で分ける。同じ名の module が 2 つ以上の根に在れば(package ごとの `tests/interpreters.hy`)、
//!   import した file と同じ根の物へ結び、同じ根に無ければ全部へ結ぶ(保守側)。選ぶ側の検は file ごとに数える。
//!
//! Hy の require(マクロへの依存)の扱い: import と同じ辺に数える — マクロの展開が変われば require した module の
//! 書き換わった本体が変わるので、実行時の import と同じく使い手の検を選ぶべきだから。ただし `doeff-hy.macros` のように
//! ほぼ全部の Hy の file が require する module を変えると、全部が選ばれて 60 秒の上限の中で意味のある順にならない。
//! そこで **広すぎる module**(直接の使い手の file の数が `max_dependents` を越える module)は、import か require かを
//! 問わず通り抜けない — その先の検は選ばず、`hubs` に名と使い手の数を出す。呼び手は「逆依存が広すぎて選ばなかった
//! (repo 全体は日次の全体の検が測る)」と名指す。require だけを特別に扱わないのは、`doeff/__init__.py` のような import の
//! 中心も同じ形で全部を選ばせるから(1 つの規則で両方を止める)。
//!
//! 限界(静的な import で選べない物): module を文字列で持って importlib で読む検(stub の突き合わせ)と、木を歩く検
//! (dogfood の孤児の走査・母集団の検)は選べない — それは契約の検の表(固定の組)が受け持つ。
//!
//! 費用: file ごとの依存の読みは file の中身と module の名だけで決まるので、facts_cache の種類 "module-dependencies" に
//! 置く(鍵の名 = path と module の名 — 置き場の形が変わって module の名が変われば、その file を読み直す)。

use std::collections::{BTreeSet, HashMap, VecDeque};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::facts::{module_dependencies, Language, ModuleDependencies};
use super::names::is_identifier;

/// 直接の使い手がこの数を越える module は通り抜けない(頭の註)。doeff の本線(2026-10-02・1700 file)の実測: 何でも読む
/// module の直接の使い手は `doeff` 611・`doeff_hy.macros` 530・`doeff_vm` 102・`doeff_core_effects.effects` 72 で、
/// 通り抜けると検の file の 500 前後(全部)を選ぶ。`sql_effects` のような普通の module は 1 桁。60 秒の上限で走る検の数
/// (1 file 約 1 秒で数十本)を目安に、その間に置く。呼び手は `--max-dependents` で変えられる。
pub const DEFAULT_MAX_DEPENDENTS: usize = 40;

/// 依存を読む file の拡張子と言語。
const READ_EXTENSIONS: &[(&str, Language)] = &[("py", Language::Python), ("hy", Language::Hy), ("hyk", Language::Hy), ("hyp", Language::Hy)];
/// module の名を持つが読まない拡張子(変えた path としてだけ数える)。
const NAMED_ONLY_EXTENSIONS: &[&str] = &["pyi"];

/// 問い(変えた path・検の file の名の規則・広すぎる module の分け目)。
#[derive(Debug, Clone)]
pub struct Query {
    /// repo の根からの path(消えた file も可)。
    pub changed: Vec<String>,
    /// 検の file の名の規則(fnmatch — `/` を含まない規則は file の名に、含む規則は根からの path に当てる)。呼び手の定義を渡す。
    pub test_patterns: Vec<String>,
    pub max_dependents: usize,
}

/// 選んだ検の file 1 つ。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AffectedTest {
    pub path: String,
    /// 変えた file からの辺の数(直接 import する検 = 1)。
    pub distance: usize,
    /// 変えた path → … → 検の file の道(最短の 1 本)。
    pub via: Vec<String>,
}

/// 通り抜けなかった広すぎる module。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Hub {
    pub module: String,
    /// module の source の根の dir(repo の根からの path・空 = repo の根)。
    pub root: String,
    /// 直接の使い手の file の数。
    pub dependents: usize,
    /// その module に届いた距離。
    pub distance: usize,
}

/// 依存を読めなかった file(読めた分の辺は使う — 選び漏れの在りうる所として名指す)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Unreadable {
    pub path: String,
    pub reason: String,
}

/// 答え(`--affected-tests` の JSON)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AffectedReport {
    /// 距離の近い順・同じ距離は path の順。
    pub tests: Vec<AffectedTest>,
    pub hubs: Vec<Hub>,
    /// module の名を持たない変えた path(Python・Hy の source でない — 逆依存を辿れない)。
    pub not_modules: Vec<String>,
    pub unreadable: Vec<Unreadable>,
    /// 依存を読んだ file の数(母集団)。
    pub files_read: usize,
    pub max_dependents: usize,
}

/// cache に置く file 1 つの依存の読み。
#[derive(Debug, Clone, Serialize, Deserialize)]
struct FileDependencies {
    rel: String,
    key: ModuleKey,
    read: ModuleDependencies,
}

/// 拡張子を外した path と拡張子。
fn split_extension(rel: &str) -> Option<(&str, &str)> {
    let name_at = rel.rfind('/').map_or(0, |at| at + 1);
    let dot = rel[name_at..].rfind('.')? + name_at;
    Some((&rel[..dot], &rel[dot + 1..]))
}

/// 読む言語(読まない file は None)。
fn language_of(rel: &str) -> Option<Language> {
    let (_, extension) = split_extension(rel)?;
    READ_EXTENSIONS.iter().find(|(e, _)| *e == extension).map(|(_, language)| *language)
}

/// source の根の判定(dir ごとに覚える)。
struct SourceRoots<'a> {
    root: &'a Path,
    memo: HashMap<String, bool>,
}

impl<'a> SourceRoots<'a> {
    fn new(root: &'a Path) -> SourceRoots<'a> {
        SourceRoots { root, memo: HashMap::new() }
    }

    /// 根からの dir(空 = repo の根)が source の根か(頭の註)。
    fn is_source_root(&mut self, dir: &str) -> bool {
        if dir.is_empty() {
            return true;
        }
        if let Some(known) = self.memo.get(dir) {
            return *known;
        }
        let (parent, name) = dir.rsplit_once('/').unwrap_or(("", dir));
        let answer = name == "src" || !is_identifier(name) || self.declared_python_source(parent).as_deref() == Some(name);
        self.memo.insert(dir.to_string(), answer);
        answer
    }

    /// dir の pyproject.toml の `[tool.maturin] python-source`(無い・読めなければ None — 読めない設定は他の根の印で決まる)。
    fn declared_python_source(&self, dir: &str) -> Option<String> {
        let text = std::fs::read_to_string(self.root.join(dir).join("pyproject.toml")).ok()?;
        let value: toml::Value = toml::from_str(&text).ok()?;
        value.get("tool")?.get("maturin")?.get("python-source")?.as_str().map(str::to_string)
    }

    /// 根からの path の module(source の根の dir と module の名 — Python・Hy の source と stub でなければ None)。
    fn module_key(&mut self, rel: &str) -> Option<ModuleKey> {
        let (stem_path, extension) = split_extension(rel)?;
        if !READ_EXTENSIONS.iter().any(|(e, _)| *e == extension) && !NAMED_ONLY_EXTENSIONS.contains(&extension) {
            return None;
        }
        let parts: Vec<&str> = stem_path.split('/').collect();
        let (stem, dirs) = parts.split_last()?;
        let mut start = dirs.len();
        while start > 0 && !self.is_source_root(&dirs[..start].join("/")) {
            start -= 1;
        }
        let named: Vec<&str> = dirs[start..].iter().copied().chain((*stem != "__init__").then_some(*stem)).collect();
        (!named.is_empty()).then(|| ModuleKey { root: dirs[..start].join("/"), module: named.join(".") })
    }
}

/// module 1 つの識別 — source の根の dir と module の名(同じ名の module が package ごとに在っても分ける)。
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
struct ModuleKey {
    root: String,
    module: String,
}

/// fnmatch の照合(`*` は `/` も越える任意の綴り・`?` は 1 文字・`[…]` / `[!…]` は文字の組 — Python の fnmatch.fnmatchcase と同じ)。
fn fnmatch(pattern: &[char], text: &[char]) -> bool {
    match pattern.split_first() {
        None => text.is_empty(),
        Some(('*', rest)) => (0..=text.len()).any(|skip| fnmatch(rest, &text[skip..])),
        Some(('?', rest)) => !text.is_empty() && fnmatch(rest, &text[1..]),
        Some(('[', rest)) => match char_class(rest) {
            Some((matches, after)) => text.first().is_some_and(|c| matches(*c)) && fnmatch(after, &text[1..]),
            None => text.first() == Some(&'[') && fnmatch(rest, &text[1..]),
        },
        Some((c, rest)) => text.first() == Some(c) && fnmatch(rest, &text[1..]),
    }
}

/// `[` の後の文字の組を読む — 照合の関数と `]` の後の残り(閉じていなければ None = `[` はただの文字)。
fn char_class(rest: &[char]) -> Option<(impl Fn(char) -> bool + '_, &[char])> {
    let (negated, body) = match rest.first() {
        Some('!') => (true, &rest[1..]),
        _ => (false, rest),
    };
    // 最初の `]` は組の中の文字(fnmatch と同じ)。
    let close = body.iter().skip(1).position(|c| *c == ']')? + 1;
    let set = &body[..close];
    let matches = move |c: char| {
        let mut at = 0;
        let mut found = false;
        while at < set.len() {
            if at + 2 < set.len() && set[at + 1] == '-' {
                found |= set[at] <= c && c <= set[at + 2];
                at += 3;
            } else {
                found |= set[at] == c;
                at += 1;
            }
        }
        found != negated
    };
    Some((matches, &body[close + 1..]))
}

/// path が検の file の名の規則に当たるか(`/` を含まない規則は file の名に、含む規則は根からの path に当てる — pytest と同じ)。
pub fn is_test_file(rel: &str, patterns: &[String]) -> bool {
    let name: Vec<char> = rel.rsplit('/').next().unwrap_or(rel).chars().collect();
    let whole: Vec<char> = rel.chars().collect();
    patterns.iter().any(|pattern| {
        let pattern: Vec<char> = pattern.chars().collect();
        fnmatch(&pattern, if pattern.contains(&'/') { &whole } else { &name })
    })
}

/// repo の git が知る file(追跡と追跡外の新しい file・無視した file は除く)の、根からの path。
pub fn known_files(root: &Path) -> Result<Vec<String>, String> {
    let output = std::process::Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| format!("git を起こせない: {}", e))?;
    if !output.status.success() {
        return Err(format!("git ls-files が失敗した: {}", String::from_utf8_lossy(&output.stderr).trim()));
    }
    let listed: BTreeSet<String> = output.stdout.split(|b| *b == 0).filter(|n| !n.is_empty()).map(|n| String::from_utf8_lossy(n).into_owned()).collect();
    Ok(listed.into_iter().collect())
}

/// 問いに答える(files = 母集団の根からの path — 呼び手は `known_files` か、検では模型の木の一覧を渡す)。
pub fn affected_tests(root: &Path, files: &[String], query: &Query) -> AffectedReport {
    let mut roots = SourceRoots::new(root);
    // cache の鍵の名 = path・source の根・module の名(置き場の形が変わって module が変われば、その file を読み直す)。
    let keyed: Vec<(String, PathBuf)> = files
        .iter()
        .filter(|rel| language_of(rel).is_some())
        .filter_map(|rel| {
            let path = root.join(rel);
            let key = roots.module_key(rel)?;
            path.is_file().then(|| (format!("{rel}\n{}\n{}", key.root, key.module), path))
        })
        .collect();
    let read: Vec<FileDependencies> = crate::timing::timed("affected-tests.read", || {
        super::facts_cache::per_file(root, "module-dependencies", &keyed, |key, path| {
            let mut parts = key.splitn(3, '\n');
            let (rel, source_root, module) = (parts.next()?, parts.next()?, parts.next()?);
            let language = language_of(rel)?;
            let read = match std::fs::read_to_string(path) {
                Ok(source) => module_dependencies(language, &source, module),
                Err(error) => ModuleDependencies { dependencies: Vec::new(), error: Some(format!("読めない: {}", error)) },
            };
            Some(FileDependencies { rel: rel.to_string(), key: ModuleKey { root: source_root.to_string(), module: module.to_string() }, read })
        })
    });
    crate::timing::timed("affected-tests.walk", || walk(&read, query, &mut roots))
}

/// 逆向きの索引を組み、変えた path から幅優先で辿る。
fn walk(read: &[FileDependencies], query: &Query, roots: &mut SourceRoots) -> AffectedReport {
    let key_of: HashMap<&str, &ModuleKey> = read.iter().map(|f| (f.rel.as_str(), &f.key)).collect();
    // module の名 → その名を持つ module(source の根ごと)。
    let mut named: HashMap<&str, BTreeSet<&ModuleKey>> = HashMap::new();
    for file in read {
        named.entry(file.key.module.as_str()).or_default().insert(&file.key);
    }
    // import の先を、知っている module の名の最も長い接頭辞へ縮める(どれも知らなければ repo の外)。同じ名が 2 つ以上の
    // source の根に在れば、import した file と同じ根の物を選び、同じ根に無ければ全部(保守側)。
    let resolve = |target: &str, importer: &ModuleKey| -> Vec<&ModuleKey> {
        let mut candidate = target;
        loop {
            if let Some(found) = named.get(candidate) {
                let same_root: Vec<&ModuleKey> = found.iter().copied().filter(|k| k.root == importer.root).collect();
                return if same_root.is_empty() { found.iter().copied().collect() } else { same_root };
            }
            match candidate.rfind('.') {
                Some(at) => candidate = &candidate[..at],
                None => return Vec::new(),
            }
        }
    };
    // module → それを import か require する file(自分自身の module は除く — import と require は同じ辺: 頭の註)。
    let mut users: HashMap<&ModuleKey, BTreeSet<&str>> = HashMap::new();
    for file in read {
        for dependency in &file.read.dependencies {
            for module in resolve(&dependency.target, &file.key).into_iter().filter(|m| **m != file.key) {
                users.entry(module).or_default().insert(file.rel.as_str());
            }
        }
    }
    let changed: Vec<(&str, ModuleKey)> = query.changed.iter().filter_map(|rel| roots.module_key(rel).map(|k| (rel.as_str(), k))).collect();
    let not_modules: Vec<String> = query.changed.iter().filter(|rel| roots.module_key(rel).is_none()).cloned().collect();
    // 幅優先: 段 = module の展開。file の訪問は 1 度(最短の道を残す)。
    let seeds: BTreeSet<&str> = query.changed.iter().map(String::as_str).collect();
    let mut parent: HashMap<&str, (&str, usize)> = HashMap::new();
    let mut expanded: BTreeSet<ModuleKey> = BTreeSet::new();
    let mut hubs: Vec<Hub> = Vec::new();
    let mut queue: VecDeque<(ModuleKey, &str, usize)> = changed.into_iter().map(|(rel, key)| (key, rel, 0)).collect();
    while let Some((module, from, distance)) = queue.pop_front() {
        if !expanded.insert(module.clone()) {
            continue;
        }
        let Some(dependents) = users.get(&module) else { continue };
        if dependents.len() > query.max_dependents {
            hubs.push(Hub { module: module.module, root: module.root, dependents: dependents.len(), distance });
            continue;
        }
        for user in dependents {
            if seeds.contains(user) || parent.contains_key(user) {
                continue;
            }
            parent.insert(user, (from, distance + 1));
            if let Some(next) = key_of.get(user) {
                queue.push_back(((*next).clone(), user, distance + 1));
            }
        }
    }
    let trail = |end: &str| -> Vec<String> {
        let mut steps = vec![end.to_string()];
        let mut here = end;
        while let Some((up, _)) = parent.get(here) {
            steps.push(up.to_string());
            here = up;
        }
        steps.reverse();
        steps
    };
    let mut tests: Vec<AffectedTest> = parent
        .iter()
        .filter(|(rel, _)| is_test_file(rel, &query.test_patterns))
        .map(|(rel, (_, distance))| AffectedTest { path: rel.to_string(), distance: *distance, via: trail(rel) })
        .collect();
    tests.sort_by(|a, b| (a.distance, &a.path).cmp(&(b.distance, &b.path)));
    hubs.sort_by(|a, b| (a.distance, &a.root, &a.module).cmp(&(b.distance, &b.root, &b.module)));
    let unreadable: Vec<Unreadable> =
        read.iter().filter_map(|f| f.read.error.as_ref().map(|reason| Unreadable { path: f.rel.clone(), reason: reason.clone() })).collect();
    AffectedReport { tests, hubs, not_modules, unreadable, files_read: read.len(), max_dependents: query.max_dependents }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 模型の木を書き、git を使わずに一覧を渡して問う。
    fn model(files: &[(&str, &str)]) -> (tempfile::TempDir, Vec<String>) {
        let dir = tempfile::tempdir().unwrap();
        for (rel, text) in files {
            let path = dir.path().join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        let listed = files.iter().map(|(rel, _)| rel.to_string()).collect();
        (dir, listed)
    }

    fn ask(dir: &tempfile::TempDir, files: &[String], changed: &[&str], max_dependents: usize) -> AffectedReport {
        let query = Query {
            changed: changed.iter().map(|c| c.to_string()).collect(),
            test_patterns: vec!["test_*.py".to_string(), "test_*.hy".to_string()],
            max_dependents,
        };
        affected_tests(dir.path(), files, &query)
    }

    fn distances(report: &AffectedReport) -> Vec<(&str, usize)> {
        report.tests.iter().map(|t| (t.path.as_str(), t.distance)).collect()
    }

    /// 失敗ケース(Python): a → b → test_x の連鎖で、a を変えると test_x が距離 2 で選ばれ、a を import しない検は選ばれない。
    #[test]
    fn a_python_chain_selects_the_test_at_distance_two_and_not_the_unrelated_test() {
        let (dir, files) = model(&[
            ("pyproject.toml", ""),
            ("packages/pkg-a/pyproject.toml", ""),
            ("packages/pkg-a/src/pkg_a/__init__.py", ""),
            ("packages/pkg-a/src/pkg_a/a.py", "X = 1\n"),
            ("packages/pkg-a/src/pkg_a/b.py", "from .a import X\n"),
            ("packages/pkg-a/tests/test_x.py", "from pkg_a.b import X\n\ndef test_x():\n    assert X\n"),
            ("packages/pkg-a/tests/test_direct.py", "def test_d():\n    import pkg_a.a\n"),
            ("packages/pkg-a/tests/test_unrelated.py", "import json\n"),
            ("tests/test_other.py", "from pkg_a import b_missing\n"),
        ]);
        let report = ask(&dir, &files, &["packages/pkg-a/src/pkg_a/a.py"], DEFAULT_MAX_DEPENDENTS);
        assert_eq!(distances(&report), vec![("packages/pkg-a/tests/test_direct.py", 1), ("packages/pkg-a/tests/test_x.py", 2)]);
        assert_eq!(
            report.tests[1].via,
            vec!["packages/pkg-a/src/pkg_a/a.py", "packages/pkg-a/src/pkg_a/b.py", "packages/pkg-a/tests/test_x.py"]
        );
        // `from pkg_a import b_missing` は pkg_a/__init__.py へ縮む — a を変えても __init__ は通らない(親の package を辺に数えない)。
        assert!(report.hubs.is_empty() && report.unreadable.is_empty() && report.not_modules.is_empty());
    }

    /// 失敗ケース(Hy): import と require の連鎖で、a を変えると test_x.hy が距離 2 で選ばれ、a に依らない検は選ばれない。
    #[test]
    fn a_hy_chain_through_require_selects_the_test_at_distance_two() {
        let (dir, files) = model(&[
            ("pyproject.toml", ""),
            ("lib/app/__init__.py", ""),
            ("lib/app/a.hy", "(defmacro m [] 1)\n"),
            ("lib/app/b.hy", "(require app.a [m])\n(defn f [] (m))\n"),
            ("lib-tests/test_x.hy", "(import app.b [f])\n(defn test-x [] (assert (f)))\n"),
            ("lib-tests/test_y.hy", "(import os)\n"),
        ]);
        // `lib` は識別子なので source の根ではない — 模型では根の pyproject.toml だけが根で、module は lib.app.a。
        let report = ask(&dir, &files, &["lib/app/a.hy"], DEFAULT_MAX_DEPENDENTS);
        assert!(report.tests.is_empty(), "module の名が合わないのに選んだ: {:?}", report.tests);
        let (dir, files) = model(&[
            ("pyproject.toml", ""),
            ("src/app/__init__.py", ""),
            ("src/app/a.hy", "(defmacro m [] 1)\n"),
            ("src/app/b.hy", "(require app.a [m])\n(defn f [] (m))\n"),
            ("tests/test_x.hy", "(import app.b [f])\n(defn test-x [] (assert (f)))\n"),
            ("tests/test_y.hy", "(import os)\n"),
        ]);
        let report = ask(&dir, &files, &["src/app/a.hy"], DEFAULT_MAX_DEPENDENTS);
        assert_eq!(distances(&report), vec![("tests/test_x.hy", 2)]);
        // stub(.pyi)を変えても同じ module の使い手を選ぶ。README は module ではないと名指す。
        let report = ask(&dir, &files, &["src/app/b.pyi", "README.md"], DEFAULT_MAX_DEPENDENTS);
        assert_eq!(distances(&report), vec![("tests/test_x.hy", 1)]);
        assert_eq!(report.not_modules, vec!["README.md".to_string()]);
    }

    /// 広すぎる module(直接の使い手が分け目を越える)は通り抜けず、名と数を hubs に出す — require でも import でも同じ。
    #[test]
    fn a_module_used_by_too_many_files_is_named_as_a_hub_and_not_walked_through() {
        let (dir, files) = model(&[
            ("pyproject.toml", ""),
            ("src/macros.hy", "(defmacro deftest [] 1)\n"),
            ("tests/test_a.hy", "(require macros [deftest])\n"),
            ("tests/test_b.hy", "(require macros [deftest])\n"),
            ("tests/test_c.hy", "(require macros *)\n"),
        ]);
        let report = ask(&dir, &files, &["src/macros.hy"], 2);
        assert!(report.tests.is_empty());
        assert_eq!(report.hubs, vec![Hub { module: "macros".to_string(), root: "src".to_string(), dependents: 3, distance: 0 }]);
        let report = ask(&dir, &files, &["src/macros.hy"], 3);
        assert_eq!(distances(&report), vec![("tests/test_a.hy", 1), ("tests/test_b.hy", 1), ("tests/test_c.hy", 1)]);
    }

    /// 同じ名の module(package ごとの tests/test_models.py)が在っても、選ぶ検は import した file だけ。
    #[test]
    fn colliding_test_module_names_do_not_select_the_other_package_test() {
        let (dir, files) = model(&[
            ("pyproject.toml", ""),
            ("packages/p-one/pyproject.toml", ""),
            ("packages/p-one/one.py", "V = 1\n"),
            ("packages/p-one/tests/test_models.py", "import one\n"),
            ("packages/p-two/pyproject.toml", ""),
            ("packages/p-two/tests/test_models.py", "import json\n"),
        ]);
        let report = ask(&dir, &files, &["packages/p-one/one.py"], DEFAULT_MAX_DEPENDENTS);
        assert_eq!(distances(&report), vec![("packages/p-one/tests/test_models.py", 1)]);
    }

    /// import の先の名が 2 つの package に在れば(`tests.interpreters`)、import した file と同じ source の根の物へ結ぶ。
    #[test]
    fn an_import_of_a_colliding_name_binds_to_the_same_source_root() {
        let (dir, files) = model(&[
            ("pyproject.toml", ""),
            ("packages/p-one/pyproject.toml", ""),
            ("packages/p-one/tests/interpreters.hy", "(setv X 1)\n"),
            ("packages/p-one/tests/test_one.hy", "(import tests.interpreters [X])\n"),
            ("packages/p-two/pyproject.toml", ""),
            ("packages/p-two/tests/interpreters.hy", "(setv X 2)\n"),
            ("packages/p-two/tests/test_two.hy", "(import tests.interpreters [X])\n"),
        ]);
        let report = ask(&dir, &files, &["packages/p-one/tests/interpreters.hy"], DEFAULT_MAX_DEPENDENTS);
        assert_eq!(distances(&report), vec![("packages/p-one/tests/test_one.hy", 1)]);
    }

    /// 失敗ケース(agora-controllers の形): 名が識別子の dir が自分の pyproject.toml を持っていても、その下の module は repo の根からの名で
    /// 知る — 直す前は `controllers/scheduling` を根にして `protocol.records_turns` と名付け、`controllers.scheduling.protocol.records_turns`
    /// を import する検を 1 本も選ばなかった。
    #[test]
    fn an_identifier_dir_with_its_own_pyproject_keeps_the_names_from_the_repo_root() {
        let (dir, files) = model(&[
            ("pyproject.toml", ""),
            ("controllers/__init__.py", ""),
            ("controllers/scheduling/pyproject.toml", "[project]\nname = \"agora-scheduling\"\n"),
            ("controllers/scheduling/__init__.py", ""),
            ("controllers/scheduling/protocol/__init__.py", ""),
            ("controllers/scheduling/protocol/records_turns.hy", "(defn records-turns [] 1)\n"),
            ("controllers/scheduling/tests/test_turn_facts.hy", "(import controllers.scheduling.protocol.records_turns [records-turns])\n"),
            ("controllers/agora_sim/tests/test_far.hy", "(import os)\n"),
        ]);
        let report = ask(&dir, &files, &["controllers/scheduling/protocol/records_turns.hy"], DEFAULT_MAX_DEPENDENTS);
        assert_eq!(distances(&report), vec![("controllers/scheduling/tests/test_turn_facts.hy", 1)]);
        let mut roots = SourceRoots::new(dir.path());
        assert_eq!(
            roots.module_key("controllers/scheduling/protocol/records_turns.hy").map(|k| k.module).as_deref(),
            Some("controllers.scheduling.protocol.records_turns")
        );
    }

    #[test]
    fn module_names_follow_the_source_roots() {
        let (dir, _) = model(&[
            ("pyproject.toml", ""),
            ("packages/doeff-x/pyproject.toml", "[tool.maturin]\npython-source = \"python\"\n"),
            ("packages/doeff-x/python/doeff_x/__init__.py", ""),
        ]);
        let mut roots = SourceRoots::new(dir.path());
        assert_eq!(roots.module_key("packages/doeff-core-effects/doeff_core_effects/sql_effects.hy").map(|k| k.module).as_deref(), Some("doeff_core_effects.sql_effects"));
        assert_eq!(roots.module_key("packages/doeff-cluster/src/doeff_cluster/model.hy").map(|k| k.module).as_deref(), Some("doeff_cluster.model"));
        assert_eq!(roots.module_key("packages/doeff-x/python/doeff_x/__init__.py").map(|k| k.module).as_deref(), Some("doeff_x"));
        assert_eq!(roots.module_key("doeff/__init__.py").map(|k| k.module).as_deref(), Some("doeff"));
        assert_eq!(roots.module_key("tests/core/test_a.py").map(|k| k.module).as_deref(), Some("tests.core.test_a"));
        assert_eq!(roots.module_key("docs/adr/defadr_x.hy").map(|k| k.module).as_deref(), Some("docs.adr.defadr_x"));
        assert_eq!(roots.module_key("README.md"), None);
    }

    #[test]
    fn test_patterns_match_like_pytest() {
        let patterns: Vec<String> = ["test_*.py", "defadr_*.hy", "docs/adr/defadr_*.hy", "*_test.py", "test_[ab].hy"].iter().map(|p| p.to_string()).collect();
        assert!(is_test_file("packages/p/tests/test_x.py", &patterns));
        assert!(is_test_file("a/b_test.py", &patterns));
        assert!(is_test_file("docs/adr/defadr_one.hy", &patterns));
        assert!(is_test_file("x/test_a.hy", &patterns));
        assert!(!is_test_file("x/test_c.hy", &patterns));
        assert!(!is_test_file("tests/helpers.py", &patterns));
        assert!(!is_test_file("test_x.pyi", &patterns));
    }
}
