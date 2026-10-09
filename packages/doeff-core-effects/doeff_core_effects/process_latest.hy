;;; 最新の値の本物の答え手 process-latest-handler(agora-redesign #1440・ADR-DOE-CORE-EFFECTS-004)— PublishLatest・ReadLatest・AwaitLatest に、
;;; process に 1 つの保存先で答える。
;;;
;;; 何のためか: 動き続ける process では、処理ループの run が断面(破れの記録・同期の進み・標本の ring …)を置き、別の run(probe の HTTP)が
;;; 書き手を待たずに読む。session の値は 1 つの run の中にしか無いので、この module が「名前 → 置き場(型 → 最新の値)」を process に 1 つ
;;; 持ち、同じ名前で入れた答え手どうし(別の run・別の thread)が同じ置き場を読み書きする。class は作らない(ADR-DOE-HY-007 R3・R4)。
;;;
;;; 書きは置き場の dict の 1 つの鍵の差し替え 1 回、読みは 1 つの鍵の参照 1 回で、どちらも lock を取らない(dict の 1 回の読み書きは
;;; free-threaded の Python でも割れない)。置き場を作る時だけ lock を取る。置き場は process の終わりまで残る。
;;; thread に触るのはこの module だけ(memory の答え手 memory_latest.hy は触らない)。
;;;
;;; 変わるまでの待ち(AwaitLatest — agora-redesign #4296): 待つ側は呼び鈴(CreateExternalPromise の約束 — 別の thread から完了してよい唯一の手段)を
;;; 「名前 → 型 → 掛かっている呼び鈴の組」(BELLS)に掛けてから保存先を確かめ直し、まだ seen なら呼び鈴の約束を park の形(Wait の PRIORITY_IDLE —
;;; 仮想の時計を止めない)で待つ。置く側の PublishLatest は値を差し替えた後に、その型の呼び鈴を全部外して鳴らす。取りこぼさない順: 待つ側は
;;; 「掛ける → 確かめ直す」、置く側は「差し替える → 外して鳴らす」で、呼び鈴の組の出し入れだけを BELLS-LOCK の中で行う — 置く側が外す時に
;;; 呼び鈴が無ければ、待つ側がもう一度読む時は差し替えた後の値を見る。呼び手が待ちの task を Cancel した時は、呼び鈴の約束の取り消しの callback
;;; (ExternalPromise.on-cancel)が呼び鈴を外す(残さない)。同じ object が置き直された時は seen のままなので、また掛けて待つ。
(require doeff-hy.macros [defhandler defk deff val var <-])
(val MODULE-TAGS {:context "latest" :role "foundation"})
(import threading)
(import functools [partial])
(import doeff_core_effects.latest_effects [PublishLatest ReadLatest AwaitLatest])
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise Wait PRIORITY_IDLE])


;; process に 1 つ: 名前 → 置き場(型 → 最新の値)と、置き場を作る時の lock。
(val BOARDS {})
(val BOARDS-LOCK (threading.Lock))
;; process に 1 つ: 名前 → 呼び鈴の保存先(型 → 掛かっている呼び鈴の組)と、呼び鈴の組を出し入れする時の lock(頭の註の変わるまでの待ち)。
(val BELLS {})
(val BELLS-LOCK (threading.Lock))


(defk latest-board [name]
  {:pre [(: name str)] :post [(: % dict)] :tags {:context "latest" :role "foundation"}}
  "答え手を入れる時に、name の置き場(型 → 最新の値)を引くため(無ければ作る — 別の run と同じ置き場を共有する)。"
  (with [BOARDS-LOCK]
    (when (not-in name BOARDS)
      (setv (get BOARDS name) {}))
    (get BOARDS name)))


(defk latest-bells [name]
  {:pre [(: name str)] :post [(: % dict)] :tags {:context "latest" :role "foundation"}}
  "答え手を入れる時に、name の呼び鈴の保存先(型 → 掛かっている呼び鈴の組)を引くため(無ければ作る — 別の run と同じ保存先を共有する)。"
  (with [BOARDS-LOCK]
    (when (not-in name BELLS)
      (setv (get BELLS name) {}))
    (get BELLS name)))


(defk hang-bell [bells kind bell]
  {:pre [(: bells dict) (: kind type) (: bell ExternalPromise)] :post [(: % None)] :tags {:context "latest" :role "foundation"}}
  "待つ側の呼び鈴 bell を、型 kind の組の末尾に掛けるため(頭の註の取りこぼさない順の「掛ける」)。"
  (with [BELLS-LOCK]
    (setv (get bells kind) (+ (.get bells kind #()) #(bell))))
  None)


(deff unhang-bell [bells kind bell]  ; defk にできない: 呼び鈴の約束の取り消しの callback(ExternalPromise.on-cancel)が scheduler の中で素の関数として呼ぶ
  {:pre [(: bells dict) (: kind type) (: bell ExternalPromise)] :post [(: % None)] :tags {:context "latest" :role "foundation"}}
  "待ち終えた・取り消された待ちの呼び鈴 bell を型 kind の組から外すため(鳴って外された後なら何もしない — 呼び鈴は object そのもので比べる)。"
  (with [BELLS-LOCK]
    (setv left (tuple (gfor other (.get bells kind #()) :if (is-not other bell) other)))
    (if left
        (setv (get bells kind) left)
        (.pop bells kind None)))
  None)


(defk ring-bells [bells kind]
  {:pre [(: bells dict) (: kind type)] :post [(: % None)] :tags {:context "latest" :role "foundation"}}
  "型 kind に掛かっている呼び鈴を全部外して鳴らすため(頭の註の「外して鳴らす」— 鳴らすのは lock の外。2 度目の完了は無視される)。"
  (with [BELLS-LOCK]
    (val rung (.pop bells kind #())))
  (for [bell rung]
    (.complete bell True))
  None)


(defhandler process-latest-handler [#^ str name]
  "PublishLatest・ReadLatest・AwaitLatest に、process に 1 つの保存先 name で答える(頭の註)。"
  ;; 引数に残す理由: name は別の run と同じ置き場を共有する鍵 — 入れる所ごとに決まる値で、Ask では区別できない。
  (session val board (! (latest-board name)))
  (session val bells (! (latest-bells name)))
  (PublishLatest [value]
    (setv (get board (type value)) value)
    (<- (ring-bells bells (type value)))
    (resume None))
  (ReadLatest [kind]
    (resume (.get board kind)))
  (AwaitLatest [kind seen]
    (var current (.get board kind))
    (while (is current seen)
      (<- bell ExternalPromise (CreateExternalPromise))
      (<- (hang-bell bells kind bell))
      ;; 掛けてから確かめ直す(読んだ直後・掛ける前に置かれた値を取りこぼさない — 頭の註)。待ちが取り消されたら callback が呼び鈴を外す。
      (when (is (.get board kind) seen)
        (.on-cancel bell (partial unhang-bell bells kind bell))
        (<- (Wait bell.future :priority PRIORITY_IDLE)))
      (unhang-bell bells kind bell)
      (.complete bell True)
      (:= current (.get board kind)))
    (resume current)))
