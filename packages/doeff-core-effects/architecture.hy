;;; doeff-core-effects の層の宣言(agora-redesign #3012)。
;;;
;;; doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い architecture.hy(この file)から決める(#2811)。
;;; 層は foundation の 1 つだけを置き、名指すのは scheduler の handler を選ぶ入口と、Ask に環境変数から答える handler の
;;; 2 module に限る。この package のほかの file の母集団は変わらない(今までどおり DOEFF004 が当たる)。
;;;
;;; foundation = 環境変数の読みが許される foundation の handler の層(利用者 2026-10-02 22:2x "reading env var from outside
;;;   foundation handler is clear violation" — 環境変数の読みは foundation の handler の中だけ)。
;;;   - doeff_core_effects.scheduler: handler の組の前に scheduler の handler(python / rust)を選ぶ入口で、DOEFF_SCHEDULER を読む。
;;;     handler の組がまだ無い所で選ぶので、ReadEnvironment で問うと doeff の全部の run に約 4 割の固定費が足される
;;;     (実測 問い 1 回 173 µs・scheduled の run 1 回 415 µs — cisco-c8 2026-10-02 23:4x の決め)。
;;;   - doeff_core_effects.handlers: Ask に環境変数から答える handler(接頭辞つきの名を読む)。
;;;   - 外す規則: DOEFF004(os.environ を直に読む)— 名指しの 2 module に限り、ほかの file を足さない。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり 2 module に当たる。この file を消すと、この package の file の母集団は
;;;   根の設定へ戻る。
(defarchitecture doeff-core-effects
  :root "."
  :layers [(layer foundation
             :summary "環境変数の読みが許される foundation の handler の層 — scheduler の handler を選ぶ入口と、Ask に環境変数から答える handler"
             :knows "環境変数の名・scheduler の実装の名・Ask の鍵から環境変数の名への写し"
             :does-not-know "業務の Program・どの業務がどの設定を使うか"
             :modules [doeff_core_effects.scheduler doeff_core_effects.handlers]
             :exempt [(rule DOEFF004 "foundation の層 — handler の組の前に scheduler の handler を選ぶ入口と、Ask に環境変数から答える handler。環境変数の読みは foundation の handler の中だけ可(利用者 2026-10-02)")])])
