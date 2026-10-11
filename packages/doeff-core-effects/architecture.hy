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
;;;
;;; 不変条件(scheduler の止まりの見張り — Program の外の scheduler の中の判じなので defservice の :invariants に置けず、条をここに
;;; 1 か所で書く。判じる関数は doeff_core_effects.scheduler の _external_wait_is_stall の 1 つで、Python と Rust の scheduler が同じ
;;; 関数を呼ぶ):
;;;   S1 a-stall-is-a-block-no-registered-wake-will-end — scheduler が外の完了を待って 1 周期(EXTERNAL_STALL_LOG_INTERVAL_SECONDS・
;;;     本番 30 秒)何も届かない時、それを止まりと数えて警告を書く(#495b の見張り)のは、次のどちらかの時だけ:
;;;       - 起こしの約束の登録された待ちが 1 つも無い。約束の登録された待ち = 期限の分かる時計の待ち(CreateExternalPromise の
;;;         deadline — Delay・WaitUntil など・#765)と、終わりの知らせの出どころを見張る待ち(source_alive — 子の process の終わりの
;;;         fd など・card ki-6222e22d4179)。
;;;       - 約束を破った待ちが在る(失われた起こし)。時計の待ちは期限から、見張りの待ちは出どころが終わったと初めて見た時から、
;;;         1 周期を過ぎても届かない。
;;;     期限も出どころも持たない外部の待ちだけで塞がっている間は、今どおり周期ごとに警告する。
;;;     失敗ケース = tests/test_scheduler.py の TestDeadlockDiagnostics(出どころの生きた見張りの待ちで黙る・出どころの終わった
;;;     待ちを報じる・期限の無い待ちを報じる)、packages/doeff-core-effects/tests/test_process_exit_wait.hy の
;;;     test-waiting-on-a-running-child-is-not-a-stall(走っている子の待ちで黙る — 直す前は周期ごとに警告)と
;;;     test-a-child-that-ended-without-its-exit-notice-is-a-stall(終わりの知らせの届かない子の待ちを報じる)、
;;;     packages/doeff-time/tests/test_async_handler.py(時計の待ちで黙る・期限を過ぎた時計の待ちを報じる)。どれも python と rust の
;;;     両方の scheduler で回す。
(defarchitecture doeff-core-effects
  :root "."
  :layers [(layer foundation
             :summary "環境変数の読みが許される foundation の handler の層 — scheduler の handler を選ぶ入口と、Ask に環境変数から答える handler"
             :knows "環境変数の名・scheduler の実装の名・Ask の鍵から環境変数の名への写し"
             :does-not-know "業務の Program・どの業務がどの設定を使うか"
             :modules [doeff_core_effects.scheduler doeff_core_effects.handlers]
             :exempt [(rule DOEFF004 "foundation の層 — handler の組の前に scheduler の handler を選ぶ入口と、Ask に環境変数から答える handler。環境変数の読みは foundation の handler の中だけ可(利用者 2026-10-02)")])])
