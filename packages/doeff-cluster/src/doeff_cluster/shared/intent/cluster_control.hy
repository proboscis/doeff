;;; cluster を外から動かす契約の effect — 系を宣言する・Service の準備の状態を読む・job を落とす・worker や coordinator を止める
;;; (2026-10-03・ADR-DOE-CLUSTER-001 R8 の追補 (1)・#3020 の決め 1・#3029)。
;;;
;;;   (<- names (Redeclare system))            ; 系を宣言し直す(答え = 宣言した Service の名の tuple)
;;;   (<- ready (ReadinessOf "tally"))         ; coordinator が数えている Service の準備の状態(ServiceReadiness)
;;;   (<- n (Crash "tally"))                   ; job の動いている process を全部 exit 1 で落とす(答え = 落とした数)
;;;
;;; テストの Program はこの effect だけを使い、どの cluster に話すかを知らない。違いは handler が吸う(R8):
;;;   sim-cluster(sim/local.hy)… 同じ process の中の本物の coordinator と、偽の機体の上の本物の worker が答える。
;;;   手元の 1 台の cluster と k3s の cluster の handler は #3031〜#3034 で足す。壊す effect(Crash・KillWorker・StopWorker・
;;;   StopCoordinator・CrashCoordinator)に k3s の handler は答えない(R8 の追補 (3))。
;;;
;;; sim だけが答えられる観測(届いた報告の全部・process の履歴・網を切る・固める・5xx を返させる)は sim/local.hy に残す。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(require doeff-hy.macros [defeffect])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff_cluster.shared.intent.service_model [System])


(defrecord ServiceReadiness
  "coordinator の Service の status の ready(ReadinessOf の答え)。state = Ready | NotReady | Unknown | Missing(Service が無い)・
   reason = その理由の 1 行。"
  (#^ str state)
  (#^ str reason))


(defeffect Redeclare
  "系を宣言し直す(版 = environ か引数を変えた系の値)。答え = 宣言した Service の名の tuple。"
  {:fields [(: system System)]
   :answer (get tuple #(str ...))
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ReadinessOf
  "coordinator が数えている Service name の準備の状態(ServiceReadiness)。"
  {:fields [(: name str)]
   :answer ServiceReadiness
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect Crash
  "job name の動いている process を全部 exit 1 で落とす(子の異常終了)。答え = 落とした数。"
  {:fields [(: name str)]
   :answer int
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect KillWorker
  "worker name が node ごと死ぬ — 動いている子 process は全部 exit -9 で止まり(中で Spawn した task も)、heartbeat が止まる
   (coordinator は lease の後に生きていないと数える)。答え = 止めた process の数(もう死んでいれば 0)。"
  {:fields [(: name str)]
   :answer int
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect StopWorker
  "worker name を優雅に止める(SIGTERM — 宣言を空として全 job を止めの手順で回収し、lease を返して抜ける)。抜けるまで待つ。答え = None。"
  {:fields [(: name str)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect StopCoordinator
  "coordinator を次の拍で優雅に止め(止めの合図 — 取った要求には返事を済ませる)、seconds 秒止めてから作り直す(同じ置き場から読み直す)。
   止まっている間の要求は接続の失敗。答え = None(止まるのは次の拍)。"
  {:fields [(: seconds float)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CrashCoordinator
  "coordinator の次の Persist を失敗させる(fsync の失敗 — 返事をせずに落ちる。取った要求の送り手には接続の失敗・その拍の書きは置き場に
   残らない)。seconds 秒の後に同じ置き場から読み直して作り直す。答え = None(落ちるのは次の書き)。"
  {:fields [(: seconds float)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})
