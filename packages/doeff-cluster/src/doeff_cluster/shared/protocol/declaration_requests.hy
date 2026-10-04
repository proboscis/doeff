;;; coordinator の資源の口へ宣言の行を書く要求の本文の形(declare の CLI と手元の sim-cluster で同じ形 — declare から分けた・#2346)。
;;; 宣言し直しは差分だけを送る(#3115): Service を全部読んでから、書く Service(無い・spec が変わった)が名指す Program だけを置き、
;;; 書く Service だけを書く。spec の変わらない Service には書きを送らない(coordinator も同じ行は版を進めないが、送れば要求 1 本ぶんの
;;; 往復がかかる)。戻し方 = service-read が spec を常に返す形に戻す(全部の Service を書き、全部の Program を置く)。
(require doeff-hy.macros [defk deff val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
;; 宣言の行(service_model の Declaration.rows — 型は .pyi の TypedDict)を読むだけなので、写像の読みの口 Mapping で受ける。
(import collections.abc [Mapping])
(import json)
(import doeff_hy.json_value [OpaqueJson])
(import doeff_cluster.shared.intent.service_model [Declaration])


(defk spec-for-update [row current]
  {:pre [(: row Mapping) (: current dict)] :post [(: % dict)]
   :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "在る Service を書き直す PUT の spec を宣言の行から作るため(declare の CLI と手元の sim-cluster で同じ形)。台数は行の値(job の
   :replicas — #3487)を書く。所有者と、readiness の無い行の readiness はいまの資源の値を保つ。"
  (val spec (dfor #(k v) (.items row) :if (!= k "name") k v))
  (| {"readiness" (.get current "readiness")}
     spec
     {"owner" (.get current "owner")}))


(defrecord ServiceRead
  "宣言し直しの前に Service 1 つを読んだ結果と、送る書き(declare の CLI と手元の sim-cluster で同じ形): name = Service の名・target = 書く
   資源の口(CLI は url・sim は path)・version = 読んだ resourceVersion(無い Service は None — 作る)・body = 送る本文(作る時は POST の
   本文・書き直す時は版つきの PUT の本文・今の spec と同じで書かない時は None — 中は送る所が body-of で読む)。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ str name)
  (#^ str target)
  (#^ (| int str None) version)
  (#^ (| OpaqueJson None) body))


(defk update-body [version spec]
  {:pre [(: version (| int str)) (: spec dict)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "在る Service を書き直す PUT の本文を作るため(読んだ版を付ける — 読んでから書くまでに誰かが書いていれば 409 で止まる)。"
  {"resourceVersion" version "spec" spec})


(defk body-of [read]
  {:pre [(: read ServiceRead)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "読んだ Service に送る本文を、資源の口へ渡す JSON の object に戻すため(書かない Service に呼ぶのは呼び手の誤り)。"
  (when (is read.body None)
    (raise (ValueError (+ "書かない Service に本文は無い: " read.name))))
  (json.loads read.body.text))


(defk service-read [name row target current]
  {:pre [(: name str) (: row Mapping) (: target str) (: current (| dict None))] :post [(: % ServiceRead)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "読んだ今の資源 current(GET の本文・無い Service は None)から、その Service に送る書きを決めるため: 無ければ作る・書き直す spec が
   今の spec と同じなら書かない・違えば読んだ版を付けて書き直す(台数が行の値と違う時も書き直す — 宣言し直しは台数を job の値へ戻す)。"
  (when (is current None)
    (return (ServiceRead :name name :target target :version None :body (OpaqueJson.of (create-body row)))))
  (val now (get current "spec"))
  (val version (get current "resourceVersion"))
  (<- spec dict (spec-for-update row now))
  (when (= spec now)
    (return (ServiceRead :name name :target target :version version :body None)))
  (<- body dict (update-body version spec))
  (ServiceRead :name name :target target :version version :body (OpaqueJson.of body)))


(defk needed-programs [declaration reads]
  {:pre [(: declaration Declaration) (: reads tuple)] :post [(: % (get tuple #(str ...)))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "書く Service(作る・書き直す)が名指す Program の sha だけを、置く順(昇順)に並べるため。書かない Service だけが名指す Program は
   置き直さない(その Service が今も名指すので coordinator の掃除に掛からない)。参照の切れた古い sha に戻す書き直しは書く Service なので
   置き直す(coordinator は同じ sha の置き直しで掃除の期限を延ばす — 行を書く前に掃かれない)。"
  (val written (sfor read reads :if (is-not read.body None) read.name))
  (tuple (sorted (sfor row declaration.rows :if (in (get row "name") written) (get (get row "run") "program")))))


(deff create-body [#^ Mapping row]  ; defk にできない: CLI の入口(Program の外)と sim-cluster の宣言が同じ形を作る純粋な判断
  {:pre [(: row Mapping)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "まだ無い Service を作る POST /resources/Service の本文を作るため(declare の CLI と手元の sim-cluster で同じ形)。台数は行の値(job の
   :replicas)。"
  {"name" (get row "name")
   "spec" (dfor #(k v) (.items row) :if (!= k "name") k v)})
