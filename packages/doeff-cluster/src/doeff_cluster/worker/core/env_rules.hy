;;; worker の実行環境の root の準備の判断 — 起こす順・準備の頼みの JSON と起こし方・準備の答えの読み・期限切れの理由・掃除の候補の形・
;;; 掃除の下限(handlers.hy の EnvStore から分けた・#2467)。I/O は呼び手(EnvStore — 後に worker/protocol の言い換え)が行う。
;;; 期限そのもの・掃除の選び・disk の条件は env_upkeep。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailure EnvFailureKind])
(import doeff_cluster.worker.core.env_upkeep [PrepareLimits SWEEP-FLOOR-RATIO])


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


(defk cold-for [declared known]
  {:pre [(: declared dict) (: known tuple)] :post [(: % bool)]}
  "準備が冷たいか(同じ lock と Python の完成した root が無い = 依存も bytecode も引き継げない)を決めるため(declared = 実行環境の宣言・
   known = 完成した root の列 {\"env\" 宣言 \"root\" path})。job の準備の期限を分けるため。"
  (val project (get declared "project"))
  (not (any (gfor k known
                  (and (= (get k "env" "project" "lockSha256") (get project "lockSha256"))
                       (= (get k "env" "project" "python") (get project "python")))))))


(defk prepare-request [declared name platform root known min-free-bytes]
  {:pre [(: declared dict) (: name str) (: platform str) (: root str) (: known tuple) (: min-free-bytes int)]
   :post [(: % dict)]}
  "準備の process(env_handlers)へ渡す頼みの JSON の中身を組むため(name = env- を外したキー・root = 作る root の path)。"
  {"env" declared "key" name "platform" platform "root" root "known" (list known) "minFreeBytes" min-free-bytes})


(defk prepare-argv [hy-command tool request result state repo-keys code-prepare uv progress]
  {:pre [(: hy-command str) (: tool str) (: request str) (: result str) (: state str) (: repo-keys str) (: code-prepare str) (: uv str)
         (: progress str)]
   :post [(: % tuple)]}
  "準備の process の起こし方(nice で優先度を下げる — 走っている手番の CPU を奪わない)を組むため。"
  #("nice" "-n" "10" hy-command "-m" tool "--request" request "--result" result "--state" state "--repo-keys" repo-keys
    "--code-prepare" code-prepare "--uv" uv "--progress" progress))


(defk prepare-outcome [answer exit-code log]
  {:pre [(: answer (| dict None)) (: exit-code int) (: log str)] :post [(: % (| EnvFailure None))]}
  "終わった準備の答え(答えの file の JSON — 読めなければ None)を読み、失敗なら理由を返すため(成功なら None)。答えを書かずに終わった
   process は、実行環境が合わない失敗(やり直さない)として log の在処を添える。"
  (cond
    (and answer (in "failure" answer))
      (EnvFailure :kind (EnvFailureKind (get answer "failure" "kind")) :detail (get answer "failure" "detail")
                  :retryable (get answer "failure" "retryable"))
    (and answer (in "ready" answer)) None
    True
      (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :retryable False
                  :detail (.format "準備の process が答えを書かずに終わった(終了 {})— log: {}" exit-code log))))


(defk overdue-failure [warm cold limits]
  {:pre [(: warm bool) (: cold bool) (: limits PrepareLimits)] :post [(: % EnvFailure)]}
  "期限を過ぎて止めた準備の失敗(やり直してよい)を返すため。先読みは停滞・job の準備は冷たい / 温いの期限。"
  (EnvFailure :kind EnvFailureKind.PREPARE-TIMEOUT :retryable True
              :detail (if warm
                          (.format "先読みの準備が {} 秒進まない(止めた)" limits.stall-seconds)
                          (.format "準備が期限({})を過ぎた(止めた)" (if cold "冷たい" "温い")))))


(defk root-project [marker]
  {:pre [(: marker dict)] :post [(: % str)]}
  "完成マーカーの宣言から、同じ project の組の名(project の repo の url と path)を返すため(掃除で project ごとの最新を残す)。"
  (val declared (get marker "env"))
  (val project (get declared "project"))
  (.format "{}:{}" (next (gfor r (get declared "repos") :if (= (get r "name") (get project "repo")) (get r "url")) "")
           (get project "path")))


(defk floor-bytes [sweep-floor-bytes min-free-bytes total]
  {:pre [(: sweep-floor-bytes (| int None)) (: min-free-bytes int) (: total int)] :post [(: % int)]}
  "掃除を始める空きの下限を決めるため(設定の値が在ればそれ・無ければ volume の SWEEP-FLOOR-RATIO と準備を始める空きの大きい方)。"
  (if (is-not sweep-floor-bytes None)
      sweep-floor-bytes
      (max min-free-bytes (int (* SWEEP-FLOOR-RATIO total)))))
