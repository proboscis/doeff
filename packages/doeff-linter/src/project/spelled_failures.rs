//! DOEFF122 の失敗の型の宣言 — 1 file の実行で、型の名を字面に含む宣言の file だけから集める(card ki-58bfad2c13a1 の残り)。
//!
//! 1 file の検査が引くのは、match の腕の型 1 つ(書いた file の記号を `Scope::qualify` で解いた名)が失敗の型かだけ。それが要る file は稀(標本 150 file の
//! 約 2%)だが、全体の実行と同じに repo の Hy の全部から宣言を集めると、冷えた作業木の最初の 1 回が 19〜27 秒かかる(hook の期限は 4 秒)。
//! 宣言(`defrecord` の `:failure True` と `defeffect` の `:failure` / `:absent` の列)を持つ file は、型の名(解いた名の最後の段)の綴りを字面に必ず
//! 含む(`spelling`)ので、`:failure` か `:absent` を綴り、型の名も綴る file だけを読み解く。答えは全体の実行(`failure_types_for`)が作る集合を引いた時と同じ。

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use rayon::prelude::*;

use super::facts::{read_facts, Language};
use super::layers::TagReading;
use super::names::module_of;
use super::rule::ProjectRule;
use super::smells::{failure_types_in, FailureLookup, FailureTypes, Scope};
use super::spelling::Spelling;
use super::hy_files;

/// 引かれた時に型の名を字面に含む宣言の file から集める失敗の型(1 file の実行)。
pub struct SpelledFailures<'a> {
    root: &'a Path,
    enabled: &'a BTreeSet<ProjectRule>,
    reading: &'a TagReading,
    /// 型の名(最後の段)ごとの、その名を綴る宣言の file から集めた失敗の型(同じ名は 2 度集めない)。
    memo: Mutex<HashMap<String, Arc<FailureTypes>>>,
}

impl<'a> SpelledFailures<'a> {
    pub fn new(root: &'a Path, enabled: &'a BTreeSet<ProjectRule>, reading: &'a TagReading) -> Self {
        SpelledFailures { root, enabled, reading, memo: Mutex::new(HashMap::new()) }
    }

    /// `name`(型の名の最後の段)を綴る宣言の file の失敗の型。
    fn declared_with(&self, name: &str) -> FailureTypes {
        let spelling = Spelling::of(name);
        // repo の tree を歩きながら file の中身を読む(待ちを重ねる — 冷えた作業木では 1 回ごとに disk を 1 回待つ)。宣言の file の中身だけ持つ。
        let sources: Vec<(String, PathBuf, Option<String>)> = hy_files::collect_reading(self.root, |path| {
            let source = std::fs::read_to_string(path).ok()?;
            ((source.contains(":failure") || source.contains(":absent")) && spelling.in_source(&source)).then_some(source)
        });
        let kept: Vec<(&str, &str)> = sources.iter().filter_map(|(rel, _, source)| source.as_deref().map(|source| (rel.as_str(), source))).collect();
        let found: Vec<FailureTypes> = kept
            .par_iter()
            .map(|(rel, source)| {
                let module = module_of(rel);
                let facts = read_facts(Language::Hy, source, &module, self.reading);
                failure_types_in(source, Scope { module: &module, bindings: &facts.bindings })
            })
            .collect();
        let mut all = FailureTypes::default();
        for one in found {
            all.extend(one);
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

    use super::super::layers::tag_reading;
    use super::super::paths::relative_path;
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
