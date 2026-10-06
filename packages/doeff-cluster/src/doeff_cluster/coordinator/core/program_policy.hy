;;; coordinator の詰めた Program の置き場(ADR-DOE-CLUSTER-001 R3b・改訂 1 の F — 純粋な判断・I/O はしない)。
;;;
;;;   PUT /programs/<sha>  {"blob" 詰めた Program の文字列 "versions" 詰めた送り手の版} → 置く(同じキーは同じ中身 — 何度でも同じ意味)
;;;   GET /programs/<sha>  → {"blob" "versions"}(無ければ 404)
;;;   POST /tasks・PUT /detached/<key> の本文 {"program" sha "blob" "versions" …} → task の行と同じ状態の替えで置く(carried-program)
;;;
;;; 宣言の行(Service)・task の行・heartbeat の返事は sha だけを運び、worker が取って cache に置く。Program は大きくなりうるので、宣言の
;;; 行と毎拍の返事に載せない。キーは中身の sha256(remote_model.program-sha — 置く時に確かめる)。service の宣言は Program を先に
;;; PUT /programs/<sha> で置いてから行を書く。task の送りの要求の本文には Program を載せ、coordinator は Program の行と task の行を同じ
;;; 拍(WAL の 1 行・fsync 1 回)で置く(#3741 の C' — 前は送り手が PUT /programs の返事を待ってから task を送ったので、task 1 本で
;;; 拍と fsync を 2 回、直列に待った。task の Program は引数ごと詰めるので 1 本ごとに sha が違い、同じ sha の置き直しを止めても効かない)。
;;; 参照(受け付けた Service の行と task の行 — 終わって結果を保持している task も含む)の無くなった Program は、置いてから
;;; PROGRAM-GRACE-MS を過ぎたら掃除する(service の送り手は Program を先に置いてから行を書くので、その間に消さない)。
(require doeff-hy.macros [defk deff])
(import dataclasses [replace])
(import re)
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ProgramRow ProgramStored ErrorReply])
(import doeff_cluster.shared.core.remote_rules [program-sha])
(import doeff_cluster.coordinator.intent.request_bodies [ProgramBody TaskBody])

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


(defk carried-program [state body now]
  {:pre [(: state ClusterState) (: body TaskBody) (: now int)] :post [(: % tuple) (= (len %) 3)]
   :tags {:context "coordinator" :role "judgment"}}
  "task の送りの本文(POST /tasks・PUT /detached)が運んだ詰めた Program を、PUT /programs/<sha> と同じ確かめ(program-write — キーの形・
   大きさ・中身の sha256 が本文の program と合う)で置き、#(次の状態 status 本文) を返す(断りは受けた状態のまま・400 か 413)。呼び手は
   この状態の上に task の行を足すので、Program の行と task の行は同じ状態の替え(同じ拍・WAL の 1 行・fsync 1 回 — #3741 の C')で
   置かれる。呼ぶ前に task-body-refusal が本文の program の形を確かめる。
   本文に blob が無ければ受けた状態のまま 200 と None: 前の送り手の形(Program は先に PUT /programs/<sha> で置いた — 置き場に在るかは
   task-body-refusal が確かめる)。本番は doeff → coordinator(この形を受ける版)→ 各 job の pin と宣言し直し(送り手が本文に blob を
   載せる版)の順に当てる。送り手を先に替えると古い coordinator が本文の blob を断るので、coordinator は両方を受ける。全部の job が
   宣言し直された後に blob を必須にしてこの道を消すのは別の変更。"
  (if (is body.blob None)
      #(state 200 None)
      (program-write state body.program (ProgramBody :blob body.blob :versions body.versions) now)))


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
