;;; 作業場所の volume の変わりの知らせの箱(protocol/workspace_volumes の VolumeNotices の本番の形 — 頭の註の待ち方)。答え手の付け・外し・
;;; 戻し・消え終わりの待ちと係の答えの待ちを、間隔で読み直させず、変わった時だけ起こす。以前は待ち 1 つが 0.5 秒ごとに API server へ GET を
;;; 1〜2 本出し、待つ volume が 30 本並ぶと答え手の process の主の thread の 1 core の大半を使った(2026-11-06 の zeus の測り — volume の
;;; 付けの task が主の thread の歩の CPU の 65%)。
;;;
;;;   VolumeNoticeBoard  箱(錠の下の可変の入れ物): 名 → 最後に変わった通し番号・名 → 掛かった鈴・file の path → 掛かった鈴。見張りの
;;;                      thread が変わった名を伝え(changed)、箱がその名の鈴を満たす。list し直した時(None)は全部の鈴を満たし、全部の名の
;;;                      印を進める(外れた間の変わりを取りこぼさない)。file の鈴は箱の file の見張りの thread(初めて掛かった時に立てる)が
;;;                      FILE-LOOK-SECONDS ごとに在る無しを見て満たす — 見るのは主の thread でない。OS の変わりの知らせ(inotify —
;;;                      watchfiles)にしない訳: 見る間は係の答えを待つ間(数秒・同時に数本)だけで、stat 1 回の重さは主の thread に
;;;                      届かない。watchfiles の notifier は GIL の無い Python で閉じと待ちが重なると process ごと落ちた(doeff-events の
;;;                      os_files の註・2026-10-10)— 答え手の process を落とす危うさを、この小さな見張りのために持ち込まない
;;;   volume-notices     箱を作り、Pod の中(ServiceAccount の token が在る)なら Longhorn の Volume と VolumeAttachment(名は同じ volume の名)を
;;;                      1 本ずつ名の見張り(kube_client の follow-names — list の後の watch)で受け始める。Pod の外では見張りの無い箱
;;;                      (鈴は名で満たされない — 待ち手は recheck-seconds ごとに読み直す)
;;;
;;; 鈴の印(mark と arm): 待ち手は読む前に名の印を取り、読んだ様子がまだ待つ物なら「印の後に変わっていなければ鈴を掛ける」— 読みと掛けの
;;; 間に来た変わりは印の違いで分かり、待たずに読み直す。見張りが届かない・断られた間は鈴は満たされず、待ち手は recheck-seconds で読み直す
;;; (理由は訳が替わった時だけ stderr に 1 行)。
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import collections.abc [Callable])
(import os)
(import sys)
(import threading)
(import time)
(import httpx)
(import doeff_core_effects [ExternalPromise])
(import doeff_cluster._foundation.kube_client [API-URL SA-DIR KubeClient follow-names kube-available kube-client])

;; Longhorn の物の API の頭(longhorn.io/v1beta2 — cluster の Longhorn v1.13 の版。protocol/workspace_volumes の LONGHORN-API と同じ — 検が縛る)。
(val LONGHORN-API "/apis/longhorn.io/v1beta2")
;; 名の見張りで受ける Longhorn の物の種類(付けの様子は Volume と VolumeAttachment の 2 つ — 名は同じ volume の名)。
(val WATCHED-KINDS #("volumes" "volumeattachments"))
;; 係の答えの file の在る無しを見る間の秒(見るのは箱の file の見張りの thread — 係の答えは mkfs を含めて数秒なので、この遅れは目立たない)。
(val FILE-LOOK-SECONDS 0.1)
;; 見張りが届かない・断られた後に list し直すまでの秒と、watch の要求の timeoutSeconds(server がこの秒で stream を閉じ、覚えた版から受け直す)。
(val RETRY-SECONDS 10.0)
(val WATCH-SECONDS 300)


(defclass VolumeNoticeBoard []
  "変わりの知らせの箱(頭の註)。錠 lock の下に: serial = 変わりの通し番号・floor = 最後に list し直した時の通し番号(全部の名の印の床)・
   marks = 名 → 最後に変わった通し番号・bells = 名 → 掛かった鈴の組・files = file の path → 掛かった鈴の組・fault = 見張りの最後の訳
   (None = 届いている)。looking = file の見張りの thread を起こす合図・looker = その thread(初めて file の鈴が掛かった時に立てる)・
   stops = 名の見張りを止める関数(検が止める)。鈴を満たすのは錠の外(満たした先で待ち手がまた箱を読む)。"

  (defn #^ None __init__ [self]
    (setv self.lock (threading.Lock)
          self.serial 0
          self.floor 0
          self.marks {}
          self.bells {}
          self.files {}
          self.fault None
          self.looking (threading.Event)
          self.looker None
          self.stops #()))

  (defn #^ int mark [self #^ str name]
    "名 name が最後に変わった印(list し直した後に変わっていなければ床)。"
    (with [self.lock]
      (max self.floor (.get self.marks name 0))))

  (defn #^ bool arm [self #^ str name #^ int seen #^ ExternalPromise bell]
    "印 seen の後に name が変わっていれば真(掛けない)。変わっていなければ bell を掛けて偽(次に変わった時に満たす)。"
    (with [self.lock]
      (if (!= (max self.floor (.get self.marks name 0)) seen)
          True
          (do (setv (get self.bells name) (+ (.get self.bells name #()) #(bell)))
              False))))

  (defn #^ bool arm-file [self #^ str path #^ ExternalPromise bell]
    "file path が在れば真(掛けない)。無ければ bell を掛けて偽(file の見張りの thread が、現れた時に満たす)。掛けた後に在る無しを見直す
     (掛ける前に現れた file を取りこぼさない)。"
    (with [self.lock]
      (setv (get self.files path) (+ (.get self.files path #()) #(bell)))
      (when (is self.looker None)
        (setv self.looker (threading.Thread :target self.look-files :name "volume-notices-files" :daemon True))
        (.start self.looker))
      (.set self.looking))
    (if (os.path.exists path)
        (do (.disarm self path bell)
            True)
        False))

  (defn #^ None disarm [self #^ str key #^ ExternalPromise bell]
    "名か file の path key に掛けた鈴 bell を外す(満たされずに待ちを終えた・待ち手が取り消された)。"
    (with [self.lock]
      (for [table #(self.bells self.files)]
        (setv left (tuple (gfor one (.get table key #()) :if (is-not one bell) one)))
        (if left
            (setv (get table key) left)
            (.pop table key None)))))

  (defn #^ None changed [self #^ (| str None) name]
    "名の見張りが伝えた変わり(name = 変わった object の名・None = list した・し直した — 全部)を印に書き、その鈴を満たす。届いた事で
     見張りの訳を消す。"
    (with [self.lock]
      (setv self.serial (+ self.serial 1))
      (if (is name None)
          (setv self.floor self.serial
                self.marks {}
                ringing (tuple (gfor bells (.values self.bells) bell bells bell))
                self.bells {})
          (setv (get self.marks name) self.serial
                ringing (.pop self.bells name #())))
      (setv recovered (and (is name None) (is-not self.fault None))
            self.fault (if (is name None) None self.fault)))
    (when recovered
      (print "volume-notices: Longhorn の見張りが届いた — 変わった時だけ起こす待ちに戻る" :file sys.stderr :flush True))
    (for [bell ringing]
      (.complete bell True)))

  (defn #^ None faulted [self #^ str reason]
    "名の見張りが届かない・断られた・読めない訳 reason を受ける(訳が替わった時だけ stderr に 1 行 — 待ち手は recheck-seconds で読み直す)。"
    (with [self.lock]
      (setv fresh (!= self.fault reason)
            self.fault reason))
    (when fresh
      (print (+ "volume-notices: Longhorn の見張りが止まった — 待ち手は間を置いて読み直す: " reason) :file sys.stderr :flush True)))

  (defn #^ None look-files [self]
    "file の見張りの thread の本体: 鈴の掛かった file の在る無しを FILE-LOOK-SECONDS ごとに見て、現れた file の鈴を満たす。鈴が無い間は
     次に掛かるまで眠る。"
    (while True
      (.wait self.looking)
      (with [self.lock]
        (setv paths (tuple self.files))
        (when (not paths)
          (.clear self.looking)))
      (setv found (tuple (gfor path paths :if (os.path.exists path) path))
            ringing [])
      (with [self.lock]
        (for [path found]
          (.extend ringing (.pop self.files path #()))))
      (for [bell ringing]
        (.complete bell True))
      (when paths
        (time.sleep FILE-LOOK-SECONDS))))

  (defn #^ None stop [self]
    "名の見張りを止める(検が使う — 本番の見張りは process と共に終わる daemon の thread)。"
    (for [stop self.stops]
      (stop))))


(defk volume-notices [namespace [sa-dir SA-DIR] [base API-URL] [transport None] [retry-seconds RETRY-SECONDS] [watch-seconds WATCH-SECONDS]]
  {:pre [(: namespace str) (: sa-dir str) (: base str) (: transport (| httpx.BaseTransport None)) (: retry-seconds float) (: watch-seconds int)]
   :post [(: % VolumeNoticeBoard)] :tags {:context "doeff-cluster" :role "foundation"}}
  "答え手の process に 1 つの変わりの知らせの箱を作るため(頭の註)。Pod の中(sa-dir に token が在る)か検が偽の API transport を渡した時は、
   namespace の Longhorn の Volume と VolumeAttachment を名の見張りで受け始める。Pod の外では見張りの無い箱を返す。"
  (val board (VolumeNoticeBoard))
  (when (or (is-not transport None) (! (kube-available sa-dir)))
    (<- client KubeClient (kube-client RuntimeError :base base :sa-dir sa-dir :transport transport :retry-seconds retry-seconds
                                       :watch-seconds watch-seconds))
    (var stops #())
    (for [kind WATCHED-KINDS]
      (<- stop (get Callable #([] None)) (follow-names client.settings (.format "{}{}/namespaces/{}/{}" base LONGHORN-API namespace kind) kind
                                                       board.changed board.faulted))
      (:= stops (+ stops #(stop))))
    (setv board.stops stops))
  board)
