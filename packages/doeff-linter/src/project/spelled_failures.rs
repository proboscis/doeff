//! DOEFF122 の失敗の型の宣言 — 1 file の実行で、型の名を字面に含む宣言の file だけから集める(card ki-58bfad2c13a1 の残り)。
//!
//! 1 file の検査が引くのは、match の腕の型 1 つ(書いた file の記号を `Scope::qualify` で解いた名)が失敗の型かだけ。それが要る file は稀(標本 150 file の
//! 約 2%)だが、全体の実行と同じに repo の Hy の全部から宣言を集めると、冷えた作業木の最初の 1 回が 19〜27 秒かかる(hook の期限は 4 秒)。
//! 宣言(`defrecord` の `:failure True` と `defeffect` の `:failure` / `:absent` の列)を持つ file は、型の名(解いた名の最後の段)の綴りを字面に必ず
//! 含む(`spelling`)ので、`:failure` か `:absent` を綴り、型の名も綴る file だけを読み解く。答えは全体の実行(`failure_types_for`)が作る集合を引いた時と同じ。

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use super::layers::TagReading;
use super::rule::ProjectRule;
use super::smells::{FailureLookup, FailureTypes};
use super::spelling::Spelling;
use super::{facts_cache, failure_types_key, failure_types_of_source, has_failure_declaration_spelling, hy_files, FAILURE_TYPES_KIND};

/// 引かれた時に型の名を字面に含む宣言の file から集める失敗の型(1 file の実行)。
pub struct SpelledFailures<'a> {
    root: &'a Path,
    enabled: &'a BTreeSet<ProjectRule>,
    reading: &'a TagReading,
    /// 型の名(最後の段)ごとの、その名を綴る宣言の file から集めた失敗の型(同じ名は 2 度集めない)。
    memo: Mutex<HashMap<String, Arc<FailureTypes>>>,
    /// 宣言を持つ file(`:failure` か `:absent` を含む file)の(根からの path・中身)— 型の名に依らないので、最初の問いで 1 度だけ repo から集める。
    /// 名ごとに repo の Hy の全部を読み直すと、match の腕の型が多い file(4,866 行の `cli_host.hy`)で CPU が 4 秒かかった。
    declarations: OnceLock<Vec<(String, String)>>,
    /// 宣言の file ごとの失敗の型(根からの path → その file が宣言する型)。file の中身と読み方だけで決まり、引く型の名に依らないので、名が変わっても同じ file を読み解き直さない
    /// (match の腕の型が違う名で何度も引かれると、`|` のように全部の宣言の file を綴る名では 246 file の読み解きが名の数だけ繰り返された)。
    parsed: Mutex<HashMap<String, Arc<FailureTypes>>>,
}

impl<'a> SpelledFailures<'a> {
    pub fn new(root: &'a Path, enabled: &'a BTreeSet<ProjectRule>, reading: &'a TagReading) -> Self {
        SpelledFailures { root, enabled, reading, memo: Mutex::new(HashMap::new()), declarations: OnceLock::new(), parsed: Mutex::new(HashMap::new()) }
    }

    /// `name`(型の名の最後の段)を綴る宣言の file の失敗の型。
    fn declared_with(&self, name: &str) -> FailureTypes {
        let spelling = Spelling::of(name);
        let declarations = self.declarations.get_or_init(|| {
            // repo の tree を歩きながら file の中身を読む(待ちを重ねる — 冷えた作業木では 1 回ごとに disk を 1 回待つ)。宣言の file の中身だけ持つ。
            let sources: Vec<(String, PathBuf, Option<String>)> = hy_files::collect_reading(self.root, |path| {
                let source = std::fs::read_to_string(path).ok()?;
                has_failure_declaration_spelling(&source).then_some(source)
            });
            sources.into_iter().filter_map(|(rel, _, source)| source.map(|source| (rel, source))).collect()
        });
        let kept: Vec<(&str, &str)> =
            declarations.iter().filter(|(_, source)| spelling.in_source(source)).map(|(rel, source)| (rel.as_str(), source.as_str())).collect();
        let mut parsed = self.parsed.lock().expect("失敗の型の file ごとの memo");
        let fresh: Vec<(&str, &str)> = kept.iter().filter(|(rel, _)| !parsed.contains_key(*rel)).copied().collect();
        if !fresh.is_empty() {
            // 読み解きは事実の置き場で覚える(全体の実行と同じ置き場・同じ鍵 — 変わっていない file は、次の実行でも読み解かない)。空の事実も覚えて、宣言でない file を読み解き直さない。
            let reading = self.reading;
            let done = facts_cache::per_file_content(self.root, FAILURE_TYPES_KIND, &failure_types_key(reading), &fresh, |rel, source| {
                Some(failure_types_of_source(rel, source, reading))
            });
            for ((rel, _), found) in fresh.iter().zip(done) {
                parsed.insert((*rel).to_string(), Arc::new(found.unwrap_or_default()));
            }
        }
        let mut all = FailureTypes::default();
        for (rel, _) in &kept {
            all.extend(parsed[*rel].as_ref().clone());
        }
        all
    }
}

impl FailureLookup for SpelledFailures<'_> {
    fn is_failure(&self, qualified: &str) -> bool {
        // 規則が無効なら宣言を集めない(全体の実行の `failure_types_for` と同じ)。
        if !self.enabled.contains(&ProjectRule::FailureRethrow) {
            return false;
        }
        let name = qualified.rsplit('.').next().unwrap_or(qualified);
        let known = self.memo.lock().expect("失敗の型の memo").get(name).map(Arc::clone);
        let types = known.unwrap_or_else(|| {
            let collected = Arc::new(crate::timing::timed("failure-types", || self.declared_with(name)));
            self.memo.lock().expect("失敗の型の memo").insert(name.to_string(), Arc::clone(&collected));
            collected
        });
        types.contains(qualified)
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
