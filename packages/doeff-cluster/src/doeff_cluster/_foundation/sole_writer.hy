;;; 耐久の置き場の書き手を 1 つの process に保つ錠(汎用の I/O)— 置き場の dir の傍の錠の file に fcntl.flock の排他を持つ。
;;; 入れ替えで新旧 2 つの process が同じ置き場の上に重なる間、新しい process は受け口を開いてから(届いた要求は列で待たせ、接続は
;;; 断らない)この錠を待ち、前の process が離した後に置き場を読み直す。前の process は調停ループを抜けた直後に離す(process の
;;; 終わりの行の送りを待たない)。process が落ちても kernel が錠を離すので、持ち主の無い錠は残らない。flock の錠は同じ機体の上でだけ効く
;;; (置き場の volume は 1 つの機体の dir)。
;;;
;;; 待ちは止めの合図を短い間隔で見る(合図を受けたら置き場を読まずに抜ける — 入れ替えが取り消されて新しい process が先に止められる時、
;;; 錠を待ったまま猶予の満了で殺されない)。flock は離された事を知らせる道を、塞いで待つ形の他に持たず、塞いで待つ flock は合図の
;;; 受け手が走った後も待ち直す(PEP 475)ので、待たない取り(LOCK_NB)を間隔を置いて繰り返す。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import fcntl)
(import os)
(import time)
(import typing [Callable])


(defclass SoleWriter []
  "置き場の書き手の錠(頭の註)。path = 錠の file の path・fd = 持っている錠の file の番号(持っていなければ None)。"
  (defn #^ None __init__ [self #^ str path]
    (setv #^ str self.path path #^ (| int None) self.fd None))

  (defn #^ bool try-take [self]
    "錠を待たずに取ってみるため。答え = 持っているか(既に持っていれば真)。"
    (when (is-not self.fd None)
      (return True))
    (os.makedirs (os.path.dirname self.path) :exist-ok True)
    (setv fd (os.open self.path (| os.O-RDWR os.O-CREAT) 0o600))
    (try
      (fcntl.flock fd (| fcntl.LOCK-EX fcntl.LOCK-NB))
      (except [BlockingIOError]
        (os.close fd)
        (return False)))
    (setv self.fd fd)
    True)

  (defn #^ (| float None) wait [self #^ Callable stopping #^ float poll-seconds]
    "前の持ち主が離すまで錠を待つため。stopping = 止めの合図を読む関数(真なら待ちを抜ける)・poll-seconds = 取り直しの間隔。
     答え = 取るまでに待った秒(取らずに止めの合図で抜けたら None)。"
    (setv started (time.monotonic))
    (while (not (.try-take self))
      (when (stopping)
        (return None))
      (time.sleep poll-seconds))
    (- (time.monotonic) started))

  (defn #^ None release [self]
    "持っている錠を離すため(持っていなければ何もしない)。"
    (setv fd self.fd)
    (when (is-not fd None)
      (setv self.fd None)
      (try (fcntl.flock fd fcntl.LOCK-UN) (finally (os.close fd))))
    None))
