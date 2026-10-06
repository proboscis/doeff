;; コードの準備の失敗を完成品として公開しないこと・壊れた木を作り直すこと。
;; 焼きの Program は fake の handler で、版ごとのコードの木の言い換え(worker/protocol/code_store の code-host — #2466)は手元の小さな
;; git repo と偽の焼きの道具で、本物の答え手(subprocess-handler・os-file-handler)の下で確かめる。
(require doeff-hy.macros [defhandler defk deftest <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import os)
(import subprocess)
(import sys)
(import time)
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects.handlers [slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [SimClock sim-time-handler sync-time-handler])
(import doeff_cluster.worker.intent.code_model [ScanTree LinkPycs CompileSources WriteMarker Note] doeff_cluster.worker.core.code_plan [MARKER tree-problem marker-problem marker-content cache-rel] doeff_cluster.worker.core.code_prepare [prepare-tree] doeff_core_effects.python_bytecode [compiled-pyc])
(import doeff_cluster.worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster.worker.intent.worker_model [CodeLayout CodeState CodeView PrepareCode])
(import doeff_cluster.worker.protocol.observations [ObserveCode])
(import doeff_cluster.worker.protocol.code_store [CodeSettings code-host])


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


(defk tree-state [sources after [failures []]]
  {:pre [(: sources list) (: after list) (: failures list)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "fake の焼きの答え手 fake-tree が読み書きする状態(走査の回数・焼く前と後の file・失敗・置いた印)の初めの値を作るため。"
  {"scans" 0 "sources" sources "after" after "failures" failures "markers" []})


(deftest test-marker-is-written-only-after-the-tree-checks
  (<- ok (tree-state ["a/m.py" "a/n.hy"] [(cache-rel "a/m.py") (cache-rel "a/n.hy")]))
  (<- summary dict ((sim-time-handler :clock (SimClock)) ((fake-tree ok) (prepare-tree "/t" "rev1" None (frozenset) 1 #(".")))))
  (assert (is (get summary "problem") None))
  (assert (= (len (get ok "markers")) 1))
  (assert (= (get ok "markers" 0 "revision") "rev1"))
  (assert (= (get ok "markers" 0 "pycs") 2))
  ;; 焼きは「成功」と答えたのに .pyc が置かれていない → 印を置かない。
  (<- silent (tree-state ["a/m.py" "a/n.hy"] [(cache-rel "a/m.py")]))
  (<- silent-summary dict ((sim-time-handler :clock (SimClock)) ((fake-tree silent) (prepare-tree "/t" "rev1" None (frozenset) 1 #(".")))))
  (assert (in "a/n.hy" (get silent-summary "problem")))
  (assert (= (get silent "markers") []))
  ;; 全部焼けなかった(道具か環境の失敗)→ 印を置かない。
  (<- broken (tree-state ["a/m.py"] [] [#("a/m.py" "ImportError: hy")]))
  (<- broken-summary dict ((sim-time-handler :clock (SimClock)) ((fake-tree broken) (prepare-tree "/t" "rev1" None (frozenset) 1 #(".")))))
  (assert (in "全部焼けなかった" (get broken-summary "problem")))
  (assert (= (get broken "markers") []))
  ;; 一部の file だけ焼けない(その file の source の誤り)は完成とし、印に理由を残す。
  (<- partial (tree-state ["a/m.py" "a/n.hy"] [(cache-rel "a/m.py")] [#("a/n.hy" "SyntaxError: x")]))
  (<- partial-summary dict ((sim-time-handler :clock (SimClock)) ((fake-tree partial) (prepare-tree "/t" "rev1" None (frozenset) 1 #(".")))))
  (assert (is (get partial-summary "problem") None))
  (assert (= (get partial "markers" 0 "failed") [{"path" "a/n.hy" "reason" "SyntaxError: x"}])))


(deftest test-tree-and-marker-checks-are-pure
  (assert (in "source が 1 つも無い" (or (! (tree-problem [] (frozenset) (frozenset))) "")))
  (setv marker (json.dumps (! (marker-content "rev1" True ["a/m.py"] (frozenset [(cache-rel "a/m.py")]) []))))
  (assert (is (! (marker-problem marker "rev1" True 1)) None))
  (assert (in "印が無い" (or (! (marker-problem None "rev1" True 1)) "")))
  (assert (in "木の名前と違う" (or (! (marker-problem marker "rev2" True 1)) "")))
  (assert (in "1 file のはずが 0 file" (or (! (marker-problem marker "rev1" True 0)) "")))
  (assert (in "読めない" (or (! (marker-problem "{" "rev1" True 1)) "")))
  ;; 焼かない worker(--no-warm)は bytecode の無い印でよい。焼く worker はそれを完成品と見ない。
  (setv plain (json.dumps {"format" 1 "revision" "rev1" "bytecode" False}))
  (assert (is (! (marker-problem plain "rev1" False 0)) None))
  (assert (in "焼かずに" (or (! (marker-problem plain "rev1" True 0)) ""))))


;; --- code-host(手元の git repo と偽の焼きの道具)---------------------------------------

(setv HY (str (/ (. (Path sys.executable) parent) "hy")))


(defk git [repo #* args]
  {:pre [(: repo Path) (: args tuple)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "検の手元の repo で git を 1 回撃ち、標準出力を返すため。"
  (.strip (. (subprocess.run ["git" "-C" (str repo) #* args] :check True :capture-output True :text True) stdout)))


(defk make-repo [root]
  {:pre [(: root Path)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "焼く道具(code_prepare.hy)を持たない小さな repo — 道具が木の中に無い古い版と同じ形。"
  (val repo (/ root "repo"))
  (.mkdir (/ repo "pkg") :parents True)
  (.write-text (/ repo "pkg" "__init__.py") "")
  (.write-text (/ repo "pkg" "m.py") "X = 1\n")
  (<- (git repo "init" "-q"))
  (<- (git repo "add" "."))
  (<- (git repo "-c" "user.name=t" "-c" "user.email=t@t" "commit" "-q" "-m" "c1"))
  #(repo (! (git repo "rev-parse" "HEAD"))))


(defk code-settings [repo cache hy-command]
  {:pre [(: repo Path) (: cache Path) (: hy-command (| str None))] :post [(: % CodeSettings)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "版ごとのコードの木の言い換え code-host の設定(手元の repo・cache・焼きの道具)を作るため。"
  (CodeSettings :repo (str repo) :cache (str cache) :hy-command hy-command :tool PREPARE-TOOL :layout (CodeLayout)))


(defk run-codes [settings program]
  {:pre [(: settings CodeSettings) (: program Program)] :post [(: % "筋書きの Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "筋書きの Program を code-host と本物の答え手の下で回す(with-handlers の並びは先頭が外側 — 準備の記録は外側の state が持つ)。"
  (<- answer (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (code-host settings)] program))
  answer)


(defk settled [revision]
  {:pre [(: revision str)] :post [(: % CodeView)] :tags {:context "doeff-cluster-test" :role "program"}}
  "版の準備が終わる(準備中でない)まで 0.1 秒ずつ観測し、その版の答えを返すため(上限 60 秒)。"
  (val deadline (+ (time.monotonic) 60))
  (var found None)
  (while (and (is found None) (< (time.monotonic) deadline))
    (<- views tuple (ObserveCode))
    (for [view views]
      (when (and (= view.revision revision) (!= view.state CodeState.PREPARING)) (:= found view)))
    (when (is found None) (time.sleep 0.1)))
  (when (is found None) (raise (TimeoutError revision)))
  found)


(defk prepared [revision]
  {:pre [(: revision str)] :post [(: % CodeView)] :tags {:context "doeff-cluster-test" :role "program"}}
  "版の準備を求め、終わるまで待つため。"
  (<- (PrepareCode revision))
  (<- view CodeView (settled revision))
  view)


(defk ready-views []
  {:pre [] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
  "観測で完成品と答える版の列を返すため。"
  (<- views tuple (ObserveCode))
  (lfor v views :if (= v.state CodeState.READY) v))


(defk failing-tool [root]
  {:pre [(: root Path)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "焼きの道具の代わり: 理由を言って 0 でない終了をする(以前の形では、これでも完成品になった)。"
  (val tool (/ root "fail.sh"))
  (.write-text tool "#!/bin/sh\necho '焼きの道具が見つからない' >&2\nexit 3\n")
  (os.chmod tool 0o755)
  (str tool))


(deftest test-failed-prepare-is-not-published-and-is-rebuilt [tmp-path]
  (setv #(repo rev) (! (make-repo tmp-path)) cache (/ tmp-path "cache"))
  (var view (! (run-codes (! (code-settings repo cache (! (failing-tool tmp-path)))) (prepared rev))))
  (assert (= view.state CodeState.FAILED))
  (assert (in "焼きの道具が見つからない" view.detail))
  (assert (is-not view.failed-ms None))
  ;; 完成品の dir も途中の dir も、標準エラーの file も残らない。
  (assert (not (.exists (/ cache rev))))
  (assert (= (lfor e (.iterdir cache) :if (not (.startswith e.name ".")) e) []))
  (assert (= (lfor e (.iterdir cache) :if (.endswith e.name ".err") e) []))
  ;; 次の準備(policy が間を置いて PrepareCode を出した後)は本物の道具で作り直せる。
  (:= view (! (run-codes (! (code-settings repo cache HY)) (prepared rev))))
  (assert (= view.state CodeState.READY) view.detail)
  (assert (= view.path (str (/ cache rev))))
  (assert (.exists (/ cache rev (cache-rel "pkg/m.py"))))
  (setv marker (json.loads (.read-text (/ cache rev MARKER))))
  (assert (= (get marker "revision") rev))
  (assert (get marker "bytecode")))


(deftest test-published-tree-without-a-valid-marker-is-rebuilt [tmp-path]
  (setv #(repo rev) (! (make-repo tmp-path)) cache (/ tmp-path "cache"))
  ;; 以前の形で「完成品」になった木: rename はされたが印も bytecode も無い(atlas の 8d7181f と同じ)。
  (setv old (/ cache rev))
  (.mkdir (/ old "pkg") :parents True)
  (.write-text (/ old "pkg" "m.py") "X = 1\n")
  (<- settings (code-settings repo cache HY))
  ;; 完成品として公開しない。
  (assert (= (! (run-codes settings (ready-views))) []))
  ;; その版を求められたら脇へ退けて作り直す。
  (setv view (! (run-codes settings (prepared rev))))
  (assert (= view.state CodeState.READY) view.detail)
  (assert (.exists (/ cache rev MARKER)))
  (assert (.exists (/ cache rev (cache-rel "pkg/m.py"))))
  (assert (= (lfor e (.iterdir cache) :if (in ".broken." e.name) e) [])))


(deftest test-next-revision-is-prepared-without-the-ready-tree [tmp-path]
  ;; 次の版も、前の完成品から引き継がずに(.pyc は source の中身で引く保存先から書く — #3858)同じ検めを通って完成品になる。
  (setv #(repo rev1) (! (make-repo tmp-path)) cache (/ tmp-path "cache"))
  (<- settings (code-settings repo cache HY))
  (assert (= (. (! (run-codes settings (prepared rev1))) state) CodeState.READY))
  (.write-text (/ repo "pkg" "n.py") "Y = 2\n")
  (<- (git repo "add" "."))
  (<- (git repo "-c" "user.name=t" "-c" "user.email=t@t" "commit" "-q" "-m" "c2"))
  (<- rev2 (git repo "rev-parse" "HEAD"))
  (setv view (! (run-codes settings (prepared rev2))))
  (assert (= view.state CodeState.READY) view.detail)
  (setv marker (json.loads (.read-text (/ cache rev2 MARKER))))
  (assert (= (get marker "pycs") 3))
  ;; 前の版の木の .pyc を hardlink しない(版の木は互いに独立 — 同じ中身の .pyc は保存先から書く)。
  (assert (!= (. (.stat (/ cache rev2 (cache-rel "pkg/m.py"))) st-ino)
              (. (.stat (/ cache rev1 (cache-rel "pkg/m.py"))) st-ino))))


(deftest test-a-ready-tree-of-another-history-does-not-affect-the-next-revision [tmp-path]
  ;; 同じ cache に別の履歴の版の完成品が在っても(以前の「<base>~<revision>」の重ねる木の名・履歴から消えた commit)、次の版の準備は
  ;; それに依らずに全部を用意して完成品にする。
  (<- made (make-repo tmp-path))
  (val cache (/ tmp-path "cache"))
  (assert (= (. (! (run-codes (! (code-settings (get made 0) cache HY)) (prepared (get made 1)))) state) CodeState.READY))
  ;; 前の木の版(made の commit)を持たない別の履歴の repo で、同じ cache に次の版を準備する。
  (val other (/ tmp-path "other"))
  (.mkdir (/ other "pkg") :parents True)
  (.write-text (/ other "pkg" "__init__.py") "")
  (.write-text (/ other "pkg" "n.py") "Y = 2\n")
  (<- (git other "init" "-q"))
  (<- (git other "add" "."))
  (<- (git other "-c" "user.name=t" "-c" "user.email=t@t" "commit" "-q" "-m" "other"))
  (<- rev (git other "rev-parse" "HEAD"))
  (<- settings (code-settings other cache HY))
  ;; 前の完成品(観測で完成品と答える唯一の版)。
  (<- ready list (run-codes settings (ready-views)))
  (assert (= (lfor v ready v.revision) [(get made 1)]) "前の完成品が在るはず")
  (<- view (run-codes settings (prepared rev)))
  (assert (= view.state CodeState.READY) view.detail)
  (assert (= (get (json.loads (.read-text (/ cache rev MARKER))) "pycs") 2))
  (assert (.exists (/ cache rev (cache-rel "pkg/n.py")))))


(deftest test-compiled-pyc-is-the-checked-hash-pyc-of-py-compile [tmp-path]
  ;; 焼いた .pyc の中身は、標準の py_compile が checked hash の方式で書く .pyc と 1 byte も違わない(PEP 552 の頭を公開の API で組む —
  ;; 以前は標準の私的な実装 _code_to_hash_pyc を import していた)。
  (import py_compile)
  (val source (/ tmp-path "mod.py"))
  (.write-text source "ANSWER = 42\n\ndef twice(x):\n    return 2 * x\n" :encoding "utf-8")
  (val expected (/ tmp-path "mod.pyc"))
  (py_compile.compile (str source) :cfile (str expected) :doraise True
                      :invalidation-mode py_compile.PycInvalidationMode.CHECKED_HASH)
  (<- baked (compiled-pyc "mod.py" "mod" (str source) (.read-bytes source)))
  (assert (= baked (.read-bytes expected))))
