;;; 期限の答え(intent/due_model の DueAt・DueNow・DueNever)を作る・合わせる純粋な判断(#3865)。
;;; 期限の関数(cluster_policy の liveness-due・task-due・sweep-due・api_policy の tick-due・idle_policy の rollout-due)が使う。
(require doeff-hy.macros [defk deff val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import functools [reduce])
(import doeff_cluster.coordinator.intent.due_model [DueAt DueNow DueNever])


(defk due-of-instants [now instants]
  {:pre [(: now int) (: instants tuple)] :post [(: % (| DueAt DueNow DueNever))] :tags {:context "coordinator" :role "judgment"}}
  "判断が比べる期限の刻の列 instants(epoch ms)から、期限の答えを作るため: どれかが now 以前なら今すぐ(期限が過ぎているのに状態が
   まだ変わっていない — 落ち着いていない)、無ければ最も早い刻、列が空なら無し。"
  (cond
    (not instants) (DueNever)
    (any (gfor at instants (<= at now))) (DueNow)
    True (DueAt :at (min instants))))


(deff earlier-of [#^ (| DueAt DueNow DueNever) first #^ (| DueAt DueNow DueNever) second]  ; defk にできない: earliest-due の reduce(Program の外)が呼ぶ純粋な比べ
  {:pre [(: first (| DueAt DueNow DueNever)) (: second (| DueAt DueNow DueNever))] :post [(: % (| DueAt DueNow DueNever))]
   :tags {:context "coordinator" :role "judgment"}}
  "期限の答え 2 つのうち、先に判断を求める方を選ぶため: 今すぐは刻より先、刻どうしは早い方、無しはもう片方に譲る。"
  (match first
    (DueNow) first
    (DueAt :at at) (match second
                     (DueNow) second
                     (DueAt :at other) (if (<= at other) first second)
                     (DueNever) first)
    (DueNever) second))


(defk earliest-due [dues]
  {:pre [(: dues tuple)] :post [(: % (| DueAt DueNow DueNever))] :tags {:context "coordinator" :role "judgment"}}
  "判断ごとの期限の答えの列 dues を 1 つに合わせるため: どれかが今すぐなら今すぐ、刻が在れば最も早い刻、全部が無しなら無し。"
  (reduce earlier-of dues (DueNever)))
