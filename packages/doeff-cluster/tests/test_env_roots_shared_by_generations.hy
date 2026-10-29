;; 同じ機体に同じ名の worker の 2 つの世代が並ぶ時(Deployment の RollingUpdate — AL-D2)、実行環境の root の置き場を世代で分け合う形の検
;; (AL-D2g)。
;;
;; 実例(2026-10-11 17:36〜17:42 JST): root を世代ごとの状態の dir に置いていたので、zeus の新しい世代が全 job の root を一から準備し直し、
;; 旧い世代の持っていた root は使われなかった。だから:
;;   - 新しい世代は、前の世代が準備した root(分け合う dir の roots の下)を準備し直さずに使う(観測で READY・準備の process は錠を取った時に
;;     完成マーカーを読めば準備し直さない)
;;   - 準備の前の途中の root の退けは、準備の process がキーの錠を持つ間だけ行う(worker が錠の外で退けると、もう片方の世代の走っている
;;     準備を壊す)
;;   - 掃除は、まだ走っている他の世代(状態の dir が在る)の固定の root を退けない。終わった世代(状態の dir が無い)の固定は数えず、
;;     その固定の file も片づける
;; 反例 = 直す前の形(root を世代ごとの state に置く・worker が錠の外で退ける・掃除が自分の固定だけを見る)は、下の断言が赤。
;; 土台: file system と子 process は本物(検の tmp の dir の中だけ)— 準備の process と uv の代わりに何もしない `true` を渡す。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import os)
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects [SlogEffect])
(import doeff_core_effects [reader state])
(import doeff_core_effects [scheduled])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [Delay sync-time-handler])
(import doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.worker.intent.worker_model [CodeState PrepareEnv SweepEnvs])
(import doeff_cluster.worker.intent.env_prepare_model [EnvReady PrepareRequest])
(import doeff_cluster.worker.protocol.observations [ObserveEnvs])
(import doeff_cluster.worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster.worker.protocol.env_store [EnvSettings SWEEP-LOG SWEEP-DONE-LOG PINS-DIR env-host])
(import doeff_cluster.worker.protocol.env_translation [env-translation prepared-once])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import tests.env_fixtures [LOCK env-of])

;; 同じ project の完成した root 3 つ(いちばん古い方は消してよい・新しい 2 つは今の版と戻し先の版なので残す — #3732)。
(val OLD-NAME "0123456789abcdef01234567")
(val MID-NAME "456789abcdef0123456789ab")
(val NEW-NAME "89abcdef0123456789abcdef")
;; 準備の途中の root(完成マーカーが無い — もう片方の世代が準備している)。
(val HALF-NAME "fedcba9876543210fedcba98")
(val MARKER {"env" {"project" {"repo" "r" "path" "p"} "repos" [{"name" "r" "url" "https://example.invalid/r"}]}
             "interpreter" "/usr/bin/python3"})
;; 準備の頼みの宣言(準備の process は `true` なので中身は読まれない)。
(val DECLARED "{\"project\": {\"lockSha256\": \"L\", \"python\": \"3.12\"}}")
;; roots の合計の上限を 0 に置いて、必ず掃除させる。
(val CAP 0)
;; 掃除の終わりを待つ SweepEnvs の回数の上限と、回の間の秒(本物の時計 — 合わせて 10 秒)。
(val SWEEP-ROUNDS 1000)
(val SWEEP-PAUSE-SECONDS 0.01)


(defrecord SweepLine
  "掃除の頭の行 1 つの欄(固定の数・候補の数・選んだ数)。"
  (#^ int pinned)
  (#^ int candidates)
  (#^ int chosen))


(defrecord NotedSweeps
  "ここまでに出た掃除の行: lines = 掃除の選びの行(出た順)・done = 掃除の終わりの行の数。"
  (#^ (get tuple #(SweepLine ...)) lines)
  (#^ int done))


(defeffect NotedSweepLines
  "ここまでに出た掃除の行を読む — 検だけの問い(sweep-lines-noted が答える)。"
  {:fields [] :answer NotedSweeps :tags {:context "doeff-cluster-test" :role "intent"}})


(defk count-field [fields name]
  {:pre [(: fields dict) (: name str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "log の行の欄 name の数を返すため(数でなければ -1 — 断言が名指す)。"
  (val value (.get fields name))
  (if (isinstance value int) value -1))


(defhandler sweep-lines-noted
  "外の世界の log の代役: 掃除の選びの行(SWEEP-LOG)の欄を出た順に覚え、終わりの行(SWEEP-DONE-LOG)を数え、他の行は受け流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var lines #())
  (session var done 0)
  (SlogEffect []
    (when (= effect.msg SWEEP-DONE-LOG)
      (:= done (+ done 1)))
    (when (= effect.msg SWEEP-LOG)
      (val fields effect.kwargs)
      (<- pinned int (count-field fields "pinned"))
      (<- candidates int (count-field fields "candidates"))
      (<- chosen int (count-field fields "chosen"))
      (:= lines (+ lines #((SweepLine :pinned pinned :candidates candidates :chosen chosen)))))
    (resume None))
  (NotedSweepLines []
    (resume (NotedSweeps :lines lines :done done))))


(defk generation [tmp name [preparer "true"]]
  {:pre [(: tmp Path) (: name str) (: preparer str)] :post [(: % EnvSettings)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "世代 name の設定を返すため: 状態の dir は tmp/gen/<name>/state(世代ごと)・分け合う dir は tmp/shared(起動の script の
   WORKER_GENERATION_DIRS と同じ並び)。準備の process は preparer(既定は何もしない `true`)・uv は `true`・roots の上限は 0。"
  (val own (/ tmp "gen" name "state"))
  (.mkdir own :parents True :exist-ok True)
  (EnvSettings :state (str own) :shared (str (/ tmp "shared")) :uv-cache (str (/ tmp "shared" "uv-cache")) :hy-command preparer
               :platform "test" :code-prepare PREPARE-TOOL :uv "true" :roots-cap-bytes CAP))


(defk shared-root [tmp name marked hours-ago]
  {:pre [(: tmp Path) (: name str) (: marked bool) (: hours-ago int)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "分け合う dir の roots の下に root name を置くため(marked = 完成マーカーを置くか・hours-ago = 印と dir の時刻を何時間前にするか)。
   中に印でない file kept を置く(退けられたか・作り直されたかを読む)。答え = root の path。"
  (val root (/ tmp "shared" "roots" name))
  (.mkdir root :parents True)
  (.write-text (/ root "kept") "x")
  (when marked
    (.write-text (/ root ENV-MARKER) (json.dumps MARKER))
    (val at (- (. (.stat (/ root ENV-MARKER)) st-mtime) (* 3600 hours-ago)))
    (os.utime (/ root ENV-MARKER) #(at at))
    (os.utime root #(at at)))
  root)


(defk on-envs [settings program]
  {:pre [(: settings EnvSettings) (: program Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "筋書きの Program を世代 1 つの env-host と本物の答え手の下で回すため(掃除の行は env-host の外側の sweep-lines-noted が受ける)。"
  (<- answer tuple (scheduled (with-handlers [(state) (sync-time-handler) sweep-lines-noted os-file-handler subprocess-handler (env-host settings)]
                                       program)))
  answer)


(defk sweep-once [pinned]
  {:pre [(: pinned frozenset)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "固定の集合 pinned で掃除を 1 回終わりまで進め(終わりの行が出るまで SweepEnvs を撃つ)、出た掃除の選びの行を返すため。"
  (for [_ (range SWEEP-ROUNDS)]
    (<- (SweepEnvs pinned))
    (<- seen NotedSweeps (NotedSweepLines))
    (when (> seen.done 0)
      (break))
    (<- (Delay SWEEP-PAUSE-SECONDS)))
  (<- noted NotedSweeps (NotedSweepLines))
  (assert (= noted.done 1) noted)
  noted.lines)


(defk asked-then-observed [key]
  {:pre [(: key str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "key の root の準備を頼んでから観測するため。答え = 観測の列。"
  (<- (PrepareEnv key DECLARED None))
  (<- views tuple (ObserveEnvs))
  views)


(deftest test-a-new-generation-uses-the-roots-the-elder-prepared-without-preparing-again [tmp-path]
  ;; 前の世代 a が準備した root を、新しい世代 b は READY と観測し(path は分け合う置き場の物)、頼まれても準備の process を起こさない。
  ;; 直す前(root を世代ごとの state に置く)は、b の置き場が空で観測に出ず、頼みで準備の process を起こした(準備の要求が書かれた)。
  (<- elder EnvSettings (generation tmp-path "a"))
  (<- newer EnvSettings (generation tmp-path "b"))
  (<- root Path (shared-root tmp-path NEW-NAME True 0))
  (val key (+ "env-" NEW-NAME))
  (<- views tuple (on-envs newer (asked-then-observed key)))
  (assert (= (lfor v views #(v.revision v.state v.path)) [#(key CodeState.READY (str root))]) views)
  (assert (not (.exists (/ (Path newer.state) "env-requests" (+ key ".json")))) "準備済みの root の準備の process を起こさない")
  ;; 前の世代も同じ root を同じ path で読む(どちらの世代の job も同じ venv を使う)。
  (<- elder-views tuple (on-envs elder (asked-then-observed key)))
  (assert (= (lfor v elder-views v.path) [(str root)]) elder-views))


(deftest test-a-generation-leaves-in-place-a-root-the-other-generation-is-preparing [tmp-path]
  ;; もう片方の世代が準備している途中の root(完成マーカーが無い)を頼まれても、worker は退けない — 退けは準備の process がキーの錠を
  ;; 持つ間だけ(prepared-once)。直す前(worker が準備の process を起こす前に退けた)は、root が .<名>.broken.<時刻> へ動いて赤。
  ;; 準備の process の代わりに 3 秒眠る script を起こす(観測の時に準備中)。
  (val preparer (/ tmp-path "slow-prepare"))
  (.write-text preparer "#!/bin/sh\nsleep 3\n" :encoding "utf-8")
  (os.chmod preparer 0o755)
  (<- newer EnvSettings (generation tmp-path "b" (str preparer)))
  (<- root Path (shared-root tmp-path HALF-NAME False 0))
  (val key (+ "env-" HALF-NAME))
  (<- views tuple (on-envs newer (asked-then-observed key)))
  (assert (= (lfor v views #(v.revision v.state)) [#(key CodeState.PREPARING)]) views)
  (assert (.exists (/ root "kept")) "準備の途中の root はその場に残る")
  (assert (= (lfor p (.iterdir (/ tmp-path "shared" "roots")) :if (.startswith p.name ".") p.name) []) "脇へ退けた dir は無い"))


(deftest test-the-prepare-tool-uses-a-root-another-generation-finished [tmp-path]
  ;; 準備の process はキーの錠を取った時に完成マーカーを読めば、準備し直さずにその root を答える(錠を待つ間にもう片方の世代が
  ;; 準備し終えた root — 中の file は作り直されず、脇へ退けた dir も無い)。
  (<- root Path (shared-root tmp-path NEW-NAME True 0))
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val request (PrepareRequest :env env :key NEW-NAME :platform "test" :root (str root) :compile-jobs None))
  (val settings {"runtime-env.state" (str (/ tmp-path "gen" "b" "state")) "runtime-env.shared" (str (/ tmp-path "shared"))
                 "runtime-env.uv-cache" (str (/ tmp-path "shared" "uv-cache")) "runtime-env.repo-keys" {}
                 "runtime-env.code-prepare" PREPARE-TOOL "runtime-env.uv" "true" "runtime-env.progress" ""
                 "runtime-env.notes" (str (/ tmp-path "notes"))})
  (<- answer (with-handlers [(state) (sync-time-handler) (reader settings) subprocess-handler os-file-handler env-translation]
                            (prepared-once request)))
  (assert (isinstance answer EnvReady) answer)
  (assert (= #(answer.root answer.interpreter answer.stages) #((str root) "/usr/bin/python3" #())) answer)
  (assert (.exists (/ root "kept")) "準備し直さない(root の中身はそのまま)")
  (assert (= (lfor p (.iterdir (/ tmp-path "shared" "roots")) :if (.startswith p.name ".") p.name) []) "脇へ退けた dir は無い")
  (assert (.exists (/ tmp-path "shared" "locks" (+ "root-" NEW-NAME))) "準備はキーの錠の中で行う"))


(deftest test-the-sweep-keeps-a-root-the-other-live-generation-pins [tmp-path]
  ;; 世代 a が固定した古い root を、世代 b の掃除は退けない(a の固定の file を読む)。a が終わって状態の dir が消えた後は、b の掃除は
  ;; a の固定を数えずにその root を消し、a の固定の file も片づける。直す前(掃除が自分の固定だけを見る)は、b の 1 回目の掃除が古い root を
  ;; 消して赤。
  (<- elder EnvSettings (generation tmp-path "a"))
  (<- newer EnvSettings (generation tmp-path "b"))
  (<- old Path (shared-root tmp-path OLD-NAME True 2))
  (<- (shared-root tmp-path MID-NAME True 1))
  (<- (shared-root tmp-path NEW-NAME True 0))
  (val old-key (+ "env-" OLD-NAME))
  (<- elder-lines tuple (on-envs elder (sweep-once (frozenset #(old-key)))))
  (assert (= (lfor l elder-lines #(l.pinned l.candidates l.chosen)) [#(1 0 0)]) elder-lines)
  (<- newer-lines tuple (on-envs newer (sweep-once (frozenset))))
  (assert (= (lfor l newer-lines #(l.pinned l.candidates l.chosen)) [#(1 0 0)]) newer-lines)
  (assert (.exists (/ old "kept")) "まだ走っている他の世代の固定の root は消さない")
  ;; a が終わった(起動の script が a の世代の dir を消した)。
  (for [path (sorted (.rglob (/ tmp-path "gen" "a") "*") :reverse True)]
    (if (.is-dir path) (.rmdir path) (.unlink path)))
  (.rmdir (/ tmp-path "gen" "a"))
  (<- after-lines tuple (on-envs newer (sweep-once (frozenset))))
  (assert (= (lfor l after-lines #(l.pinned l.candidates l.chosen)) [#(0 1 1)]) after-lines)
  (assert (not (.exists old)) "終わった世代の固定は数えない")
  (assert (= (lfor p (.iterdir (/ tmp-path "shared" PINS-DIR)) p.name) []) "終わった世代の固定の file は片づける"))
