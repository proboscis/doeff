;;; 検の口: process の死を注入する effect(公開 effect ではない)。
;;;
;;; 「手番の途中に process が消えたら BackendLost・次の ResumeSession は通る」(設計 7 節・8 節 8)を、fake と本番の handler の両方で
;;; 同じ筋書きで確かめるための口。本番の handler は会話の process に SIGKILL を送る(OOM・kill と同じ形 — 読み手は終わりの行を
;;; 読まずに stdout の EOF を見る)。fake は走っている手番を BackendLost で終える。業務の Program はこの effect を使わない。
;;;
;;; ClaudeForgetSession は家を空にした形(上の層の Pod を作り直して claude の家が消えた — 上の層が記憶の写しを持ち込んで続ける筋書きの口)。
;;; fake だけが答える(本番の handler は家の file を消さない)。
(require doeff-hy.record [defrecord defenum])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff [EffectBase])


(defclass [(dataclass :frozen True)] ClaudeDropProcess [EffectBase]
  "会話の process を消す。答え = 消す process が在ったか(bool)。"
  (#^ str session-id))


(defclass [(dataclass :frozen True)] ClaudeForgetSession [EffectBase]
  "家から会話を消す(走っている手番は BackendLost で終わる)。答え = 忘れた物が在ったか(bool)。fake だけが答える。"
  (#^ str session-id))


;; 会話ごとに CLI の process を生かしたまま待たせる形(#3672)の守りを、fake と本番の handler の両方で同じ筋書きで
;; 確かめる口。手番の外で CLI が出力したら host はその process を降ろして訳を残す(#517 の事故 = result の後も stdin が開いた
;; CLI が、背景の仕事の完了で手番の外に動いた形を、生かしたままの形で再び起こさないため)。業務の Program はこの 2 つを使わない。

(defclass [(dataclass :frozen True)] ClaudeEmitOutsideTurn [EffectBase]
  "生きていて手番を走らせていない process に、手番の外の出力を 1 行させる(本番の handler は替え玉の CLI へ検だけの行を書く —
   本物の claude には撃たない)。答え = 出させる process が在ったか(bool)。"
  (#^ str session-id))


(defclass [(dataclass :frozen True)] ClaudeLiveProcess [EffectBase]
  "会話の process の見え方を読む。答え = LiveProcess(生きて降りる途中でない process が在る)か NoLiveProcess。"
  (#^ str session-id))


;; process を降ろした訳(会話の process は手番をまたいで生き、次の時だけ降ろす — #3672): SESSION-CLOSED = 会話を閉じた /
;; LAUNCH-CHANGED = 次の手番の起こした時の条件の鍵(argv.hy の launch-key)が違う / OUTSIDE-TURN-OUTPUT = 手番の外で出力した(守り)/
;; INTERRUPT-SIGNAL = 止めるを SIGINT で伝えた(CLI は result の後に自分で降りる — 2.1.282。降りる途中の process を次の手番が使い回さ
;; ない)/ CREDENTIAL-FLOOR = 資格の期限 − 床(ClaudeCodeHost の credential-floor-seconds)を過ぎた(D2 — 呼び手は
;; この訳を読んで借りた資格を返す)。process が自分で終わった時(落ちた・消された)は訳を付けない。
;; 生かす本数の上限では降ろさない(#4072 の E1b — 越える起動は handler が知らせ ClaudeLiveLimitExceeded でホストへ伝え、止める CLI は
;; ホストが選ぶ)。
(defenum StopReason SESSION-CLOSED LAUNCH-CHANGED OUTSIDE-TURN-OUTPUT INTERRUPT-SIGNAL CREDENTIAL-FLOOR)


(defrecord LiveProcess
  "会話に生きた process が在る。launches = この会話でこれまでに起こした process の数(使い回しなら増えない)。"
  (#^ int launches))


(defrecord NoLiveProcess
  "会話に生きた process が無い。launches = これまでに起こした数・stopped-because = 最後の process を降ろした訳(まだ 1 つも
   起こしていなければ None)。"
  (#^ int launches)
  (#^ (| StopReason None) stopped-because))
