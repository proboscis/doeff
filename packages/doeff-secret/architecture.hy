;;; doeff-secret の層の宣言(agora-redesign #3012)。
;;;
;;; doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い architecture.hy(この file)から決める(#2811)。
;;; 層は foundation の 1 つだけを置き、名指すのは GetSecret に環境変数から答える handler の 1 module に限る。この package の
;;; ほかの file の母集団は変わらない(今までどおり DOEFF004 が当たる)。
;;;
;;; foundation = 環境変数の読みが許される foundation の handler の層(利用者 2026-10-02 22:2x "reading env var from outside
;;;   foundation handler is clear violation" — 環境変数の読みは foundation の handler の中だけ)。
;;;   - doeff_secret.handlers: GetSecret に環境変数から答える handler(env_var_handler・env_var_handlers)。写像を渡されなければ
;;;     この process の環境(os.environ)を読む。DOEFF004 を「os.environ への参照そのもの」へ広げた時に当たった 2 行
;;;     (63・97 行)がこれ(cisco-c8 2026-10-03 02:0x の決め — 規則が許す場所の宣言で、違反を隠す除外ではない)。
;;;   - 外す規則: DOEFF004(os.environ を直に読む)— 名指しの 1 module に限り、ほかの file を足さない。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり handlers に当たる。この file を消すと、この package の file の母集団は
;;;   根の設定へ戻る。
(defarchitecture doeff-secret
  :root "."
  :layers [(layer foundation
             :summary "環境変数の読みが許される foundation の handler の層 — GetSecret に環境変数から答える handler"
             :knows "環境変数の名・secret の id から環境変数の名への写し(接頭辞)"
             :does-not-know "業務の Program・どの業務がどの secret を使うか"
             :modules [doeff_secret.handlers]
             :exempt [(rule DOEFF004 "foundation の層 — GetSecret に環境変数から答える handler。環境変数の読みは foundation の handler の中だけ可(利用者 2026-10-02)")])])
