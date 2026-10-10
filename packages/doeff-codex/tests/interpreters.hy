;;; 検の解釈器(composition root)— 同じ筋書きの Program を、handler の組だけ替えて走らせる。
;;;
;;;   plain 効果を出さない純関数の検(行の分類・要求の組み立て)— handler を被せずに 1 回回す。
;;;   fake  fake の handler + 仮想の時計(sim-time-handler)。筋書きの答えは respond の規則。
;;;   stub  本番の handler + 替え玉の app-server(stub_cli/codex_app_server.py — 録った実物の行を返す)+ 壁の時計。
;;;
;;; 筋書きは ScenarioSettings の effect で自分の宣言(会話の宣言・待ちの上限)を読み、handler を知らない。
(require doeff-hy.macros [defhandler defk deff val])
(val MODULE-TAGS {:context "codex-test" :role "entry"})
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import pathlib [Path])
(import sys)
(import doeff [EffectBase run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler sync-time-handler])
(import doeff_codex.values [CodexHome CodexSessionSpec CodexInput])
(import doeff_codex.rpc [ApprovalPolicy SandboxMode])
(import doeff_codex.handler [CodexHost codex-handler])
(import doeff_codex.fake [FakeCodexWorld FakeReply fake-codex-handler])

(val PLAIN "plain")
(val FAKE "fake")
(val STUB "stub")
(val STUB-PATH (str (/ (. (Path __file__) (resolve) parent) "stub_cli" "codex_app_server.py")))
;; 筋書きの答えの規則(fake と stub で同じ): 入力に SLOW が在れば止めまで途中の文字を出し続け、画像を数える言い方なら画像の数を答え、
;; どちらでもなければ録った 1 ターンの 4 つの欠片。
(val SLOW-WORD "SLOW")
(val IMAGES-PHRASE "Count the attached images.")
(val ANSWER-PIECES #("Hel" "lo, " "wor" "ld."))
;; 元の CODEX_HOME の dir の名(検ごとの tmp の dir の下 — config.toml を 1 つ置く)。
(val CODEX-HOME-DIR "codex-home")


(defrecord Settings
  "筋書きが読む宣言: spec = 会話の宣言 / turn-timeout = 1 ターンの終わりを待つ上限(秒)/ interpreter = 名。"
  {:tags {:context "codex-test" :role "type"}}
  (#^ CodexSessionSpec spec)
  (#^ float turn-timeout)
  (#^ str interpreter))

(defclass [(dataclass :frozen True)] ScenarioSettings [EffectBase]
  "筋書きが自分の宣言を読む effect。")

;; 引数に残す理由: 解釈器ごとに違う宣言を、同じ筋書きへ渡す(Ask で読む設定の handler は無い — 検の composition root の値)。
(defhandler scenario-settings [settings]
  (ScenarioSettings []
    (resume settings)))


(defk respond [#^ CodexInput input]
  {:pre [(: input CodexInput)] :post [(: % FakeReply)] :tags {:context "codex-test" :role "entry"}}
  "fake の筋書きの答えを入力から決めるため(stub の替え玉と同じ規則)。"
  (cond
    (in SLOW-WORD input.text) (FakeReply :pieces #("slow-0 " "slow-1 ") :hold True)
    (in IMAGES-PHRASE input.text) (FakeReply :pieces #((.format "IMAGES {}" (len input.images))))
    True (FakeReply :pieces ANSWER-PIECES)))


(defk handlers-for [#^ str name]
  {:pre [(: name str)] :post [(: % list)] :tags {:context "codex-test" :role "entry"}}
  "解釈器の名から、筋書きの下に敷く handler の組(時計と codex の handler)を選ぶため。"
  (match name
    "fake" [(sim-time-handler :clock (SimClock))
            (fake-codex-handler (FakeCodexWorld (fn [input] (run (respond input)))))]
    "stub" [(sync-time-handler)
            (codex-handler (CodexHost #(sys.executable STUB-PATH) :launch-timeout 30.0))]
    _ (raise (ValueError (.format "知らない解釈器: {}" name)))))


(defk settings-for [#^ str name #^ Path tmp-path]
  {:pre [(: name str) (: tmp-path Path)] :post [(: % Settings)] :tags {:context "codex-test" :role "entry"}}
  "筋書きの宣言を作るため(作業の dir は検ごとの tmp の dir・元の CODEX_HOME はその下の codex-home・許可の問いを出させない方針)。"
  (Settings :spec (CodexSessionSpec :home (CodexHome :env {"PATH" "/usr/bin:/bin" "HOME" (str tmp-path)
                                                           "CODEX_HOME" (str (/ tmp-path CODEX-HOME-DIR))})
                                    :cwd (str tmp-path) :approval-policy ApprovalPolicy.NEVER :sandbox SandboxMode.READ-ONLY)
            :turn-timeout 30.0
            :interpreter name))


(deff build-interpreter [#^ str name #^ Path tmp-path]  ; defk にできない: pytest の fixture(framework の入口)が呼んで解釈器の関数を受ける
  {:pre [(: name str) (: tmp-path Path)] :post [(: % Callable)]}
  "名 → Program を走らせる関数。plain は handler を被せない(純関数の検)。"
  (when (= name PLAIN)
    (return (fn [program] (run program))))
  (setv codex-home (/ tmp-path CODEX-HOME-DIR))
  (.mkdir codex-home :exist-ok True)
  (.write-text (/ codex-home "config.toml") "# 検の元の CODEX_HOME\n" :encoding "utf-8")
  (setv stack (+ (run (handlers-for name)) [(scenario-settings (run (settings-for name tmp-path)))]))
  (fn [program] (run (scheduled (with_handlers stack program)))))
