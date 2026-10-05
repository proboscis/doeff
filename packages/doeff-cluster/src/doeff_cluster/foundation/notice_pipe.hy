;;; 宿が子 process へ継がせた知らせの pipe の行を受ける汎用の I/O(#3672)。worker の shim が job へ pipe の読み口を継がせ、fd の番号を
;;; 環境変数(宿の契約 HOST-CONTRACT の notice-env — 呼び手が名を渡す)で渡す。ここは行の中身(語)を読まない: 1 行を前後の空白を除いた
;;; 文字列にして箱に置き、待ちを起こすだけ。語を知らせの型に読むのは入口の側の答え手(worker/entry/retirement_notices — 層 foundation は
;;; intent の型を読まない)。
;;;
;;; 待ちは出来事で起きる: 読みの thread が pipe の行を受けた時に、外部の Promise(CreateExternalPromise — 別の thread から満たしてよい)を
;;; 満たす。間隔で読み直さない。箱は「最後に受けた語」と「待ち(前に受けた語 after と Promise)」を持ち、語が after と違う待ちだけを起こす
;;; (同じ語の待ちは次の語まで残す)。pipe の EOF(shim が終わった)では何も起こさない — 止めと終わりは別の口(止めの合図)が運ぶ。
;;; 環境変数の値は <fd の番号>:<pipe の inode>(shim が置く)。環境変数が無い(worker の子でない — 手元で走らせた Program)か、fd がその
;;; pipe でない(環境変数だけを祖先から継いだ子孫の process — fd は継いでいない)なら読み手の無い箱を返す(語は来ない)。
(require doeff-hy.macros [defk deff <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import errno)
(import os)
(import stat)
(import threading)
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise Wait])


(defrecord NoticeWaiter
  "知らせの待ち 1 つ: after = 待ち手が前に受けた語(None = まだ何も)・promise = 語が after と違う値になった時に、その語で満たす外部の Promise。"
  (#^ (| str None) after)
  (#^ ExternalPromise promise))


(defclass NoticeBox []
  "知らせの pipe の行を受ける可変の箱(読みの thread が書き、待ちが読む — 1 つの錠の下)。last = 最後に受けた語(None = まだ)・
   waiters = 待ち(NoticeWaiter)の列。外部の Promise を満たすのは錠の外(満たした先で待ち手が箱をまた読む)。"

  (#^ threading.Lock lock)
  (#^ (| str None) last)
  (#^ (get tuple #(NoticeWaiter ...)) waiters)

  (defn #^ None __init__ [self]
    (setv self.lock (threading.Lock)
          self.last None
          self.waiters #()))

  (defn #^ (| str None) park [self #^ NoticeWaiter waiter]
    "今の語が waiter の after と違えば(まだ何も無い時を除く)その語を返して待ちを掛けない。同じ・まだ無ければ waiter を掛けて None を返す。"
    (with [self.lock]
      (if (and (is-not self.last None) (!= self.last waiter.after))
          self.last
          (do (setv self.waiters (+ self.waiters #(waiter)))
              None))))

  (defn #^ None forget [self #^ ExternalPromise promise]
    "取り消された待ち(待ち手の task が止まった)の Promise を外す。"
    (with [self.lock]
      (setv self.waiters (tuple (gfor w self.waiters :if (is-not w.promise promise) w)))))

  (defn #^ None receive [self #^ str word]
    "pipe の 1 行の語を置き、after がその語と違う待ちを起こす(同じ語の待ちは次の語まで残す)。"
    (with [self.lock]
      (setv self.last word
            waking (tuple (gfor w self.waiters :if (!= w.after word) w.promise))
            self.waiters (tuple (gfor w self.waiters :if (= w.after word) w))))
    (for [promise waking]
      (.complete promise word))))


(deff read-notice-lines [#^ int fd #^ NoticeBox box]  ; defk にできない: 読みの thread の target(threading が呼ぶ素の関数 — Program の外)
  {:pre [(: fd int) (: box NoticeBox)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "知らせの pipe の読み口 fd を行ごとに読み、前後の空白を除いた語を箱へ置くため(EOF まで — shim が終わると EOF)。空の行は捨てる。"
  (with [stream (os.fdopen fd "rb")]
    (for [line stream]
      (setv word (.strip (.decode line "utf-8" "surrogateescape")))
      (when word
        (.receive box word))))
  None)


(defrecord NoticePipe
  "宿が環境変数で渡した知らせの pipe の印: fd = 読み口の番号・inode = その pipe の inode(fd が同じ pipe かを確かめる)。"
  (#^ int fd)
  (#^ int inode))


(defk notice-pipe-of [env-name given]
  {:pre [(: env-name str) (: given str)] :post [(: % NoticePipe)] :tags {:context "doeff-cluster" :role "foundation"}}
  "環境変数 env-name の値 given(<fd の番号>:<pipe の inode>)を知らせの pipe の印に読むため。形が違えば名指しで断る(ValueError — 宿の
   契約の破れを黙って読み手の無い箱に倒さない)。"
  (val parts (.split given ":"))
  (when (not (and (= (len parts) 2) (all (gfor p parts (.isdigit p)))))
    (raise (ValueError (.format "知らせの pipe の環境変数 {} の値が <fd>:<inode> でない: {!r}" env-name given))))
  (NoticePipe :fd (int (get parts 0)) :inode (int (get parts 1))))


(defk own-pipe [pipe]
  {:pre [(: pipe NoticePipe)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "foundation"}}
  "印の fd がこの process で開いていて、印の inode の pipe かを確かめるため(環境変数だけを祖先から継いだ子孫では、同じ番号の fd が無いか
   別の file — 読まない)。開いていない fd(EBADF)の他の失敗はそのまま上げる。"
  (try
    (do (val seen (os.fstat pipe.fd))
        (and (stat.S-ISFIFO seen.st-mode) (= seen.st-ino pipe.inode)))
    (except [error OSError]
      (if (= error.errno errno.EBADF) False (raise)))))


(defk open-notice-box [env-name]
  {:pre [(: env-name str)] :post [(: % NoticeBox)] :tags {:context "doeff-cluster" :role "foundation"}}
  "宿が環境変数 env-name で渡した知らせの pipe の読み口の行を読む thread を立て、箱を返すため(頭の註)。環境変数が無い・fd がその pipe で
   ない(祖先から環境変数だけを継いだ子孫)なら読み手の無い箱(語は来ない)。読む fd はこの process の子へ継がせない。"
  (val box (NoticeBox))
  (val given (.get os.environ env-name))
  (when (is-not given None)
    (<- pipe NoticePipe (notice-pipe-of env-name given))
    (<- own bool (own-pipe pipe))
    (when own
      (os.set-inheritable pipe.fd False)
      (.start (threading.Thread :target read-notice-lines :args #(pipe.fd box) :daemon True :name "notice-pipe"))))
  box)


(defk next-notice [box after]
  {:pre [(: box NoticeBox) (: after (| str None))] :post [(: % str)] :tags {:context "doeff-cluster" :role "foundation"}}
  "箱の語が after と違う値になるまで待ち、その語を返すため(after = 前に受けた語・None = まだ何も)。待ちは外部の Promise で、読みの thread
   が満たす(間隔で読み直さない)。待ち手の task が取り消されたら待ちを箱から外す。外側に scheduler が要る。"
  (<- promise ExternalPromise (CreateExternalPromise))
  (val now (.park box (NoticeWaiter :after after :promise promise)))
  (if (is-not now None)
      now
      (do (.on-cancel promise (fn [] (.forget box promise)))
          (<- word str (Wait promise.future))
          word)))
