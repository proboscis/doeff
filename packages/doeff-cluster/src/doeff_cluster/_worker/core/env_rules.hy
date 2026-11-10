;;; worker の実行環境の root の準備の判断 — 起こす順・準備の頼みの JSON と起こし方・準備の答えの読み・期限切れの理由・掃除の候補の形
;;; (handlers.hy の EnvStore から分けた・#2467)。I/O は呼び手(worker/protocol/env_store の env-host)が行う。
;;; 期限そのもの・掃除の選びと下限(roots の合計の上限・共有の disk の空きの最低 — #3732)・disk の条件は env_upkeep。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import json)
(import re)
(import doeff_cluster._shared.intent.runtime_env_model [EnvFailure EnvFailureKind])
(import doeff_cluster._worker.core.env_upkeep [PrepareLimits])

(val ANSWER-HEAD-CHARS 200)   ; 形の読めない答えの file の中身を失敗の理由に載せる長さ


(defrecord ReadyAnswer
  "準備の process が答えの file に完成(ready)を書いた、という答え(root = 完成を書いた root の path)。失敗の答えは EnvFailure で表す
   — 答えの file の中身を読むのは answer-of-text の 1 か所。"
  (#^ str root))


(defrecord PrepareRoom
  "準備の層の空きの読み(準備を層の下に置く worker だけが launch-order に渡す — 層の無い worker は None で、memory で絞らない):
   free = 層の memory.max − 層の今の回収できない使い − 走っている準備の見込みの山の和(byte・負もある。層を読めなければ None = 走っている
   準備が無い時だけ 1 本起こす)・needs = 待っている準備のキー → 見込みの山(byte — 無いキーは default-need)・alone = 1 本だけで起こし直す
   待ちのキー(層の memory の上限で殺された準備)・running-alone = 1 本だけで走っている準備が在るか。"
  (#^ (| int None) free)
  (#^ int default-need)
  (#^ dict needs)
  (setv #^ frozenset alone (frozenset))
  (setv #^ bool running-alone False))


(defk launch-order [waiting running warm-running max-parallel [room None]]
  {:pre [(: waiting tuple) (: running int) (: warm-running int) (: max-parallel int) (: room (| PrepareRoom None))] :post [(: % tuple)]}
  "待っている準備(#(キー 先読みか) の列・頼まれた順)のうち、今起こすキーを起こす順に返すため(running = 走っている準備の数・
   warm-running = そのうち先読みの数)。同時の準備は max-parallel 本まで(走っている手番の CPU を奪わない)。job の準備を先に起こし、
   先読みは枠の 1 つを job に残す。
   room(準備の層の空き — PrepareRoom)が在れば、層の memory でも絞る: 次の準備は、空き free がその見込みの山以上の時だけ起こし、起こした
   分の山を空きから引く。足りない準備は断らずに待ちに残し、後ろの準備も追い越さない(大きい準備が小さい準備に先を取られ続けない)。
   空きを読めない層では、走っている準備が無い時の 1 本だけ。1 本だけで起こし直すキー(alone)は走っている準備が無い時だけ起こし、それが
   走っている間は何も起こさない。CPU の枠だけで決めると、1 本分の層に 2 本が並んで互いを OOM kill で止め合った(2026-11-09 の node-f —
   準備の層 640M に 1 本 約 360 MB の準備が 2 本)。"
  (val order (+ (lfor #(k w) waiting :if (not w) #(k w)) (lfor #(k w) waiting :if w #(k w))))
  (var launched #())
  (var busy running)
  (var warm-busy warm-running)
  (var free (if (is room None) None room.free))
  (when (and (is-not room None) room.running-alone)
    (return #()))
  (for [#(key warm) order]
    (when (>= busy max-parallel) (break))
    (when (and warm (>= warm-busy (max 1 (- max-parallel 1)))) (continue))
    (when (is-not room None)
      (val need (.get room.needs key room.default-need))
      (when (and (in key room.alone) (> busy 0)) (break))
      (when (if (is free None) (> busy 0) (< free need)) (break))
      (when (is-not free None) (:= free (- free need))))
    (:= launched (+ launched #(key)))
    (:= busy (+ busy 1))
    (when warm (:= warm-busy (+ warm-busy 1)))
    (when (and (is-not room None) (in key room.alone)) (break)))
  launched)


(defk prepare-request [declared name platform root known min-free-bytes compile-jobs]
  {:pre [(: declared dict) (: name str) (: platform str) (: root str) (: known tuple) (: min-free-bytes int) (: compile-jobs (| int None))]
   :post [(: % dict)]}
  "準備の process(env_handlers)へ渡す頼みの JSON の中身を組むため(name = env- を外したキー・root = 作る root の path・compile-jobs =
   bytecode を焼く道具の並べる数 — null = 道具の既定・2026-10-08)。"
  {"env" declared "key" name "platform" platform "root" root "known" (list known) "minFreeBytes" min-free-bytes
   "compileJobs" compile-jobs})


(defk lowered-priority-argv [platform]
  {:pre [(: platform str)] :post [(: % tuple)]}
  "準備の process の CPU の優先度を下げる命令の頭を組むため(platform = この worker の platform)。Linux は SCHED_IDLE(chrt --idle)と
   nice 10、他(macOS の worker — chrt が無い)は nice 10 だけ。SCHED_IDLE は同じ Pod の service(SCHED_OTHER・nice 0)が起きた時に
   必ず譲り、重みも 3 なので、焼きを何本並べても service の CPU の取り分が並べた本数で決まらない(
   nice 10 の焼き 31 本・20 core の窓で service の和の CPU の待ちが 59%、前後の窓は 15〜16%)。"
  (if (.startswith platform "linux")
      #("chrt" "--idle" "0" "nice" "-n" "10")
      #("nice" "-n" "10")))


(defk prepare-argv [hy-command tool request result state shared uv-cache repo-keys code-prepare uv progress platform memory]
  {:pre [(: hy-command str) (: tool str) (: request str) (: result str) (: state str) (: shared str) (: uv-cache str) (: repo-keys str)
         (: code-prepare str) (: uv str) (: progress str) (: platform str) (: memory str)]
   :post [(: % tuple)]}
  "準備の process の起こし方(優先度を下げる — 走っている手番と service の CPU を奪わない・lowered-priority-argv)を組むため。
   state = 世代ごとの state の dir・shared = 世代をまたいで分け合う dir(Python・wheel・mirror と錠 — AL-D2)・platform = この worker の
   platform・memory = worker が組みの間の層の読みを置く file(準備の process が組みの山に読む — env_store の sample-layers)。"
  (<- lowered tuple (lowered-priority-argv platform))
  #(#* lowered hy-command "-m" tool "--request" request "--result" result "--state" state "--shared" shared "--uv-cache" uv-cache
    "--repo-keys" repo-keys
    "--code-prepare" code-prepare "--uv" uv "--progress" progress "--memory" memory))


(defk answer-of-text [text]
  {:pre [(: text str)] :post [(: % (| EnvFailure ReadyAnswer))] :tags {:context "worker" :role "judgment" :reads "json"}}
  "準備の process が書いた答えの file の中身(env_translation の answer-json の JSON)を答えの型にするため — 終わった準備の結末
   (prepare-outcome)と、走っている準備を期限で止めるか(env_store の observe-envs)が同じ読みを使う。失敗を先に読む。形の読めない
   中身は、実行環境が合わない失敗(やり直さない)として中身の頭を添える(完成とは読まない)。"
  (val data (try (json.loads text) (except [ValueError] None)))
  (match data
    {"failure" {"kind" kind "detail" detail "retryable" retryable}}
      (EnvFailure :kind (EnvFailureKind kind) :detail detail :retryable retryable)
    {"ready" {"root" root}} (ReadyAnswer :root root)
    _ (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :retryable False
                  :detail (.format "準備の答えの file の形を読めない: {}" (cut text 0 ANSWER-HEAD-CHARS)))))


(val OOM-KILL-LINE (re.compile r"(?m)^oom_kill (\d+)$"))   ; cgroup v2 の memory.events の oom_kill の行


(defk oom-kills-of [events]
  {:pre [(: events str)] :post [(: % int) (>= % 0)]}
  "準備の子の cgroup の memory.events の中身(読めなければ空)から、子の下の process が memory の上限で殺された数を読むため(行が無ければ 0)。"
  (val found (.search OOM-KILL-LINE events))
  (if found (int (.group found 1)) 0))


(defk memory-killed-failure [kills exit-code log]
  {:pre [(: kills int) (: exit-code int) (: log str)] :post [(: % EnvFailure)]}
  "1 本だけで起こし直した準備が、なお準備の層の memory の上限で殺された失敗を返すため(memory-killed — 層の上限の下では組めない。
   env-incompatible と読まない)。"
  (EnvFailure :kind EnvFailureKind.MEMORY-KILLED :retryable False
              :detail (.format "準備の層に 1 本だけで起こした準備が memory の上限で殺された(oom_kill {}・終了 {})— log: {}"
                               kills exit-code log)))


(defk prepare-outcome [answer exit-code log]
  {:pre [(: answer (| EnvFailure ReadyAnswer None)) (: exit-code int) (: log str)] :post [(: % (| EnvFailure None))]}
  "終わった準備の答え(answer-of-text で読んだ物 — 答えの file が無い・読めなければ None)から、失敗なら理由を返すため(完成なら None)。
   答えを書かずに終わった process は、実行環境が合わない失敗(やり直さない)として log の在処を添える。"
  (match answer
    (EnvFailure) answer
    (ReadyAnswer) None
    _ (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :retryable False
                  :detail (.format "準備の process が答えを書かずに終わった(終了 {})— log: {}" exit-code log))))


(val STALL-TAIL-CHARS 600)    ; 止めた準備の log の末尾のうち、失敗の文へ載せる長さ


(defk overdue-failure [warm limits stage log log-tail]
  {:pre [(: warm bool) (: limits PrepareLimits) (: stage str) (: log str) (: log-tail str)] :post [(: % EnvFailure)]}
  "期限を過ぎて止めた準備の失敗(やり直してよい)を返すため。先読みも job の準備も、進みの印が stall-seconds 動かなかった準備
   (warm = 先読みの準備か — 失敗の文で名指す)。どこで止まったか読めるように、最後に進んだ処理ステージ(stage — 進みの印の file の
   中身・空なら「不明」)・log の在処・log の末尾(log-tail — 空なら載せない)を失敗の文へ添える(理由の行が無いと、止まった処理が
   分からなかった)。"
  (val seen (.strip stage))
  (val tail (.strip log-tail))
  (EnvFailure :kind EnvFailureKind.PREPARE-TIMEOUT :retryable True
              :detail (.format "{}準備が {} 秒進まない(止めた)— 最後に進んだ処理: {}・log: {}{}"
                               (if warm "先読みの" "") limits.stall-seconds (or seen "不明") log
                               (if tail (+ "・log の末尾: " (cut tail (- STALL-TAIL-CHARS) None)) ""))))


(defk root-project [marker]
  {:pre [(: marker dict)] :post [(: % str)]}
  "完成マーカーの宣言から、同じ project の組の名(project の repo の url と path)を返すため(掃除で project ごとの最新を残す)。"
  (<- named str (declared-project (get marker "env")))
  named)


(defk declared-project [declared]
  {:pre [(: declared dict)] :post [(: % str)]}
  "宣言の JSON(runtimeEnv の形)から、同じ project の組の名(project の repo の url と path)を返すため(完成マーカーの宣言と、先の組みの
   頼みの宣言 — #3748 の見積もりが同じ project の root を引く — が同じ綴りで読む)。"
  (val project (get declared "project"))
  (.format "{}:{}" (next (gfor r (get declared "repos") :if (= (get r "name") (get project "repo")) (get r "url")) "")
           (get project "path")))

