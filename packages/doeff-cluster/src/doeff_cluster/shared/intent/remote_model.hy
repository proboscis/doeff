;;; task(呼んだ側に寿命が縛られる短い仕事)の effect と、送る形・戻す形。
;;;
;;;   (<- result (remote-job (summarize foundation rows) :needs (frozenset ["net"])))   ; 構築関数(core の remote_rules)が needs を検めて出す
;;;
;;; RemoteJob は「未実行の Program を走らせ、戻り値(または例外)を返す」効果。Program は自分の handler を中の with-handlers で並べる
;;; (ADR-DOE-CLUSTER-001 R1・R2 — 実行先は handler を足さない)。handler の値も I/O の資源も送らない(送れば UnsendableProgram)。
;;; 送るのは cloudpickle した Program の値・要る能力・送り手の commit と版の識別だけ。
;;;
;;; 答える物:
;;;   remote-cluster(remote.hy)… coordinator へ出し、worker がその commit のコードを準備した子 process で走らせる
;;;   sim-cluster の偽の宿(local.hy)… 手元で同じ要求を本物の coordinator の模擬へ送り、task を別の process(別のスコープ)で走らせる
;;;   (呼び手の handler を継がない — 以前の remote-inline は継いでいたので消した)
;;;
;;; 意味は doeff の Spawn / Wait / Cancel と揃える: RemoteJob は「Spawn して Wait する」を 1 つにした形で、
;;; 並行に走らせたい・止めたい時は呼び手が Spawn / Cancel で包む(効果を 2 つに割らない)。
;;; 呼んだ側が止まれば task も止まる: cluster では呼び手の問い合わせが lease を延ばし、途絶えれば coordinator が task を
;;; 落として worker が子 process を止める。
;;;
;;; cloudpickle は長期保存の形式ではない。blob には必ず commit と Python / doeff の版を添え、受け側は版が違えば復元せずに断る。
;;; 詰めた Program は task の本文に載せず、coordinator の置き場 /programs/<sha>(program-sha)に先に置き、本文は sha と送り手の版を運ぶ
;;; (service の宣言と同じ運び方 — ADR-DOE-CLUSTER-001 R3b。版は task の事実で置き場は持たない — #3762)。
;;; ここは型だけ。版の突き合わせ・置き場のキー・失敗の値の組み立て(version-diffs・program-sha・failed-from ほか)は
;;; doeff_cluster.shared.core.remote_rules、Program と結果を詰める・戻す(encode-program・decode-outcome ほか)は
;;; doeff_cluster.shared.protocol.program_codec。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass field])
(import doeff [EffectBase Program])
(import doeff.do)
(import .runtime_env_model [EnvVar])


(defclass [(dataclass :frozen True)] RemoteJob [EffectBase]
  "program = 未実行の Program(値 — handler は Program の中の with-handlers で並べる・ADR-DOE-CLUSTER-001 R1・R2)・needs = 要る能力の名の frozenset(置く worker は needs ⊆ provides)。
   environ = 子の環境変数(名 → 文字列 — service の :environ と同じ規則・既定は空)。本番は worker が子 process の環境変数に置き、
   sim は sim の宿が同じ名の Ask に答える(Program は名の Ask で読む — 本番の土台は host_contract.environ-reader・sim の宿も同じ読みの定義で字面どおり返す)。
   結果 = Program の戻り値。Program が投げた例外はそのまま呼び手へ届く。"
  (#^ (| Program EffectBase) program)
  (setv #^ frozenset needs (frozenset))
  (setv #^ str name "")
  (setv #^ dict environ (field :default-factory dict))
  (defn #^ None __post-init__ [self]
    "environ を作る時に検める(名の形・予約・秘密の名を断る — EnvVar.environ-refusal)。needs の検め(空・旧い形)は型の外 —
     作り手は構築関数 doeff_cluster.shared.core.remote_rules.remote-job を通す(intent は core を読まない・#2564)。"
    (setv problem (EnvVar.environ-refusal self.environ))
    (when problem (raise (TypeError (+ "RemoteJob.environ: " problem))))))


(defclass RemoteJobFailed [Exception]
  "実行先で Program を走らせられなかった(版の不一致・復元不能・結果なし・コードの準備の失敗)。業務の例外ではない。")


(defclass UnsendableProgram [RemoteJobFailed]
  "送れない値(lock・file・socket・生の thread 等)を捕まえた Program。送り手の側で、送る前に断る。")


(defclass [(dataclass :frozen True)] VersionDiff []
  "版の辞書の食い違い 1 欄: field = 欄の名・sender = 送り手の値・env = 実行する側(env の root)の値(無い欄は None)。"
  (#^ str field)
  (#^ (| str None) sender)
  (#^ (| str None) env))


(defclass VersionMismatch [RemoteJobFailed]
  "送り手と受け側の Python / cloudpickle / doeff の版が違う。受け側は復元せずに断る。
   diffs = 食い違った欄(VersionDiff の tuple)・env-key = 実行する側の env のキー(env の task でなければ空)。"
  (defn #^ None __init__ [self #^ str message #^ tuple [diffs #()] #^ str [env-key ""]]  ; defk にできない: 例外の class の初期化
    (.__init__ (super) message)
    (setv self.diffs diffs self.env-key env-key)))


(defclass EnvUnavailable [RemoteJobFailed]
  "実行環境(runtime env)を準備できなかった(子 process を起こす前 — 同じ task を 2 度実行していない)。
   kind = runtime_env_model.EnvFailureKind の値・detail = 理由。"
  (defn #^ None __init__ [self #^ str kind #^ str detail]  ; defk にできない: 例外の class の初期化
    (.__init__ (super) (.format "実行環境を準備できない({}): {}" kind detail))
    (setv self.kind kind self.detail detail)))


(defclass [(dataclass :frozen True)] TaskSucceeded []
  (#^ object value))


(defclass [(dataclass :frozen True)] TaskFailed []
  "kind / message / traceback は常に文字列で持つ。error は例外そのもの(pickle できない例外なら None)。"
  (#^ str kind)
  (#^ str message)
  (#^ str traceback)
  (setv #^ (| BaseException None) error None))


(setv TaskOutcome (| TaskSucceeded TaskFailed))
