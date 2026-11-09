;; 実行環境の root の準備の判断(worker/core/env_rules — #2467)を、準備の process を起こさずに確かめる。
;;   * 起こす順: job の準備を先に・同時は max-parallel 本まで・先読みは枠の 1 つを job に残す。準備の層の空きを渡せば、空きが見込みの山
;;     以上の準備だけを起こし、memory の上限で殺された準備は 1 本だけで起こし直す。
;;   * 準備の答えの読み: 答えの file の中身(失敗・完成・形の読めない中身)・失敗の答え・成功の答え・答えを書かずに終わった process・
;;     期限切れの理由。
;;   * 掃除の候補の project の名・準備の process の起こし方(掃除の下限は env_upkeep の 2 つの絶対の量 — test_env_warm・#3732)。
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster._shared.intent.runtime_env_model [EnvFailure EnvFailureKind])
(import doeff_cluster._worker.core.env_upkeep [PrepareLimits])
(import doeff_cluster._worker.core.env_rules [ReadyAnswer PrepareRoom launch-order prepare-argv answer-of-text prepare-outcome overdue-failure
                                             root-project oom-kills-of memory-killed-failure])


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


(val MIB (** 1024 2))


(deftest test-a-prepare-layer-for-one-preparation-launches-one-at-a-time
  ;; 失敗ケース(2026-11-09 の k3s-2): 準備の層 640 MiB(1 本分)に job の準備 2 本を CPU の枠 2 だけで並べ、1 本 約 360 MB の準備が層の
  ;; 上限で互いを OOM kill し合った。層の空きで絞れば、見込みの山 480 MiB(640 MiB × 0.75)の準備は空いた層に 1 本だけ起き、走っている
  ;; 間(取り置きを引いた空き 160 MiB)は次を起こさず待たせる。反例: 層の空きを渡さない worker(層の無い機体)は今どおり枠まで起こす。
  (val waiting #(#("env-j1" False) #("env-j2" False)))
  (val needs {"env-j1" (* 480 MIB) "env-j2" (* 480 MIB)})
  (<- empty tuple (launch-order waiting 0 0 2 (PrepareRoom :free (* 640 MIB) :default-need (* 480 MIB) :needs needs)))
  (assert (= empty #("env-j1")) empty)
  (<- busy tuple (launch-order #(#("env-j2" False)) 1 0 2 (PrepareRoom :free (* 160 MIB) :default-need (* 480 MIB) :needs needs)))
  (assert (= busy #()) busy)
  (<- unlimited tuple (launch-order waiting 0 0 2))
  (assert (= unlimited #("env-j1" "env-j2")) unlimited)
  ;; 層が 2 本分あれば、CPU の枠まで並べる(層で絞るのは足りない時だけ)。
  (<- wide tuple (launch-order waiting 0 0 2 (PrepareRoom :free (* 1024 MIB) :default-need (* 480 MIB) :needs needs)))
  (assert (= wide #("env-j1" "env-j2")) wide))


(deftest test-a-preparation-that-does-not-fit-holds-back-the-ones-behind-it
  ;; 足りない準備は待ちに残し、後ろの小さい準備にも先を譲らない(大きい準備が待ち続けない)。見込みの無いキーは既定の山で数える。
  (val room (PrepareRoom :free (* 300 MIB) :default-need (* 480 MIB) :needs {"env-small" (* 100 MIB)}))
  (<- held tuple (launch-order #(#("env-big" False) #("env-small" False)) 0 0 2 room))
  (assert (= held #()) held)
  (<- small tuple (launch-order #(#("env-small" False) #("env-big" False)) 0 0 2 room))
  (assert (= small #("env-small")) small))


(deftest test-a-preparation-killed-for-memory-is-relaunched-alone
  ;; 層の memory の上限で殺された準備(alone)は、走っている準備が無い時だけ起こし、それが走っている間は何も起こさない。層を読めない
  ;; (free None)時は、走っている準備が無い時の 1 本だけ。
  (val needs {"env-a" (* 100 MIB) "env-b" (* 100 MIB)})
  (val wide (* 4096 MIB))
  (<- waits tuple (launch-order #(#("env-a" False) #("env-b" False)) 1 0 3
                                (PrepareRoom :free wide :default-need (* 100 MIB) :needs needs :alone (frozenset ["env-a"]))))
  (assert (= waits #()) waits)
  (<- alone tuple (launch-order #(#("env-a" False) #("env-b" False)) 0 0 3
                                (PrepareRoom :free wide :default-need (* 100 MIB) :needs needs :alone (frozenset ["env-a"]))))
  (assert (= alone #("env-a")) alone)
  (<- behind tuple (launch-order #(#("env-b" False)) 1 0 3
                                 (PrepareRoom :free wide :default-need (* 100 MIB) :needs needs :running-alone True)))
  (assert (= behind #()) behind)
  (<- unread tuple (launch-order #(#("env-a" False) #("env-b" False)) 0 0 3 (PrepareRoom :free None :default-need 0 :needs needs)))
  (assert (= unread #("env-a")) unread)
  (<- unread-busy tuple (launch-order #(#("env-b" False)) 1 0 3 (PrepareRoom :free None :default-need 0 :needs needs)))
  (assert (= unread-busy #()) unread-busy))


(deftest test-the-oom-kills-of-a-preparation-group-are-read-from-its-memory-events
  (<- killed int (oom-kills-of "low 0\nhigh 0\nmax 12\noom 3\noom_kill 2\noom_group_kill 1\n"))
  (assert (= killed 2) killed)
  (<- none int (oom-kills-of ""))
  (assert (= none 0) none)
  (<- failure (memory-killed-failure 1 -9 "/s/k.log"))
  (assert (= #(failure.kind failure.retryable) #(EnvFailureKind.MEMORY-KILLED False)) failure)
  (assert (in "/s/k.log" failure.detail) failure))


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
  (<- warm (overdue-failure True (PrepareLimits :stall-seconds 600.0) "sync" "/s/k.log" ""))
  (assert (= #(warm.kind warm.retryable) #(EnvFailureKind.PREPARE-TIMEOUT True)) warm)
  (assert (and (in "先読み" warm.detail) (in "600" warm.detail)) warm)
  (<- job (overdue-failure False (PrepareLimits :stall-seconds 600.0) "sync\n" "/s/k.log" "Permission denied (publickey)\n"))
  (assert (= #(job.kind job.retryable) #(EnvFailureKind.PREPARE-TIMEOUT True)) job)
  (assert (and (not-in "先読み" job.detail) (in "600" job.detail)) job)
  ;; 理由: 最後に進んだ処理・log の在処・log の末尾が失敗の文に載る(進みの印も log も空なら「不明」)。
  (assert (and (in "処理: sync・" job.detail) (in "/s/k.log" job.detail) (in "Permission denied" job.detail)) job)
  (<- bare (overdue-failure False (PrepareLimits :stall-seconds 600.0) "" "/s/k.log" ""))
  (assert (and (in "処理: 不明" bare.detail) (not-in "log の末尾" bare.detail)) bare))


(deftest test-project-and-argv
  (val declared {"project" {"lockSha256" "L" "python" "3.12" "repo" "r" "path" "p"} "repos" [{"name" "r" "url" "git@x:r"}]})
  (<- project str (root-project {"env" declared}))
  (assert (= project "git@x:r:p") project)
  (<- argv tuple (prepare-argv "/hy" "tool" "/q" "/r" "/s" "/shared" "/uc" "/keys" "/cp" "uv" "/p" "linux-x86_64" "/m"))
  ;; Linux の準備は SCHED_IDLE と nice 10 で起きる(service より必ず後)。
  (assert (= (cut argv 0 9) #("chrt" "--idle" "0" "nice" "-n" "10" "/hy" "-m" "tool")) argv)
  ;; chrt の無い機体(macOS の worker)は nice 10 だけ。
  (<- mac-argv tuple (prepare-argv "/hy" "tool" "/q" "/r" "/s" "/shared" "/uc" "/keys" "/cp" "uv" "/p" "darwin-arm64" "/m"))
  (assert (= (cut mac-argv 0 6) #("nice" "-n" "10" "/hy" "-m" "tool")) mac-argv)
  ;; worker の uv の cache の dir(main の --uv-cache)は準備の process へそのまま渡る(state の下に固定しない)。
  (assert (in #("--uv-cache" "/uc") (zip (cut argv 0 -1) (cut argv 1 None))) argv)
  ;; 世代ごとの state と、世代をまたいで分け合う dir(Python・wheel・mirror と錠 — AL-D2)は別の引数で渡る。
  (assert (in #("--state" "/s") (zip (cut argv 0 -1) (cut argv 1 None))) argv)
  (assert (in #("--shared" "/shared") (zip (cut argv 0 -1) (cut argv 1 None))) argv)
  (assert (in #("--progress" "/p") (zip (cut argv 0 -1) (cut argv 1 None))) argv)
  ;; 組みの間の層の読みの file(memory.peak の無い kernel の組みの山)も渡る。
  (assert (= (cut argv -2 None) #("--memory" "/m")) argv))


(import json)
(import doeff_cluster [RepoCheckout PythonProject RuntimeEnv])
(import doeff_cluster [runtime-env->json])
(import doeff_cluster._worker.intent.env_prepare_model [BytecodeTree PrepareRequest])
(import doeff_cluster._worker.core.env_rules [prepare-request])
(import doeff_cluster._worker.protocol.env_translation [compile-argv request-of-json])

(deftest test-the-compile-jobs-ride-the-request-into-the-compile-command-only-when-given
  ;; 失敗ケース(2026-10-08): 準備の頼みの並べる数(recreate の job の旧い process が動いている間の新しい版の準備 — 旧と同じ memory の上限を
  ;; 分け合う)は、env-host が書く頼みの JSON(compileJobs)→ 準備の process が読む PrepareRequest → 焼く道具の命令の `--jobs N` と運ぶ。
  ;; 持たない頼み(null)は命令に `--jobs` を足さず、道具の既定(cgroup の CPU の上限・天井 BAKE-JOBS-CEILING)のまま。直す前は頼みにも命令にも並べる数が無かった。
  (val env (RuntimeEnv :repos #((RepoCheckout :name "app" :url "file:///remotes/app.git" :commit (* "1" 40)))
                       :project (PythonProject :repo "app" :path "." :lock-sha256 (* "0" 64) :python "3.14")
                       :import-roots #("app/.")))
  (<- declared dict (runtime-env->json env))
  (val trees #((BytecodeTree :tree "/s/roots/k/app" :roots #(".") :declared True)))
  (for [jobs [2 None]]
    (<- body dict (prepare-request declared "k" "linux-x86_64" "/s/roots/k" #() 0 jobs))
    (assert (= (get body "compileJobs") jobs) body)
    (<- request PrepareRequest (request-of-json (json.loads (json.dumps body))))
    (assert (= request.compile-jobs jobs) request)
    (<- argv tuple (compile-argv "uv" "/w/code_prepare.hy" "/s/roots/k/app" trees #() request.compile-jobs ""))
    (val pairs (tuple (zip (cut argv 0 -1) (cut argv 1 None))))
    (if (is jobs None)
        (assert (not-in "--jobs" argv) argv)
        (assert (in #("--jobs" (str jobs)) pairs) argv))))


(deftest test-the-progress-mark-rides-into-the-compile-command-only-when-given
  ;; 失敗ケース(2026-10-26): 準備の process の進みの印は、bytecode の処理ステージの前と後にしか触られず、焼く道具が
  ;; disk の遅さで 600 秒を越えると、進んでいる準備が停滞として止められた。印の file を焼く道具へ `--progress <file>` で渡し、道具が
  ;; 閉包の一回りと焼きの前後に印を触る。印を書かない設定(空)は命令に足さない。
  (val trees #((BytecodeTree :tree "/s/roots/k/app" :roots #(".") :declared True)))
  (<- marked tuple (compile-argv "uv" "/w/code_prepare.hy" "/s/roots/k/app" trees #() None "/s/env-requests/k.progress"))
  (assert (in #("--progress" "/s/env-requests/k.progress") (tuple (zip (cut marked 0 -1) (cut marked 1 None)))) marked)
  (<- bare tuple (compile-argv "uv" "/w/code_prepare.hy" "/s/roots/k/app" trees #() None ""))
  (assert (not-in "--progress" bare) bare))
