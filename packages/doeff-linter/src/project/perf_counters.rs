//! Test-only per-stage counters for file reads, comment stripping, Hy parses, and WalkDir walks.
//! Used to verify that each stage reads/strips/parses each file at most once per run and walks only collapsed roots.

use std::collections::BTreeMap;
use std::sync::{Mutex, MutexGuard, OnceLock};

#[derive(Default)]
struct State {
    active: bool,
    reads: BTreeMap<(String, String), usize>,
    strips: BTreeMap<(String, String), usize>,
    parses: BTreeMap<(String, String), usize>,
    walks: BTreeMap<String, Vec<String>>,
}

static STATE: OnceLock<Mutex<State>> = OnceLock::new();
static TEST_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

fn state() -> &'static Mutex<State> {
    STATE.get_or_init(|| Mutex::new(State::default()))
}

pub struct CounterSession {
    _guard: MutexGuard<'static, ()>,
}

impl CounterSession {
    pub fn start() -> Self {
        let guard = TEST_LOCK.get_or_init(|| Mutex::new(())).lock().unwrap_or_else(|e| e.into_inner());
        let mut s = state().lock().unwrap_or_else(|e| e.into_inner());
        *s = State { active: true, ..State::default() };
        Self { _guard: guard }
    }

    pub fn reads(&self, stage: &str) -> BTreeMap<String, usize> {
        let s = state().lock().unwrap_or_else(|e| e.into_inner());
        s.reads.iter().filter(|((st, _), _)| st == stage).map(|((_, f), &c)| (f.clone(), c)).collect()
    }

    pub fn strips(&self, stage: &str) -> BTreeMap<String, usize> {
        let s = state().lock().unwrap_or_else(|e| e.into_inner());
        s.strips.iter().filter(|((st, _), _)| st == stage).map(|((_, f), &c)| (f.clone(), c)).collect()
    }

    pub fn parses(&self, stage: &str) -> BTreeMap<String, usize> {
        let s = state().lock().unwrap_or_else(|e| e.into_inner());
        s.parses.iter().filter(|((st, _), _)| st == stage).map(|((_, f), &c)| (f.clone(), c)).collect()
    }

    pub fn walks(&self, stage: &str) -> Vec<String> {
        let s = state().lock().unwrap_or_else(|e| e.into_inner());
        s.walks.get(stage).cloned().unwrap_or_default()
    }
}

impl Drop for CounterSession {
    fn drop(&mut self) {
        let mut s = state().lock().unwrap_or_else(|e| e.into_inner());
        s.active = false;
    }
}

pub fn record_read(stage: &str, rel: &str) {
    let mut s = state().lock().unwrap_or_else(|e| e.into_inner());
    if s.active {
        *s.reads.entry((stage.to_string(), rel.to_string())).or_insert(0) += 1;
    }
}

pub fn record_strip(stage: &str, rel: &str) {
    let mut s = state().lock().unwrap_or_else(|e| e.into_inner());
    if s.active {
        *s.strips.entry((stage.to_string(), rel.to_string())).or_insert(0) += 1;
    }
}

pub fn record_parse(stage: &str, rel: &str) {
    let mut s = state().lock().unwrap_or_else(|e| e.into_inner());
    if s.active {
        *s.parses.entry((stage.to_string(), rel.to_string())).or_insert(0) += 1;
    }
}

pub fn record_walk(stage: &str, path: &std::path::Path) {
    let mut s = state().lock().unwrap_or_else(|e| e.into_inner());
    if s.active {
        s.walks.entry(stage.to_string()).or_default().push(path.to_string_lossy().into_owned());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    use crate::project::architecture::{BusinessFakes, ConfinedSpelling, ContractSpellings, CountedSpelling, RetiredCalls, RetiredWords, VocabularyScope, WantedCount, WordPlace};
    use crate::project::{confined_spellings, counted_spellings, retired, single_point_vocabulary, timed_refetch};
    use doeff_indexer::hy_index::Range;

    #[test]
    fn each_stage_reads_strips_parses_each_file_at_most_once_and_walks_collapsed_roots() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        for (rel, content) in [
            (
                "a/x.hy",
                "(defk f1 [x] (while True (<- (Delay 5.0)) x))\n(defk f2 [y] (setv v \"OLD\") (Now) y)\n",
            ),
            (
                "a/sub/y.hy",
                "(defk helper [x] (<- (Delay 2.0)) x)\n(defk caller [] (while True (<- (helper 1))))\n",
            ),
            (
                "b/z.hy",
                "(defk z-fn [] (setv old-var \"OLD\") (Now))\n",
            ),
            (
                "a/p.py",
                "def old_py():\n    x = \"OLD\"\n    return x\n",
            ),
        ] {
            let path = root.join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, content).unwrap();
        }

        let retired_words = vec![
            RetiredWords {
                name: "w-lines-1".into(),
                words: vec!["OLD".into()],
                patterns: vec![],
                files: vec!["a/**".into(), "a/sub/**".into(), "b/**".into()],
                except: vec![],
                rule_lines: vec![],
                contracts: ContractSpellings { files: vec!["c.json".into()], spellings: ["OLD".to_string()].into_iter().collect() },
                place: WordPlace::Lines,
                instead: "new".into(),
            },
            RetiredWords {
                name: "w-lines-2".into(),
                words: vec!["LEGACY".into()],
                patterns: vec![],
                files: vec!["a/**".into(), "b/**".into()],
                except: vec![],
                rule_lines: vec![],
                contracts: ContractSpellings { files: vec!["c.json".into()], spellings: ["LEGACY".to_string()].into_iter().collect() },
                place: WordPlace::Lines,
                instead: "new".into(),
            },
            RetiredWords {
                name: "w-names-1".into(),
                words: vec!["f1".into()],
                patterns: vec![],
                files: vec!["a/**".into(), "a/sub/**".into()],
                except: vec![],
                rule_lines: vec![],
                contracts: Default::default(),
                place: WordPlace::Names,
                instead: "new".into(),
            },
            RetiredWords {
                name: "w-names-2".into(),
                words: vec!["f2".into()],
                patterns: vec![],
                files: vec!["a/**".into(), "b/**".into()],
                except: vec![],
                rule_lines: vec![],
                contracts: Default::default(),
                place: WordPlace::Names,
                instead: "new".into(),
            },
        ];
        let retired_calls = vec![
            RetiredCalls {
                name: "c-1".into(),
                calls: vec!["Now".into()],
                files: vec!["a/**".into(), "b/**".into()],
                except: vec![],
                instead: "GetTime".into(),
            },
            RetiredCalls {
                name: "c-2".into(),
                calls: vec!["time.time".into()],
                files: vec!["a/sub/**".into(), "a/**".into()],
                except: vec![],
                instead: "GetTime".into(),
            },
        ];

        let confined_groups = vec![
            ConfinedSpelling {
                name: "cs-1".into(),
                patterns: vec![r"OLD".into()],
                files: vec!["a/**".into(), "b/**".into()],
                except: vec![],
                names_bound_in: vec![],
                bound_names: vec![],
                why: "w".into(),
                range: Range::default(),
                file: PathBuf::from("architecture.hy"),
            },
            ConfinedSpelling {
                name: "cs-2".into(),
                patterns: vec![r"LEGACY".into()],
                files: vec!["a/**".into(), "a/sub/**".into()],
                except: vec![],
                names_bound_in: vec![],
                bound_names: vec![],
                why: "w".into(),
                range: Range::default(),
                file: PathBuf::from("architecture.hy"),
            },
            ConfinedSpelling {
                name: "cs-3".into(),
                patterns: vec![r"Now".into()],
                files: vec!["a/**".into(), "b/**".into()],
                except: vec![],
                names_bound_in: vec![],
                bound_names: vec![],
                why: "w".into(),
                range: Range::default(),
                file: PathBuf::from("architecture.hy"),
            },
        ];

        let vocab_scopes = vec![
            VocabularyScope {
                name: "spv-1".into(),
                patterns: vec![r"\bOLD\b".into()],
                files: vec!["a/**".into(), "b/**".into()],
                except: vec![],
                instead: "x".into(),
            },
            VocabularyScope {
                name: "spv-2".into(),
                patterns: vec![r"\bNow\b".into()],
                files: vec!["a/**".into(), "b/**".into()],
                except: vec![],
                instead: "x".into(),
            },
            VocabularyScope {
                name: "spv-3".into(),
                patterns: vec![r"\bDelay\b".into()],
                files: vec!["a/**".into()],
                except: vec![],
                instead: "x".into(),
            },
        ];

        let counted_decls = vec![
            CountedSpelling {
                name: "cnt-1".into(),
                pattern: r"OLD".into(),
                files: vec!["a/**".into(), "b/**".into()],
                within: vec![],
                wanted: WantedCount::AtLeast(1),
                why: "w".into(),
                range: Range::default(),
                file: PathBuf::from("architecture.hy"),
            },
            CountedSpelling {
                name: "cnt-2".into(),
                pattern: r"Delay".into(),
                files: vec!["a/x.hy".into()],
                within: vec!["f1".into(), "f2".into()],
                wanted: WantedCount::AtLeast(0),
                why: "w".into(),
                range: Range::default(),
                file: PathBuf::from("architecture.hy"),
            },
            CountedSpelling {
                name: "cnt-3".into(),
                pattern: r"Now".into(),
                files: vec!["a/x.hy".into()],
                within: vec!["f1".into(), "f2".into()],
                wanted: WantedCount::AtLeast(0),
                why: "w".into(),
                range: Range::default(),
                file: PathBuf::from("architecture.hy"),
            },
        ];

        let fakes = BusinessFakes::default();

        let session = CounterSession::start();
        let _ = retired::find(root, &retired_words, &retired_calls, &retired::PartCalls::default(), None);
        let _ = confined_spellings::find(root, &confined_groups, |_| "architecture.hy".to_string(), None);
        let _ = single_point_vocabulary::find(root, &vocab_scopes, None);
        let _ = counted_spellings::find(root, &counted_decls, |_| "architecture.hy".to_string());
        let _ = timed_refetch::find(root, &fakes);

        let mut failures: Vec<String> = Vec::new();
        for stage in ["retired", "confined_spellings", "single_point_vocabulary", "counted_spellings", "timed_refetch"] {
            let reads = session.reads(stage);
            assert!(!reads.is_empty(), "{stage}: expected at least 1 file read to be recorded");
            for (rel, count) in &reads {
                if *count > 1 {
                    failures.push(format!("{stage}: read_to_string({rel}) called {count} times (max 1)"));
                }
            }
            for (rel, count) in &session.strips(stage) {
                if *count > 1 {
                    failures.push(format!("{stage}: comment-strip({rel}) called {count} times (max 1)"));
                }
            }
            for (rel, count) in &session.parses(stage) {
                if *count > 1 {
                    failures.push(format!("{stage}: Reader::read_all({rel}) called {count} times (max 1)"));
                }
            }
        }
        let retired_walks = session.walks("retired");
        if retired_walks.len() != 2 {
            failures.push(format!("retired: WalkDir called {} times (expected 2 collapsed unique roots a and b): {:?}", retired_walks.len(), retired_walks));
        }

        assert!(failures.is_empty(), "O(G×F) / redundant work detected:\n{}", failures.join("\n"));
    }
}
