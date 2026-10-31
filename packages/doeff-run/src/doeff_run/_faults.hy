;;; 検の口: run を handler の知らない所で消す effect(公開 effect ではない — 業務の Program は使わない)。
;;;
;;; 「取っ手の知らない所で run が消えたら、出来事 RunGone が届き、名が空く」を、fake と本物の handler(docker・process・k3s)の
;;; 両方で同じ筋書きで確かめるための口。本物の handler の解釈器は backend の手で run を消す(docker rm -f・Pod の即時の削除・
;;; process の SIGKILL)。fake は run を世界から消す。
;;;
;;; 基底の EffectBase の型引数が答えの型。
(require doeff-hy.macros [defeffect])
(import doeff_run._values [RunHandle])


(defeffect RunVanish
  "取っ手の run を handler の知らない所で消す(状態が何であっても — 止まった跡も消える)。消えた run の最後の出来事は RunGone。
   答え = 消す run が在ったか(bool)。"
  {:fields [(: handle RunHandle)]
   :pre [(: handle RunHandle)]
   :answer bool
   :tags {:context "run" :role "foundation"}})
