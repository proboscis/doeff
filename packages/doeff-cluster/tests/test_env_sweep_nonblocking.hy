;; 掃除の uv の cache の prune が worker のループを止めない(E13 の実験用の pod で見つけた — E11 の掃除の欠陥)の検。
;;
;; 実測(2026-09-26・atlas): node の disk の空きが掃除の下限を切り、worker の掃除が `uv cache prune` を同期で撃った。prune は uv の
;; cache の lock を取るので、root の準備の `uv run`(bytecode の焼き)が終わるまで待ち、その間 worker の heartbeat が止まって
;; coordinator が worker を沈黙と見なし、task を落とした。だから:
;;   - prune は別の process として起こし、worker は待たない(掃除は数秒で返る)
;;   - 前の prune が走っている間は次を起こさない
;;   - root の準備が走っている間は起こさない(lock で準備と競わない)
;; 検は uv の代わりに、起きた印を書いて眠る script を渡す。掃除の数えと消しはループの外の task で走り、prune は消しの終わった拍で起きる
;; (#3715)ので、筋書きは SweepEnvs を短い間を置いて撃ち続ける(worker の拍の代わり)。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import os)
(import time)
(import pathlib [Path])
(import doeff_time [Delay])
(import doeff_cluster.worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster.worker.intent.worker_model [PrepareEnv SweepEnvs])
(import doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.worker.protocol.env_store [EnvSettings])
(import tests.careful_rig [run-envs])


(defk sleeping-uv [tmp]
  {:pre [(: tmp Path)] :post [(: % str)]}
  "起きるたびに印の file に 1 行足して 30 秒眠る、uv の代わりの script(prune が起きたかと、worker が待たないかを見るため)。"
  (val script (/ tmp "fake-uv"))
  (.write-text script (.format "#!/bin/sh\necho \"$@\" >> {}\nsleep 30\n" (/ tmp "uv-calls")) :encoding "utf-8")
  (os.chmod script 0o755)
  (str script))


(defk calls [tmp]
  {:pre [(: tmp Path)] :post [(: % list)]}
  "印の file に書かれた呼び出し(少し待って読む)。"
  (val path (/ tmp "uv-calls"))
  (val deadline (+ (time.monotonic) 3))
  (while (and (not (.exists path)) (< (time.monotonic) deadline)) (time.sleep 0.05))
  (if (.exists path) (.splitlines (.read-text path :encoding "utf-8")) []))


(defk sweeping [tmp uv [hy-command "hy"]]
  {:pre [(: tmp Path) (: uv str) (: hy-command str)] :post [(: % EnvSettings)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "必ず掃除させ(roots の合計の上限 0 を、消せない root 1 つ — project の最新 — が越えたままにする)、消しの終わりに prune を起こさせる
   (共有の disk の空きの最低を disk の大きさより上に置く — #3732)設定を組むため。"
  (val root (/ tmp "state" "roots" "fedcba9876543210fedcba98"))
  (.mkdir root :parents True)
  (.write-text (/ root ENV-MARKER)
               (json.dumps {"env" {"project" {"repo" "r" "path" "p"} "repos" [{"name" "r" "url" "https://example.invalid/r"}]}}))
  (EnvSettings :state (str (/ tmp "state")) :uv-cache (str (/ tmp "state" "uv-cache")) :hy-command hy-command :platform "test" :code-prepare PREPARE-TOOL :uv uv :roots-cap-bytes 0
               :min-free-bytes (** 10 18)))


(val SWEEP-PAUSE-SECONDS 0.02)   ; 撃つ SweepEnvs の間(worker の拍の代わり)
(val SWEEP-SECONDS 1.0)          ; 1 つの固定の集合で撃ち続ける秒(掃除 1 回が数え・消し・prune の起こしまで進む長さ)


(defk sweep-for [pinned seconds]
  {:pre [(: pinned frozenset) (: seconds float)] :post [(: % float)] :tags {:context "doeff-cluster-test" :role "program"}}
  "固定の集合 pinned で seconds 秒の間 SweepEnvs を撃ち続け、1 回の SweepEnvs にかかったいちばん長い秒を返すため。"
  (val until (+ (time.monotonic) seconds))
  (var longest 0.0)
  (while (< (time.monotonic) until)
    (val started (time.monotonic))
    (<- (SweepEnvs pinned))
    (:= longest (max longest (- (time.monotonic) started)))
    (<- (Delay SWEEP-PAUSE-SECONDS)))
  longest)


(defk sweep-twice [pinned]
  {:pre [(: pinned frozenset)] :post [(: % float)] :tags {:context "doeff-cluster-test" :role "program"}}
  "固定の無い掃除を撃ち続け、固定の集合を pinned に変えてすぐの掃除をもう 1 つ撃ち続けて、1 回の SweepEnvs のいちばん長い秒を返すため
   (同じ記録の上で)。"
  (<- first float (sweep-for (frozenset) SWEEP-SECONDS))
  (<- second float (sweep-for pinned SWEEP-SECONDS))
  (max first second))


(deftest test-the-prune-does-not-block-the-worker-loop [tmp-path]
  (<- uv str (sleeping-uv tmp-path))
  ;; 前の prune が走っている間は次を起こさない(固定の集合を変えて、すぐの掃除を起こしても)。
  (val took (! (run-envs (! (sweeping tmp-path uv)) (sweep-twice (frozenset #("env-other"))))))
  (assert (< took 1) "掃除は prune を待たずに返る")
  (time.sleep 0.3)
  (<- again list (calls tmp-path))
  (assert (= again ["cache prune"]) again))


(defk preparing-then-sweep [declared]
  {:pre [(: declared str)] :post [(: % None)]}
  "root の準備を 1 本起こし、走っている間に掃除するため。"
  (<- (PrepareEnv "env-0123456789abcdef01234567" declared None))
  (<- (sweep-for (frozenset) SWEEP-SECONDS))
  None)


(deftest test-the-prune-waits-while-a-root-is-being-prepared [tmp-path]
  (<- uv str (sleeping-uv tmp-path))
  ;; 準備が 1 本走っている間は prune を起こさない(準備の process の代わりに眠る script を起こす)。
  (val preparer (/ tmp-path "slow-prepare"))
  (.write-text preparer "#!/bin/sh\nsleep 5\n" :encoding "utf-8")
  (os.chmod preparer 0o755)
  (val declared "{\"project\": {\"lockSha256\": \"L\", \"python\": \"3.12\"}}")
  (<- (run-envs (! (sweeping tmp-path uv (str preparer))) (preparing-then-sweep declared)))
  (time.sleep 0.5)
  (assert (not (.exists (/ tmp-path "uv-calls"))) "準備の間は prune を起こさない"))


(deftest test-a-finished-prune-is-not-restarted-within-the-interval [tmp-path]
  ;; node の disk を他の物が使っていると、worker が消せる量では下限に戻らない(atlas 2026-09-26 — 掃除の拍 30 秒ごとに prune が
  ;; 起き続けた)。前の prune が終わっていても、PRUNE-EVERY-SECONDS の間は起こし直さない。
  (val script (/ tmp-path "fake-uv"))
  (.write-text script (.format "#!/bin/sh\necho \"$@\" >> {}\n" (/ tmp-path "uv-calls")) :encoding "utf-8")
  (os.chmod script 0o755)
  ;; 1 回目の掃除の prune はすぐ終わる。固定の集合を変えて、すぐの掃除を起こす(prune は終わっている)。
  (<- (run-envs (! (sweeping tmp-path (str script))) (sweep-twice (frozenset #("env-other")))))
  (time.sleep 0.3)
  (<- again list (calls tmp-path))
  (assert (= again ["cache prune"]) again))
