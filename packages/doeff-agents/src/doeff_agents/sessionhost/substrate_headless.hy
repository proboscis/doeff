;;; headless substrate handler(agora-redesign #37・段 2 lane 2d)— Headless* effect の
;;; 子 process への束縛。
;;;
;;; real-substrate(substrate.hy)が生 IO の唯一の家であることは変えない: この handler は
;;; headless の effect(spawn / deliver / poll / interrupt / kill / has-session)だけを
;;; 解釈し、非 headless の substrate(Clock / Fs / Env / SessionStore / Tmux)は未処理の
;;; まま外側の real-substrate へ素通しする。設置は host.hy run-hosted の 1 箇所で、
;;; backend=headless の時に real-substrate の内側に挿す(herdr-substrate と同じ形)。
;;;
;;; 器(process・thread・events file)は headless_process.py(HeadlessRegistry)が持ち、
;;; 判断(stdin の綴り・手番の終わり・割り込み)は headless_protocol.py の Dialogue が
;;; 効果の値で返す — この handler はどちらも持たない(運ぶだけ)。registry は host の
;;; process に 1 つ(module の値)— session の名 → process の対応で、host が落ちれば
;;; process も消える(pipe の子)。再起動後の行は HeadlessPoll が None を返し、monitor が
;;; gone と読む。手番の途中のまま残った行は host の起動時の復帰(headless.hy
;;; recover-headless-rows)が HeadlessLiveness(pid の存在 + registry の所有)で観測して終端に倒す
;;; (段 10 lane 10h・agora-redesign #84)。
;;;
;;; 禁止 env(ANTHROPIC_API_KEY*)の hard reject は tmux の substrate と同じ 1 点
;;; (ensure-no-forbidden-agent-env)を使う。

(require doeff-hy.macros [defhandler])

(import os)

(import doeff_agents.sessionhost.effects [
  HeadlessDeliver
  HeadlessHasSession
  HeadlessInject
  HeadlessInterrupt
  HeadlessKill
  HeadlessKillAll
  HeadlessLiveness
  HeadlessPoll
  HeadlessSpawn])
(import doeff_agents.sessionhost.headless_process [HeadlessRegistry pid-exists])
(import doeff_agents.sessionhost.headless_protocol [BackendLiveness])
(import doeff_agents.sessionhost.substrate [
  SHELL-PROMPT-SUPPRESSING-ENV
  ensure-no-forbidden-agent-env])


;; host の process に 1 つの登記簿(session の名 → process)。
(setv HEADLESS-REGISTRY (HeadlessRegistry))


(defn headless-spawn-env [env]
  "子 process の実効 env: 呼び手の env(非 auth overlay ∪ binding 由来の auth env)を
   host の process env の上に重ねる(PATH・HOME を継ぐ — tmux が shell 経由で継ぐのと
   同じ物理)。prompt 抑制 env は tui の物理で headless には要らないが、置いても害は
   無く、shell を経由する hook との parity のため揃える。"
  (ensure-no-forbidden-agent-env env)
  (setv effective (dict os.environ))
  (for [[key value] SHELL-PROMPT-SUPPRESSING-ENV]
    (when (not-in key env)
      (setv (get effective key) value)))
  (for [[key value] (.items env)]
    (setv (get effective key) (str value)))
  effective)


(defhandler headless-substrate [registry]
  (HeadlessSpawn [session-name work-dir env argv events-path dialogue]
    (setv process (.spawn registry session-name argv work-dir
                          (headless-spawn-env env) events-path dialogue))
    (resume process.pid))

  (HeadlessDeliver [session-name text]
    (setv process (.get registry session-name))
    (resume (if (is process None) False (.deliver process text))))

  (HeadlessPoll [session-name]
    (setv process (.get registry session-name))
    (resume (if (is process None) None (.observe process))))

  (HeadlessInterrupt [session-name]
    (setv process (.get registry session-name))
    (resume (if (is process None) False (.interrupt process))))

  (HeadlessInject [session-name text]
    (setv process (.get registry session-name))
    (resume (if (is process None) False (.inject process text))))

  (HeadlessKill [session-name]
    (resume (.kill registry session-name)))

  (HeadlessHasSession [session-name]
    (resume (.has-alive registry session-name)))

  (HeadlessKillAll []
    (resume (.kill-all registry)))

  (HeadlessLiveness [session-name pid]
    ;; 観測だけ(判断は headless_protocol.recovery_verdict / backend_alive): pid の存在は kill 0、
    ;; 所有は registry の同じ名の生きた process の pid が一致すること。
    (setv process (.get registry session-name))
    (setv owned (and (is-not pid None)
                     (is-not process None)
                     (.alive process)
                     (= process.pid pid)))
    (resume (BackendLiveness :pid pid
                             :exists (and (is-not pid None) (pid-exists pid))
                             :owned owned))))
