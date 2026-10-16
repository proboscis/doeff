//! DOEFF126 の 2 つ目の形の材料 — 1 file の実行で、呼び先の名を字面に含む file だけから「呼び手が引数に渡した Program」を集める(card ki-58bfad2c13a1 の残り)。
//!
//! 1 file の検査が引くのは「書いた file の関数が呼び先の呼び」の事実だけ(`param_calls_in` が自分の引数を素で呼ぶ定義の名で引く)。全体の実行は
//! repo の Hy の全部の呼びから事実を作る(`program_params_for`)が、その作り方を 1 file の実行で踏むと、冷えた作業木の最初の 1 回で repo の Hy の
//! 約 2900 file を全部読んで(defk の名)全部解析し直す(8〜13 秒 — hook の期限は 4 秒)。引く事実は呼び先が決まっているので、全部作る必要は無い。
//!
//! 呼び手の file は、呼び先の定義の名(mangle 済み)の綴りを字面に必ず含む(`spelling` — 綴れる形と、綴りを決められない名は絞らない事)。含む file だけを解析し、
//! 他は読んだだけで捨てる。
//!
//! 呼び手の事実を引くのは実行の中で他の表を作った後になるので、書いた file が引きうる呼び先は実行の頭で見積もり、repo の読みを先に別の thread で走らせ始める
//! (`SpelledCallers::started` — 冷えた作業木ではどの段も disk の待ちなので重ねる)。見積りは実際に引く呼び先を全部含み、収まらなければ引く時に読み直す。
//!
//! 渡した記号が defk かは、repo 全体の defk の名(全部の file を読む)でなく、引かれた module の file だけで決める(`defk_names_over` の名指し —
//! 記号は module まで解かれ、defk の名は `module.名` で、その module の file だけで答えが同じ)。答え(事実とその並び)は `program_params_for` が作る物と
//! 同じ: 解析する file は `hy_rel_files` の順、事実は file の中の呼びの順。

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, PoisonError};
use std::thread::JoinHandle;

use rayon::prelude::*;

use super::bare_calls::{defk_names_in, defk_names_over, DefkNames};
use super::facts_cache::Reach;
use super::names::module_of;
use super::param_calls::{asked_callees, candidates_in, Candidate, PassedProgram, ProgramParamsLookup};
use super::rule::ProjectRule;
use super::smells::Scope;
use super::spelling::Spelling;
use super::{facts, hy_files, named_modules};

/// 引かれた時に呼び先の名を字面に含む file から集める「呼び手が引数に渡した Program」(1 file の実行)。
pub struct SpelledCallers<'a> {
    root: &'a Path,
    enabled: &'a BTreeSet<ProjectRule>,
    /// 字面が合って解析した file の数(検のため)。
    analyzed: AtomicUsize,
    /// 実行の頭で先に走らせ始めた読み(`started`)。引く呼び先がその読みの呼び先に収まれば、これを待って使う。
    started: Mutex<Option<JoinHandle<Started>>>,
}

/// 先に走らせ始めた読みの結果 — どの呼び先の名で字面を探したかと、repo の Hy の全 file の読み。
struct Started {
    callees: Vec<String>,
    scans: Vec<(String, PathBuf, Scan)>,
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

/// repo の tree を歩きながら file の中身を読む(dir を開く待ちと file を読む待ちを重ねる — 冷えた作業木では 1 回ごとに disk を 1 回待つ)。
/// 字面が合った file の中身だけ持つ。
fn read_scans(root: &Path, callees: &[String]) -> Vec<(String, PathBuf, Scan)> {
    let needles: Vec<Needle> = callees.iter().map(|callee| Needle::of(callee)).collect();
    crate::timing::timed("program-params.scan", || {
        hy_files::collect_reading(root, |path| match std::fs::read_to_string(path) {
            Ok(source) => {
                let mentions_defk = source.contains("(defk");
                let spelled = needles.iter().any(|needle| needle.spelling.in_source(&source));
                Scan { source: spelled.then_some(source), mentions_defk }
            }
            Err(_) => Scan::default(),
        })
    })
}

impl<'a> SpelledCallers<'a> {
    pub fn new(root: &'a Path, enabled: &'a BTreeSet<ProjectRule>) -> Self {
        SpelledCallers { root, enabled, analyzed: AtomicUsize::new(0), started: Mutex::new(None) }
    }

    /// 書いた file(`rel`・`source`)が自分の引数を素で呼ぶ定義を持つなら、その呼び先の名で repo の読みを今すぐ別の thread で走らせ始める。
    /// 呼び手の事実を引くのは、実行の中で defk の名・登録簿・effect の世界を作った後になる。冷えた作業木ではそれぞれが disk の待ちなので、頭から重ねる。
    /// 呼び先は defk の集まりが空の見積り(`asked_callees` — 実際に引く呼び先を全部含む)で決める。引く呼び先が収まらなければ、引く時に読み直す。
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

    /// 字面が合って解析した file の数。
    pub fn analyzed(&self) -> usize {
        self.analyzed.load(Ordering::SeqCst)
    }

    /// `callees` の字面を探した repo の Hy の全 file の読み — 先に走らせ始めた読みがあり、呼び先が収まるならそれ(待つ)、でなければ今読む。
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

    /// `symbols`(module まで解いた記号)のうち defk の物 — 記号の module の file だけで決める。
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
            // repo に defk が 1 つも無ければ、全体の実行は事実を作らない(`(fnk …)` の事実も)— 同じ答えにする。
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

    /// repo の Hy の全部から作る事実 — 全体の実行(`program_params_for`)と同じ作り方: 全部の file の defk の名を集め、全部の file の呼びから積む(歩く物は
    /// `hy_index::collect_hy_files` — 検の相手と歩き方を分ける)。
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

    /// 実行の頭で先に走らせ始めた読みを使っても、事実もその並びも変わらない。引く呼び先が見積りに収まらなければ、引く時に読み直して同じ答えを返す。
    #[test]
    fn a_read_started_early_gives_the_same_facts_and_falls_back_when_the_callees_do_not_fit() {
        let dir = repo(&sample());
        let whole = whole(dir.path());
        let enabled = enabled();
        // hof.hy の素の呼びを持つ定義: rows-by-text(`setv`)・_private-fn・check?。safe-one は `<-` の右辺なので素の呼びではない。
        let callers = SpelledCallers::started(dir.path(), &enabled, "app/hof.hy", HOF);
        assert!(callers.started.lock().unwrap().is_some(), "先に読み始めていない");
        let fits = ["app.hof.rows_by_text".to_string(), "app.hof._private_fn".to_string()];
        let facts = callers.passed_to_all(&fits);
        for (callee, found) in fits.iter().zip(&facts) {
            assert_eq!(found.as_slice(), whole.of(callee), "{callee}");
        }
        assert!(callers.started.lock().unwrap().is_none(), "待った読みが残っている");
        // 見積りに無い呼び先(safe-one)は、先の読みを使えないので読み直す — 答えは同じ。
        let again = SpelledCallers::started(dir.path(), &enabled, "app/hof.hy", HOF);
        let unfit = ["app.hof.safe_one".to_string()];
        assert_eq!(again.passed_to_all(&unfit)[0].as_slice(), whole.of(&unfit[0]));
        assert_eq!(whole.of(&unfit[0]).len(), 1, "検の材料が足りない");
    }

    /// 書いた file に素の呼びを持つ定義が無ければ、先に読み始めない(読みを無駄にしない)。
    #[test]
    fn nothing_is_read_early_when_the_written_file_calls_no_parameter_bare() {
        let dir = repo(&sample());
        let enabled = enabled();
        let callers = SpelledCallers::started(dir.path(), &enabled, "app/other.hy", "(defk unrelated [x] x)\n");
        assert!(callers.started.lock().unwrap().is_none());
        let off = BTreeSet::new();
        assert!(SpelledCallers::started(dir.path(), &off, "app/hof.hy", HOF).started.lock().unwrap().is_none());
    }

    /// 見積り(defk の集まりが空)は、実際の集まりで引く呼び先を全部含む。
    #[test]
    fn the_estimate_with_no_defks_contains_every_callee_asked_with_the_real_defks() {
        let source = "(defk apply-all [rows f] (for [r rows] (setv v (f r))) rows)\n(defk through [rows g] (! (apply-all rows (fnk [r] (g r)))))\n(defk direct [rows h] (setv w (h rows)) w)\n(defk calm [rows k] (<- v (k rows)) v)\n(defk wrapped [rows m] (! (step-of (m rows))))\n";
        let bindings = std::collections::BTreeMap::new();
        let scope = Scope { module: "app.m", bindings: &bindings };
        let guess = asked_callees(source, scope, &DefkNames::default());
        let real = asked_callees(source, scope, &DefkNames::of(&["app.m.step_of", "app.m.apply_all"]));
        assert!(real.iter().all(|callee| guess.contains(callee)), "{real:?} ⊄ {guess:?}");
        assert!(guess.contains(&"app.m.wrapped".to_string()) && !real.contains(&"app.m.wrapped".to_string()), "検の材料が足りない: {guess:?} / {real:?}");
    }
}
