//! Declarations of DOEFF122 failure types — in a single-file run, collected only from declaration files whose text literally contains the type name.
//!
//! The check for one file only asks whether the type of one match arm (the name resolved from the written file's symbol with `Scope::qualify`) is a failure type. Files that need that are rare (about 2% of a
//! sample of 150 files), but collecting declarations from all of the repo's Hy as the whole run does takes 19 to 27 seconds on the first run in a cold worktree (the hook's deadline is 4 seconds).
//! A file that has declarations (`:failure True` of `defrecord`, and the `:failure` / `:absent` lists of `defeffect`) always literally contains the spelling of the type name (the last segment of the resolved name)
//! (`spelling`), so only files that spell `:failure` or `:absent` and also spell the type name are parsed. The answer is the same as looking up the set built by the whole run (`failure_types_for`).
//!
//! The repo walk that looks for declaration files does not read the contents of unchanged files (the wait for reading all of the repo's Hy in a cold worktree is 3 seconds):
//! it looks up the spelling memo by git blob id (`blob_spell` — whether it contains a declaration spelling, whether it contains `(defeffect`, and a Bloom filter of which names it spells), and even a file that contains a declaration spelling
//! has its contents read only when it is looked up by name and the memo says it may spell that name.

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};

use super::layers::TagReading;
use super::rule::ProjectRule;
use super::smells::{failure_types_key, failure_types_of_source, has_failure_declaration_spelling, EffectOutcomes, FailureLookup, FailureTypes, FAILURE_TYPES_KIND};
use super::blob_spell::{self, BlobSpell, Seen};
use super::spelling::Spelling;
use super::{bulk_read, facts_cache};

/// Failure types collected, when looked up, from the declaration files whose text literally contains the type name (single-file run).
pub struct SpelledFailures<'a> {
    root: &'a Path,
    enabled: &'a BTreeSet<ProjectRule>,
    reading: &'a TagReading,
    /// For each type name (last segment), the failure types collected from the declaration files that spell that name (the same name is not collected twice).
    memo: Mutex<HashMap<String, Arc<FailureTypes>>>,
    /// Files that have declarations (files containing `:failure` or `:absent`) — independent of the type name, so collected from the repo only once, at the first query.
    /// Re-reading all of the repo's Hy for each name cost 4 seconds of CPU on a file with many match-arm types (`cli_host.hy`, 4,866 lines).
    declarations: OnceLock<Vec<(String, Declaring)>>,
    /// Contents, read when looked up by name, of declaration files that the memo settled and whose contents were not yet read (path from the root → contents; None if unreadable).
    sources: Mutex<HashMap<String, Option<Arc<str>>>>,
    /// Failure types per declaration file (path from the root → the types that file declares). Decided only by the file's contents and how it is read, independent of the type name being looked up, so the same file is not parsed again when the name changes
    /// (when match-arm types were looked up under different names many times, a name like `|` that spells every declaration file repeated the parsing of 246 files once per name).
    parsed: Mutex<HashMap<String, Arc<FailureTypes>>>,
    /// All of the repo's defeffect declarations (the absent and failure types per effect) — DOEFF175 looks up by effect name. A file with effect declarations contains the spelling `(defeffect`, so
    /// build it only once by parsing just the declaration files that contain it (do not search for declaration files again per effect name — with 40 names in one file that was 40 re-searches).
    effects: OnceLock<FailureTypes>,
}

impl<'a> SpelledFailures<'a> {
    pub fn new(root: &'a Path, enabled: &'a BTreeSet<ProjectRule>, reading: &'a TagReading) -> Self {
        SpelledFailures {
            root,
            enabled,
            reading,
            memo: Mutex::new(HashMap::new()),
            declarations: OnceLock::new(),
            sources: Mutex::new(HashMap::new()),
            parsed: Mutex::new(HashMap::new()),
            effects: OnceLock::new(),
        }
    }

    /// Files that have declarations — collected by walking the repo only once, at the first query (files settled by the memo do not have their contents read — `blob_spell::scan`).
    fn declarations(&self) -> &Vec<(String, Declaring)> {
        self.declarations.get_or_init(|| {
            blob_spell::scan(self.root, "failure-types.collect", |_| false)
                .into_iter()
                .filter_map(|(rel, path, seen)| match seen {
                    Seen::Spell(spell) => spell.failure.then(|| (rel, Declaring::Spelled(path, spell))),
                    Seen::Source(source) => has_failure_declaration_spelling(&source).then(|| (rel, Declaring::Read(Arc::from(source)))),
                    Seen::Unreadable => None,
                })
                .collect()
        })
    }

    /// Among the declaration files, those whose memo (files whose contents were not read) or contents (files that were read) match `keep` (path from the root, contents). Files matched by the memo are
    /// read here (stacking the wait; contents read once are remembered), and the contents are re-checked with `exact` (the memo only says "may match").
    fn sources_where(&self, keep: impl Fn(&BlobSpell) -> bool, exact: impl Fn(&str) -> bool) -> Vec<(String, Arc<str>)> {
        let declarations = self.declarations();
        let unread: Vec<(&String, &PathBuf)> = {
            let sources = self.sources.lock().unwrap_or_else(PoisonError::into_inner);
            declarations
                .iter()
                .filter_map(|(rel, declaring)| match declaring {
                    Declaring::Spelled(path, spell) if keep(spell) && !sources.contains_key(rel) => Some((rel, path)),
                    Declaring::Spelled(..) | Declaring::Read(_) => None,
                })
                .collect()
        };
        let read = crate::timing::timed("failure-types.read", || bulk_read::fan_out(&unread, |(_, path)| std::fs::read_to_string(path).ok().map(Arc::<str>::from)));
        let mut sources = self.sources.lock().unwrap_or_else(PoisonError::into_inner);
        for ((rel, _), source) in unread.iter().zip(read) {
            sources.insert((*rel).clone(), source);
        }
        declarations
            .iter()
            .filter_map(|(rel, declaring)| match declaring {
                Declaring::Read(source) => Some((rel, Arc::clone(source))),
                Declaring::Spelled(_, spell) => keep(spell).then(|| sources.get(rel).cloned().flatten()).flatten().map(|source| (rel, source)),
            })
            .filter(|(_, source)| exact(source))
            .map(|(rel, source)| (rel.clone(), source))
            .collect()
    }

    /// The failure types of the declaration files that spell `name` (the last segment of a type name).
    fn declared_with(&self, name: &str) -> FailureTypes {
        let spelling = Spelling::of(name);
        let kept = self.sources_where(|spell| spell.may_spell(&spelling), |source| spelling.in_source(source));
        let kept: Vec<(&str, &str)> = kept.iter().map(|(rel, source)| (rel.as_str(), source.as_ref())).collect();
        self.merged(&kept)
    }

    /// Parses a list of declaration files (path from the root, contents) and bundles them into one set (the parsing of each file is remembered).
    fn merged(&self, kept: &[(&str, &str)]) -> FailureTypes {
        let mut parsed = self.parsed.lock().expect("per-file memo of failure types");
        let fresh: Vec<(&str, &str)> = kept.iter().filter(|(rel, _)| !parsed.contains_key(*rel)).copied().collect();
        if !fresh.is_empty() {
            // The parsing is remembered in the facts store (the same store and same key as the whole run — an unchanged file is not parsed again on the next run). Empty facts are remembered too, so files that are not declarations are not re-parsed.
            let reading = self.reading;
            let done = crate::timing::timed("failure-types.parse", || facts_cache::per_file_content(self.root, FAILURE_TYPES_KIND, &failure_types_key(reading), &fresh, |rel, source| {
                Some(failure_types_of_source(rel, source, reading))
            }));
            for ((rel, _), found) in fresh.iter().zip(done) {
                parsed.insert((*rel).to_string(), Arc::new(found.unwrap_or_default()));
            }
        }
        let mut all = FailureTypes::default();
        for (rel, _) in kept {
            all.extend(parsed[*rel].as_ref().clone());
        }
        all
    }

    /// All of the repo's defeffect declarations (built only once at the first query). Among the declaration files, only those that spell `(defeffect` are parsed.
    fn all_effects(&self) -> &FailureTypes {
        self.effects.get_or_init(|| {
            let kept = self.sources_where(|spell| spell.defeffect, |source| source.contains("(defeffect"));
            let kept: Vec<(&str, &str)> = kept.iter().map(|(rel, source)| (rel.as_str(), source.as_ref())).collect();
            crate::timing::timed("failure-types.effects", || self.merged(&kept))
        })
    }
}

/// One declaration-bearing file — either one whose contents were read during the walk, or one settled by the spelling memo whose contents have not yet been read.
enum Declaring {
    Read(Arc<str>),
    Spelled(PathBuf, BlobSpell),
}

impl SpelledFailures<'_> {
    /// Declarations collected from the declaration files that spell the last segment of `qualified` (a type or effect name) (the same name is not collected twice).
    fn declared_for(&self, qualified: &str) -> Arc<FailureTypes> {
        let name = qualified.rsplit('.').next().unwrap_or(qualified);
        let known = self.memo.lock().expect("memo of failure types").get(name).map(Arc::clone);
        known.unwrap_or_else(|| {
            let collected = Arc::new(crate::timing::timed("failure-types", || self.declared_with(name)));
            self.memo.lock().expect("memo of failure types").insert(name.to_string(), Arc::clone(&collected));
            collected
        })
    }
}

impl FailureLookup for SpelledFailures<'_> {
    fn is_failure(&self, qualified: &str) -> bool {
        // If the rule is disabled, do not collect declarations (same as `failure_types_for` of the whole run).
        if !self.enabled.contains(&ProjectRule::FailureRethrow) {
            return false;
        }
        self.declared_for(qualified).contains(qualified)
    }

    fn effect_outcomes(&self, qualified_effect: &str) -> Option<EffectOutcomes> {
        // If DOEFF175 is disabled, do not collect per-effect declarations (same as `failure_types_for` of the whole run).
        if !self.enabled.contains(&ProjectRule::StaleOutcomeBranch) {
            return None;
        }
        self.all_effects().effect_outcomes(qualified_effect)
    }
}

#[cfg(test)]
mod tests {
    use doeff_indexer::hy_index::collect_hy_files;

    use super::super::facts::{read_facts, Language};
    use super::super::layers::tag_reading;
    use super::super::names::module_of;
    use super::super::paths::relative_path;
    use super::super::smells::{failure_types_in, Scope};
    use super::*;

    /// Failure types collected from all of the repo's Hy — built the same way as the whole run (`failure_types_for`) (what is walked is `hy_index::collect_hy_files` — separate from how the check's target is walked).
    fn failure_types_of_whole(root: &Path, enabled: &BTreeSet<ProjectRule>, reading: &TagReading) -> FailureTypes {
        let mut all = FailureTypes::default();
        if !enabled.contains(&ProjectRule::FailureRethrow) {
            return all;
        }
        for path in collect_hy_files(root) {
            let (Some(rel), Ok(source)) = (relative_path(root, &path), std::fs::read_to_string(&path)) else { continue };
            if !(source.contains(":failure") || source.contains(":absent")) {
                continue;
            }
            let module = module_of(&rel);
            let facts = read_facts(Language::Hy, &source, &module, reading);
            all.extend(failure_types_in(&source, Scope { module: &module, bindings: &facts.bindings }));
        }
        all
    }

    fn enabled() -> BTreeSet<ProjectRule> {
        BTreeSet::from([ProjectRule::FailureRethrow])
    }

    fn repo(files: &[(&str, &str)]) -> tempfile::TempDir {
        let dir = tempfile::TempDir::new().unwrap();
        for (rel, text) in files {
            let path = dir.path().join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        dir
    }

    fn sample() -> Vec<(&'static str, &'static str)> {
        vec![
            ("app/errs.hy", "(defrecord Refusal \"refusal\" {:failure True} #^ str reason)\n(defrecord Rejection #^ str reason)\n(defrecord Unreachable [reason])\n"),
            (
                "app/effs.hy",
                "(import app.errs)\n(import app.store :as store)\n(defeffect ReadRow \"reads a row\" {:fields [key] :answer None :failure [app.errs.Unreachable store.Gone] :absent [Missing]})\n",
            ),
            ("app/store.hy", "(defrecord Gone [x])\n"),
            // Even if a same-named type exists in another module, only the declared one is a failure type.
            ("app/errs2.hy", "(defrecord Unreachable [r])\n"),
            // A file that spells `:failure`, but spells the type name only inside a longer symbol.
            ("app/long.hy", "(defeffect Z {:fields [] :answer None :failure [RefusalAll]})\n"),
            // A file that spells the type name but is not a declaration.
            ("app/use.hy", "(defk decide [x]\n  (match x\n    (Refusal) (return x)))\n; :failure is not a declaration here\n"),
            ("app/plain.hy", "(defn f [] 1)\n"),
        ]
    }

    /// The answer collected only from declaration files that literally contain the type name equals the answer from looking up the failure types collected from all of the repo (declared types, undeclared types, and a same-named type in another module).
    #[test]
    fn the_answer_for_a_type_equals_the_answer_from_the_whole_repo() {
        let dir = repo(&sample());
        let reading = tag_reading(None, None);
        let enabled = enabled();
        let whole = failure_types_of_whole(dir.path(), &enabled, &reading);
        assert!(whole.contains("app.errs.Refusal") && whole.contains("app.errs.Unreachable") && whole.contains("app.store.Gone") && whole.contains("app.effs.Missing"), "the test material is insufficient");
        let lookup = SpelledFailures::new(dir.path(), &enabled, &reading);
        for qualified in [
            "app.errs.Refusal",
            "app.errs.Unreachable",
            "app.store.Gone",
            "app.effs.Missing",
            "app.long.RefusalAll",
            "app.errs.Rejection",
            "app.errs2.Unreachable",
            "app.use.Refusal",
            "app.errs.Nothing",
            "Refusal",
        ] {
            assert_eq!(lookup.is_failure(qualified), whole.contains(qualified), "{qualified}");
        }
    }

    /// Per-effect declarations equal what the whole run collects (a declared effect, an effect with no declaration, and a same-named one in another module).
    #[test]
    fn the_effect_declarations_equal_the_whole_repo() {
        let dir = repo(&sample());
        let reading = tag_reading(None, None);
        let enabled = BTreeSet::from([ProjectRule::StaleOutcomeBranch]);
        let whole = {
            let mut all = FailureTypes::default();
            for path in collect_hy_files(dir.path()) {
                let (Some(rel), Ok(source)) = (relative_path(dir.path(), &path), std::fs::read_to_string(&path)) else { continue };
                if !(source.contains(":failure") || source.contains(":absent")) {
                    continue;
                }
                let module = module_of(&rel);
                let facts = read_facts(Language::Hy, &source, &module, &reading);
                all.extend(failure_types_in(&source, Scope { module: &module, bindings: &facts.bindings }));
            }
            all
        };
        let lookup = SpelledFailures::new(dir.path(), &enabled, &reading);
        for effect in ["app.effs.ReadRow", "app.long.Z", "app.use.ReadRow", "app.effs.Nothing", "ReadRow"] {
            assert_eq!(lookup.effect_outcomes(effect), whole.effect_outcomes(effect), "{effect}");
        }
        let read_row = lookup.effect_outcomes("app.effs.ReadRow").expect("ReadRow has a declaration");
        assert!(read_row.absent.contains("app.effs.Missing") && read_row.failure.contains("app.errs.Unreachable") && read_row.failure.contains("app.store.Gone"), "{read_row:?}");
        // If the rule is disabled, per-effect declarations are not collected either (same as the whole run).
        assert_eq!(SpelledFailures::new(dir.path(), &BTreeSet::new(), &reading).effect_outcomes("app.effs.ReadRow"), None);
    }

    /// If the rule is disabled, there are no failure types even when declarations exist (same as the whole run).
    #[test]
    fn nothing_is_a_failure_while_the_rule_is_off() {
        let dir = repo(&sample());
        let reading = tag_reading(None, None);
        let off = BTreeSet::new();
        assert!(!SpelledFailures::new(dir.path(), &off, &reading).is_failure("app.errs.Refusal"));
        assert!(failure_types_of_whole(dir.path(), &off, &reading).is_empty());
    }

    /// Looking up a different type name does not re-read the repo's Hy — the files that have declarations (files containing `:failure` or `:absent`) do not depend on the name, so they are collected only once
    /// (a file with 20 match-arm types re-read about 2,900 of the repo's Hy files per name and cost 4 seconds of CPU — `cli_host.hy`, 2026-10-17).
    #[test]
    fn different_names_share_one_read_of_the_repo() {
        let dir = repo(&sample());
        let reading = tag_reading(None, None);
        let enabled = enabled();
        let lookup = SpelledFailures::new(dir.path(), &enabled, &reading);
        assert!(lookup.is_failure("app.errs.Refusal"));
        // Even if a declaration file is deleted, a different name is answered from the declarations collected by the first read.
        std::fs::remove_file(dir.path().join("app/errs.hy")).unwrap();
        std::fs::remove_file(dir.path().join("app/effs.hy")).unwrap();
        assert!(lookup.is_failure("app.errs.Unreachable"), "the repo is re-read every time a different name is looked up");
        assert!(lookup.is_failure("app.store.Gone"));
        assert!(!lookup.is_failure("app.errs.Rejection"), "an undeclared type is not a failure type even under a different name");
    }

    /// The parsing of a declaration file happens only once even if the name that spells that file changes (decided only by the file's contents and how it is read, independent of the name looked up).
    /// When re-parsed per name, a name that spells every declaration file (such as `|`) repeated the parsing of 246 files once per name.
    #[test]
    fn a_declaration_file_is_read_through_once_for_every_name_that_spells_it() {
        let dir = repo(&sample());
        let reading = tag_reading(None, None);
        let enabled = enabled();
        let lookup = SpelledFailures::new(dir.path(), &enabled, &reading);
        assert!(lookup.is_failure("app.errs.Refusal"));
        let first = Arc::clone(&lookup.parsed.lock().unwrap()["app/errs.hy"]);
        // errs.hy also spells Unreachable: even when a different name is looked up, the already-collected parsing is used as is.
        assert!(lookup.is_failure("app.errs.Unreachable"));
        let again = Arc::clone(&lookup.parsed.lock().unwrap()["app/errs.hy"]);
        assert!(Arc::ptr_eq(&first, &again), "the same declaration file is re-parsed every time a different name is looked up");
    }

    /// A query with the same name is not collected twice (memo) — answered from the set collected the first time.
    #[test]
    fn the_same_name_is_collected_once() {
        let dir = repo(&sample());
        let reading = tag_reading(None, None);
        let enabled = enabled();
        let lookup = SpelledFailures::new(dir.path(), &enabled, &reading);
        assert!(lookup.is_failure("app.errs.Refusal"));
        // Even if a declaration file is deleted, answer from the collected set.
        std::fs::remove_file(dir.path().join("app/errs.hy")).unwrap();
        assert!(lookup.is_failure("app.errs.Refusal"));
        assert!(!SpelledFailures::new(dir.path(), &enabled, &reading).is_failure("app.errs.Refusal"));
    }
}
