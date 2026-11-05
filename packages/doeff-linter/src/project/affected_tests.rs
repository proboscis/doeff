//! Selects, with distances, the test files that import (and for Hy, also require) a changed file directly or indirectly — the entry point `--affected-tests`
//! (second release). Entry points before registration (doeff's scripts/run_changed_tests.py, and agora's pre-land checks)
//! append them in order of closeness after the set of contract tests and the changed tests.
//!
//! How it reads:
//! - Population = the files git knows in the repo (`git ls-files --cached --others --exclude-standard` — includes new untracked files and
//!   excludes ignored files) that are Python (`.py`) or Hy (`.hy`, `.hyk`, `.hyp`). Dependencies are read the same way as the layer rules
//!   (`facts::module_dependencies` — imports come from `collect_imports` and `python_imports`, with the modules of Hy `(require …)` added).
//! - Module name = the file's path spelled with dots from its source root. Source root = among the dirs found walking up from the file, the first dir that
//!   is named `src`, has a name that is not a Python identifier (`doeff-core-effects`), or is one named by the parent's pyproject.toml
//!   `[tool.maturin] python-source` (the repo root is also a root). A dir whose name is an identifier is not made a root even if it has its own `pyproject.toml` — agora-controllers'
//!   `controllers/scheduling` has a pyproject.toml, but every user imports it by a name from the repo root (`controllers.scheduling.protocol.…`).
//!   Making it a root would name the module `protocol.…`, which would not connect to its users, and the reverse-dependency check silently became 0 (2026-10-05;
//!   files that import by the nested side's name were counted as 0 across the 4 repos that use the linter before this was dropped).
//!   `__init__` is the package's name. `.pyi` is not read, but
//!   as a changed path it gets the same name as the neighboring module (changing a stub selects the users of that module).
//! - An import target `a.b.name` is shrunk to the longest prefix among the known module names (`from pkg import name` is `pkg/name.py` if
//!   it exists, otherwise `pkg/__init__.py`). If no prefix is known, it is outside the repo (a standard or third-party library). The parent package's
//!   `__init__` is not counted as an edge (if it were, every test would be selected for a package whose `__init__` reads everything).
//! - Modules are distinguished by the pair of source root and name. If modules of the same name exist under 2 or more roots (`tests/interpreters.hy` per package),
//!   connect to the one under the same root as the importing file, and to all of them if none is under the same root (the conservative side). Selected tests are counted per file.
//!
//! Handling of Hy require (dependency on macros): it counts as the same kind of edge as import — if macro expansion changes, the
//! rewritten body of the required module changes, so, as with a runtime import, the users' tests should be selected. However, changing a module such as `doeff-hy.macros`
//! that nearly every Hy file requires would select everything, and the order would stop being meaningful within the 60-second limit.
//! So a **too-broad module** (one whose number of direct user files exceeds `max_dependents`) is not passed through, whether by import or by
//! require — the tests beyond it are not selected, and its name and number of users are reported in `hubs`. The caller says
//! "the reverse dependencies are too broad, so nothing was selected (the daily full run measures the whole repo)". The reason require is not treated specially is that an import
//! hub such as `doeff/__init__.py` would likewise select everything (one rule stops both).
//!
//! Limits (what static imports cannot select): tests that hold a module as a string and load it with importlib (stub matching), and tests that walk the tree
//! (dogfood orphan scan, population checks) cannot be selected — the table of contract tests (a fixed set) is responsible for those.
//!
//! Cost: the dependency read for each file is decided only by the file's contents and module name, so it is stored under the facts_cache kind "module-dependencies"
//! (key = path and module name — if the layout changes and the module name changes, that file is re-read).

use std::collections::{BTreeSet, HashMap, VecDeque};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::facts::{module_dependencies, Language, ModuleDependencies};
use super::names::is_identifier;

/// A module with more direct users than this is not passed through (see the header note). Measured on doeff's main line (2026-10-02, 1700 files): the direct users of the modules that read
/// everything are `doeff` 611, `doeff_hy.macros` 530, `doeff_vm` 102, `doeff_core_effects.effects` 72, so
/// passing through would select around 500 test files (all of them). Ordinary modules like `sql_effects` have a single digit. Chosen between those, using as a guide the number of tests that run in the 60-second limit
/// (about 1 second per file, a few dozen). The caller can change it with `--max-dependents`.
pub const DEFAULT_MAX_DEPENDENTS: usize = 40;

/// Extensions and languages of files whose dependencies are read.
const READ_EXTENSIONS: &[(&str, Language)] = &[("py", Language::Python), ("hy", Language::Hy), ("hyk", Language::Hy), ("hyp", Language::Hy)];
/// Extensions that have a module name but are not read (counted only as changed paths).
const NAMED_ONLY_EXTENSIONS: &[&str] = &["pyi"];

/// The query (changed paths, rules for test file names, and the split point for too-broad modules).
#[derive(Debug, Clone)]
pub struct Query {
    /// Paths from the repo root (deleted files are allowed).
    pub changed: Vec<String>,
    /// Rules for test file names (fnmatch — a rule without `/` is applied to the file name, one with `/` to the path from the root). The caller passes its definition.
    pub test_patterns: Vec<String>,
    pub max_dependents: usize,
}

/// One selected test file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AffectedTest {
    pub path: String,
    /// Number of edges from the changed file (a test that imports it directly = 1).
    pub distance: usize,
    /// Path changed path → … → test file (one shortest path).
    pub via: Vec<String>,
}

/// A too-broad module that was not passed through.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Hub {
    pub module: String,
    /// The dir of the module's source root (path from the repo root; empty = the repo root).
    pub root: String,
    /// Number of direct user files.
    pub dependents: usize,
    /// The distance at which that module was reached.
    pub distance: usize,
}

/// A file whose dependencies could not be read (the edges that could be read are used — named as a place where selections may be missed).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Unreadable {
    pub path: String,
    pub reason: String,
}

/// The answer (the JSON of `--affected-tests`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AffectedReport {
    /// In order of closeness; ties in path order.
    pub tests: Vec<AffectedTest>,
    pub hubs: Vec<Hub>,
    /// Changed paths that have no module name (not Python or Hy source — reverse dependencies cannot be traced).
    pub not_modules: Vec<String>,
    pub unreadable: Vec<Unreadable>,
    /// Number of files whose dependencies were read (the population).
    pub files_read: usize,
    pub max_dependents: usize,
}

/// The dependency read of one file, stored in the cache.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct FileDependencies {
    rel: String,
    key: ModuleKey,
    read: ModuleDependencies,
}

/// A path with the extension removed, and the extension.
fn split_extension(rel: &str) -> Option<(&str, &str)> {
    let name_at = rel.rfind('/').map_or(0, |at| at + 1);
    let dot = rel[name_at..].rfind('.')? + name_at;
    Some((&rel[..dot], &rel[dot + 1..]))
}

/// The language to read (None for a file that is not read).
fn language_of(rel: &str) -> Option<Language> {
    let (_, extension) = split_extension(rel)?;
    READ_EXTENSIONS.iter().find(|(e, _)| *e == extension).map(|(_, language)| *language)
}

/// Decision of the source root (remembered per dir).
struct SourceRoots<'a> {
    root: &'a Path,
    memo: HashMap<String, bool>,
}

impl<'a> SourceRoots<'a> {
    fn new(root: &'a Path) -> SourceRoots<'a> {
        SourceRoots { root, memo: HashMap::new() }
    }

    /// Whether the dir from the root (empty = the repo root) is a source root (see the header note).
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

    /// The pyproject.toml `[tool.maturin] python-source` of the dir (None if absent or unreadable — an unreadable setting is decided by the markers of other roots).
    fn declared_python_source(&self, dir: &str) -> Option<String> {
        let text = std::fs::read_to_string(self.root.join(dir).join("pyproject.toml")).ok()?;
        let value: toml::Value = toml::from_str(&text).ok()?;
        value.get("tool")?.get("maturin")?.get("python-source")?.as_str().map(str::to_string)
    }

    /// The module of a path from the root (the source root dir and module name — None unless it is Python or Hy source or a stub).
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

/// Identity of one module — the source root dir and the module name (kept distinct even if modules of the same name exist in each package).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
struct ModuleKey {
    root: String,
    module: String,
}

/// fnmatch matching (`*` is any spelling and also crosses `/`, `?` is one character, `[…]` / `[!…]` is a character set — the same as Python's fnmatch.fnmatchcase).
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

/// Reads the character set after `[` — returns the matching function and the rest after `]` (None if not closed = `[` is just a character).
fn char_class(rest: &[char]) -> Option<(impl Fn(char) -> bool + '_, &[char])> {
    let (negated, body) = match rest.first() {
        Some('!') => (true, &rest[1..]),
        _ => (false, rest),
    };
    // The first `]` is a character in the set (same as fnmatch).
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

/// Whether the path matches the rule for test file names (a rule without `/` is applied to the file name, one with `/` to the path from the root — same as pytest).
pub fn is_test_file(rel: &str, patterns: &[String]) -> bool {
    let name: Vec<char> = rel.rsplit('/').next().unwrap_or(rel).chars().collect();
    let whole: Vec<char> = rel.chars().collect();
    patterns.iter().any(|pattern| {
        let pattern: Vec<char> = pattern.chars().collect();
        fnmatch(&pattern, if pattern.contains(&'/') { &whole } else { &name })
    })
}

/// Paths from the root of the files git knows in the repo (tracked and new untracked files; ignored files excluded).
pub fn known_files(root: &Path) -> Result<Vec<String>, String> {
    let output = std::process::Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| format!("cannot start git: {}", e))?;
    if !output.status.success() {
        return Err(format!("git ls-files failed: {}", String::from_utf8_lossy(&output.stderr).trim()));
    }
    let listed: BTreeSet<String> = output.stdout.split(|b| *b == 0).filter(|n| !n.is_empty()).map(|n| String::from_utf8_lossy(n).into_owned()).collect();
    Ok(listed.into_iter().collect())
}

/// Answers the query (files = paths from the root of the population — the caller passes `known_files`, and tests pass the list of the model tree).
pub fn affected_tests(root: &Path, files: &[String], query: &Query) -> AffectedReport {
    let mut roots = SourceRoots::new(root);
    // Cache key = path, source root, module name (if the layout changes and the module changes, that file is re-read).
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
                Err(error) => ModuleDependencies { dependencies: Vec::new(), error: Some(format!("cannot read: {}", error)) },
            };
            Some(FileDependencies { rel: rel.to_string(), key: ModuleKey { root: source_root.to_string(), module: module.to_string() }, read })
        })
    });
    crate::timing::timed("affected-tests.walk", || walk(&read, query, &mut roots))
}

/// Builds the reverse index and traces breadth-first from the changed paths.
fn walk(read: &[FileDependencies], query: &Query, roots: &mut SourceRoots) -> AffectedReport {
    let key_of: HashMap<&str, &ModuleKey> = read.iter().map(|f| (f.rel.as_str(), &f.key)).collect();
    // Module name → the modules having that name (per source root).
    let mut named: HashMap<&str, BTreeSet<&ModuleKey>> = HashMap::new();
    for file in read {
        named.entry(file.key.module.as_str()).or_default().insert(&file.key);
    }
    // Shrink the import target to the longest prefix of the known module names (outside the repo if none is known). If the same name exists under 2 or more
    // source roots, choose the one under the same root as the importing file, and all of them if none is under the same root (the conservative side).
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
    // module → the files that import or require it (excluding the module itself — import and require are the same edge: see the header note).
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
    // Breadth-first: a level = the expansion of a module. Each file is visited once (the shortest path is kept).
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

    /// Writes a model tree and asks, passing the list without using git.
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

    /// Failure case (Python): in the chain a → b → test_x, changing a selects test_x at distance 2, and tests that do not import a are not selected.
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
        // `from pkg_a import b_missing` shrinks to pkg_a/__init__.py — changing a does not pass through __init__ (the parent package is not counted as an edge).
        assert!(report.hubs.is_empty() && report.unreadable.is_empty() && report.not_modules.is_empty());
    }

    /// Failure case (Hy): in the chain of import and require, changing a selects test_x.hy at distance 2, and tests that do not depend on a are not selected.
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
        // `lib` is an identifier, so it is not a source root — in the model only the root pyproject.toml is a root, and the module is lib.app.a.
        let report = ask(&dir, &files, &["lib/app/a.hy"], DEFAULT_MAX_DEPENDENTS);
        assert!(report.tests.is_empty(), "selected even though the module names do not match: {:?}", report.tests);
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
        // Changing a stub (.pyi) also selects the users of the same module. State that the README is not a module.
        let report = ask(&dir, &files, &["src/app/b.pyi", "README.md"], DEFAULT_MAX_DEPENDENTS);
        assert_eq!(distances(&report), vec![("tests/test_x.hy", 1)]);
        assert_eq!(report.not_modules, vec!["README.md".to_string()]);
    }

    /// A too-broad module (direct users exceed the split point) is not passed through, and its name and number are put in hubs — the same for require and import.
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

    /// Even if modules of the same name exist (tests/test_models.py per package), the tests selected are only the files that imported it.
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

    /// If an import target's name exists in 2 packages (`tests.interpreters`), connect to the one under the same source root as the importing file.
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

    /// Failure case (agora-controllers shape): even if a dir whose name is an identifier has its own pyproject.toml, the modules under it are known by the name from the repo root —
    /// before the fix, `controllers/scheduling` was made a root and named `protocol.records_turns`, and not a single test that imports `controllers.scheduling.protocol.records_turns`
    /// was selected.
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
