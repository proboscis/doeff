;; 実行環境の root の準備の判断(worker/core/env_rules — #2467)を、準備の process を起こさずに確かめる。
;;   * 起こす順: job の準備を先に・同時は max-parallel 本まで・先読みは枠の 1 つを job に残す。
;;   * 準備の答えの読み: 答えの file の中身(失敗・完成・形の読めない中身)・失敗の答え・成功の答え・答えを書かずに終わった process・
;;     期限切れの理由。
;;   * 掃除の候補の project の名・準備の process の起こし方(掃除の下限は env_upkeep の 2 つの絶対の量 — test_env_warm・#3732)。
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailure EnvFailureKind])
(import doeff_cluster.worker.core.env_upkeep [PrepareLimits])
(import doeff_cluster.worker.core.env_rules [ReadyAnswer launch-order prepare-argv answer-of-text prepare-outcome overdue-failure
                                             root-project])


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


(deftest test-answer-files-are-read-into-typed-answers
  ;; 答えの file の中身(env_translation の answer-json の形): 失敗を先に読む・完成は ReadyAnswer・形の読めない中身は完成と読まない。
  (<- failed (answer-of-text "{\"failure\": {\"kind\": \"disk-full\", \"detail\": \"空きが無い\", \"retryable\": true}}"))
  (assert (= #(failed.kind failed.detail failed.retryable) #(EnvFailureKind.DISK-FULL "空きが無い" True)) failed)
  (<- ready (answer-of-text "{\"ready\": {\"key\": \"k\", \"root\": \"/s/roots/k\", \"interpreter\": \"\", \"downloaded\": 0, \"built\": 0}}"))
  (assert (= ready (ReadyAnswer :root "/s/roots/k")) ready)
  (for [text ["{\"ready\": {}}" "{}" "[]" "not json"]]
    (<- odd (answer-of-text text))
    (assert (and (isinstance odd EnvFailure) (= odd.kind EnvFailureKind.ENV-INCOMPATIBLE) (not odd.retryable)) #(text odd))
    (assert (in text odd.detail) #(text odd))))


(deftest test-prepare-answers-are-read-into-failures
  (val written (EnvFailure :kind EnvFailureKind.DISK-FULL :detail "空きが無い" :retryable True))
  (<- failed (prepare-outcome written 1 "/s/k.log"))
  (assert (= failed written) failed)
  (<- ready (prepare-outcome (ReadyAnswer :root "/s/roots/k") 0 "/s/k.log"))
  (assert (is ready None) ready)
  (<- silent (prepare-outcome None -9 "/s/k.log"))
  (assert (= silent.kind EnvFailureKind.ENV-INCOMPATIBLE) silent)
  (assert (not silent.retryable) silent)
  (assert (in "終了 -9" silent.detail) silent)
  (assert (in "/s/k.log" silent.detail) silent)
  ;; 期限で止めた準備: 先読みも job の準備も停滞の秒で止めた失敗(やり直してよい)・先読みは名指す。
  (<- warm (overdue-failure True (PrepareLimits :stall-seconds 600.0)))
  (assert (= #(warm.kind warm.retryable) #(EnvFailureKind.PREPARE-TIMEOUT True)) warm)
  (assert (and (in "先読み" warm.detail) (in "600" warm.detail)) warm)
  (<- job (overdue-failure False (PrepareLimits :stall-seconds 600.0)))
  (assert (= #(job.kind job.retryable) #(EnvFailureKind.PREPARE-TIMEOUT True)) job)
  (assert (and (not-in "先読み" job.detail) (in "600" job.detail)) job))


(deftest test-project-and-argv
  (val declared {"project" {"lockSha256" "L" "python" "3.12" "repo" "r" "path" "p"} "repos" [{"name" "r" "url" "git@x:r"}]})
  (<- project str (root-project {"env" declared}))
  (assert (= project "git@x:r:p") project)
  (<- argv tuple (prepare-argv "/hy" "tool" "/q" "/r" "/s" "/uc" "/keys" "/cp" "uv" "/p"))
  (assert (= (cut argv 0 6) #("nice" "-n" "10" "/hy" "-m" "tool")) argv)
  ;; worker の uv の cache の dir(main の --uv-cache)は準備の process へそのまま渡る(state の下に固定しない)。
  (assert (in #("--uv-cache" "/uc") (zip (cut argv 0 -1) (cut argv 1 None))) argv)
  (assert (= (cut argv -2 None) #("--progress" "/p")) argv))
