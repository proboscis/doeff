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
(import doeff_hy._json_value [OpaqueJson])
(import doeff_cluster._shared.intent.cluster_control [RestartRefused])
(import doeff_cluster._shared.intent.service_model [Declaration])
(import doeff_cluster._shared.intent.detached_model [GroupLimit])
(import doeff_cluster._shared.core.detached_rules [group-limit-spec])


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
   資源の口(CLI は url・sim は path)・version = 読んだ resourceVersion(無い Service は None — 作る)・spec = 読んだ時の spec(無い
   Service は None — 409 の後の読み直しで、他の書き手が spec を書いたかを比べる)・body = 送る本文(作る時は POST の本文・書き直す時は
   版つきの PUT の本文・今の spec と同じで書かない時は None — 中は送る所が body-of で読む)。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ str name)
  (#^ str target)
  (#^ (| int str None) version)
  (#^ (| OpaqueJson None) spec)
  (#^ (| OpaqueJson None) body))


;; 書き直しの PUT が 409 を受けた後に読み直して書き直す回数の上限。読み直すのは「読んでから書くまでに状態の欄だけが変わった」時だけ
;; (coordinator は状態の欄の変化でも版を進める — 落ち続ける job は落ちるたびに版が進む)。上限を越えたら 409 のまま止まる。
(val CONFLICT-REREADS 3)


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
    (return (ServiceRead :name name :target target :version None :spec None :body (OpaqueJson.of (create-body row)))))
  (val now (get current "spec"))
  (val version (get current "resourceVersion"))
  (<- spec dict (spec-for-update row now))
  (when (= spec now)
    (return (ServiceRead :name name :target target :version version :spec (OpaqueJson.of now) :body None)))
  (<- body dict (update-body version spec))
  (ServiceRead :name name :target target :version version :spec (OpaqueJson.of now) :body (OpaqueJson.of body)))


;; --- 同じ版の起こし直し(RestartService)--------------------------------------------------------------
;; 起こし直しの印の環境変数の名。子の環境変数は spec の指紋(job_model.JobSpec.fingerprint)に入るので、値を 1 つ進めれば版が同じでも別の spec になり、
;; 入れ替えの形が handoff の job は新を旧と並べて起こす。名は子の環境変数の規則(EnvVar — 大文字と数字と _ ・予約の頭 DOEFF_ などの外)に合う。
(val RESTART-MARK "RESTART_MARK")


(defrecord RestartPlan
  "起こし直しの書き 1 つ: read = 送る書き(読んだ版つきの PUT — 409 の後の読み直しは reread-after-conflict が行う)・mark = 書く印の値。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ ServiceRead read)
  (#^ str mark))


(defk restarted-spec [spec]
  {:pre [(: spec dict)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "読んだ spec の環境変数 RESTART_MARK を 1 つ進めた spec を作るため(印が無い・数字でなければ 1 — 起こし直すたびに 1 ずつ増え、前の印と同じ値は
   書かない。印以外の spec は読んだまま)。"
  (val environ (.get spec "environ" {}))
  (val now (.get environ RESTART-MARK ""))
  (val mark (if (and (isinstance now str) (.isascii now) (.isdigit now)) (str (+ (int now) 1)) "1"))
  (| spec {"environ" (| environ {RESTART-MARK mark})}))


(defk restart-read [name target current]
  {:pre [(: name str) (: target str) (: current (| dict None))] :post [(: % (| RestartPlan RestartRefused))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "読んだ今の資源 current(GET の本文・無い Service は None)から、版を替えずに重ねて起こし直す書きを決めるため: spec の environ の RESTART_MARK を 1 つ
   進め、それ以外の spec は読んだまま読んだ版つきで書き直す。書かずに RestartRefused を返すのは、Service が無い・台数 0 の取り下げ・入れ替えの形が
   handoff でない時(recreate の job は旧を止めてから新を起こすので重ならず、印を書くと次の宣言し直しでもう 1 度起こし直される)。"
  (when (is current None)
    (return (RestartRefused :name name :reason "Service が無い")))
  (val spec (get current "spec"))
  (when (= (.get spec "replicas") 0)
    (return (RestartRefused :name name :reason "台数 0 の取り下げ — 動いていない Service は起こし直せない(動かすのは台数を 1 にして宣言し直す事)")))
  (when (!= (.get spec "update") "handoff")
    (return (RestartRefused :name name :reason (.format "入れ替えの形が {} — 重ねて起こし直せるのは handoff の Service だけ(recreate は止めてから起こす形)"
                                                         (.get spec "update")))))
  (<- written dict (restarted-spec spec))
  (val version (get current "resourceVersion"))
  (<- body dict (update-body version written))
  (RestartPlan :read (ServiceRead :name name :target target :version version :spec (OpaqueJson.of spec) :body (OpaqueJson.of body))
               :mark (get (get written "environ") RESTART-MARK)))


(defk reread-after-conflict [sent current]
  {:pre [(: sent ServiceRead) (: current (| dict None))] :post [(: % (| ServiceRead None))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "版つきの書き直し sent が 409 を受けた後に読み直した今の資源 current(GET の本文・消えていれば None)から、もう 1 度送る書きを決めるため
   (coordinator の 409 の文「読み直してから書く」): spec が sent を作った時に読んだ spec と同じなら、読んでから書くまでに変わったのは
   状態の欄だけ — 同じ spec を今の版で書き直す。spec が違う(他の書き手が書いた)か Service が消えていれば None(書き直さずに止まる —
   他の作業係の変更を消さない)。"
  (when (or (is current None) (!= (OpaqueJson.of (get current "spec")) sent.spec))
    (return None))
  (val version (get current "resourceVersion"))
  (<- written dict (body-of sent))
  (<- body dict (update-body version (get written "spec")))
  (ServiceRead :name sent.name :target sent.target :version version :spec sent.spec :body (OpaqueJson.of body)))


(defk needed-programs [declaration reads]
  {:pre [(: declaration Declaration) (: reads tuple)] :post [(: % (get tuple #(str ...)))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "書く Service(作る・書き直す)が名指す Program の sha だけを、置く順(昇順)に並べるため。書かない Service だけが名指す Program は
   置き直さない(その Service が今も名指すので coordinator の掃除に掛からない)。参照の切れた古い sha に戻す書き直しは書く Service なので
   置き直す(coordinator は同じ sha の置き直しで掃除の期限を延ばす — 行を書く前に掃かれない)。"
  (val written (sfor read reads :if (is-not read.body None) read.name))
  (tuple (sorted (sfor row declaration.rows :if (in (get row "name") written) (get (get row "run") "program")))))


(defk group-limit-write [limit current]
  {:pre [(: limit GroupLimit) (: current (| dict None))] :post [(: % (| dict None))]
   :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "系の宣言の組の上限 1 つを資源 GroupLimit へ書く本文を、読んだ今の資源 current(GET の本文・無ければ None)から決めるため(declare の
   CLI と手元の sim-cluster で同じ形 — 送り手は current が None なら POST /resources/GroupLimit・在れば PUT /resources/GroupLimit/<名>):
   無ければ作る本文 {name spec}・spec が今と同じなら None(書かない)・違えば読んだ版つきの本文 {resourceVersion spec}(読んでから書く
   までに誰かが書いていれば 409 で止まる — 資源 GroupLimit の版は spec の変化でだけ進むので、読み直して書き直さない)。"
  (val spec (group-limit-spec limit))
  (cond
    (is current None) {"name" limit.group "spec" spec}
    (= (get current "spec") spec) None
    True (! (update-body (get current "resourceVersion") spec))))


(deff create-body [#^ Mapping row]  ; defk にできない: CLI の入口(Program の外)と sim-cluster の宣言が同じ形を作る純粋な判断
  {:pre [(: row Mapping)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "まだ無い Service を作る POST /resources/Service の本文を作るため(declare の CLI と手元の sim-cluster で同じ形)。台数は行の値(job の
   :replicas)。"
  {"name" (get row "name")
   "spec" (dfor #(k v) (.items row) :if (!= k "name") k v)})
