//! Material for the second form of DOEFF126 — in a single-file run, collect "Programs the caller passed as arguments" only from files that contain the callee's name in their text.
//!
//! A single-file check pulls only the facts of "calls to callees made by functions in the written file" (`param_calls_in` pulls by the names of definitions that call their own arguments bare). A whole run
//! builds facts from all Hy calls in the repo (`program_params_for`), but following that approach in a single-file run would read all of the repo's Hy on the first run in a cold working tree
//! (about 2,900 files, for defk names) and re-analyze all of them (8 to 13 seconds — the hook's deadline is 4 seconds). The facts to pull have fixed callees, so building everything is unnecessary.
//!
//! A caller's file always contains the spelling of the callee definition's name (already mangled) in its text (`spelling` — the shapes that can be spelled, and the rule that names whose spelling cannot be fixed are not narrowed). Only files that contain it are analyzed;
//! the others are read and then discarded.
//!
//! Pulling a caller's facts happens in a run after other tables are built, so the callees the written file may pull are estimated at the start of the run, and the repo read is started earlier on a separate thread
//! (`SpelledCallers::started` — in a cold working tree every stage waits on disk, so they are overlapped). The estimate includes every callee actually pulled; if it does not fit, the read is redone when pulling.
//!
//! Whether a passed symbol is a defk is decided not by the defk names of the whole repo (reading all files) but only by the file of the module being pulled (the naming of `defk_names_over` —
//! a symbol is resolved up to its module, a defk's name is `module.name`, and the answer is the same from that module's file alone). The answer (the facts and their order) is the same as what `program_params_for` builds:
//! files are analyzed in `hy_rel_files` order, and facts are in the order of calls within a file.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, PoisonError};
use std::thread::JoinHandle;

use rayon::prelude::*;

use super::bare_calls::{defk_names_in, defk_names_over, DefkNames};
use super::blob_spell::{self, Seen};
use super::facts_cache::Reach;
use super::names::module_of;
use super::param_calls::{asked_callees, candidates_in, Candidate, PassedProgram, ProgramParamsLookup};
use super::rule::ProjectRule;
use super::smells::Scope;
use super::spelling::Spelling;
use super::{facts, hy_files, named_modules};

/// "Programs the caller passed as arguments", collected when requested from files that contain the callee's name in their text (single-file run).
pub struct SpelledCallers<'a> {
    root: &'a Path,
    enabled: &'a BTreeSet<ProjectRule>,
    /// The number of files analyzed because the text matched (for tests).
    analyzed: AtomicUsize,
    /// The read started early at the start of the run (`started`). If the callee being pulled fits within that read's callees, wait for it and use it.
    started: Mutex<Option<JoinHandle<Started>>>,
}

/// The result of the read that was started early — which callee names the text was searched for, and the read of all Hy files in the repo.
struct Started {
    callees: Vec<String>,
    scans: Vec<(String, PathBuf, Scan)>,
}

/// How to probe the text for one callee.
struct Needle {
    callee: String,
    /// The spelling of the callee's name (its last component).
    spelling: Spelling,
}

impl Needle {
    fn of(callee: &str) -> Needle {
        let name = callee.rsplit('.').next().unwrap_or(callee);
        Needle { callee: callee.to_string(), spelling: Spelling::of(name) }
    }
}

/// The result of reading one file.
#[derive(Default)]
struct Scan {
    /// The contents of a file whose text matched.
    source: Option<String>,
    /// Whether it contains the spelling `(defk`.
    mentions_defk: bool,
}

/// Read file contents while walking the repo tree (overlapping the wait for opening dirs and the wait for reading files — in a cold working tree each one waits on disk once).
/// Keep only the contents of files whose text matched. For contents read by another working tree, look up the spelling memory by the git blob id, and skip reading if it is known that no callee name is spelled
/// (`blob_spell::scan`).
fn read_scans(root: &Path, callees: &[String]) -> Vec<(String, PathBuf, Scan)> {
    let needles: Vec<Needle> = callees.iter().map(|callee| Needle::of(callee)).collect();
    blob_spell::scan(root, "program-params.scan", |spell| needles.iter().any(|needle| spell.may_spell(&needle.spelling)))
        .into_iter()
        .map(|(rel, path, seen)| {
            let scan = match seen {
                Seen::Spell(spell) => Scan { source: None, mentions_defk: spell.defk },
                Seen::Source(source) => {
                    let mentions_defk = source.contains("(defk");
                    let spelled = needles.iter().any(|needle| needle.spelling.in_source(&source));
                    Scan { source: spelled.then_some(source), mentions_defk }
                }
                Seen::Unreadable => Scan::default(),
            };
            (rel, path, scan)
        })
        .collect()
}

impl<'a> SpelledCallers<'a> {
    pub fn new(root: &'a Path, enabled: &'a BTreeSet<ProjectRule>) -> Self {
        SpelledCallers { root, enabled, analyzed: AtomicUsize::new(0), started: Mutex::new(None) }
    }

    /// If the written file (`rel`, `source`) has a definition that calls its own argument bare, start reading the repo right now on a separate thread, using that callee's name.
    /// Pulling a caller's facts happens in a run after the defk names, the registry, and the effect world are built. In a cold working tree each of those waits on disk, so overlap them from the start.
    /// The callees are decided by the estimate with an empty set of defks (`asked_callees` — it includes every callee actually pulled). If the callee being pulled does not fit, the read is redone when pulling.
    pub fn started(root: &'a Path, enabled: &'a BTreeSet<ProjectRule>, rel: &str, source: &str) -> Self {
        let callers = Self::new(root, enabled);
        if !enabled.contains(&ProjectRule::DefkCalledBare) {
            return callers;
        }
        let module = module_of(rel);
        let bindings = facts::hy_bindings(source, &module);
        let callees = asked_callees(source, Scope { module: &module, bindings: &bindings }, &DefkNames::default());
        if callees.is_empty() {
            return callers;
        }
        let owned_root = root.to_path_buf();
        let spawned = std::thread::Builder::new().name("program-params-read".to_string()).spawn(move || {
            let scans = crate::timing::timed("program-params.started", || read_scans(&owned_root, &callees));
            Started { callees, scans }
        });
        if let Ok(handle) = spawned {
            *callers.started.lock().unwrap_or_else(PoisonError::into_inner) = Some(handle);
        }
        callers
    }

    /// The number of files analyzed because the text matched.
    pub fn analyzed(&self) -> usize {
        self.analyzed.load(Ordering::SeqCst)
    }

    /// The read of all Hy files in the repo that searched for the text of `callees` — if there is a read started early and the callees fit, use it (wait); otherwise read now.
    fn scans_for(&self, callees: &[String]) -> Vec<(String, PathBuf, Scan)> {
        let started = self.started.lock().unwrap_or_else(PoisonError::into_inner).take();
        if let Some(handle) = started {
            if let Ok(done) = crate::timing::timed("program-params.wait", || handle.join()) {
                if callees.iter().all(|callee| done.callees.contains(callee)) {
                    return done.scans;
                }
            }
        }
        read_scans(self.root, callees)
    }

    /// Of `symbols` (symbols resolved up to the module), those that are defks — decided only from the symbol's module file.
    fn defks_among(&self, symbols: &BTreeSet<&str>) -> DefkNames {
        let known = hy_files::by_module(self.root);
        let modules: BTreeSet<String> = symbols.iter().filter_map(|symbol| symbol.rsplit_once('.')).map(|(module, _)| module.to_string()).collect();
        let files = named_modules::files_of(&known, &modules, None);
        defk_names_over(self.root, self.enabled, Reach::Named, &files)
    }
}

impl ProgramParamsLookup for SpelledCallers<'_> {
    fn passed_to_all(&self, callees: &[String]) -> Vec<Vec<PassedProgram>> {
        let mut settled: Vec<Vec<PassedProgram>> = vec![Vec::new(); callees.len()];
        if callees.is_empty() || !self.enabled.contains(&ProjectRule::DefkCalledBare) {
            return settled;
        }
        crate::timing::timed("program-params", || {
            let scans = self.scans_for(callees);
            let matched: Vec<(&str, &str)> = scans.iter().filter_map(|(rel, _, scan)| scan.source.as_deref().map(|source| (rel.as_str(), source))).collect();
            self.analyzed.fetch_add(matched.len(), Ordering::SeqCst);
            let wants = |callee: &str| callees.iter().any(|wanted| wanted == callee);
            let candidates: Vec<Candidate> = crate::timing::timed("program-params.analyze", || {
                let per_file: Vec<Vec<Candidate>> = matched
                    .par_iter()
                    .map(|(rel, source)| {
                        let module = module_of(rel);
                        let bindings = facts::hy_bindings(source, &module);
                        candidates_in(source, rel, Scope { module: &module, bindings: &bindings }, &wants)
                    })
                    .collect();
                per_file.into_iter().flatten().collect()
            });
            let symbols: BTreeSet<&str> = candidates.iter().filter_map(Candidate::qualified).collect();
            let defks = crate::timing::timed("program-params.defks", || self.defks_among(&symbols));
            let mut proved_defk = false;
            for candidate in candidates {
                let Some(at) = callees.iter().position(|callee| *callee == candidate.callee) else { continue };
                let by_defk = candidate.qualified().is_some();
                if let Some(fact) = candidate.settle(|qualified| defks.contains(qualified)) {
                    proved_defk |= by_defk;
                    settled[at].push(fact);
                }
            }
            // If the repo has no defk at all, the whole run builds no facts (including the `(fnk …)` facts) — give the same answer.
            if settled.iter().any(|facts| !facts.is_empty()) && !proved_defk {
                let with_defk: Vec<(&str, &PathBuf)> = scans.iter().filter(|(_, _, scan)| scan.mentions_defk).map(|(rel, path, _)| (rel.as_str(), path)).collect();
                let defines = crate::timing::timed("program-params.has-defk", || {
                    with_defk.par_iter().any(|(rel, path)| std::fs::read_to_string(path).is_ok_and(|source| !defk_names_in(&source, &module_of(rel)).is_empty()))
                });
                if !defines {
                    settled.iter_mut().for_each(Vec::clear);
                }
            }
        });
        settled
    }
}

#[cfg(test)]
mod tests {
    use doeff_indexer::hy_index::collect_hy_files;

    use super::super::param_calls::ProgramParams;
    use super::super::paths::relative_path;
    use super::*;

    fn enabled() -> BTreeSet<ProjectRule> {
        BTreeSet::from([ProjectRule::DefkCalledBare])
    }

    /// A repo with the tree `files` (path from the root, and contents) placed.
    fn repo(files: &[(&str, &str)]) -> tempfile::TempDir {
        let dir = tempfile::TempDir::new().unwrap();
        for (rel, text) in files {
            let path = dir.path().join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        dir
    }

    /// Facts built from all of the repo's Hy — built the same way as the whole run (`program_params_for`): collect the defk names of all files and accumulate from the calls of all files (the walker is
    /// `hy_index::collect_hy_files` — the test's counterpart and the way it walks are kept separate).
    fn whole(root: &Path) -> ProgramParams {
        let files: Vec<(String, String)> = collect_hy_files(root)
            .into_iter()
            .filter_map(|path| Some((relative_path(root, &path)?, std::fs::read_to_string(&path).ok()?)))
            .collect();
        let mut defks = DefkNames::default();
        for (rel, source) in files.iter().filter(|(_, source)| source.contains("(defk")) {
            defks.extend(defk_names_in(source, &module_of(rel)));
        }
        let mut params = ProgramParams::default();
        if defks.is_empty() {
            return params;
        }
        for (rel, source) in &files {
            let module = module_of(rel);
            let bindings = facts::hy_bindings(source, &module);
            let mut one = ProgramParams::default();
            one.collect(source, rel, Scope { module: &module, bindings: &bindings }, &defks);
            params.merge(one);
        }
        params
    }

    /// Facts built only from files that contain the callee's name in their text — what a single-file run uses (in the same order as the callees).
    fn named(root: &std::path::Path, callees: &[String]) -> (Vec<Vec<PassedProgram>>, usize) {
        let enabled = enabled();
        let callers = SpelledCallers::new(root, &enabled);
        let facts = callers.passed_to_all(callees);
        (facts, callers.analyzed())
    }

    const HOF: &str = "(import app.payload [payload-of])
(defk rows-by-text [rows field-of] (for [row rows] (setv value (field-of row))) rows)
(defk safe-one [rows pick-of] (<- v (pick-of (get rows 0))) v)
(deff _private-fn [f] (f 1))
(defk check? [pred] (pred 1))
(defk twice [rows] (! (rows-by-text rows payload-of)))
";

    fn sample() -> Vec<(&'static str, &'static str)> {
        vec![
            ("app/hof.hy", HOF),
            ("app/payload.hy", "(defk payload-of [row] row)\n(defn plain-of [row] row)\n"),
            (
                "app/user_a.hy",
                "(import app.hof [rows-by-text])
(import app.payload [payload-of plain-of])
(defk a [tags] (! (rows-by-text tags payload-of)))
(defk b [tags] (! (rows-by-text tags (fnk [row] (<- p (payload-of row)) p))))
(defk c [tags] (! (rows-by-text tags plain-of)))
",
            ),
            (
                "app/user_b.hy",
                "(import app.hof :as h)
(import app.payload [payload-of])
(defk d [tags] (! (h.rows_by_text tags :field-of payload-of)))
(defk e [tags] (! (h.safe-one tags payload-of)))
",
            ),
            (
                "app/user_c.hy",
                "(import app.payload)
(defk f [tags] (! (app.hof.rows-by-text tags app.payload.payload-of)))
(defk g [tags] (! (app.hof._private-fn app.payload.payload-of)))
(defk h [tags] (! (app.hof.check? app.payload.payload-of)))
(defk i [tags] (! (app.hof.check? payload-of)))
",
            ),
            ("app/other.hy", "(defk unrelated [x] x)\n"),
            ("app/docs.hy", "; an example calling rows-by-text is in user_a\n(defn note [] \"rows_by_text\")\n"),
            // Files that merely contain the name's spelling in the middle of a longer symbol (a module path component, or the first half of another name) are not call heads.
            ("app/longer.hy", "(import controllers.rows-by-text.system)\n(defk rows-by-text-all [x] x)\n(defk all-rows-by-text [x] x)\n"),
        ]
    }

    /// Facts built by narrowing callees by text are the same as facts built from all of the repo, in both facts and order (per callee).
    #[test]
    fn the_facts_for_a_callee_equal_the_facts_made_from_the_whole_repo() {
        let dir = repo(&sample());
        let whole = whole(dir.path());
        let mut callees = whole.callees();
        // Callees with no facts and callees with no definition also answer empty.
        callees.extend(["app.hof.twice", "app.nowhere.missing", "app.hof.rows_by_text_not"].map(String::from));
        assert!(callees.len() >= 6, "not enough test material: {callees:?}");
        let (facts, _) = named(dir.path(), &callees);
        for (callee, found) in callees.iter().zip(&facts) {
            assert_eq!(found.as_slice(), whole.of(callee), "{callee}");
        }
        // It does not pass vacuously by comparing empties: symbols (positional and keyword), fnk, module-qualified, aliased, and same-module calls all become facts.
        assert_eq!(whole.of("app.hof.rows_by_text").len(), 5);
        assert_eq!(whole.of("app.hof.safe_one").len(), 1);
        assert_eq!(whole.of("app.hof._private_fn").len(), 1);
        assert_eq!(whole.of("app.hof.hyx_checkXquestion_markX").len(), 1);
    }

    /// Pulling several callees at once gives the same result as pulling one at a time (files are read once).
    #[test]
    fn asking_for_several_callees_at_once_gives_the_same_facts_as_asking_one_by_one() {
        let dir = repo(&sample());
        let callees: Vec<String> = ["app.hof.rows_by_text", "app.hof.safe_one", "app.hof._private_fn"].map(String::from).to_vec();
        let (together, _) = named(dir.path(), &callees);
        for (callee, found) in callees.iter().zip(&together) {
            let (alone, _) = named(dir.path(), std::slice::from_ref(callee));
            assert_eq!(&alone[0], found, "{callee}");
        }
    }

    /// Analyze only files that contain the callee's name in their text (including files that spell it with either `-` or `_`, and files that spell it only in a comment or string. Files that do not contain it are not analyzed).
    #[test]
    fn only_the_files_that_spell_the_callee_name_are_analyzed() {
        let dir = repo(&sample());
        // hof (a call in the same module as the definition), user_a (`-`), user_b (`_`), user_c (module-qualified), docs (comment and string). payload and other do not spell it.
        let (_, analyzed) = named(dir.path(), &["app.hof.rows_by_text".to_string()]);
        assert_eq!(analyzed, 5);
        let (_, analyzed) = named(dir.path(), &["app.hof.safe_one".to_string()]);
        assert_eq!(analyzed, 2, "only hof (the definition) and user_b spell safe-one");
    }

    /// A name containing non-identifier characters (that mangle escaped to `hyx_`) cannot have its spelling fixed, so the text does not narrow it — the answer is still the same.
    #[test]
    fn a_name_that_mangle_escapes_is_not_narrowed_but_still_gives_the_same_facts() {
        let dir = repo(&sample());
        let callee = "app.hof.hyx_checkXquestion_markX".to_string();
        let (facts, analyzed) = named(dir.path(), std::slice::from_ref(&callee));
        assert_eq!(analyzed, sample().len());
        assert_eq!(facts[0].as_slice(), whole(dir.path()).of(&callee));
    }

    /// If the repo has no defk at all, the whole run builds no facts that pass `(fnk …)` either — a single-file run is the same. If a defk exists, they are built.
    #[test]
    fn without_any_defk_in_the_repo_no_facts_are_made_like_the_whole_run() {
        let lib = ("lib.hy", "(defn apply-it [f] (f 1))\n");
        let user = ("user.hy", "(import lib [apply-it])\n(defn go [] (apply-it (fnk [x] x)))\n");
        let callee = ["lib.apply_it".to_string()];
        let without = repo(&[lib, user]);
        assert!(whole(without.path()).of(&callee[0]).is_empty());
        assert!(named(without.path(), &callee).0[0].is_empty());
        let with = repo(&[lib, user, ("other.hy", "(defk d [x] x)\n")]);
        assert_eq!(whole(with.path()).of(&callee[0]).len(), 1);
        assert_eq!(named(with.path(), &callee).0[0].as_slice(), whole(with.path()).of(&callee[0]));
    }

    /// Using the read started early at the start of the run changes neither the facts nor their order. If the callee being pulled does not fit the estimate, it re-reads when pulling and returns the same answer.
    #[test]
    fn a_read_started_early_gives_the_same_facts_and_falls_back_when_the_callees_do_not_fit() {
        let dir = repo(&sample());
        let whole = whole(dir.path());
        let enabled = enabled();
        // Definitions in hof.hy with bare calls: rows-by-text (`setv`), _private-fn, check?. safe-one is the right-hand side of `<-`, so it is not a bare call.
        let callers = SpelledCallers::started(dir.path(), &enabled, "app/hof.hy", HOF);
        assert!(callers.started.lock().unwrap().is_some(), "the read was not started early");
        let fits = ["app.hof.rows_by_text".to_string(), "app.hof._private_fn".to_string()];
        let facts = callers.passed_to_all(&fits);
        for (callee, found) in fits.iter().zip(&facts) {
            assert_eq!(found.as_slice(), whole.of(callee), "{callee}");
        }
        assert!(callers.started.lock().unwrap().is_none(), "the awaited read remains");
        // A callee not in the estimate (safe-one) cannot use the early read, so it re-reads — the answer is the same.
        let again = SpelledCallers::started(dir.path(), &enabled, "app/hof.hy", HOF);
        let unfit = ["app.hof.safe_one".to_string()];
        assert_eq!(again.passed_to_all(&unfit)[0].as_slice(), whole.of(&unfit[0]));
        assert_eq!(whole.of(&unfit[0]).len(), 1, "not enough test material");
    }

    /// If the written file has no definition with a bare call, the read is not started early (do not waste the read).
    #[test]
    fn nothing_is_read_early_when_the_written_file_calls_no_parameter_bare() {
        let dir = repo(&sample());
        let enabled = enabled();
        let callers = SpelledCallers::started(dir.path(), &enabled, "app/other.hy", "(defk unrelated [x] x)\n");
        assert!(callers.started.lock().unwrap().is_none());
        let off = BTreeSet::new();
        assert!(SpelledCallers::started(dir.path(), &off, "app/hof.hy", HOF).started.lock().unwrap().is_none());
    }

    /// The estimate (with an empty set of defks) includes every callee pulled with the actual set.
    #[test]
    fn the_estimate_with_no_defks_contains_every_callee_asked_with_the_real_defks() {
        let source = "(defk apply-all [rows f] (for [r rows] (setv v (f r))) rows)\n(defk through [rows g] (! (apply-all rows (fnk [r] (g r)))))\n(defk direct [rows h] (setv w (h rows)) w)\n(defk calm [rows k] (<- v (k rows)) v)\n(defk wrapped [rows m] (! (step-of (m rows))))\n";
        let bindings = std::collections::BTreeMap::new();
        let scope = Scope { module: "app.m", bindings: &bindings };
        let guess = asked_callees(source, scope, &DefkNames::default());
        let real = asked_callees(source, scope, &DefkNames::of(&["app.m.step_of", "app.m.apply_all"]));
        assert!(real.iter().all(|callee| guess.contains(callee)), "{real:?} ⊄ {guess:?}");
        assert!(guess.contains(&"app.m.wrapped".to_string()) && !real.contains(&"app.m.wrapped".to_string()), "not enough test material: {guess:?} / {real:?}");
    }
}
