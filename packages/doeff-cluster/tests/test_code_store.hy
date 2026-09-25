;; コードの準備の失敗を完成品として公開しないこと・壊れた木を作り直すこと。
;; 焼きの Program は fake の handler で、CodeStore は手元の小さな git repo と偽の焼きの道具で確かめる。
(require doeff-hy.macros [defhandler deftest <-])
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
(import doeff_cluster.worker_model [CodeState CodeLayout])


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
  (<- summary dict ((sim-time-handler :clock (SimClock)) ((fake-tree silent) (prepare-tree "/t" "rev1" None (frozenset) 1 #(".")))))
  (assert (in "a/n.hy" (get summary "problem")))
  (assert (= (get silent "markers") []))
  ;; 全部焼けなかった(道具か環境の失敗)→ 印を置かない。
  (setv broken (tree-state ["a/m.py"] [] [#("a/m.py" "ImportError: hy")]))
  (<- summary dict ((sim-time-handler :clock (SimClock)) ((fake-tree broken) (prepare-tree "/t" "rev1" None (frozenset) 1 #(".")))))
  (assert (in "全部焼けなかった" (get summary "problem")))
  (assert (= (get broken "markers") []))
  ;; 一部の file だけ焼けない(その file の source の誤り)は完成とし、印に理由を残す。
  (setv partial (tree-state ["a/m.py" "a/n.hy"] [(cache-rel "a/m.py")] [#("a/n.hy" "SyntaxError: x")]))
  (<- summary dict ((sim-time-handler :clock (SimClock)) ((fake-tree partial) (prepare-tree "/t" "rev1" None (frozenset) 1 #(".")))))
  (assert (is (get summary "problem") None))
  (assert (= (get partial "markers" 0 "failed") [{"path" "a/n.hy" "reason" "SyntaxError: x"}])))


(deftest test-tree-and-marker-checks-are-pure
  (assert (in "source が 1 つも無い" (tree-problem [] (frozenset) (frozenset))))
  (setv marker (json.dumps (marker-content "rev1" True ["a/m.py"] (frozenset [(cache-rel "a/m.py")]) [])))
  (assert (is (marker-problem marker "rev1" True 1) None))
  (assert (in "印が無い" (marker-problem None "rev1" True 1)))
  (assert (in "木の名前と違う" (marker-problem marker "rev2" True 1)))
  (assert (in "1 file のはずが 0 file" (marker-problem marker "rev1" True 0)))
  (assert (in "読めない" (marker-problem "{" "rev1" True 1)))
  ;; 焼かない worker(--no-warm)は bytecode の無い印でよい。焼く worker はそれを完成品と見ない。
  (setv plain (json.dumps {"format" 1 "revision" "rev1" "bytecode" False}))
  (assert (is (marker-problem plain "rev1" False 0) None))
  (assert (in "焼かずに" (marker-problem plain "rev1" True 0))))


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
  (setv view (wait-settled store rev))
  (assert (= view.state CodeState.FAILED))
  (assert (in "焼きの道具が見つからない" view.detail))
  (assert (is-not view.failed-ms None))
  ;; 完成品の dir も途中の dir も残らない。
  (assert (not (.exists (/ cache rev))))
  (assert (= (lfor e (.iterdir cache) :if (not (.startswith e.name ".")) e) []))
  ;; 次の準備(policy が間を置いて PrepareCode を出した後)は本物の道具で作り直せる。
  (setv store.hy-command HY)
  (.start store rev)
  (setv view (wait-settled store rev))
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


(deftest test-layered-tree-takes-the-worker-dir-from-the-overlay-commit [tmp-path]
  ;; 土台の木(本番の commit)に、重ねる commit の重ねる dir(app/wrap)を重ねる(2026-09-24 — 業務コードは本番・service の包みは宣言)。
  ;; 土台に在る重ねる dir は消えて、重ねる commit の物だけが残る。前の重ねた木から引き継ぐ道も同じ検めを通る。
  (setv repo (/ tmp-path "repo") cache (/ tmp-path "cache"))
  (defn commit [files message]
    (for [#(path text) (.items files)]
      (.mkdir (. (/ repo path) parent) :parents True :exist-ok True)
      (.write-text (/ repo path) text))
    (git repo "add" "-A")
    (git repo "-c" "user.name=t" "-c" "user.email=t@t" "commit" "-q" "-m" message)
    (git repo "rev-parse" "HEAD"))
  (.mkdir repo :parents True)
  (git repo "init" "-q")
  ;; 包みの branch の commit(worker の dir と、古い業務コード)
  (setv wrap (commit {"app/__init__.py" "" "app/core/__init__.py" "" "app/core/loop.py" "V = 'old'\n"
                      "app/wrap/__init__.py" "" "app/wrap/w.py" "W = 'wrap'\n"} "wrap"))
  ;; 本番の commit(新しい業務コード・worker の dir は古い物が残っている形)
  (setv base (commit {"app/core/loop.py" "V = 'new'\n" "app/wrap/w.py" "W = 'stale'\n"
                      "app/wrap/extra.py" "E = 1\n"} "base"))
  (setv store (CodeStore (str repo) (str cache) HY :layout (CodeLayout :overlay-path "app/wrap")) key (+ base "~" wrap))
  (.start store key)
  (setv view (wait-settled store key))
  (assert (= view.state CodeState.READY) view.detail)
  (setv tree (/ cache key))
  (assert (= (.read-text (/ tree "app/core/loop.py")) "V = 'new'\n"))
  (assert (= (.read-text (/ tree "app/wrap/w.py")) "W = 'wrap'\n"))
  (assert (not (.exists (/ tree "app/wrap/extra.py"))) "土台の重ねる dir は残さない")
  (assert (= (get (json.loads (.read-text (/ tree MARKER))) "revision") key))
  ;; 本番が次の版へ進んだ(業務コードだけ変わる)→ 前の重ねた木から引き継いで準備する。
  (setv base2 (commit {"app/core/loop.py" "V = 'newer'\n"} "base2"))
  (setv key2 (+ base2 "~" wrap))
  (.start store key2)
  (setv view (wait-settled store key2))
  (assert (= view.state CodeState.READY) view.detail)
  (assert (= (.read-text (/ cache key2 "app/core/loop.py")) "V = 'newer'\n"))
  (assert (= (.read-text (/ cache key2 "app/wrap/w.py")) "W = 'wrap'\n"))
  ;; 変わっていない包みの .pyc は前の木と同じ inode(引き継いだ)。
  (assert (= (. (.stat (/ cache key2 (cache-rel "app/wrap/w.py"))) st-ino)
             (. (.stat (/ cache key (cache-rel "app/wrap/w.py"))) st-ino))))
