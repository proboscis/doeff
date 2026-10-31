;;; doeff-run の公開 effect 7 つと、その答えの型。
;;;
;;; RunStart(走らせ始める)・RunStop(止める)・RunRemove(止まった run の跡を片付ける)・RunStatus(今の状態を読む)・
;;; RunReadEvents(状態の出来事を読む)。単位は「image を走らせた run 1 つ」で、上の層が持つのは取っ手(RunHandle)だけ。
;;; VolumeEnsure(volume を無ければ作る)・VolumeStatus(volume が在るかを問う)。volume は run の寿命より長く、run を止めても片付けても
;;; 残る。volume を消す effect は無い(消すのはマージを知る側が別に決める)。
;;;
;;; 失敗は例外ではなく答えの型で返す(handler の実装の誤り — backend の予期しない例外 — だけが例外として上がる)。
;;; 取っ手の run を handler が知らない時(名にも取っ手にも覚えが無い・片付けた・消えた)は、どの effect も「消えた」として答える
;;; (RunStatus = RunGone・RunStop・RunRemove = RunGone・RunReadEvents = 出来事の無い頁と RunGone)— 呼び手が名を持つので、
;;; 名の誤りと消失は見分けない。
;;;
;;; 基底の EffectBase の型引数が答えの型(使い手の `<-` が答えを型つきで読む)。
(require doeff-hy.macros [defeffect val])
(require doeff-hy.record [defrecord defenum])
(import dataclasses [dataclass])
(import datetime [datetime])
(import enum [StrEnum])
(import doeff_run._values [RUN-NAME RunSpec RunHandle RunPending RunLive RunStopped RunGone RunState])


;; --- 走らせ始める ---------------------------------------------------------------------------------------

(defrecord RunAccepted
  "受け付けた。handle = この run の取っ手。受け付けは「走り始めた」ではない — 起きるのは出来事 RunLive で届き、起きずに
   止まる(image を取り寄せられない など)のは RunStopped(NEVER-BEGAN)で届く。"
  {:tags {:context "run" :role "type"}
   :check [(isinstance handle RunHandle)]}
  (#^ RunHandle handle))

(defrecord RunNameInUse
  "その名は今、別の run が使っている(走っている・止まった跡が残っている)。handle = 今の持ち主の取っ手。取っ手を失った呼び手は
   これで引き取れる(状態を読んで、使うか止めて片付けるかを決める)。名は片付ける(RunRemove)か消える(RunGone)まで空かない。"
  {:tags {:context "run" :role "type"}
   :check [(isinstance handle RunHandle)]}
  (#^ RunHandle handle))

;; 受け付けなかった訳: NO-CAPACITY = 今は空きが無い(後で、または別の所で)/ UNSUPPORTED = この handler が設定の一部を守れない
;; (守れない資源の上限や volume を、黙って無視せず断る)。
(defenum RefusalWhy NO-CAPACITY UNSUPPORTED)

(defrecord RunRefused
  "受け付けなかった(走らせ始めていない・名は空いたまま)。why = 訳・detail = 何が守れないかの 1 行(env の値は写さない)。"
  {:tags {:context "run" :role "type"}
   :check [(RUN-NAME.fullmatch name) (isinstance why RefusalWhy) (isinstance detail str)]}
  (#^ str name)
  (#^ RefusalWhy why)
  (setv #^ str detail ""))

(val RunStartOutcome (| RunAccepted RunNameInUse RunRefused))


;; --- volume ---------------------------------------------------------------------------------------------

(defrecord VolumeCreated
  "作った: 無かった volume を、この頼みで作った(中身は空)。同じ名の作成が重なった時、作ったと答えるのは作った 1 つだけ。"
  {:tags {:context "run" :role "type"}
   :check [(and (isinstance name str) (bool name))]}
  (#^ str name))

(defrecord VolumeExisted
  "在った: volume は頼まれる前から在った(作っていない・中身はそのまま)。同じ名の作成が重なって他の頼みが先に作った時も、在った。"
  {:tags {:context "run" :role "type"}
   :check [(and (isinstance name str) (bool name))]}
  (#^ str name))

(defrecord VolumePresent
  "在る(問いの答え): volume が今ある。"
  {:tags {:context "run" :role "type"}
   :check [(and (isinstance name str) (bool name))]}
  (#^ str name))

(defrecord VolumeAbsent
  "無い(問いの答え): volume が今ない(作っていない・消えた)。問いは作らない。"
  {:tags {:context "run" :role "type"}
   :check [(and (isinstance name str) (bool name))]}
  (#^ str name))

(defrecord VolumeRefused
  "作れなかった・問えなかった(volume は作っていない)。why = 訳(RefusalWhy: UNSUPPORTED = 名が backend の規則に合わない・この handler は
   volume を持たない / NO-CAPACITY = backend が作らなかった・作ったと言うが見えない)・detail = 何が守れないかの 1 行。"
  {:tags {:context "run" :role "type"}
   :check [(and (isinstance name str) (bool name)) (isinstance why RefusalWhy) (isinstance detail str)]}
  (#^ str name)
  (#^ RefusalWhy why)
  (setv #^ str detail ""))

(val VolumeEnsureOutcome (| VolumeCreated VolumeExisted VolumeRefused))
(val VolumeStatusOutcome (| VolumePresent VolumeAbsent VolumeRefused))


;; --- 止める・片付ける -------------------------------------------------------------------------------------

(defrecord StopFailed
  "止められなかった(backend が止めを断った・止めの手順を全部踏んでも降りない)。run の状態は変わっていない — 次の頼みで
   もう一度止める。detail = backend の文。"
  {:tags {:context "run" :role "type"}
   :check [(isinstance handle RunHandle) (isinstance detail str)]}
  (#^ RunHandle handle)
  (setv #^ str detail ""))

(defrecord RunStillAlive
  "片付けなかった: run がまだ止まっていない(起きていない・走っている)。先に止める。"
  {:tags {:context "run" :role "type"}
   :check [(isinstance handle RunHandle)]}
  (#^ RunHandle handle))

(val RunStopOutcome (| RunStopped RunGone StopFailed))
(val RunRemoveOutcome (| RunGone RunStillAlive))


;; --- 状態の出来事 -----------------------------------------------------------------------------------------

(defrecord RunEvent
  "状態の出来事 = run がその状態に入った(起きた RunLive・止まった RunStopped・消えた RunGone)。seq = run の中で 1 から欠けずに
   増える番号・at = 入った刻(時刻帯つき)。受け付けた状態(RunPending)への入りは出来事ではない。"
  {:tags {:context "run" :role "type"}
   :check [(and (isinstance seq int) (not (isinstance seq bool)) (>= seq 1))
           (and (isinstance at datetime) (is-not at.tzinfo None))
           (isinstance state #(RunLive RunStopped RunGone))]}
  (#^ int seq)
  (#^ datetime at)
  (#^ (| RunLive RunStopped RunGone) state))

(defrecord RunEventPage
  "出来事の頁。events = after-seq より後の出来事(seq の順)・next-seq = 次に渡す after-seq(出来事が在れば最後の seq・無ければ
   渡された after-seq)・state = 頁を作った時の run の今の状態(出来事の列の最後の状態と同じ。出来事が無くても状態は在る)。"
  {:tags {:context "run" :role "type"}
   :check [(isinstance events tuple)
           (all (gfor event events (isinstance event RunEvent)))
           (and (isinstance next-seq int) (not (isinstance next-seq bool)) (>= next-seq 0))
           (or (not events) (= next-seq (. (get events -1) seq)))
           (isinstance state #(RunPending RunLive RunStopped RunGone))]}
  (#^ (get tuple #(RunEvent ...)) events)
  (#^ int next-seq)
  (#^ RunState state))


;; --- effect ----------------------------------------------------------------------------------------------

(defeffect RunStart
  "image を spec の設定で走らせ始める。name = 呼び手が付ける run の名(RUN-NAME の形 — docker の container 名と k8s の資源名の両方が
   受ける)。受け付けたら取っ手を答えるだけで、起きるまでは待たない(起きたかは RunStatus・RunReadEvents で読む)。
   答え = RunAccepted | RunNameInUse(その名を使っている run の取っ手)| RunRefused(空きが無い・設定の一部を守れない)。
   image を取り寄せられない・命令が起こせないなど、受け付けた後に分かる誤りは RunStopped(NEVER-BEGAN)で届く。"
  {:fields [(: name str) (: spec RunSpec)]
   :pre [(: name str) (RUN-NAME.fullmatch name) (: spec RunSpec)]
   :answer RunStartOutcome
   :tags {:context "run" :role "intent"}})

(defeffect RunStop
  "run を止める(冪等)。止めの合図を送り、grace-seconds 秒の内に終わらなければ強制で止めて、止まった(か消えた)状態を答える —
   答えが返る時、run はもう走っていない。止まった run には止まった時の状態をそのまま答える(終わり方は変わらない)・起きる前の
   run は REQUESTED で止まる。止めが始まった後の 2 度目の頼みは、始まった止めの終わりを待つ(猶予は最初の頼みの物)。
   答え = RunStopped | RunGone(取っ手の run が無い)| StopFailed。"
  {:fields [(: handle RunHandle) (: grace-seconds float)]
   :pre [(: handle RunHandle) (: grace-seconds float) (>= grace-seconds 0.0)]
   :answer RunStopOutcome
   :tags {:context "run" :role "intent"}})

(defeffect RunRemove
  "止まった run の跡(container・Pod など)を片付け、名を空ける。volume は消さない。走っていない run だけ(先に RunStop)。
   片付けた run の最後の出来事は RunGone。答え = RunGone | RunStillAlive(まだ止まっていない)。"
  {:fields [(: handle RunHandle)]
   :pre [(: handle RunHandle)]
   :answer RunRemoveOutcome
   :tags {:context "run" :role "intent"}})

(defeffect RunStatus
  "run の今の状態を読む(待たない)。取っ手の run が無ければ RunGone。答え = RunPending | RunLive | RunStopped | RunGone。"
  {:fields [(: handle RunHandle)]
   :pre [(: handle RunHandle)]
   :answer RunState
   :tags {:context "run" :role "intent"}})

(defeffect RunReadEvents
  "状態の出来事を読む: after-seq(0 = 始めから)より後の出来事を、新しい出来事が起きるか wait-up-to 秒が過ぎるまで待って返す。
   run がもう終わっていて新しい出来事が無ければ待たずに返す(状態は最後の状態)。取っ手の run が無ければ、出来事の無い頁と RunGone を
   待たずに返す。答え = RunEventPage。"
  {:fields [(: handle RunHandle) (: after-seq int) (: wait-up-to float)]
   :pre [(: handle RunHandle) (: after-seq int) (>= after-seq 0) (: wait-up-to float) (>= wait-up-to 0.0)]
   :answer RunEventPage
   :tags {:context "run" :role "intent"}})

(defeffect VolumeEnsure
  "volume を無ければ作る(在れば在るまま — 中身を作り直さない)。name = RunSpec.volumes の VolumeMount.source に書く名。
   答え = VolumeCreated(この頼みで作った)| VolumeExisted(頼まれる前から在った)| VolumeRefused(作れなかった・訳つき)。
   名が backend の規則に合わない時は、作る前に UNSUPPORTED で断る。volume を持たない handler も UNSUPPORTED で断る(黙って成功にしない)。
   使い分け: 会話の最初の「開く」だけがこの効果を撃つ。開き直しは VolumeStatus で先に確かめ、無ければ作らずに断る — 消えた volume へ
   黙って「戻って」空になる事故を、呼び手が見分けるため(消えた volume にこの効果を撃つと、空の volume が作られて VolumeCreated と答える)。"
  {:fields [(: name str)]
   :pre [(: name str) (bool name)]
   :answer VolumeEnsureOutcome
   :tags {:context "run" :role "intent"}})

(defeffect VolumeStatus
  "volume が在るかを問う(待たない・作らない)。答え = VolumePresent | VolumeAbsent | VolumeRefused(名が backend の規則に合わない・
   この handler は volume を持たない — どちらも UNSUPPORTED。volume を持たない handler に「無い」と答えさせると、置き場が消えた事と
   見分けがつかない)。"
  {:fields [(: name str)]
   :pre [(: name str) (bool name)]
   :answer VolumeStatusOutcome
   :tags {:context "run" :role "intent"}})
