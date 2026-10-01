;;; coordinator の盤(/board)と lease の口(/leases)へ送る要求の形 #(method path query 本文)。本番の client(shared_handlers.hy の
;;; shared-http)と手元の sim-cluster の偽の宿(sim/local.hy)が同じ関数で作る(本文を写さない)。
;;; 盤の行の値は形を書き手が決める JSON なので、本文へ組むこの module が JSON の値を扱う送受信の口になる(DOEFF120 の :wire-modules —
;;; agora-redesign #2527。前は shared_handlers.hy の中に在った)。書きの条件の印 ANY(intent の語彙)はここへ持ち込まず、呼び手が
;;; 「条件を付けるか」の真偽に訳して渡す。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import urllib.parse [quote :as url-quote])
(import doeff_hy.json_value [JsonValue])


(deff board-read-request [#^ str prefix]  ; defk にできない: 本番の client(Program の外の I/O の道具)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: prefix str)] :post [(: % tuple) (= (len %) 4)] :tags {:context "doeff-cluster" :role "foundation" :spells "http"}}
  "ReadShared を coordinator の盤の読みの要求 #(method path query 本文) にするため(本番の shared-http と sim の宿で同じ形)。"
  #("GET" "/board" {"prefix" prefix} None))


(deff board-write-request [#^ str key value #^ bool conditioned expect #^ (| int float None) ttl-seconds]  ; defk にできない: 本番の client と sim の宿が同じ形を作る純粋な判断
  {:pre [(: key str) (: value JsonValue) (: conditioned bool) (: expect JsonValue)
         (: ttl-seconds (| int float None))]
   :post [(: % tuple) (= (len %) 4)] :tags {:context "doeff-cluster" :role "foundation" :spells "http"}}
  "WriteShared を盤の compare-and-set の要求 #(method path query 本文) にするため。expect の 3 値を JSON で運ぶ: 欄が無い(conditioned が偽 —
   WriteShared の expect が ANY)= 無条件・null = 行が無い時だけ・値 = その値の時だけ。答えの読みは 409 = 偽(合わなかった)・300 未満 = 真。"
  #("PUT" (+ "/board/" key) {}
    (| {"value" value}
       (if conditioned {"expect" expect} {})
       (if (is ttl-seconds None) {} {"ttlSeconds" ttl-seconds}))))


(deff lease-request [#^ str name #^ str op #^ str token #^ int permits #^ int ttl-ms]  ; defk にできない: 本番の client と sim の宿が同じ形を作る純粋な判断
  {:pre [(: name str) (: op str) (: token str) (: permits int) (: ttl-ms int)] :post [(: % tuple) (= (len %) 4)]
   :tags {:context "doeff-cluster" :role "foundation" :spells "http"}}
  "LeaseOp を coordinator の lease の口の要求 #(method path query 本文) にするため(本番の shared-http と sim の宿で同じ形)。"
  #("POST" (+ "/leases/" (url-quote name :safe "")) {} {"op" op "token" token "permits" permits "ttlMs" ttl-ms}))
