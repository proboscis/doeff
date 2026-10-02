;;; coordinator の詰めた Program の置き場(ADR-DOE-CLUSTER-001 R3b・改訂 1 の F — 純粋な判断・I/O はしない)。
;;;
;;;   PUT /programs/<sha>  {"blob" 詰めた Program の文字列 "versions" 詰めた送り手の版} → 置く(同じキーは同じ中身 — 何度でも同じ意味)
;;;   GET /programs/<sha>  → {"blob" "versions"}(無ければ 404)
;;;
;;; 宣言の行(Service)・task の本文と行(POST /tasks・PUT /detached)・heartbeat の返事は sha だけを運び、worker が取って cache に置く。
;;; service と task で運び方を分けない(operator 2026-09-27 "i dont find any reason to have different api for services")。Program は
;;; 大きくなりうるので、宣言の行と毎拍の返事に載せない。キーは中身の sha256(remote_model.program-sha — 置く時に確かめる)。
;;; 参照(受け付けた Service の行と task の行 — 終わって結果を保持している task も含む)の無くなった Program は、置いてから
;;; PROGRAM-GRACE-MS を過ぎたら掃除する(送り手は Program を先に置いてから行を書くので、その間に消さない)。
(require doeff-hy.macros [deff])
(import dataclasses [replace])
(import re)
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ProgramRow ProgramStored ErrorReply])
(import doeff_cluster.shared.core.remote_rules [program-sha])
(import doeff_cluster.coordinator.intent.request_bodies [ProgramBody])

(setv PROGRAM-KEY (re.compile r"[0-9a-f]{64}"))
(setv PROGRAM-MAX-BYTES (* 4 1024 1024))       ; 詰めた Program 1 つの上限(base64 の文字列の長さ)
(setv PROGRAM-GRACE-MS (* 10 60 1000))          ; 参照の無い Program を残す長さ(置いてから)


(deff program-write [#^ ClusterState state #^ str sha #^ ProgramBody body #^ int now]  ; defk にできない: coordinator の要求の振り分け(Program の外の純粋な判断)が呼ぶ
  {:pre [(: state ClusterState) (: sha str) (: body ProgramBody) (: now int)] :post [(: % tuple) (= (len %) 3)]
   :tags {:context "coordinator" :role "judgment"}}
  "PUT /programs/<sha>: キーの形と大きさと中身の sha256 を確かめて置き、#(次の状態 status 本文) を返す。同じキーを置き直すと期限だけ
   延びる。本文の欄の型は解く所(coordinator/protocol/request_bodies — #2445)が検めた。"
  (setv blob body.blob versions (or body.versions {}))
  (cond
    (not (PROGRAM-KEY.fullmatch sha)) #(state 400 (ErrorReply :message (.format "キーは 64 桁の sha256: {!r}" sha)))
    (> (len blob) PROGRAM-MAX-BYTES) #(state 413 (ErrorReply :message (.format "詰めた Program が上限 {} byte を越える" PROGRAM-MAX-BYTES)))
    (!= (program-sha blob) sha)
      #(state 400 (ErrorReply :message "blob の sha256 がキーと合わない"))
    True #((replace state :programs (| state.programs {sha (ProgramRow :blob blob :versions versions :put-ms now)}))
           200 (ProgramStored :sha sha))))


(deff program-read [#^ ClusterState state #^ str sha]  ; defk にできない: coordinator の要求の振り分け(Program の外の純粋な判断)が呼ぶ
  {:pre [(: state ClusterState) (: sha str)] :post [(: % tuple) (= (len %) 3)] :tags {:context "coordinator" :role "judgment"}}
  "GET /programs/<sha>: 置いた Program(無ければ 404)。"
  (setv row (.get state.programs sha))
  (if (is row None)
      #(state 404 (ErrorReply :message (.format "Program {} は置かれていない(宣言・task の前に送り手が置く)" sha)))
      ;; 置いた行そのもの(JSON の {blob versions} は coordinator/protocol/replies が綴る — #2614)。
      #(state 200 row)))


(deff program-refs [#^ ClusterState state]  ; defk にできない: coordinator の調停(Program の外の純粋な判断)が呼ぶ
  {:pre [(: state ClusterState)] :post [(: % frozenset)] :tags {:context "coordinator" :role "judgment"}}
  "置き場の Program を今参照している物のキーの集合 — 掃除で残す物を決めるため。受け付けた Service の行と、task の行(待ち・走っている・
   終わって結果を保持している物の全部 — 行が消えるまで参照は続く)。参照の定義点はここ 1 つ。"
  (frozenset (+ (lfor j state.jobs :if j.spec.program j.spec.program)
                (lfor t (.values state.tasks) :if t.program t.program))))


(deff sweep-programs [#^ ClusterState state #^ int now]  ; defk にできない: coordinator の調停(Program の外の純粋な判断)が呼ぶ
  {:pre [(: state ClusterState) (: now int)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "受け付けた Service と task の行のどれも参照せず、置いてから PROGRAM-GRACE-MS を過ぎた Program を消す。"
  (let [used (program-refs state)
        kept (dfor #(sha row) (.items state.programs)
                   :if (or (in sha used) (<= (- now row.put-ms) PROGRAM-GRACE-MS))
                   sha row)]
    (if (= (len kept) (len state.programs)) state (replace state :programs kept))))
