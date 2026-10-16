//! DOEFF126 の 2 つ目の形の材料 — 1 file の実行で、呼び先の名を字面に含む file だけから「呼び手が引数に渡した Program」を集める(card ki-58bfad2c13a1 の残り)。
//!
//! 1 file の検査が引くのは「書いた file の関数が呼び先の呼び」の事実だけ(`param_calls_in` が自分の引数を素で呼ぶ定義の名で引く)。全体の実行は
//! repo の Hy の全部の呼びから事実を作る(`program_params_for`)が、その作り方を 1 file の実行で踏むと、冷えた作業木の最初の 1 回で repo の Hy の
//! 約 2900 file を全部読んで(defk の名)全部解析し直す(8〜13 秒 — hook の期限は 4 秒)。引く事実は呼び先が決まっているので、全部作る必要は無い。
//!
//! 呼び手の file は、呼び先の定義の名(mangle 済み)の綴りを字面に必ず含む(`spelling` — 綴れる形と、綴りを決められない名は絞らない事)。含む file だけを解析し、
//! 他は読んだだけで捨てる。
//!
//! 渡した記号が defk かは、repo 全体の defk の名(全部の file を読む)でなく、引かれた module の file だけで決める(`defk_names_over` の名指し —
//! 記号は module まで解かれ、defk の名は `module.名` で、その module の file だけで答えが同じ)。答え(事実とその並び)は `program_params_for` が作る物と
//! 同じ: 解析する file は `hy_rel_files` の順、事実は file の中の呼びの順。

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};

use rayon::prelude::*;

use super::bare_calls::defk_names_in;
use super::facts_cache::Reach;
use super::names::module_of;
use super::param_calls::{candidates_in, Candidate, PassedProgram, ProgramParamsLookup};
use super::rule::ProjectRule;
use super::smells::Scope;
use super::spelling::Spelling;
use super::{bulk_read, facts, hy_files, named_modules};

/// 引かれた時に呼び先の名を字面に含む file から集める「呼び手が引数に渡した Program」(1 file の実行)。
pub struct SpelledCallers<'a> {
    root: &'a std::path::Path,
    enabled: &'a BTreeSet<ProjectRule>,
    /// 字面が合って解析した file の数(検のため)。
    analyzed: AtomicUsize,
}

/// 呼び先 1 つの字面の探り方。
struct Needle {
    callee: String,
    /// 呼び先の名(最後の段)の綴り。
    spelling: Spelling,
}

impl Needle {
    fn of(callee: &str) -> Needle {
        let name = callee.rsplit('.').next().unwrap_or(callee);
        Needle { callee: callee.to_string(), spelling: Spelling::of(name) }
    }
}

/// file 1 つを読んだ結果。
#[derive(Default)]
struct Scan {
    /// 字面が合った file の中身。
    source: Option<String>,
    /// `(defk` の綴りを含むか。
    mentions_defk: bool,
}

impl<'a> SpelledCallers<'a> {
    pub fn new(root: &'a std::path::Path, enabled: &'a BTreeSet<ProjectRule>) -> Self {
        SpelledCallers { root, enabled, analyzed: AtomicUsize::new(0) }
    }

    /// 字面が合って解析した file の数。
    pub fn analyzed(&self) -> usize {
        self.analyzed.load(Ordering::SeqCst)
    }

    /// `symbols`(module まで解いた記号)のうち defk の物 — 記号の module の file だけで決める。
    fn defks_among(&self, symbols: &BTreeSet<&str>) -> super::bare_calls::DefkNames {
        let known = hy_files::by_module(self.root);
        let modules: BTreeSet<String> = symbols.iter().filter_map(|symbol| symbol.rsplit_once('.')).map(|(module, _)| module.to_string()).collect();
        let files = named_modules::files_of(&known, &modules, None);
        super::defk_names_over(self.root, self.enabled, Reach::Named, &files)
    }
}

impl ProgramParamsLookup for SpelledCallers<'_> {
    fn passed_to_all(&self, callees: &[String]) -> Vec<Vec<PassedProgram>> {
        let mut settled: Vec<Vec<PassedProgram>> = vec![Vec::new(); callees.len()];
        if callees.is_empty() || !self.enabled.contains(&ProjectRule::DefkCalledBare) {
            return settled;
        }
        crate::timing::timed("program-params", || {
            let needles: Vec<Needle> = callees.iter().map(|callee| Needle::of(callee)).collect();
            let files = crate::timing::timed("program-params.walk", || super::hy_rel_files(self.root));
            // file の中身を読む待ちは重ねる(冷えた作業木では 1 file ごとに disk を 1 回待つ)。字面が合った file の中身だけ持つ。
            let scans: Vec<Scan> = crate::timing::timed("program-params.scan", || {
                bulk_read::fan_out_wide(&files, |(_, path)| match std::fs::read_to_string(path) {
                    Ok(source) => {
                        let mentions_defk = source.contains("(defk");
                        let spelled = needles.iter().any(|needle| needle.spelling.in_source(&source));
                        Scan { source: spelled.then_some(source), mentions_defk }
                    }
                    Err(_) => Scan::default(),
                })
            });
            let matched: Vec<(&str, &str)> =
                files.iter().zip(&scans).filter_map(|((rel, _), scan)| scan.source.as_deref().map(|source| (rel.as_str(), source))).collect();
            self.analyzed.fetch_add(matched.len(), Ordering::SeqCst);
            let wants = |callee: &str| needles.iter().any(|needle| needle.callee == callee);
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
            // repo に defk が 1 つも無ければ、全体の実行は事実を作らない(`(fnk …)` の事実も)— 同じ答えにする。
            if settled.iter().any(|facts| !facts.is_empty()) && !proved_defk {
                let with_defk: Vec<&(String, PathBuf)> = files.iter().zip(&scans).filter(|(_, scan)| scan.mentions_defk).map(|(file, _)| file).collect();
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
    use super::super::param_calls::ProgramParams;
    use super::super::{defk_names_for, program_params_for};
    use super::*;

    fn enabled() -> BTreeSet<ProjectRule> {
        BTreeSet::from([ProjectRule::DefkCalledBare])
    }

    /// 木 `files`(根からの path と中身)を置いた repo。
    fn repo(files: &[(&str, &str)]) -> tempfile::TempDir {
        let dir = tempfile::TempDir::new().unwrap();
        for (rel, text) in files {
            let path = dir.path().join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        dir
    }

    /// repo の Hy の全部から作る事実 — 全体の実行が使う物。
    fn whole(root: &std::path::Path) -> ProgramParams {
        let enabled = enabled();
        let defks = defk_names_for(root, &enabled);
        program_params_for(root, &enabled, &defks)
    }

    /// 呼び先の名を字面に含む file だけから作る事実 — 1 file の実行が使う物(呼び先の並びと同じ順)。
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
            ("app/docs.hy", "; rows-by-text を呼ぶ例は user_a にある\n(defn note [] \"rows_by_text\")\n"),
            // 名の綴りを長い記号の途中に含むだけの file(module の path の段・別の名の前半)は、呼びの頭ではない。
            ("app/longer.hy", "(import controllers.rows-by-text.system)\n(defk rows-by-text-all [x] x)\n(defk all-rows-by-text [x] x)\n"),
        ]
    }

    /// 呼び先を字面で絞って作った事実は、repo の全部から作った事実と、事実もその並びも同じ(呼び先ごとに)。
    #[test]
    fn the_facts_for_a_callee_equal_the_facts_made_from_the_whole_repo() {
        let dir = repo(&sample());
        let whole = whole(dir.path());
        let mut callees = whole.callees();
        // 事実の無い呼び先と、定義の無い呼び先も、空と答える。
        callees.extend(["app.hof.twice", "app.nowhere.missing", "app.hof.rows_by_text_not"].map(String::from));
        assert!(callees.len() >= 6, "検の材料が足りない: {callees:?}");
        let (facts, _) = named(dir.path(), &callees);
        for (callee, found) in callees.iter().zip(&facts) {
            assert_eq!(found.as_slice(), whole.of(callee), "{callee}");
        }
        // 空の比べで通らない: 記号(位置と keyword)・fnk・module 修飾・別名・同じ module の呼びが、どれも事実になっている。
        assert_eq!(whole.of("app.hof.rows_by_text").len(), 5);
        assert_eq!(whole.of("app.hof.safe_one").len(), 1);
        assert_eq!(whole.of("app.hof._private_fn").len(), 1);
        assert_eq!(whole.of("app.hof.hyx_checkXquestion_markX").len(), 1);
    }

    /// 1 度に複数の呼び先を引いても、1 つずつ引いた時と同じ(file を読むのは 1 度)。
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

    /// 呼び先の名を字面に含む file だけを解析する(`-` でも `_` でも綴った file・コメントや文字列にだけ綴った file も含む。含まない file は解析しない)。
    #[test]
    fn only_the_files_that_spell_the_callee_name_are_analyzed() {
        let dir = repo(&sample());
        // hof(定義と同じ module の呼び)・user_a(`-`)・user_b(`_`)・user_c(module 修飾)・docs(コメントと文字列)。payload と other は綴らない。
        let (_, analyzed) = named(dir.path(), &["app.hof.rows_by_text".to_string()]);
        assert_eq!(analyzed, 5);
        let (_, analyzed) = named(dir.path(), &["app.hof.safe_one".to_string()]);
        assert_eq!(analyzed, 2, "safe-one を綴るのは hof(定義)と user_b だけ");
    }

    /// 名に識別子でない文字が入る(mangle が `hyx_` へ逃がした)名は、綴りを決められないので字面で絞らない — それでも答えは同じ。
    #[test]
    fn a_name_that_mangle_escapes_is_not_narrowed_but_still_gives_the_same_facts() {
        let dir = repo(&sample());
        let callee = "app.hof.hyx_checkXquestion_markX".to_string();
        let (facts, analyzed) = named(dir.path(), std::slice::from_ref(&callee));
        assert_eq!(analyzed, sample().len());
        assert_eq!(facts[0].as_slice(), whole(dir.path()).of(&callee));
    }

    /// repo に defk が 1 つも無ければ、全体の実行は `(fnk …)` を渡す事実も作らない — 1 file の実行も同じ。defk が在れば作る。
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
}
