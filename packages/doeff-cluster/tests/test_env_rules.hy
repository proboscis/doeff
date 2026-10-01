;; 実行環境の root の準備の判断(worker/core/env_rules — #2467)を、準備の process を起こさずに確かめる。
;;   * 起こす順: job の準備を先に・同時は max-parallel 本まで・先読みは枠の 1 つを job に残す。
;;   * 準備の答えの読み: 失敗の答え・成功の答え・答えを書かずに終わった process・期限切れの理由。
;;   * 冷たい準備の見分け・掃除の候補の project の名・掃除の下限。
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailureKind])
(import doeff_cluster.worker.core.env_upkeep [PrepareLimits])
(import doeff_cluster.worker.core.env_rules [launch-order cold-for prepare-argv prepare-outcome overdue-failure root-project floor-bytes])


(deftest test-job-prepares-go-first-and-warm-ones-leave-a-slot
  (val waiting #(#("env-w1" True) #("env-j1" False) #("env-w2" True) #("env-j2" False)))
  (<- order tuple (launch-order waiting 0 0 3))
  ;; job の 2 本を先に、先読みは枠の 1 つを job に残して 1 本だけ(3 本の枠 — 先読みの上限 2 だが枠が埋まる)。
  (assert (= order #("env-j1" "env-j2" "env-w1")) order)
  (<- full tuple (launch-order waiting 2 0 2))
  (assert (= full #()) full)
  ;; 枠が 2 本なら先読みは 1 本まで(走っている先読みが 1 本在れば、残りの枠は job だけに使う)。
  (<- warm-only tuple (launch-order #(#("env-w1" True) #("env-w2" True)) 1 1 2))
  (assert (= warm-only #()) warm-only)
  (<- one-slot tuple (launch-order #(#("env-w1" True) #("env-w2" True)) 0 0 1))
  (assert (= one-slot #("env-w1")) one-slot))


(deftest test-prepare-answers-are-read-into-failures
  (<- failed (prepare-outcome {"failure" {"kind" "disk-full" "detail" "空きが無い" "retryable" True}} 1 "/s/k.log"))
  (assert (= #(failed.kind failed.detail failed.retryable) #(EnvFailureKind.DISK-FULL "空きが無い" True)) failed)
  (<- ready (prepare-outcome {"ready" {}} 0 "/s/k.log"))
  (assert (is ready None) ready)
  (<- silent (prepare-outcome None -9 "/s/k.log"))
  (assert (= silent.kind EnvFailureKind.ENV-INCOMPATIBLE) silent)
  (assert (not silent.retryable) silent)
  (assert (in "終了 -9" silent.detail) silent)
  (assert (in "/s/k.log" silent.detail) silent)
  (<- warm (overdue-failure True False (PrepareLimits)))
  (assert (= warm.kind EnvFailureKind.PREPARE-TIMEOUT) warm)
  (assert (in "先読み" warm.detail) warm)
  (<- cold (overdue-failure False True (PrepareLimits)))
  (assert (in "冷たい" cold.detail) cold))


(deftest test-cold-project-floor-and-argv
  (val declared {"project" {"lockSha256" "L" "python" "3.12" "repo" "r" "path" "p"} "repos" [{"name" "r" "url" "git@x:r"}]})
  (<- cold bool (cold-for declared #()))
  (assert cold)
  (<- warm bool (cold-for declared #({"env" declared "root" "/s/roots/a"})))
  (assert (not warm))
  (<- project str (root-project {"env" declared}))
  (assert (= project "git@x:r:p") project)
  (<- set-floor int (floor-bytes 5 100 1000))
  (assert (= set-floor 5) set-floor)
  (<- ratio-floor int (floor-bytes None 100 10000))
  (assert (= ratio-floor 1500) ratio-floor)
  (<- argv tuple (prepare-argv "/hy" "tool" "/q" "/r" "/s" "/keys" "/cp" "uv" "/p"))
  (assert (= (cut argv 0 6) #("nice" "-n" "10" "/hy" "-m" "tool")) argv)
  (assert (= (cut argv -2 None) #("--progress" "/p")) argv))
