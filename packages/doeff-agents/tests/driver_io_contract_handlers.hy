;;; driver 層の I/O の契約テストの解釈器(composition root)— 同じ契約の Program を、io_effects の handler だけ替えて走らせる。
;;;
;;;   driver-io       本物: driver-io-handler(一時 dir の中の実 file・実 unix socket・実の時計)
;;;   fake-driver-io  fake: fake-driver-io-handler(FakeIoWorld — 記憶の中の file 系と台本の socket)
;;;
;;; 契約の世界は解釈器ごとに同じ形で用意する:
;;;   * 置き場の根(ContractRoot の答え): 本物 = 走るたびに新しい一時 dir(実の path)・fake = 記憶の中の dir の MEMORY-ROOT
;;;   * 環境変数(CONTRACT-ENV): 本物 = 走る間だけ os.environ に置き(UNSET-NAME は外し)、走った後に前の値へ戻す・fake = world の env
;;;   * 繋がる socket(根の下の LIVE-SOCKET): 本物 = 走る間だけ聞く実の unix socket・fake = world の sockets。どちらも 1 行を
;;;     answer-line で答える(答えの関数は 1 つ)
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk deff defhandler <- val var])
(import dataclasses [dataclass])
(import os)
(import socket)
(import tempfile)
(import threading)
(import doeff [EffectBase Program with_handlers])
(import doeff_agents.io_fake [FakeIoWorld fake-driver-io-handler])
(import doeff_agents.io_handlers [driver-io-handler])

(val DRIVER-IO "driver-io")
(val FAKE-DRIVER-IO "fake-driver-io")
(val MEMORY-ROOT "/contract-root")
(val LIVE-SOCKET "live.sock")

;; 契約の世界の環境変数(EnvValue が読む)と、世界に無い名。
(val SET-NAME "DOEFF_DRIVER_IO_SET")
(val UNSET-NAME "DOEFF_DRIVER_IO_UNSET")
(val CONTRACT-ENV {SET-NAME "在る値"})


(defclass [(dataclass :frozen True)] ContractRoot [EffectBase]
  "契約の置き場の根(在る dir の絶対 path)を求める効果。")


(defhandler contract-root [root]
  ;; 引数に残す理由: 根は解釈器ごとに組み立ての側で決まる値(本物は走るたびに作る一時 dir)。
  (ContractRoot []
    (resume root)))


(deff answer-line [line]  ; defk にできない: fake の handler が台本の callback として素の値で呼ぶ(world の sockets)
  {:pre [(: line str)] :post [(: % str)]}
  "繋がる socket が受けた 1 行への答えの 1 行(本物の聞き手と fake の台本の両方が呼ぶ)。"
  (+ "got:" line))


(deff serve-lines [server]  ; defk にできない: threading.Thread が別の thread で呼ぶ callback
  {:pre [(: server socket.socket)] :post [(: % None)]}
  "聞く socket の接続を 1 つずつ受け、1 行を読んで answer-line で答える(何も送らずに閉じた接続 — 在否の観測 — には答えない)。
   聞き手が閉じられたら(accept が OSError)終わる。"
  (try
    (while True
      (with [connection (get (.accept server) 0)]
        (with [reader (.makefile connection "r" :encoding "utf-8")]
          (match (.readline reader)
            "" None
            line (.sendall connection (.encode (answer-line line) "utf-8"))))))
    (except [OSError] None))
  None)


(defk restore-environment [saved]
  {:pre [(: saved dict)] :post [(: % None)] :tags {:context "driver-io-test" :role "foundation"}}
  "os.environ の名を saved の値(None = 無い)へそろえる。"
  (for [#(name value) (.items saved)]
    (match value
      None (.pop os.environ name None)
      _ (.update os.environ {name value})))
  None)


(defk under-driver-io [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "driver-io-test" :role "foundation"}}
  "本物の driver-io-handler の下で program を走らせる。根は新しい一時 dir(実の path)、CONTRACT-ENV は走る間だけ os.environ に置き
   (UNSET-NAME は外し)、根の下の LIVE-SOCKET で実の unix socket が聞く。走った後に聞き手を閉じ、環境を前の値へ戻す。"
  (var answer None)
  (with [directory (tempfile.TemporaryDirectory)]
    (val root (os.path.realpath directory))
    (val saved (dfor name [SET-NAME UNSET-NAME] name (.get os.environ name)))
    (<- (restore-environment {SET-NAME (get CONTRACT-ENV SET-NAME) UNSET-NAME None}))
    (val server (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
    (.bind server (os.path.join root LIVE-SOCKET))
    (.listen server)
    (.start (threading.Thread :target serve-lines :args #(server) :daemon True))
    (try
      (<- ran (with_handlers [(contract-root root) driver-io-handler] program))
      (:= answer ran)
      (finally
        (.shutdown server socket.SHUT-RDWR)
        (.close server)
        (<- (restore-environment saved)))))
  answer)


(defk under-fake-driver-io [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "driver-io-test" :role "foundation"}}
  "fake の fake-driver-io-handler の下で program を走らせる。世界は根の dir だけが在り、環境は CONTRACT-ENV、根の下の LIVE-SOCKET が
   answer-line で答える。"
  (val world (FakeIoWorld :dirs [MEMORY-ROOT]
                          :env CONTRACT-ENV
                          :sockets {(os.path.join MEMORY-ROOT LIVE-SOCKET) answer-line}))
  (<- answer (with_handlers [(contract-root MEMORY-ROOT) (fake-driver-io-handler world)] program))
  answer)


(val INTERPRETERS {DRIVER-IO under-driver-io
                   FAKE-DRIVER-IO under-fake-driver-io})
