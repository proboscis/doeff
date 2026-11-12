;;; worker が準備で足す能力と準備の状態を、準備の file から読む判断 — I/O は呼び手(worker/protocol/coordinator_link の prepare-noted)が行う。
;;;
;;; 上に載る系は worker の Pod の中で、worker の起動とは別に準備(道具の取り寄せ・依存の組み立て)を走らせ、その準備が済んで初めて渡せる
;;; 能力を持つ。準備を起動の前の待ちにして起動の引数 --provides で名乗ると、準備が遅れた・落ちた worker は process の一生のあいだ能力を
;;; 欠いたまま動き、その事を名乗らない(2026-11-12 — 準備の上限を越えた機体が何時間も仕事を受けなかった)。そこで worker は
;;; 起動の引数の能力で先に起き、準備の書き手が書く準備の file を heartbeat の前に読み直して、file の能力を起動の引数の能力に足して名乗り、
;;; 準備の状態も heartbeat の欄 prepare で名乗る。coordinator は heartbeat ごとに Worker の行の provides を書き直すので、準備が済んだ
;;; 次の heartbeat から置き方に効く。
;;;
;;; file の形(JSON の object — 書き手は別の名で書いて rename で置き換える。知らない欄は読まずに通す):
;;;   state     必須。"running"(準備が続いている)・"done"(済んだ)・"failed"(落ちた — 書き手は間を置いてやり直してよい)
;;;   provides  省ける。準備で足す能力の名の列(capability-refusal で受けられる名。機体の印 host- と組の印 pool- は書けない —
;;;             機体の境界は起動の引数、組の印は枠の file が名乗る)。state を問わず書いた能力を名乗る(済んだ後のやり直しで能力を
;;;             落とすかは書き手が決める)
;;;   sinceMs   省ける。その状態に入った刻(epoch ms・0 以上の整数)
;;;   stage     省ける。running では今の段・failed では落ちた段の名
;;;   reason    省ける。訳の文(failed の訳・running の進み)
;;;   {"state": "done", "provides": ["agent"], "sinceMs": 1794470000000, "stage": "", "reason": ""}
;;;
;;; worker が名乗る状態(heartbeat の prepare.state — file の state の 3 つに 2 つを足す):
;;;   running     file の state が running か、file がまだ無い(準備の書き手がまだ書いていない — sinceMs は worker の起動の刻・
;;;               訳は「準備の file がまだ無い」)
;;;   done        file の state が done
;;;   failed      file の state が failed
;;;   unreadable  file が在るのに読めない(権限など)
;;;   malformed   file は読めたが、JSON の object でない・state が 3 つの語でない・欄の形の誤り
;;; unreadable・malformed・file が無い間は、前に名乗った能力のままにする(初回は足す能力なし)— 書きの途中の file で能力を落とさない。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う
(import json)
(import doeff_cluster._shared.core.capabilities [HOST-PREFIX POOL-PREFIX capability-refusal])

(defenum PrepareState RUNNING DONE FAILED UNREADABLE MALFORMED)

;; file の state に書ける語(worker が足す unreadable・malformed は書き手が書かない)。
(val WRITTEN-STATES #("running" "done" "failed"))


(defrecord PrepareReading
  "準備の file を読んだ答えと、worker が名乗る準備の姿: state = 準備の状態・provides = 準備で足す能力(名の順の重なりの無い tuple)・
   since-ms = その状態に入った刻(None = 書き手が書かなかった)・stage = 段の名(空 = 書かなかった)・reason = 訳の文(空 = 無い)。"
  {:check [(or (is since-ms None) (>= since-ms 0))]}
  (#^ PrepareState state)
  (#^ (get tuple #(str ...)) provides)
  (#^ (| int None) since-ms)
  (#^ str stage)
  (#^ str reason))


(defk prepared-provides-refusal [value]
  {:pre [(: value (| list dict str int float bool None))] :post [(: % (| (get tuple #(str ...)) str))] :tags {:context "worker" :role "judgment" :reads "json"}}
  "file の欄 provides の値を、準備で足す能力の名の順の重なりの無い tuple にするため。列でない・文字列でない名・能力の名として受けられない名・
   機体の印 host- と組の印 pool- の名は、malformed の訳の文(黙って外して残りを名乗らない)。"
  (val names (if (isinstance value list) value []))
  ;; 最初の受けられない名の訳(None = 全部受けられる)。
  (val problem
    (next (gfor n names
                :setv p (cond
                          (not (isinstance n str)) (.format "provides の {!r} が文字列でない" n)
                          (.startswith n HOST-PREFIX) (.format "provides の {!r} は機体の印 — 機体の境界は起動の引数で名乗る" n)
                          (.startswith n POOL-PREFIX) (.format "provides の {!r} は組の印 — 組の印は枠の file の pools で名乗る" n)
                          True (capability-refusal n))
                :if p p)
          None))
  (cond
    (not (isinstance value list)) (.format "provides が列でない: {!r}" value)
    (is-not problem None) problem
    True (tuple (sorted (set names)))))


(defk prepare-of-text [text]
  {:pre [(: text str)] :post [(: % PrepareReading)] :tags {:context "worker" :role "judgment" :reads "json"}}
  "準備の file の中身 text を読むため。読めた形は file の state(running・done・failed)と欄の値、形の誤り(JSON の object でない・state が
   3 つの語でない・provides の誤り・sinceMs が 0 以上の整数でない・stage と reason が文字列でない)は malformed とその訳(能力は空 —
   前の能力を残すかは呼び手の prepare-after が決める)。知らない欄は読まない。"
  ;; 解けた値を 1 つの組に包む(空の組 = JSON でない — 中身の null と分ける)。
  (val decoded (try #((json.loads text)) (except [json.JSONDecodeError] #())))
  (val parsed (if decoded (get decoded 0) None))
  (val row (if (isinstance parsed dict) parsed {}))
  (val state (.get row "state"))
  (<- provides (| tuple str) (prepared-provides-refusal (.get row "provides" [])))
  (val since (.get row "sinceMs"))
  (val stage (.get row "stage" ""))
  (val reason (.get row "reason" ""))
  (val malformed (fn [why] (PrepareReading :state PrepareState.MALFORMED :provides #() :since-ms None :stage "" :reason why)))
  (cond
    (not decoded) (malformed "JSON でない")
    (not (isinstance parsed dict)) (malformed "JSON の object でない")
    (not-in state WRITTEN-STATES) (malformed (.format "state は running・done・failed のどれかで書く: {!r}" state))
    (isinstance provides str) (malformed provides)
    (not (or (is since None) (and (isinstance since int) (not (isinstance since bool)) (>= since 0))))
      (malformed (.format "sinceMs が 0 以上の整数でない: {!r}" since))
    (not (isinstance stage str)) (malformed (.format "stage が文字列でない: {!r}" stage))
    (not (isinstance reason str)) (malformed (.format "reason が文字列でない: {!r}" reason))
    True (PrepareReading :state (PrepareState state) :provides provides :since-ms since :stage stage :reason reason)))


(defk prepare-missing [before boot-at]
  {:pre [(: before (| PrepareReading None)) (: boot-at int)] :post [(: % PrepareReading)] :tags {:context "worker" :role "judgment"}}
  "準備の file がまだ無い時に名乗る姿を決めるため: 準備は続いている(running)と読み、その刻は worker の起動の刻 boot-at(準備は worker と
   同じ container で起動の時に始まる)。能力は前に名乗った物のまま(before が None なら足す能力なし)。"
  (PrepareReading :state PrepareState.RUNNING :provides (if (is before None) #() before.provides) :since-ms boot-at
                  :stage "" :reason "準備の file がまだ無い"))


(defk prepare-unreadable [detail]
  {:pre [(: detail str)] :post [(: % PrepareReading)] :tags {:context "worker" :role "judgment"}}
  "準備の file が在るのに読めない時の読みを作るため(detail = 読めない訳)。"
  (PrepareReading :state PrepareState.UNREADABLE :provides #() :since-ms None :stage "" :reason detail))


(defk prepare-after [before reading now-ms]
  {:pre [(: before (| PrepareReading None)) (: reading PrepareReading) (: now-ms int)] :post [(: % PrepareReading)]
   :tags {:context "worker" :role "judgment"}}
  "前に名乗った姿 before(None = まだ読んでいない)と、在る file の今の読み reading から、次に名乗る姿を決めるため。書き手の書いた file
   (running・done・failed)はそのまま名乗る。読めない・形の誤りの間は前に名乗った能力のまま(書きの途中の file で能力を落とさない)にし、
   その刻は状態に入った刻(前も同じ状態ならその刻・違えば今 now-ms)にする。"
  (match reading.state
    (| PrepareState.UNREADABLE PrepareState.MALFORMED)
      (PrepareReading :state reading.state :provides (if (is before None) #() before.provides)
                      :since-ms (if (and (is-not before None) (= before.state reading.state)) before.since-ms now-ms)
                      :stage "" :reason reading.reason)
    _ reading))


(defk prepare-line [before after]
  {:pre [(: before (| PrepareReading None)) (: after PrepareReading)] :post [(: % (| str None))] :tags {:context "worker" :role "judgment"}}
  "準備の姿の移り(状態・足す能力・段・訳が変わった)を worker の log の 1 行にするため(変わらなければ None — 同じ姿の 2 度目は出さない)。"
  (val said (fn [r] (+ r.state.value
                       (if r.stage (.format "・段 {}" r.stage) "")
                       (if r.reason (.format "・{}" r.reason) "")
                       (.format "・足す能力 {}" (if r.provides (.join "," r.provides) "なし")))))
  (if (and (is-not before None) (= #(before.state before.provides before.stage before.reason)
                                   #(after.state after.provides after.stage after.reason)))
      None
      (.format "worker: 準備の file から {} を名乗る" (said after))))
