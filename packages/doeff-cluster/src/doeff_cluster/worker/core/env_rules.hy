;;; worker の実行環境の root の準備の判断 — 起こす順・準備の頼みの JSON と起こし方・準備の答えの読み・期限切れの理由・掃除の候補の形
;;; (handlers.hy の EnvStore から分けた・#2467)。I/O は呼び手(worker/protocol/env_store の env-host)が行う。
;;; 期限そのもの・掃除の選びと下限(roots の合計の上限・共有の disk の空きの最低 — #3732)・disk の条件は env_upkeep。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import json)
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailure EnvFailureKind])
(import doeff_cluster.worker.core.env_upkeep [PrepareLimits])

(val ANSWER-HEAD-CHARS 200)   ; 形の読めない答えの file の中身を失敗の理由に載せる長さ


(defrecord ReadyAnswer
  "準備の process が答えの file に完成(ready)を書いた、という答え(root = 完成を書いた root の path)。失敗の答えは EnvFailure で表す
   — 答えの file の中身を読むのは answer-of-text の 1 か所。"
  (#^ str root))


(defk launch-order [waiting running warm-running max-parallel]
  {:pre [(: waiting tuple) (: running int) (: warm-running int) (: max-parallel int)] :post [(: % tuple)]}
  "待っている準備(#(キー 先読みか) の列・頼まれた順)のうち、今起こすキーを起こす順に返すため(running = 走っている準備の数・
   warm-running = そのうち先読みの数)。同時の準備は max-parallel 本まで(走っている手番の CPU を奪わない)。job の準備を先に起こし、
   先読みは枠の 1 つを job に残す。"
  (val order (+ (lfor #(k w) waiting :if (not w) #(k w)) (lfor #(k w) waiting :if w #(k w))))
  (var launched #())
  (var busy running)
  (var warm-busy warm-running)
  (for [#(key warm) order]
    (when (>= busy max-parallel) (break))
    (when (and warm (>= warm-busy (max 1 (- max-parallel 1)))) (continue))
    (:= launched (+ launched #(key)))
    (:= busy (+ busy 1))
    (when warm (:= warm-busy (+ warm-busy 1))))
  launched)


(defk prepare-request [declared name platform root known min-free-bytes compile-jobs]
  {:pre [(: declared dict) (: name str) (: platform str) (: root str) (: known tuple) (: min-free-bytes int) (: compile-jobs (| int None))]
   :post [(: % dict)]}
  "準備の process(env_handlers)へ渡す頼みの JSON の中身を組むため(name = env- を外したキー・root = 作る root の path・compile-jobs =
   bytecode を焼く道具の並べる数 — null = 道具の既定・2026-10-08)。"
  {"env" declared "key" name "platform" platform "root" root "known" (list known) "minFreeBytes" min-free-bytes
   "compileJobs" compile-jobs})


(defk prepare-argv [hy-command tool request result state uv-cache repo-keys code-prepare uv progress]
  {:pre [(: hy-command str) (: tool str) (: request str) (: result str) (: state str) (: uv-cache str) (: repo-keys str) (: code-prepare str)
         (: uv str) (: progress str)]
   :post [(: % tuple)]}
  "準備の process の起こし方(nice で優先度を下げる — 走っている手番の CPU を奪わない)を組むため。"
  #("nice" "-n" "10" hy-command "-m" tool "--request" request "--result" result "--state" state "--uv-cache" uv-cache "--repo-keys" repo-keys
    "--code-prepare" code-prepare "--uv" uv "--progress" progress))


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


(defk prepare-outcome [answer exit-code log]
  {:pre [(: answer (| EnvFailure ReadyAnswer None)) (: exit-code int) (: log str)] :post [(: % (| EnvFailure None))]}
  "終わった準備の答え(answer-of-text で読んだ物 — 答えの file が無い・読めなければ None)から、失敗なら理由を返すため(完成なら None)。
   答えを書かずに終わった process は、実行環境が合わない失敗(やり直さない)として log の在処を添える。"
  (match answer
    (EnvFailure) answer
    (ReadyAnswer) None
    _ (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :retryable False
                  :detail (.format "準備の process が答えを書かずに終わった(終了 {})— log: {}" exit-code log))))


(defk overdue-failure [warm limits]
  {:pre [(: warm bool) (: limits PrepareLimits)] :post [(: % EnvFailure)]}
  "期限を過ぎて止めた準備の失敗(やり直してよい)を返すため。先読みも job の準備も、進みの印が stall-seconds 動かなかった準備
   (warm = 先読みの準備か — 失敗の文で名指す)。"
  (EnvFailure :kind EnvFailureKind.PREPARE-TIMEOUT :retryable True
              :detail (.format "{}準備が {} 秒進まない(止めた)" (if warm "先読みの" "") limits.stall-seconds)))


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

