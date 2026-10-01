;; 掃除の uv の cache の prune が worker のループを止めない(E13 の実験用の pod で見つけた — E11 の掃除の欠陥)の検。
;;
;; 実測(2026-09-26・atlas): node の disk の空きが掃除の下限を切り、worker の掃除が `uv cache prune` を同期で撃った。prune は uv の
;; cache の lock を取るので、root の準備の `uv run`(bytecode の焼き)が終わるまで待ち、その間 worker の heartbeat が止まって
;; coordinator が worker を沈黙と見なし、task を落とした。だから:
;;   - prune は別の process として起こし、worker は待たない(掃除は数秒で返る)
;;   - 前の prune が走っている間は次を起こさない
;;   - root の準備が走っている間は起こさない(lock で準備と競わない)
;; 検は uv の代わりに、起きた印を書いて眠る script を渡す。
(require doeff-hy.macros [deftest defk <- val])
(import os)
(import time)
(import pathlib [Path])
(import doeff_cluster.worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster.worker.intent.worker_model [PrepareEnv SweepEnvs])
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


(defn #^ EnvSettings sweeping [#^ Path tmp #^ str uv #^ str [hy-command "hy"]]
  "下限を disk の大きさより上に置いて、必ず掃除させる設定。"
  (EnvSettings :state (str (/ tmp "state")) :hy-command hy-command :platform "test" :code-prepare PREPARE-TOOL :uv uv :sweep-floor-bytes (** 10 18)))


(defk sweep-twice [pinned]
  {:pre [(: pinned frozenset)] :post [(: % float)]}
  "掃除を 1 回して、かかった秒を返し、固定の集合を pinned に変えてすぐの掃除をもう 1 回起こすため(同じ記録の上で)。"
  (val started (time.monotonic))
  (<- (SweepEnvs (frozenset)))
  (val took (- (time.monotonic) started))
  (time.sleep 0.5)
  (<- (SweepEnvs pinned))
  took)


(deftest test-the-prune-does-not-block-the-worker-loop [tmp-path]
  (<- uv str (sleeping-uv tmp-path))
  ;; 前の prune が走っている間は次を起こさない(固定の集合を変えて、すぐの掃除を起こしても)。
  (val took (run-envs (sweeping tmp-path uv) (sweep-twice (frozenset #("env-other")))))
  (assert (< took 5) "掃除は prune を待たずに返る")
  (time.sleep 0.3)
  (<- again list (calls tmp-path))
  (assert (= again ["cache prune"]) again))


(defk preparing-then-sweep [declared]
  {:pre [(: declared str)] :post [(: % None)]}
  "root の準備を 1 本起こし、走っている間に掃除するため。"
  (<- (PrepareEnv "env-0123456789abcdef01234567" declared))
  (<- (SweepEnvs (frozenset)))
  None)


(deftest test-the-prune-waits-while-a-root-is-being-prepared [tmp-path]
  (<- uv str (sleeping-uv tmp-path))
  ;; 準備が 1 本走っている間は prune を起こさない(準備の process の代わりに眠る script を起こす)。
  (val preparer (/ tmp-path "slow-prepare"))
  (.write-text preparer "#!/bin/sh\nsleep 5\n" :encoding "utf-8")
  (os.chmod preparer 0o755)
  (val declared "{\"project\": {\"lockSha256\": \"L\", \"python\": \"3.12\"}}")
  (run-envs (sweeping tmp-path uv (str preparer)) (preparing-then-sweep declared))
  (time.sleep 0.5)
  (assert (not (.exists (/ tmp-path "uv-calls"))) "準備の間は prune を起こさない"))


(deftest test-a-finished-prune-is-not-restarted-within-the-interval [tmp-path]
  ;; node の disk を他の物が使っていると、worker が消せる量では下限に戻らない(atlas 2026-09-26 — 掃除の拍 30 秒ごとに prune が
  ;; 起き続けた)。前の prune が終わっていても、PRUNE-EVERY-SECONDS の間は起こし直さない。
  (val script (/ tmp-path "fake-uv"))
  (.write-text script (.format "#!/bin/sh\necho \"$@\" >> {}\n" (/ tmp-path "uv-calls")) :encoding "utf-8")
  (os.chmod script 0o755)
  ;; 1 回目の掃除の prune はすぐ終わる。固定の集合を変えて、すぐの掃除を起こす(prune は終わっている)。
  (run-envs (sweeping tmp-path (str script)) (sweep-twice (frozenset #("env-other"))))
  (time.sleep 0.3)
  (<- again list (calls tmp-path))
  (assert (= again ["cache prune"]) again))
