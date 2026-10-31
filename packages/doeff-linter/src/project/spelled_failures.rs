//! DOEFF122 の失敗の型の宣言 — 1 file の実行で、型の名を字面に含む宣言の file だけから集める。
//!
//! 1 file の検査が引くのは、match の腕の型 1 つ(書いた file の記号を `Scope::qualify` で解いた名)が失敗の型かだけ。それが要る file は稀(標本 150 file の
//! 約 2%)だが、全体の実行と同じに repo の Hy の全部から宣言を集めると、冷えた作業木の最初の 1 回が 19〜27 秒かかる(hook の期限は 4 秒)。
//! 宣言(`defrecord` の `:failure True` と `defeffect` の `:failure` / `:absent` の列)を持つ file は、型の名(解いた名の最後の段)の綴りを字面に必ず
//! 含む(`spelling`)ので、`:failure` か `:absent` を綴り、型の名も綴る file だけを読み解く。答えは全体の実行(`failure_types_for`)が作る集合を引いた時と同じ。
//!
//! 宣言の file を探す repo の歩きは、変わっていない file の中身を読まない(冷えた作業木で repo の Hy の全部を読む待ちが 3 秒):
//! git の blob の id で綴りの覚え(`blob_spell` — 宣言の綴りを含むか・`(defeffect` を含むか・どの名を綴るかの Bloom filter)を引き、宣言の綴りを含む file
//! でも、中身は名で引かれて覚えがその名を綴るかもしれないと言った時に初めて読む。

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};

use super::layers::TagReading;
use super::rule::ProjectRule;
use super::smells::{failure_types_key, failure_types_of_source, has_failure_declaration_spelling, EffectOutcomes, FailureLookup, FailureTypes, FAILURE_TYPES_KIND};
use super::blob_spell::{self, BlobSpell, Seen};
use super::spelling::Spelling;
use super::{bulk_read, facts_cache};

/// 引かれた時に型の名を字面に含む宣言の file から集める失敗の型(1 file の実行)。
pub struct SpelledFailures<'a> {
    root: &'a Path,
    enabled: &'a BTreeSet<ProjectRule>,
    reading: &'a TagReading,
    /// 型の名(最後の段)ごとの、その名を綴る宣言の file から集めた失敗の型(同じ名は 2 度集めない)。
    memo: Mutex<HashMap<String, Arc<FailureTypes>>>,
    /// 宣言を持つ file(`:failure` か `:absent` を含む file)— 型の名に依らないので、最初の問いで 1 度だけ repo から集める。
    /// 名ごとに repo の Hy の全部を読み直すと、match の腕の型が多い file(4,866 行の `cli_host.hy`)で CPU が 4 秒かかった。
    declarations: OnceLock<Vec<(String, Declaring)>>,
    /// 覚えで済んで中身をまだ読んでいない宣言の file の、名で引かれて読んだ中身(根からの path → 中身・読めなければ None)。
    sources: Mutex<HashMap<String, Option<Arc<str>>>>,
    /// 宣言の file ごとの失敗の型(根からの path → その file が宣言する型)。file の中身と読み方だけで決まり、引く型の名に依らないので、名が変わっても同じ file を読み解き直さない
    /// (match の腕の型が違う名で何度も引かれると、`|` のように全部の宣言の file を綴る名では 246 file の読み解きが名の数だけ繰り返された)。
    parsed: Mutex<HashMap<String, Arc<FailureTypes>>>,
    /// repo の defeffect の宣言(effect ごとの不在・失敗の型)の全部 — DOEFF175 が effect の名で引く。effect の宣言を持つ file は `(defeffect` の綴りを含むので、
    /// 宣言の file の中からそれを含む物だけを読み解いて 1 度だけ作る(effect の名ごとに宣言の file を探し直さない — 1 file に 40 の名があると 40 回の探し直しだった)。
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

    /// 宣言を持つ file — 最初の問いで 1 度だけ repo を歩いて集める(覚えで済む file は中身を読まない — `blob_spell::scan`)。
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

    /// 宣言の file のうち、覚え(中身を読んでいない file)か中身(読んだ file)が `keep` に当たる物の(根からの path・中身)。覚えで当たった file は
    /// ここで中身を読み(待ちを重ねる・1 度読んだ中身は覚える)、`exact` で中身を確かめ直す(覚えは「当たるかもしれない」しか言わない)。
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

    /// `name`(型の名の最後の段)を綴る宣言の file の失敗の型。
    fn declared_with(&self, name: &str) -> FailureTypes {
        let spelling = Spelling::of(name);
        let kept = self.sources_where(|spell| spell.may_spell(&spelling), |source| spelling.in_source(source));
        let kept: Vec<(&str, &str)> = kept.iter().map(|(rel, source)| (rel.as_str(), source.as_ref())).collect();
        self.merged(&kept)
    }

    /// 宣言の file(根からの path・中身)の列を読み解いて 1 つの集合に束ねる(file ごとの読み解きは覚える)。
    fn merged(&self, kept: &[(&str, &str)]) -> FailureTypes {
        let mut parsed = self.parsed.lock().expect("失敗の型の file ごとの memo");
        let fresh: Vec<(&str, &str)> = kept.iter().filter(|(rel, _)| !parsed.contains_key(*rel)).copied().collect();
        if !fresh.is_empty() {
            // 読み解きは事実の置き場で覚える(全体の実行と同じ置き場・同じ鍵 — 変わっていない file は、次の実行でも読み解かない)。空の事実も覚えて、宣言でない file を読み解き直さない。
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

    /// repo の defeffect の宣言の全部(最初の問いで 1 度だけ作る)。宣言の file のうち `(defeffect` を綴る物だけを読み解く。
    fn all_effects(&self) -> &FailureTypes {
        self.effects.get_or_init(|| {
            let kept = self.sources_where(|spell| spell.defeffect, |source| source.contains("(defeffect"));
            let kept: Vec<(&str, &str)> = kept.iter().map(|(rel, source)| (rel.as_str(), source.as_ref())).collect();
            crate::timing::timed("failure-types.effects", || self.merged(&kept))
        })
    }
}

/// 宣言を持つ file 1 つ — 歩く間に中身を読んだ物と、綴りの覚えで済んで中身をまだ読んでいない物。
enum Declaring {
    Read(Arc<str>),
    Spelled(PathBuf, BlobSpell),
}

impl SpelledFailures<'_> {
    /// `qualified`(型か effect の名)の最後の段を綴る宣言の file から集めた宣言(同じ名は 2 度集めない)。
    fn declared_for(&self, qualified: &str) -> Arc<FailureTypes> {
        let name = qualified.rsplit('.').next().unwrap_or(qualified);
        let known = self.memo.lock().expect("失敗の型の memo").get(name).map(Arc::clone);
        known.unwrap_or_else(|| {
            let collected = Arc::new(crate::timing::timed("failure-types", || self.declared_with(name)));
            self.memo.lock().expect("失敗の型の memo").insert(name.to_string(), Arc::clone(&collected));
            collected
        })
    }
}

impl FailureLookup for SpelledFailures<'_> {
    fn is_failure(&self, qualified: &str) -> bool {
        // 規則が無効なら宣言を集めない(全体の実行の `failure_types_for` と同じ)。
        if !self.enabled.contains(&ProjectRule::FailureRethrow) {
            return false;
        }
        self.declared_for(qualified).contains(qualified)
    }

    fn effect_outcomes(&self, qualified_effect: &str) -> Option<EffectOutcomes> {
        // DOEFF175 が無効なら effect ごとの宣言を集めない(全体の実行の `failure_types_for` と同じ)。
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

    /// repo の Hy の全部から集める失敗の型 — 全体の実行(`failure_types_for`)と同じ作り方(歩く物は `hy_index::collect_hy_files` — 検の相手と歩き方を分ける)。
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
            ("app/errs.hy", "(defrecord Refusal \"断り\" {:failure True} #^ str reason)\n(defrecord Rejection #^ str reason)\n(defrecord Unreachable [reason])\n"),
            (
                "app/effs.hy",
                "(import app.errs)\n(import app.store :as store)\n(defeffect ReadRow \"行を読む\" {:fields [key] :answer None :failure [app.errs.Unreachable store.Gone] :absent [Missing]})\n",
            ),
            ("app/store.hy", "(defrecord Gone [x])\n"),
            // 同じ名の型が別の module に在っても、宣言した方だけが失敗の型。
            ("app/errs2.hy", "(defrecord Unreachable [r])\n"),
            // `:failure` を綴るが、型の名は長い記号の途中にしか綴らない file。
            ("app/long.hy", "(defeffect Z {:fields [] :answer None :failure [RefusalAll]})\n"),
            // 型の名を綴るが、宣言ではない file。
            ("app/use.hy", "(defk decide [x]\n  (match x\n    (Refusal) (return x)))\n; :failure はここでは宣言ではない\n"),
            ("app/plain.hy", "(defn f [] 1)\n"),
        ]
    }

    /// 型の名を字面に含む宣言の file だけから集めた答えは、repo の全部から集めた失敗の型を引いた答えと同じ(宣言した型も、しない型も、別の module の同じ名も)。
    #[test]
    fn the_answer_for_a_type_equals_the_answer_from_the_whole_repo() {
        let dir = repo(&sample());
        let reading = tag_reading(None, None);
        let enabled = enabled();
        let whole = failure_types_of_whole(dir.path(), &enabled, &reading);
        assert!(whole.contains("app.errs.Refusal") && whole.contains("app.errs.Unreachable") && whole.contains("app.store.Gone") && whole.contains("app.effs.Missing"), "検の材料が足りない");
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

    /// effect ごとの宣言は、全体の実行が集める物と同じ(宣言した effect も、宣言の無い effect も、別の module の同じ名も)。
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
        let read_row = lookup.effect_outcomes("app.effs.ReadRow").expect("ReadRow は宣言つき");
        assert!(read_row.absent.contains("app.effs.Missing") && read_row.failure.contains("app.errs.Unreachable") && read_row.failure.contains("app.store.Gone"), "{read_row:?}");
        // 規則が無効なら、effect の宣言も集めない(全体の実行と同じ)。
        assert_eq!(SpelledFailures::new(dir.path(), &BTreeSet::new(), &reading).effect_outcomes("app.effs.ReadRow"), None);
    }

    /// 規則が無効なら、宣言があっても失敗の型は無い(全体の実行と同じ)。
    #[test]
    fn nothing_is_a_failure_while_the_rule_is_off() {
        let dir = repo(&sample());
        let reading = tag_reading(None, None);
        let off = BTreeSet::new();
        assert!(!SpelledFailures::new(dir.path(), &off, &reading).is_failure("app.errs.Refusal"));
        assert!(failure_types_of_whole(dir.path(), &off, &reading).is_empty());
    }

    /// 別の型の名を引いても repo の Hy を読み直さない — 宣言を持つ file(`:failure` か `:absent` を含む file)は名に依らないので 1 回だけ集める
    /// (match の腕の型が 20 個の file で、名ごとに repo の Hy 約 2,900 file を読み直して CPU が 4 秒だった — 2026-10-17 の `cli_host.hy`)。
    #[test]
    fn different_names_share_one_read_of_the_repo() {
        let dir = repo(&sample());
        let reading = tag_reading(None, None);
        let enabled = enabled();
        let lookup = SpelledFailures::new(dir.path(), &enabled, &reading);
        assert!(lookup.is_failure("app.errs.Refusal"));
        // 宣言の file を消しても、別の名は 1 度目の読みで集めた宣言から答える。
        std::fs::remove_file(dir.path().join("app/errs.hy")).unwrap();
        std::fs::remove_file(dir.path().join("app/effs.hy")).unwrap();
        assert!(lookup.is_failure("app.errs.Unreachable"), "別の名を引くたびに repo を読み直している");
        assert!(lookup.is_failure("app.store.Gone"));
        assert!(!lookup.is_failure("app.errs.Rejection"), "宣言しない型は、別の名でも失敗の型ではない");
    }

    /// 宣言の file の読み解きは、その file を綴る名が変わっても 1 度だけ(file の中身と読み方だけで決まり、引く名に依らない)。
    /// 名ごとに読み解き直すと、全部の宣言の file を綴る名(`|` など)で、246 file の読み解きが名の数だけ繰り返された。
    #[test]
    fn a_declaration_file_is_read_through_once_for_every_name_that_spells_it() {
        let dir = repo(&sample());
        let reading = tag_reading(None, None);
        let enabled = enabled();
        let lookup = SpelledFailures::new(dir.path(), &enabled, &reading);
        assert!(lookup.is_failure("app.errs.Refusal"));
        let first = Arc::clone(&lookup.parsed.lock().unwrap()["app/errs.hy"]);
        // errs.hy は Unreachable も綴る: 別の名を引いても、集めた読み解きをそのまま使う。
        assert!(lookup.is_failure("app.errs.Unreachable"));
        let again = Arc::clone(&lookup.parsed.lock().unwrap()["app/errs.hy"]);
        assert!(Arc::ptr_eq(&first, &again), "別の名を引くたびに同じ宣言の file を読み解き直している");
    }

    /// 同じ名の問いは 2 度集めない(memo)— 1 度目で集めた集合から答える。
    #[test]
    fn the_same_name_is_collected_once() {
        let dir = repo(&sample());
        let reading = tag_reading(None, None);
        let enabled = enabled();
        let lookup = SpelledFailures::new(dir.path(), &enabled, &reading);
        assert!(lookup.is_failure("app.errs.Refusal"));
        // 宣言の file を消しても、集めた集合から答える。
        std::fs::remove_file(dir.path().join("app/errs.hy")).unwrap();
        assert!(lookup.is_failure("app.errs.Refusal"));
        assert!(!SpelledFailures::new(dir.path(), &enabled, &reading).is_failure("app.errs.Refusal"));
    }
}
