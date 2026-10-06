;;; coordinator の盤(/board)と lease の口(/leases)へ送る要求の形 #(method path query 本文)。本番の client(同じ層の shared_handlers.hy の
;;; shared-http)と手元の sim-cluster の偽の宿(sim/local.hy)が同じ関数で作る(本文を写さない)。
;;; 層 protocol に置く(#2979 — 前は foundation/ に在った): 使い手の shared-http が protocol に在り、protocol は foundation を読めない。
;;; この module は標準の library と doeff_hy.json_value だけを読む。
;;; 盤の行の値は形を書き手が決める JSON なので、本文へ組むこの module が JSON の値を扱う送受信の口になる(DOEFF120 の :wire-modules —
;;; 前は shared_handlers.hy の中に在った)。書きの条件の印 ANY(intent の語彙)はここへ持ち込まず、呼び手が
;;; 「条件を付けるか」の真偽に訳して渡す。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import json)
(import urllib.parse [quote :as url-quote])
(import doeff_hy.json_value [OpaqueJson])


(deff board-read-request [#^ str prefix]  ; defk にできない: 本番の client(Program の外の I/O の道具)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: prefix str)] :post [(: % tuple) (= (len %) 4)] :tags {:context "doeff-cluster" :role "protocol" :spells "http"}}
  "ReadShared を coordinator の盤の読みの要求 #(method path query 本文) にするため(本番の shared-http と sim の宿で同じ形)。"
  #("GET" "/board" {"prefix" prefix} None))


(deff board-write-request [#^ str key #^ OpaqueJson value #^ bool conditioned #^ (| OpaqueJson None) expect #^ (| int float None) ttl-seconds]  ; defk にできない: 本番の client と sim の宿が同じ形を作る純粋な判断
  {:pre [(: key str) (: value OpaqueJson) (: conditioned bool) (: expect (| OpaqueJson None))
         (: ttl-seconds (| int float None))]
   :post [(: % tuple) (= (len %) 4)] :tags {:context "doeff-cluster" :role "protocol" :spells "http"}}
  "WriteShared を盤の compare-and-set の要求 #(method path query 本文) にするため。値と expect の値は OpaqueJson で受け、ここで JSON の値へ
   戻して本文に置く(#2543 — 盤は解いた値で比べるので、欄の順が違うだけの expect も合う)。expect の 3 値を JSON で運ぶ: 欄が無い
   (conditioned が偽 — WriteShared の expect が ANY)= 無条件・null(expect が None)= 行が無い時だけ・値 = その値の時だけ。
   答えの読みは 409 = 偽(合わなかった)・300 未満 = 真。"
  #("PUT" (+ "/board/" key) {}
    (| {"value" (json.loads value.text)}
       (if conditioned {"expect" (if (is expect None) None (json.loads expect.text))} {})
       (if (is ttl-seconds None) {} {"ttlSeconds" ttl-seconds}))))


(deff lease-request [#^ str name #^ str op #^ str token #^ int permits #^ int ttl-ms]  ; defk にできない: 本番の client と sim の宿が同じ形を作る純粋な判断
  {:pre [(: name str) (: op str) (: token str) (: permits int) (: ttl-ms int)] :post [(: % tuple) (= (len %) 4)]
   :tags {:context "doeff-cluster" :role "protocol" :spells "http"}}
  "LeaseOp を coordinator の lease の口の要求 #(method path query 本文) にするため(本番の shared-http と sim の宿で同じ形)。"
  #("POST" (+ "/leases/" (url-quote name :safe "")) {} {"op" op "token" token "permits" permits "ttlMs" ttl-ms}))


(deff lease-wait-request [#^ str name]  ; defk にできない: 本番の client と sim の宿が同じ形を作る純粋な判断
  {:pre [(: name str)] :post [(: % tuple) (= (len %) 4)] :tags {:context "doeff-cluster" :role "protocol" :spells "http"}}
  "AwaitLeaseFree を coordinator の待ちの口の要求 #(method path query 本文) にするため(本番の shared-http と sim の宿で同じ形)。
   timeoutSeconds は付けない — coordinator が自分の待ちの上限(ClusterTiming.watch-max-ms)で返す。"
  #("GET" "/watch" {"lease" name} None))


(deff lease-wait-answer [#^ dict answered]  ; defk にできない: 本番の client と sim の宿が同じ読みをする純粋な判断
  {:pre [(: answered dict)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "GET /watch?lease=<名> の返事の本文 {\"revision\" … \"changed\" …} を、空きを見たか(changed)にするため。形が違えば ValueError。"
  (if (isinstance (.get answered "changed") bool)
      (get answered "changed")
      (raise (ValueError (.format "lease の待ちの返事の形が違う: {!r}" answered)))))
