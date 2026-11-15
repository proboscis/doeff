;;; 1 つの file の置き換えの知らせの箱(worker/protocol/coordinator_link の FileNotices の本番の形)。worker が準備の file を間隔で読み直さず、
;;; 書き手がその file を置き換えた時だけ読み直すため。以前は枠の file と同じ 10 秒の間で読み直したので、準備が済んでから能力が置き方に
;;; 効くまで、読みの間と heartbeat の間の遅れが残った(2026-11-12)。
;;;
;;;   FileNoticeBoard  箱(錠の下の可変の入れ物): serial = file の名に来た変わりの通し番号・bells = 掛かった鈴・watching = OS の見張りが
;;;                    掛かっている・fault = 掛からない訳。見張りの thread が file の dir を OS の変わりの知らせで受け、file の名に
;;;                    変わりが来た時に通し番号を進めて鈴を全部満たす。見張りが掛かった時(起動の時・dir が現れた・掛け直した)と
;;;                    知らせが溢れた時も同じく満たす(見張りの無い間の変わりを取りこぼさない)
;;;   file-notices     箱を作り、見張りの thread を立てる。Linux = inotify(dir に IN_MOVED_TO・IN_CLOSE_WRITE・IN_CREATE・IN_DELETE・
;;;                    IN_MOVED_FROM — 名で file を選ぶ)・macOS = kqueue(dir の fd に EVFILT_VNODE の NOTE_WRITE — dir の項の
;;;                    出入りで鳴り、名は分からないので dir の変わりごとに満たす。置き換えずに中身を書き足した file は鳴らない —
;;;                    書き手は別の名で書いて rename で置き換える約束)。ほかの OS では見張りを掛けない(watching 偽・fault に訳)
;;;
;;; dir が無い間(書き手がまだ作っていない)は RETRY-SECONDS ごとに掛け直す。dir が消えた・動いた時も掛け直しへ戻る。見張りの無い間は
;;; watching が偽で、待ち手は間を置いて読み直す(coordinator_link の prepare-noted)。
;;; watchfiles(doeff-events の os_files)にしない訳: 見るのは dir 1 つの名 1 つで、標準 library で足りる。watchfiles の notifier は GIL の
;;; 無い Python で閉じと待ちが重なると process ごと落ちた(doeff-events の os_files の註・2026-10-10)— worker の process を落とす危うさを、
;;; この小さな見張りのために持ち込まない。
;;;
;;; 鈴の印(mark と arm): 待ち手は読む前に印を取り、読んだ後に「印の後に変わっていなければ鈴を掛ける」— 読みと掛けの間に来た変わりは印の
;;; 違いで分かり、待たずに読み直す。
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import ctypes)
(import os)
(import platform)
(import select)
(import struct)
(import sys)
(import threading)
(import doeff_core_effects [ExternalPromise])

;; dir が無い・見張りを掛けられない間に、掛け直すまでの秒。
(val RETRY-SECONDS 5.0)
;; inotify の印(linux/inotify.h)。dir の項の出入りと書き終わりを受ける — 名で file を選ぶ。
(val IN-CLOSE-WRITE 0x8)
(val IN-MOVED-FROM 0x40)
(val IN-MOVED-TO 0x80)
(val IN-CREATE 0x100)
(val IN-DELETE 0x200)
(val IN-DELETE-SELF 0x400)
(val IN-MOVE-SELF 0x800)
(val IN-Q-OVERFLOW 0x4000)
(val IN-IGNORED 0x8000)
(val IN-NONBLOCK 0o4000)
(val IN-CLOEXEC 0o2000000)
(val NAME-EVENTS (| IN-CLOSE-WRITE IN-MOVED-FROM IN-MOVED-TO IN-CREATE IN-DELETE))
(val SELF-EVENTS (| IN-DELETE-SELF IN-MOVE-SELF IN-IGNORED))
;; inotify の 1 つの知らせの頭(int wd・uint32 mask・uint32 cookie・uint32 len — 後に len byte の名)。
(val EVENT-HEAD (struct.Struct "iIII"))


(defclass FileNoticeBoard []
  "1 つの file の置き換えの知らせの箱(頭の註)。path = 見る file・錠 lock の下に: serial = 変わりの通し番号・bells = 掛かった鈴の組・
   watching = OS の見張りが掛かっている・fault = 掛からない訳(None = 掛かっている)・told = 見張りの様子を 1 度でも書いた印(検が待つ)。retry-seconds = 掛からない間に掛け直すまでの秒・stopping = 見張りの thread を止める合図(検が止める —
   本番は process と共に終わる daemon の thread)・wake = 止めの合図で待ちを起こす pipe の #(読み口 書き口)。鈴を満たすのは錠の外。"

  (defn #^ None __init__ [self #^ str path #^ float [retry-seconds RETRY-SECONDS]]
    (setv self.path path
          self.retry-seconds retry-seconds
          self.lock (threading.Lock)
          self.serial 0
          self.bells #()
          self.watching-now False
          self.fault-now None
          self.told (threading.Event)
          self.stopping (threading.Event)
          self.wake (os.pipe)
          self.thread None))

  (defn #^ int mark [self]
    "file が最後に変わった印(読む前に取る)。"
    (with [self.lock]
      self.serial))

  (defn #^ bool arm [self #^ int seen #^ ExternalPromise bell]
    "印 seen の後に file が変わっていれば真(掛けない)。変わっていなければ bell を掛けて偽(次に変わった時に True で満たす)。"
    (with [self.lock]
      (if (!= self.serial seen)
          True
          (do (setv self.bells (+ self.bells #(bell)))
              False))))

  (defn #^ bool watching [self]
    "OS の見張りが今 掛かっているか(偽の間は待ち手が間を置いて読み直す)。"
    (with [self.lock]
      self.watching-now))

  (defn #^ (| str None) fault [self]
    "見張りが掛からない訳(None = 掛かっている)。"
    (with [self.lock]
      self.fault-now))

  (defn #^ None changed [self]
    "file が変わった(か、見張りの無い間に変わったかもしれない)事を印に書き、掛かった鈴を全部満たす。"
    (with [self.lock]
      (setv self.serial (+ self.serial 1)
            ringing self.bells
            self.bells #()))
    (for [bell ringing]
      (.complete bell True)))

  (defn #^ None watched [self #^ (| str None) fault]
    "見張りの様子を書く(fault = None は掛かった・文字列は掛からない訳)。掛かった時は見張りの無い間の変わりを取りこぼさないよう鈴を満たす。"
    (with [self.lock]
      (setv self.watching-now (is fault None)
            self.fault-now fault))
    (.set self.told)
    (when (is fault None)
      (.changed self)))

  (defn #^ None stop [self]
    "見張りの thread を止める(検が使う)。"
    (.set self.stopping)
    (os.write (get self.wake 1) b"x")
    (when (is-not self.thread None)
      (.join self.thread 5.0))))


(deff libc-inotify []  ; cannot be a defk: 見張りの thread(threading が呼ぶ素の関数 — Program の外)が使う OS の口
  {:pre [] :post [(: % ctypes.CDLL)] :tags {:context "doeff-cluster" :role "foundation"}}
  "inotify の関数を持つ libc を開くため(errno を読めるように)。process に既に結んだ libc の名を引く(find_library は ldconfig の子を起こす)。"
  (ctypes.CDLL None :use-errno True))


(deff changed-names [#^ bytes data]  ; cannot be a defk: 見張りの thread が inotify の読みを解く純粋な関数
  {:pre [(: data bytes)] :post [(: % (get tuple #(tuple ...)))] :tags {:context "doeff-cluster" :role "foundation"}}
  "inotify の fd から読んだ byte の列を、知らせの #(mask 名) の組に解くため(名は NUL を除いた文字列 — dir 自身の知らせは空)。"
  (setv events #() at 0)
  (while (<= (+ at EVENT-HEAD.size) (len data))
    (setv #(_wd mask _cookie size) (.unpack-from EVENT-HEAD data at)
          start (+ at EVENT-HEAD.size)
          name (.decode (.rstrip (cut data start (+ start size)) b"\x00") "utf-8" "surrogateescape")
          at (+ start size)
          events (+ events #(#(mask name)))))
  events)


(deff watch-inotify [#^ FileNoticeBoard board]  ; cannot be a defk: 見張りの thread の target(threading が呼ぶ素の関数 — Program の外)
  {:pre [(: board FileNoticeBoard)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "Linux の見張りの thread の本体: file の dir に inotify の見張りを掛け、file の名の変わり・知らせの溢れで箱を鳴らす。dir が無い・消えた
   間は RETRY-SECONDS ごとに掛け直す。止めの合図で抜ける。"
  (setv libc (libc-inotify)
        fd (.inotify-init1 libc (| IN-NONBLOCK IN-CLOEXEC))
        directory (os.path.dirname board.path)
        name (os.path.basename board.path)
        wd None)
  (when (< fd 0)
    (.watched board (+ "inotify を開けない: " (os.strerror (ctypes.get-errno))))
    (return None))
  (try
    (while (not (.is-set board.stopping))
      (when (is wd None)
        (setv added (.inotify-add-watch libc fd (os.fsencode directory) (| NAME-EVENTS IN-DELETE-SELF IN-MOVE-SELF)))
        (if (< added 0)
            (do (.watched board (.format "dir {} に見張りを掛けられない: {}" directory (os.strerror (ctypes.get-errno))))
                (select.select [(get board.wake 0)] [] [] board.retry-seconds)
                (continue))
            (do (setv wd added)
                (.watched board None))))
      (setv #(ready _ _) (select.select [fd (get board.wake 0)] [] []))
      (when (in fd ready)
        (setv data (try (os.read fd 65536) (except [BlockingIOError] b""))
              events (changed-names data)
              lost (any (gfor #(mask _) events (& mask SELF-EVENTS)))
              hit (any (gfor #(mask n) events (or (& mask IN-Q-OVERFLOW) (and (& mask NAME-EVENTS) (= n name))))))
        (when lost
          ;; dir が消えた・動いた: 掛け直しへ戻る(掛かった時に鳴らす)。
          (setv wd None)
          (.watched board (.format "dir {} が消えた・動いた" directory)))
        (when (and hit (not lost))
          (.changed board))))
    (finally
      (os.close fd)))
  None)


(deff watch-kqueue [#^ FileNoticeBoard board]  ; cannot be a defk: 見張りの thread の target(threading が呼ぶ素の関数 — Program の外)
  {:pre [(: board FileNoticeBoard)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "macOS の見張りの thread の本体: file の dir の fd に kqueue の EVFILT_VNODE(NOTE_WRITE — 項の出入り)を掛け、dir の変わりごとに箱を
   鳴らす(名は分からない)。dir が無い・消えた・動いた間は RETRY-SECONDS ごとに掛け直す。止めの合図で抜ける。"
  (setv queue (select.kqueue)
        directory (os.path.dirname board.path)
        dir-fd None)
  (.control queue [(select.kevent (get board.wake 0) :filter select.KQ-FILTER-READ :flags select.KQ-EV-ADD)] 0 0)
  (try
    (while (not (.is-set board.stopping))
      (when (is dir-fd None)
        (try
          (setv opened (os.open directory os.O-RDONLY))
          (.control queue [(select.kevent opened :filter select.KQ-FILTER-VNODE :flags (| select.KQ-EV-ADD select.KQ-EV-CLEAR)
                                          :fflags (| select.KQ-NOTE-WRITE select.KQ-NOTE-DELETE select.KQ-NOTE-RENAME))]
                    0 0)
          (setv dir-fd opened)
          (.watched board None)
          (except [error OSError]
            (.watched board (.format "dir {} に見張りを掛けられない: {}" directory error.strerror))
            (select.select [(get board.wake 0)] [] [] board.retry-seconds)
            (continue))))
      (setv events (.control queue None 8 None))
      (for [event events :if (= event.ident dir-fd)]
        (if (& event.fflags (| select.KQ-NOTE-DELETE select.KQ-NOTE-RENAME))
            (do (os.close dir-fd)
                (setv dir-fd None)
                (.watched board (.format "dir {} が消えた・動いた" directory)))
            (.changed board))))
    (finally
      (when (is-not dir-fd None)
        (os.close dir-fd))
      (.close queue)))
  None)


;; OS ごとの見張りの thread の本体(platform.system の答え → 本体)。無い OS では見張りを掛けない。
(val WATCHERS {"Linux" watch-inotify "Darwin" watch-kqueue})


(deff watch-guarded [#^ FileNoticeBoard board #^ object body]  ; cannot be a defk: 見張りの thread の target(threading が呼ぶ素の関数 — Program の外)
  {:pre [(: board FileNoticeBoard)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "見張りの thread の本体 body を走らせ、思わぬ例外では訳を箱に書いて抜けるため(待ち手は間を置いて読み直す側へ戻る — 黙って見張りを
   失わない)。"
  (try
    (body board)
    (except [error Exception]
      (print (+ "file-notices: 見張りの thread が止まった: " (repr error)) :file sys.stderr :flush True)
      (.watched board (+ "見張りの thread が止まった: " (repr error)))))
  None)


(defk file-notices [path [system None] [retry-seconds RETRY-SECONDS]]
  {:pre [(: path str) (: system (| str None)) (: retry-seconds float)] :post [(: % FileNoticeBoard)] :tags {:context "doeff-cluster" :role "foundation"}}
  "file path(絶対の path)の置き換えの知らせの箱を作り、OS の見張りの thread を立てるため(頭の註)。system = OS の名(None = この
   process の OS — platform.system)・retry-seconds = dir が無い間に掛け直すまでの秒。見張りの無い OS では thread を立てず、訳つきの見張りの無い箱を返す。"
  (val board (FileNoticeBoard path retry-seconds))
  (val os-name (or system (platform.system)))
  (val body (.get WATCHERS os-name))
  (if (is body None)
      (.watched board (.format "{} では file の変わりの知らせを受けない" os-name))
      (do (setv board.thread (threading.Thread :target watch-guarded :args #(board body) :name "file-notices" :daemon True))
          (.start board.thread)))
  board)
