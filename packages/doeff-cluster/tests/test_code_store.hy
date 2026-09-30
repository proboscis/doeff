;; コードの準備の失敗を完成品として公開しないこと・壊れた木を作り直すこと。
;; 焼きの Program は fake の handler で、CodeStore は手元の小さな git repo と偽の焼きの道具で確かめる。
(require doeff-hy.macros [defhandler deftest <- val var])
(import json)
(import os)
(import subprocess)
(import sys)
(import time)
(import pathlib [Path])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.code_prepare [ScanTree LinkPycs CompileSources WriteMarker Note MARKER
                        prepare-tree tree-problem marker-problem marker-content cache-rel])
(import doeff_cluster.handlers [CodeStore])
(import doeff_cluster.worker_model [CodeState])


;; --- 焼きの Program(fake の handler)---------------------------------------------------

(defhandler fake-tree [#^ dict state]
  ;; 1 回目の走査 = 焼く前、2 回目 = 焼いた後(state "after" の .pyc を返す)。
  (ScanTree [tree]
    (setv (get state "scans") (+ (get state "scans") 1))
    (resume #((get state "sources") (if (= (get state "scans") 1) [] (get state "after")))))
  (LinkPycs [old new pycs] (resume 0))
  (CompileSources [tree items jobs roots] (resume (get state "failures")))
  (WriteMarker [tree content] (.append (get state "markers") content) (resume None))
  (Note [line] (resume None)))


(defn tree-state [sources after [failures []]]
  {"scans" 0 "sources" sources "after" after "failures" failures "markers" []})


(deftest test-marker-is-written-only-after-the-tree-checks
  (setv ok (tree-state ["a/m.py" "a/n.hy"] [(cache-rel "a/m.py") (cache-rel "a/n.hy")]))
  (<- summary dict ((sim-time-handler :clock (SimClock)) ((fake-tree ok) (prepare-tree "/t" "rev1" None (frozenset) 1 #(".")))))
  (assert (is (get summary "problem") None))
  (assert (= (len (get ok "markers")) 1))
  (assert (= (get ok "markers" 0 "revision") "rev1"))
  (assert (= (get ok "markers" 0 "pycs") 2))
  ;; 焼きは「成功」と答えたのに .pyc が置かれていない → 印を置かない。
  (setv silent (tree-state ["a/m.py" "a/n.hy"] [(cache-rel "a/m.py")]))
  (<- silent-summary dict ((sim-time-handler :clock (SimClock)) ((fake-tree silent) (prepare-tree "/t" "rev1" None (frozenset) 1 #(".")))))
  (assert (in "a/n.hy" (get silent-summary "problem")))
  (assert (= (get silent "markers") []))
  ;; 全部焼けなかった(道具か環境の失敗)→ 印を置かない。
  (setv broken (tree-state ["a/m.py"] [] [#("a/m.py" "ImportError: hy")]))
  (<- broken-summary dict ((sim-time-handler :clock (SimClock)) ((fake-tree broken) (prepare-tree "/t" "rev1" None (frozenset) 1 #(".")))))
  (assert (in "全部焼けなかった" (get broken-summary "problem")))
  (assert (= (get broken "markers") []))
  ;; 一部の file だけ焼けない(その file の source の誤り)は完成とし、印に理由を残す。
  (setv partial (tree-state ["a/m.py" "a/n.hy"] [(cache-rel "a/m.py")] [#("a/n.hy" "SyntaxError: x")]))
  (<- partial-summary dict ((sim-time-handler :clock (SimClock)) ((fake-tree partial) (prepare-tree "/t" "rev1" None (frozenset) 1 #(".")))))
  (assert (is (get partial-summary "problem") None))
  (assert (= (get partial "markers" 0 "failed") [{"path" "a/n.hy" "reason" "SyntaxError: x"}])))


(deftest test-tree-and-marker-checks-are-pure
  (assert (in "source が 1 つも無い" (or (tree-problem [] (frozenset) (frozenset)) "")))
  (setv marker (json.dumps (marker-content "rev1" True ["a/m.py"] (frozenset [(cache-rel "a/m.py")]) [])))
  (assert (is (marker-problem marker "rev1" True 1) None))
  (assert (in "印が無い" (or (marker-problem None "rev1" True 1) "")))
  (assert (in "木の名前と違う" (or (marker-problem marker "rev2" True 1) "")))
  (assert (in "1 file のはずが 0 file" (or (marker-problem marker "rev1" True 0) "")))
  (assert (in "読めない" (or (marker-problem "{" "rev1" True 1) "")))
  ;; 焼かない worker(--no-warm)は bytecode の無い印でよい。焼く worker はそれを完成品と見ない。
  (setv plain (json.dumps {"format" 1 "revision" "rev1" "bytecode" False}))
  (assert (is (marker-problem plain "rev1" False 0) None))
  (assert (in "焼かずに" (or (marker-problem plain "rev1" True 0) ""))))


;; --- CodeStore(手元の git repo と偽の焼きの道具)---------------------------------------

(setv HY (str (/ (. (Path sys.executable) parent) "hy")))


(defn git [repo #* args]
  (.strip (. (subprocess.run ["git" "-C" (str repo) #* args] :check True :capture-output True :text True) stdout)))


(defn make-repo [root]
  "焼く道具(code_prepare.hy)を持たない小さな repo — 道具が木の中に無い古い版と同じ形。"
  (setv repo (/ root "repo"))
  (.mkdir (/ repo "pkg") :parents True)
  (.write-text (/ repo "pkg" "__init__.py") "")
  (.write-text (/ repo "pkg" "m.py") "X = 1\n")
  (git repo "init" "-q")
  (git repo "add" ".")
  (git repo "-c" "user.name=t" "-c" "user.email=t@t" "commit" "-q" "-m" "c1")
  #(repo (git repo "rev-parse" "HEAD")))


(defn wait-settled [store revision [limit 60]]
  (setv deadline (+ (time.monotonic) limit))
  (while (< (time.monotonic) deadline)
    (setv views (lfor v (.observe store) :if (= v.revision revision) v))
    (when (and views (!= (. (get views 0) state) CodeState.PREPARING)) (return (get views 0)))
    (time.sleep 0.1))
  (raise (TimeoutError revision)))


(defn failing-tool [root]
  "焼きの道具の代わり: 理由を言って 0 でない終了をする(以前の形では、これでも完成品になった)。"
  (setv tool (/ root "fail.sh"))
  (.write-text tool "#!/bin/sh\necho '焼きの道具が見つからない' >&2\nexit 3\n")
  (os.chmod tool 0o755)
  (str tool))


(deftest test-failed-prepare-is-not-published-and-is-rebuilt [tmp-path]
  (setv #(repo rev) (make-repo tmp-path) cache (/ tmp-path "cache"))
  (setv store (CodeStore (str repo) (str cache) (failing-tool tmp-path)))
  (.start store rev)
  (var view (wait-settled store rev))
  (assert (= view.state CodeState.FAILED))
  (assert (in "焼きの道具が見つからない" view.detail))
  (assert (is-not view.failed-ms None))
  ;; 完成品の dir も途中の dir も残らない。
  (assert (not (.exists (/ cache rev))))
  (assert (= (lfor e (.iterdir cache) :if (not (.startswith e.name ".")) e) []))
  ;; 次の準備(policy が間を置いて PrepareCode を出した後)は本物の道具で作り直せる。
  (setv store.hy-command HY)
  (.start store rev)
  (:= view (wait-settled store rev))
  (assert (= view.state CodeState.READY) view.detail)
  (assert (.exists (/ cache rev (cache-rel "pkg/m.py"))))
  (setv marker (json.loads (.read-text (/ cache rev MARKER))))
  (assert (= (get marker "revision") rev))
  (assert (get marker "bytecode")))


(deftest test-published-tree-without-a-valid-marker-is-rebuilt [tmp-path]
  (setv #(repo rev) (make-repo tmp-path) cache (/ tmp-path "cache"))
  ;; 以前の形で「完成品」になった木: rename はされたが印も bytecode も無い(atlas の 8d7181f と同じ)。
  (setv old (/ cache rev))
  (.mkdir (/ old "pkg") :parents True)
  (.write-text (/ old "pkg" "m.py") "X = 1\n")
  (setv store (CodeStore (str repo) (str cache) HY))
  ;; 完成品として公開しない・引き継ぎ元にもしない。
  (assert (= (lfor v (.observe store) :if (= v.state CodeState.READY) v) []))
  (assert (is (.latest-ready store) None))
  ;; その版を求められたら脇へ退けて作り直す。
  (.start store rev)
  (setv view (wait-settled store rev))
  (assert (= view.state CodeState.READY) view.detail)
  (assert (.exists (/ cache rev MARKER)))
  (assert (.exists (/ cache rev (cache-rel "pkg/m.py"))))
  (assert (= (lfor e (.iterdir cache) :if (in ".broken." e.name) e) [])))


(deftest test-next-revision-carries-from-the-ready-tree [tmp-path]
  ;; 前の完成品から引き継ぐ道(git diff → --from / --changed)も、同じ検めを通って完成品になる。
  (setv #(repo rev1) (make-repo tmp-path) cache (/ tmp-path "cache"))
  (setv store (CodeStore (str repo) (str cache) HY))
  (.start store rev1)
  (assert (= (. (wait-settled store rev1) state) CodeState.READY))
  (.write-text (/ repo "pkg" "n.py") "Y = 2\n")
  (git repo "add" ".")
  (git repo "-c" "user.name=t" "-c" "user.email=t@t" "commit" "-q" "-m" "c2")
  (setv rev2 (git repo "rev-parse" "HEAD"))
  (.start store rev2)
  (setv view (wait-settled store rev2))
  (assert (= view.state CodeState.READY) view.detail)
  (setv marker (json.loads (.read-text (/ cache rev2 MARKER))))
  (assert (= (get marker "pycs") 3))
  ;; 引き継いだ .pyc は前の木と同じ inode(hardlink)。
  (assert (= (. (.stat (/ cache rev2 (cache-rel "pkg/m.py"))) st-ino)
             (. (.stat (/ cache rev1 (cache-rel "pkg/m.py"))) st-ino))))


(deftest test-a-previous-tree-this-repo-cannot-resolve-is-not-carried-from [tmp-path]
  ;; 前の完成品の版をこの repo で解けない時(以前の「<base>~<revision>」の重ねる木の名・履歴から消えた commit — 2026-09-28 に
  ;; 重ねる木を消した)は、引き継がずに全部を焼いて完成品にする。引き継ぎは速さのためだけで、引き継げないことを準備の失敗にしない。
  (val made (make-repo tmp-path))
  (val cache (/ tmp-path "cache"))
  (val first (CodeStore (str (get made 0)) (str cache) HY))
  (.start first (get made 1))
  (assert (= (. (wait-settled first (get made 1)) state) CodeState.READY))
  ;; 前の木の版(made の commit)を持たない別の履歴の repo で、同じ cache に次の版を準備する。
  (val other (/ tmp-path "other"))
  (.mkdir (/ other "pkg") :parents True)
  (.write-text (/ other "pkg" "__init__.py") "")
  (.write-text (/ other "pkg" "n.py") "Y = 2\n")
  (git other "init" "-q")
  (git other "add" ".")
  (git other "-c" "user.name=t" "-c" "user.email=t@t" "commit" "-q" "-m" "other")
  (val rev (git other "rev-parse" "HEAD"))
  (val store (CodeStore (str other) (str cache) HY))
  (val latest (.latest-ready store))
  (assert (is-not latest None) "前の完成品が在るはず")
  (assert (= latest.name (get made 1)) "引き継ぎ元の候補は前の完成品")
  (.start store rev)
  (val view (wait-settled store rev))
  (assert (= view.state CodeState.READY) view.detail)
  (assert (= (get (json.loads (.read-text (/ cache rev MARKER))) "pycs") 2))
  (assert (.exists (/ cache rev (cache-rel "pkg/n.py")))))
