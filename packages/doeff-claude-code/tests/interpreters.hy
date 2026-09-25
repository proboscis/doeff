;;; 検の解釈器 3 つ(composition root)— 同じ筋書きの Program を、handler の組だけ替えて走らせる。
;;;
;;;   fake  fake の handler + 仮想の時計(sim-time-handler)。筋書きの返事は scenario_rules の規則。
;;;   stub  本番の handler + 替え玉の CLI(stub_cli/claude.hy)+ 壁の時計(sync-time-handler)。API は撃たない。
;;;   real  本番の handler + 本物の claude(個人の profile・model haiku)+ 壁の時計。env DOEFF_CLAUDE_CODE_REAL_CONFIG_DIR が
;;;         無ければ skip・印 e2e(日次と着地の門は -m "not e2e" で除く)。会社の profile は使わない。
;;;
;;; 筋書きは ScenarioSettings の effect で自分の宣言(家・作業 dir・model・待ちの上限)を読む。
(require doeff-hy.macros [defhandler])
(import dataclasses [dataclass])
(import os)
(import pathlib [Path])
(import sys)
(import doeff [EffectBase run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler sync-time-handler])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [ClaudeCodeHost claude-code-handler])
(import doeff_claude_code.fake [FakeClaudeWorld FakeReply fake-claude-code-handler])
(import tests.scenario_rules [reply-for])

(setv FAKE "fake" STUB "stub" REAL "real")
(setv REAL-CONFIG-ENV "DOEFF_CLAUDE_CODE_REAL_CONFIG_DIR")
(setv STUB-PATH (str (/ (. (Path __file__) (resolve) parent) "stub_cli" "claude.hy")))
;; 子の claude に親の会話の印・hook の socket を継がせない(#602 の測定と同じ)。
(setv INHERITED-PREFIXES #("CLAUDECODE" "CLAUDE_CODE_" "CLAUDE_CONFIG_DIR" "AI_AGENT" "CLAUDE_PID"
                           "CLAUDE_EFFORT" "DOEFF_CLAUDE_CODE_"))


(defclass [(dataclass :frozen True)] Settings []
  "筋書きが読む宣言: base = 会話の宣言の雛形(家・作業 dir・model・settings)/ other-home = 持ち込みの元の家 /
   turn-timeout = 1 手番の終わりを待つ上限(秒)/ start-timeout = 道具が始まるのを待つ上限(秒)/ interpreter = 名。"
  (#^ ClaudeSessionSpec base)
  (#^ ClaudeHome other-home)
  (#^ float turn-timeout)
  (#^ str interpreter)
  (#^ str work-dir))

(defclass [(dataclass :frozen True)] ScenarioSettings [EffectBase])

(defhandler scenario-settings [settings]
  (ScenarioSettings []
    (resume settings)))


(defn real-marker-needed [name] (= name REAL))

(defn skip-reason [#^ str name]
  (cond
    (and (= name REAL) (not (.get os.environ REAL-CONFIG-ENV)))
      (.format "本物の claude の筋書きは env {} に個人の profile の CLAUDE_CONFIG_DIR を置いた時だけ走る" REAL-CONFIG-ENV)
    True ""))

(defn child-env [#^ str home-dir]
  "子の process の env: 今の env から親の会話の印を外し、HOME はそのまま(本物の claude は HOME の下の道具を使う)。"
  (dfor #(key value) (.items os.environ)
        :if (not (.startswith key INHERITED-PREFIXES))
        key value))

(defn settings-for [#^ str name #^ Path tmp-path]
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (setv home-dir (if (= name REAL) (get os.environ REAL-CONFIG-ENV) (str (/ tmp-path "home"))))
  (setv home (ClaudeHome home-dir (child-env home-dir)))
  (Settings :base (ClaudeSessionSpec :home home :cwd (str work)
                                     :model (if (= name REAL) "haiku" None)
                                     :settings {"disableAllHooks" True})
            :other-home (ClaudeHome (str (/ tmp-path "other-home")) (child-env home-dir))
            :turn-timeout (if (= name REAL) 180.0 60.0)
            :interpreter name
            :work-dir (str work)))

(defn fake-responder [#^ str text #^ tuple memory]
  (setv rule (reply-for text memory))
  (FakeReply (get rule "text") :tool-seconds (get rule "tool_seconds") :needs-permission (get rule "permission")))

(defn handlers-for [#^ str name]
  (cond
    (= name FAKE) [(sim-time-handler :clock (SimClock)) (fake-claude-code-handler (FakeClaudeWorld fake-responder))]
    (= name STUB) [(sync-time-handler)
                   (claude-code-handler (ClaudeCodeHost #(sys.executable "-m" "hy" STUB-PATH) (clock-of (sync-time-handler))
                                                        :launch-timeout 60.0))]
    (= name REAL) [(sync-time-handler)
                   (claude-code-handler (ClaudeCodeHost #("claude") (clock-of (sync-time-handler)) :launch-timeout 120.0))]
    True []))

(defn build-interpreter [#^ str name #^ Path tmp-path]
  "名 → Program を走らせる関数。筋書きに要らない plain は scheduler だけ(純関数の検)。"
  (setv stack (+ (handlers-for name) [(scenario-settings (settings-for name tmp-path))]))
  (fn [program] (run (scheduled (with_handlers stack program)))))
