;;; 検の解釈器(composition root)— 同じ筋書きの Program を、handler の組だけ替えて走らせる。
;;;
;;;   fake  fake の handler(FakeRunWorld)+ 仮想の時計(sim-time-handler)。負荷の振る舞いは workloads.fake-behavior-of。
;;;
;;; docker・process・k3s の handler を足す card(Y-7・Y-8・Y-9)は、ここに解釈器の名と組(handlers-for・settings-for)を足し、
;;; test_contract.hy の各 deftest の :interpreters に名を足す。筋書きと workloads.hy の約束は変えない — 同じ契約を通す事が目的。
;;;
;;; 筋書きは ScenarioSettings の effect で自分の宣言(負荷の image・時間の許容)を読む。
(require doeff-hy.macros [defhandler])
(import dataclasses [dataclass])
(import pathlib [Path])
(import doeff [EffectBase run with_handlers])
(import doeff_core_effects [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_run [FakeRunWorld fake-run-handler])
(import tests.workloads [fake-behavior-of])

(setv FAKE "fake")
(setv FAKE-IMAGE "registry.invalid/contract-workload@sha256:0000000000000000000000000000000000000000000000000000000000000000")
(setv FAKE-MISSING-IMAGE "registry.invalid/missing@sha256:1111111111111111111111111111111111111111111111111111111111111111")


(defclass [(dataclass :frozen True)] Settings []
  "筋書きが読む宣言: image = 契約の負荷 contract-workload が入った image / missing-image = 起動できない image の名 /
   slack = 時間の比べの許容(秒 — 仮想の時計は刻み 1 マイクロ秒、壁の時計の handler はもっと広く)/ interpreter = 名。"
  (#^ str image)
  (#^ str missing-image)
  (#^ float slack)
  (#^ str interpreter))

(defclass [(dataclass :frozen True)] ScenarioSettings [EffectBase])

(defhandler scenario-settings [settings]
  (ScenarioSettings []
    (resume settings)))


(defn settings-for [#^ str name #^ Path tmp-path]
  (cond
    (= name FAKE) (Settings :image FAKE-IMAGE :missing-image FAKE-MISSING-IMAGE :slack 0.01 :interpreter name)
    True (raise (ValueError (.format "知らない解釈器: {!r}" name)))))

(defn handlers-for [#^ str name #^ Settings settings]
  (cond
    (= name FAKE)
      [(sim-time-handler :clock (SimClock))
       (fake-run-handler (FakeRunWorld :behavior-of (fn [spec] (fake-behavior-of spec settings.missing-image))))]
    True []))

(defn build-interpreter [#^ str name #^ Path tmp-path]
  "名 → Program を走らせる関数。名が plain の時は scheduler だけ(handler を要しない純関数の検)。"
  (if (= name "plain")
      (fn [program] (run (scheduled program)))
      (do
        (setv settings (settings-for name tmp-path))
        (setv stack (+ (handlers-for name settings) [(scenario-settings settings)]))
        (fn [program] (run (scheduled (with_handlers stack program)))))))
