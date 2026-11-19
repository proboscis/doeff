;;; unix socket の上の 1 行の頼みと 1 行の答え(汎用の I/O — 行の中身を読まない)。受ける側(LineInbox)は process に 1 つの受けの
;;; thread で接続を受け、接続ごとの短い thread が 1 行を読んで箱に置き、待ちを起こす。待ちは出来事で起きる(外部の Promise —
;;; CreateExternalPromise は別の thread から満たしてよい)。間隔で読み直さない。行の中身を型に読むのは呼び手の層(worker の預かりの口 —
;;; worker/entry/hosted_serve。層 foundation は intent の型を読まない)。頼む側(line-call)は接続して 1 行を書き、1 行の答えを読んで返す。
;;;
;;; 置き場: socket は持ち主だけの dir(0700)の中に置く。前の process が残した同じ path の socket は消してから bind する。dir が他人の物か
;;; 他人に開いていれば受けを断る(同じ機体の他の利用者が頼みを書けない — /tmp の下に置くため)。
(require doeff-hy.macros [defk deff <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import contextlib)
(import os)
(import posixpath)
(import socket)
(import stat)
(import threading)
(import doeff_core_effects [CreateExternalPromise ExternalPromise Wait])

;; 1 行の上限(byte)。越える頼みは読まずに切る。
(val LINE-LIMIT (* 1024 1024))
;; 接続から 1 行を読む上限の秒(書かずに繋いだままの相手で thread を残さない)。
(val READ-SECONDS 5.0)
;; 受けの列の長さ。
(val BACKLOG 64)


(defrecord LineCall
  "受けた頼み 1 つ: text = 1 行(終わりの改行を除く)・conn = 答えを書く接続(answer-call が 1 度だけ書いて閉じる)。"
  (#^ str text)
  (#^ socket.socket conn))


(defrecord LineCallFailed
  "頼む側の失敗(繋げない・答えが来ない・答えが切れた)。detail = OS の訳の文。"
  (#^ str detail))


(defclass LineInbox []
  "受けた頼みの箱(受けの thread が書き、待ちが読む — 1 つの錠の下)。calls = まだ渡していない頼み・bells = 頼みが来たら満たす外部の
   Promise。Promise を満たすのは錠の外。"

  (defn __init__ [self #^ str path #^ socket.socket server]
    "受けの socket(bind と listen を済ませた物)を持ち、空の箱で始めるため。"
    (setv self.path path
          self.server server
          self.lock (threading.Lock)
          self.calls #()
          self.bells #()
          self.closing (threading.Event)
          self.thread None))

  (defn #^ tuple take [self]
    "まだ渡していない頼みを全部取り出す(取り出した物は箱から消える)。"
    (with [self.lock]
      (setv taken self.calls
            self.calls #()))
    taken)

  (defn #^ bool arm [self #^ ExternalPromise bell]
    "頼みが箱に在れば真(掛けない)。無ければ bell を掛けて偽(次の頼みで満たす)。"
    (with [self.lock]
      (if self.calls
          True
          (do (setv self.bells (+ self.bells #(bell)))
              False))))

  (defn #^ None forget [self #^ ExternalPromise bell]
    "取り消された待ちの bell を外す。"
    (with [self.lock]
      (setv self.bells (tuple (gfor b self.bells :if (is-not b bell) b)))))

  (defn #^ None received [self #^ LineCall call]
    "受けた頼みを箱に置き、掛かった bell を全部満たす(受けの thread が呼ぶ)。"
    (with [self.lock]
      (setv self.calls (+ self.calls #(call))
            ringing self.bells
            self.bells #()))
    (for [bell ringing]
      (.complete bell True)))

  (defn #^ None close [self]
    "受けを閉じて socket の file を消す(worker の終わり)。受けた後でまだ答えていない頼みの接続も閉じる。"
    (.set self.closing)
    (with [(contextlib.suppress OSError)]
      (.shutdown self.server socket.SHUT_RDWR))
    (with [(contextlib.suppress OSError)]
      (.close self.server))
    (with [(contextlib.suppress OSError)]
      (os.unlink self.path))
    (for [call (.take self)]
      (with [(contextlib.suppress OSError)]
        (.close call.conn)))))


(deff private-dir [path]  ; cannot be a defk: 起動の時に Program の外で呼ぶ置き場の用意(受けの thread を立てる前)
  {:pre [(: path str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "socket を置く dir を持ち主だけの物(0700)として用意するため。在れば持ち主と mode を確かめ、他人の物か他人に開いていれば断る
   (PermissionError — 他の利用者が頼みを書ける口を開かない)。"
  (with [(contextlib.suppress FileExistsError)]
    (os.mkdir path 0o700))
  (setv seen (os.lstat path))
  (when (or (not (stat.S_ISDIR seen.st_mode)) (!= seen.st-uid (os.getuid)) (& seen.st-mode 0o077))
    (raise (PermissionError (.format "socket の dir {} が持ち主だけの dir でない(uid {}・mode {:o})" path seen.st-uid
                                     (stat.S_IMODE seen.st-mode))))))


(deff read-line [conn]  ; cannot be a defk: 接続ごとの OS の thread の上で読む(scheduler の外)
  {:pre [(: conn socket.socket)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "接続から改行までの 1 行を読むため(LINE-LIMIT の内・待ちの上限は呼び手が接続に掛けた timeout)。読めなければ None。"
  (setv data b"")
  (while True
    (setv chunk (try (.recv conn 65536) (except [OSError] None)))
    (when (not chunk)
      (return None))
    (setv data (+ data chunk))
    (when (in b"\n" chunk)
      (return (.decode (get (.split data b"\n" 1) 0) "utf-8" "replace")))
    (when (> (len data) LINE-LIMIT)
      (return None))))


(deff serve-one [inbox conn]  ; cannot be a defk: 接続ごとの OS の thread の本体
  {:pre [(: inbox LineInbox) (: conn socket.socket)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "1 つの接続から 1 行を読んで箱に置くため。読めなければ接続を閉じる。"
  (.settimeout conn READ-SECONDS)
  (setv text (read-line conn))
  (if (is text None)
      (with [(contextlib.suppress OSError)]
        (.close conn))
      (.received inbox (LineCall :text text :conn conn))))


(deff accept-loop [inbox]  ; cannot be a defk: 受けの OS の thread の本体
  {:pre [(: inbox LineInbox)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "閉じるまで接続を受け、接続ごとに 1 行を読む短い thread を立てるため。"
  (while (not (.is-set inbox.closing))
    (setv accepted (try (.accept inbox.server) (except [OSError] None)))
    (if (is accepted None)
        (when (not (.is-set inbox.closing))
          (.wait inbox.closing 0.1))
        (.start (threading.Thread :target serve-one :args #(inbox (get accepted 0)) :daemon True
                                  :name "line-socket-read")))))


(deff open-line-inbox [path]  ; cannot be a defk: 起動の時に 1 度だけ受けの thread を立てる(Program の外の資源)
  {:pre [(: path str)] :post [(: % LineInbox)] :tags {:context "doeff-cluster" :role "foundation"}}
  "path に受けの socket を開き(持ち主だけの dir・前の socket の file は消す・socket の file は 0600)、受けの thread を立てて箱を返すため。"
  (private-dir (posixpath.dirname path))
  (with [(contextlib.suppress FileNotFoundError)]
    (os.unlink path))
  (setv server (socket.socket socket.AF_UNIX socket.SOCK_STREAM))
  (.bind server path)
  (os.chmod path 0o600)
  (.listen server BACKLOG)
  (setv inbox (LineInbox path server))
  (setv inbox.thread (threading.Thread :target accept-loop :args #(inbox) :daemon True :name "line-socket-accept"))
  (.start inbox.thread)
  inbox)


(defk next-calls [inbox]
  {:pre [(: inbox LineInbox)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "foundation"}}
  "箱に頼みが来るまで待ち、来た頼み(LineCall)を全部返すため。待ちは外部の Promise で、受けの thread が満たす。待ち手の task が
   取り消されたら bell を箱から外す。外側に scheduler が要る。"
  (val now (.take inbox))
  (when now
    (return now))
  (<- bell ExternalPromise (CreateExternalPromise))
  (when (not (.arm inbox bell))
    (.on-cancel bell (fn [] (.forget inbox bell)))
    (<- (Wait bell.future)))
  (.take inbox))


(deff answer-call [call text]  ; cannot be a defk: 接続への短い書き(答えの 1 行)と閉じ
  {:pre [(: call LineCall) (: text str) (not-in "\n" text)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "頼みに 1 行で答えて接続を閉じるため。相手が先に切っていれば捨てる(答えを待たずに去った頼み手)。"
  (with [(contextlib.suppress OSError)]
    (.settimeout call.conn READ-SECONDS)
    (.sendall call.conn (.encode (+ text "\n") "utf-8")))
  (with [(contextlib.suppress OSError)]
    (.close call.conn)))


(deff line-call [path text seconds]  ; cannot be a defk: 頼む側の短い往復(接続・1 行の書き・1 行の読み)
  {:pre [(: path str) (: text str) (not-in "\n" text) (: seconds float)] :post [(: % (| str LineCallFailed))]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "path の口へ 1 行を書き、1 行の答えを読んで返すため(seconds = 往復の上限)。繋げない・答えが無い時は LineCallFailed。"
  (try
    (with [conn (socket.socket socket.AF_UNIX socket.SOCK_STREAM)]
      (.settimeout conn seconds)
      (.connect conn path)
      (.sendall conn (.encode (+ text "\n") "utf-8"))
      (setv answer (read-line conn))
      (if (is answer None)
          (LineCallFailed :detail (.format "{} が答えずに切った" path))
          answer))
    (except [error OSError]
      (LineCallFailed :detail (.format "{}: {}" path error)))))
