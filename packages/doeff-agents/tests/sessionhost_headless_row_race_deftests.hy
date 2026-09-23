;;; 監視の読み → 送信の書き → 監視の書き、の順でも送信の pid と待機中の印が行に残る
;;; (実弾 2026-09-23 18:41 JST・pod agentd-pool-0・手番 aj-0QCM2MSBX1C79PSM70GPQ255AD)。
;;;
;;; 起きたこと: host の監視ループ(observe-headless-row)が行を読んでから器を観測する間に、
;;; session.send(headless-send-program)が降りていた process を `--resume` で起こし直して
;;; 新しい pid・awaiting True・turn_ended_at None を行に書いた。監視ループはその後、送信の
;;; 前に読んだ古い行を**全欄**で書き戻した(store の merge が「行の写し全体」を重ねる形だった)。
;;; 行は終了済みの旧 pid を指し・awaiting False・turn_ended_at は前の手番のまま になり、agentd の
;;; 生存判定(行の pid が登記の生きた process と一致するか)が偽を返して手番を session-lost に
;;; 倒した。新しい process は手番を完了していたが、その答えは捨てられた。
;;;
;;; 直した不変量(store の merge が持つ): **書き手は自分が導いた欄だけを書く**。SessionStoreUpsert は
;;; 「読んだ行(observed)」を運び、store は読んだ値から変わった欄だけを既存の行に重ねる。
;;; 監視ループが触らなかった backend_ref / awaiting / turn_ended_at は、間に着地した送信の値のまま残る。
;;;
;;; 検の形: 実 SQLite の store を最外に置き、監視側の器の観測(HeadlessPoll)の**その拍**に送信の
;;; program を丸ごと走らせる(handler の節から外側へ program を委ねる)— 読みと書きの間に送信が
;;; 着地する、あの順序を決定的に再現する。

(require doeff-hy.macros [deftest defk <- defhandler])

(import dataclasses [replace])
(import datetime [datetime timezone])
(import os)
(import shutil)
(import tempfile)

(import doeff_agents.sessionhost.effects [
  BuildHeadlessLaunch
  ClockNow
  EnvGet
  FsMakeDirs
  FsReadText
  FsRemoveFile
  FsWriteTextAtomic
  HeadlessDeliver
  HeadlessPoll
  HeadlessSpawn
  LogLine
  SessionRow
  session-store-get
  session-store-upsert])
(import doeff_agents.sessionhost.headless [headless-send-program observe-headless-row])
(import doeff_agents.sessionhost.headless_protocol [HeadlessObservation])
(import doeff_agents.sessionhost.impls.headless_argv [build-headless])
(import doeff_agents.sessionhost.impls.claude_code [claude-code-impl])
(import doeff_agents.sessionhost.store [StoreActor sqlite-session-store])


(setv OLD-PID 266549)
(setv NEW-PID 4242)
(setv PREVIOUS-TURN-ENDED-AT "2026-09-23T09:30:00+00:00")


(defclass RaceWorld []
  "送信側の器の写し + 監視側の観測の拍に差し込む送信。"
  (defn __init__ [self]
    (setv self.send-on-poll None)   ;; 監視側の HeadlessPoll の拍に走らせる program(1 度だけ)
    (setv self.spawns [])
    (setv self.delivered [])
    (setv self.monitor-polls 0)))


(defhandler fake-send-substrate [world]
  "送信側から見た器: process は降りている(Poll None)→ --resume で起こし直す(Spawn = 新 pid)→ 本文を書く。"
  (HeadlessPoll [session-name]
    (resume None))
  (HeadlessSpawn [session-name work-dir env argv events-path dialogue]
    (.append world.spawns (list argv))
    (resume NEW-PID))
  (HeadlessDeliver [session-name text attachments]
    (.append world.delivered text)
    (resume True))
  (BuildHeadlessLaunch [agent-type params]
    (<- built (build-headless agent-type params))
    (resume built))
  (ClockNow []
    (resume (datetime 2026 9 23 9 41 4 :tzinfo timezone.utc)))
  (EnvGet [name]
    (resume None))
  (LogLine [text]
    (resume None))
  (FsMakeDirs [path]
    (resume None))
  (FsWriteTextAtomic [path text tmp-suffix]
    (resume None))
  (FsRemoveFile [path]
    (resume False))
  (FsReadText [path]
    (resume None)))


(defhandler fake-monitor-substrate [world]
  "監視側から見た器: 行を読んだ後の観測の拍に、送信が丸ごと着地する(実弾の順序)。
   観測の値は「起こし直された新しい process が走っている」。"
  (HeadlessPoll [session-name]
    (setv world.monitor-polls (+ world.monitor-polls 1))
    (setv pending world.send-on-poll)
    (setv world.send-on-poll None)
    (when (is-not pending None)
      (<- _ (pending)))
    (resume (HeadlessObservation :alive True :exit-code None :accepts-turn False)))
  (ClockNow []
    (resume (datetime 2026 9 23 9 41 10 :tzinfo timezone.utc))))


(defn #^ SessionRow warm-row-with-the-old-process []
  "前の手番が終わった温かい行: process は降りていて、行は旧 pid を指す。"
  (SessionRow :session-id "s1" :session-name "doeff-s1" :pane-id "headless:doeff-s1"
              :agent-type "claude" :lifecycle "multi_turn" :status "running"
              :started-at "2026-09-23T09:00:00+00:00"
              :last-observed-at "2026-09-23T09:40:00+00:00"
              :awaiting-response False
              :turn-ended-at PREVIOUS-TURN-ENDED-AT
              :work-dir "/work/dir"
              :effective-identity {"CLAUDE_CONFIG_DIR" "/x/claude"}
              :backend-kind "headless"
              :backend-ref {"session_name" "doeff-s1" "pid" OLD-PID
                            "events_path" "/state/events/s1.events.jsonl"
                            "argv" [] "socket_path" ""}
              :launch-overlay {"session_env" {} "model" None "effort" None "mcp_servers" {}}
              :conversation {"session_id" "conv-1"} :generation 1))


(defn with-race-stack [actor world program]
  "handler の重ね: 実 SQLite の store(最外)← 送信側の器 ← claude の kind の impl ← 監視側の器 ← program。
   監視側の節から委ねた送信の program の effect は外側(送信側の器・impl・store)へ届く。"
  ((sqlite-session-store actor)
   ((fake-send-substrate world)
    ((claude-code-impl "/opt/doeff-sessionhost")
     ((fake-monitor-substrate world)
      program)))))


(deftest test-the-monitor-write-after-the-send-keeps-the-new-pid-and-the-awaiting-latch
  ;; 監視の読み → 送信の書き(新 pid・awaiting True・turn_ended_at None)→ 監視の書き。
  ;; 監視は自分が導いた欄(観測の時刻)だけを書き、送信の欄は残る。
  (setv d (tempfile.mkdtemp))
  (try
    (setv actor (StoreActor (os.path.join d "agentd.sqlite")))
    (try
      (setv world (RaceWorld))
      (setv stale (warm-row-with-the-old-process))
      (<- _ (with-race-stack actor world (session-store-upsert stale)))
      ;; 監視側の観測の拍に送信を差し込む
      (setv world.send-on-poll
            (fn [] (headless-send-program "s1" "next turn" True {} {})))
      (<- _ (with-race-stack actor world (observe-headless-row stale)))
      (assert (= world.monitor-polls 1) world.monitor-polls)
      (assert (= (len world.spawns) 1) #("送信は --resume で 1 度起こし直す" world.spawns))
      (assert (= world.delivered ["next turn"]) world.delivered)
      (<- after (with-race-stack actor world (session-store-get "s1")))
      (assert (= (get after.backend-ref "pid") NEW-PID)
              #("監視の書き戻しが送信の pid を旧 pid へ戻した" after.backend-ref))
      (assert (is after.awaiting-response True)
              #("監視の書き戻しが待機中の印を下ろした" after.awaiting-response))
      (assert (is after.turn-ended-at None)
              #("監視の書き戻しが前の手番の終わりの印を復活させた" after.turn-ended-at))
      (assert (= after.awaiting-response-since "2026-09-23T09:41:04+00:00") after.awaiting-response-since)
      ;; 監視が導いた欄は書かれている
      (assert (= after.last-observed-at "2026-09-23T09:41:10+00:00") after.last-observed-at)
      (assert (= after.status "running") after.status)
      (finally (.close actor)))
    (finally (shutil.rmtree d :ignore-errors True))))


(deftest test-the-monitor-still-lands-the-turn-end-it-derived
  ;; 対照: 送信が間に無ければ、監視が導いた欄(手番の終わり)はそのまま行に載る —
  ;; 「変わった欄だけ書く」は監視の仕事を削らない。
  (setv d (tempfile.mkdtemp))
  (try
    (setv actor (StoreActor (os.path.join d "agentd.sqlite")))
    (try
      (setv world (RaceWorld))
      (setv in-flight (replace (warm-row-with-the-old-process)
                               :awaiting-response True
                               :awaiting-response-since "2026-09-23T09:35:00+00:00"
                               :turn-ended-at None))
      (<- _ (with-race-stack actor world (session-store-upsert in-flight)))
      (<- _ ((sqlite-session-store actor)
             ((handle-turn-ended world)
              (observe-headless-row in-flight))))
      (<- after (with-race-stack actor world (session-store-get "s1")))
      (assert (is after.awaiting-response False) after.awaiting-response)
      (assert (= after.turn-ended-at "2026-09-23T09:41:10+00:00") after.turn-ended-at)
      (assert (is after.awaiting-response-since None) after.awaiting-response-since)
      (assert (= (get after.backend-ref "pid") OLD-PID) after.backend-ref)
      (finally (.close actor)))
    (finally (shutil.rmtree d :ignore-errors True))))


(import doeff_agents.sessionhost.headless_protocol [TurnEnded])


(defhandler handle-turn-ended [world]
  "対照の器: 手番の終わりを 1 つ読んだ観測。"
  (HeadlessPoll [session-name]
    (resume (HeadlessObservation :alive False :exit-code 0
                                 :ended #((TurnEnded :ok True :detail "")))))
  (ClockNow []
    (resume (datetime 2026 9 23 9 41 10 :tzinfo timezone.utc))))
