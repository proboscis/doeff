;;; 系(System)の宣言の値と、coordinator へ渡す宣言の行(ADR-DOE-CLUSTER-001 の段 3)。
;;;
;;; job(常駐の service)は Program の値 1 つ(defk の関数を呼んだ結果 — R1)と、置き場所と台数と入れ替えの約束(needs・replicas・
;;; readiness・update・environ)を持つ。handler は Program の中(defk の本体の with-handlers)で作り、宣言には入れない(R3b)。系は doeff-hy の defsystem で
;;; 書き、土台を引数に受けて System の値を返す。土台 = 本体の Program を受け、自分の handler(と scheduler・時計)の下で走らせて答えを
;;; 返す module の最上位の defk(計画 10.1 — job は自分で scheduled を包まない。本番の土台は scheduler を含み、sim の土台は含まない):
;;;
;;;   (defk production-foundation [body]
;;;     (<- answer (scheduled (with-handlers [(state) (environ-reader) host-reader (sync-time-handler) …] body)))
;;;     answer)
;;;   (defk tally-program [foundation step]
;;;     (<- total (foundation (tally-body step)))
;;;     total)
;;;   (defsystem lab [foundation]
;;;     "見本の系"
;;;     (tally (tally-program foundation 2) :needs #{"net"} :replicas 1 :environ {"TALLY_BASE" "1"}))
;;;
;;; 手元で系を回すのは local.sim-cluster(sim の土台で作った同じ系の値を、本物の coordinator と worker の上で走らせる)。
;;;
;;; defsystem の展開が呼ぶのは job と system-of(doeff_cluster.shared.entry.service_build)。宣言の行は同じ module の system-declaration が作る:
;;;   - Program は encode-program で詰め、中身の sha256 を鍵に置き場(coordinator の /programs/<sha>)へ別に送る。行は sha だけを持つ
;;;     (改訂 1 の F)。
;;;   - 行の identity = 呼んだ関数の module:qualname・引数の正規 JSON(関数は {"ref": "module:qualname"})。spec-hash は identity・
;;;     revision・versions・environ から作り、詰めた文字列は比べない(改訂 1 の A — cloudpickle の出力は同じ Program でも揺れる)。
;;;   - describe = identity から作る表示の 1 行(coordinator は業務の code を持たず Program を解けないので、表示は宣言が運ぶ)。
;;; 旧い宣言(:env・:config・:env-config・:requires・関数の参照 + 設定)は受け付けない(operator 2026-09-27)。
;;; 置き場(#2540): この module は型だけ(CallShape・Job・System・RecordArgument・Declaration・UPDATE-FORMS・REPLICAS-VALUES)。identity と検めの判断は
;;; doeff_cluster.shared.core.service_rules、構成子と宣言の行の組み立ては doeff_cluster.shared.entry.service_build。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import typing [ClassVar Protocol runtime-checkable])

(val UPDATE-FORMS #("recreate" "handoff"))
;; job の望む台数として受ける値(coordinator の Service の replicas と同じ — 0 = 取り下げ・1 = 動かす)。
(val REPLICAS-VALUES #(0 1))


(defrecord CallShape
  "Program を作った呼び出しの形(defsystem の展開が残す — defk の呼び出しの結果からは引数を読めないため)。
   function = 呼んだ関数・args = 位置の引数の値・kwargs = 名の引数の値(Hy の名 → 値)。identity と describe の材料。"
  (#^ Callable function)
  (#^ list args)
  (#^ dict kwargs))


(defrecord Job
  "系の job 1 つ(常駐の service)。program = Program の値(R1)・call = それを作った呼び出しの形・needs = 要る能力の名(空でない
   frozenset — R4b)・replicas = 望む台数(0 = 取り下げ・1 = 動かす — 宣言し直しが Service に書く値)・readiness = {\"windowSeconds\" n …}
   か None・update = recreate | handoff・environ = 子の環境変数(EnvVar の tuple — 名の順)。"
  (#^ str name)
  (#^ object program)
  (#^ CallShape call)
  (#^ frozenset needs)
  (#^ int replicas)
  (#^ (| dict None) readiness)
  (#^ str update)
  (#^ tuple environ))


(defrecord System
  "系 = job の組(defsystem の関数が返す値)。name = 系の名・jobs = Job の tuple(名は重ならない)。"
  (#^ str name)
  (#^ tuple jobs))


(defclass [runtime-checkable] RecordArgument [Protocol]
  "系の引数に渡せる record(defrecord・dataclass の値)の印 — 欄の宣言 __dataclass_fields__ を持つ値。"
  (setv #^ (get ClassVar dict) __dataclass_fields__ {}))


(defrecord Declaration
  "system-declaration の答え: rows = coordinator へ渡す宣言の行(Service ごと)・programs = 行が参照する詰めた Program(sha → 文字列)。
   declare は programs を /programs へ置いてから rows を書く。"
  (#^ list rows)
  (#^ dict programs))
