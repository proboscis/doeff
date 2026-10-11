;;; doeff-codex の検の替え玉の codex app-server の層の宣言(日次 10-11 08:00 の DOEFF004 の直し — 調整役 cisco-c8 の知らせ lt-Z6SXNECV)。
;;;
;;; 今は、規則の母集団から外す層 1 つだけを置く。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い
;;; architecture.hy から決める(#2811)。ここで外すのは下の名指しの module だけで、この dir のほかの file の母集団は変わらない。
;;;
;;; stub = 検が `codex app-server` の代わりに子の process として起こす替え玉(codex_app_server.py)。本物の codex と同じく、家の場所を
;;;   環境変数 CODEX_HOME から読む — handler が子の process に借りた資格の家を渡したかを、子の側から見るのが検の要(HOME_PHRASE)。
;;;   doeff の Program の外の process で、環境変数そのものが本物の codex の口なので、依存の注入(doeff の Ask)で受ける入口が無い。
;;;   - 外す規則: DOEFF004(os.environ を直に読む)— 名指しの module(codex_app_server)に限る。
;;;   - 禁じる import: doeff の業務の module。替え玉に業務の code が入ると DOEFF032 が赤にする。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり替え玉に当たる。この file を消すと、この dir の file の母集団は根の設定へ戻る。
(defarchitecture doeff-codex-stub-cli
  :root "."
  :layers [(layer stub
             :summary "検が codex app-server の代わりに起こす替え玉の process — doeff の Program の外の code"
             :knows "codex app-server の JSON-RPC の行・録った実物の行・CODEX_HOME の家の形"
             :does-not-know "doeff の Program・effect・handler"
             :modules [codex_app_server]
             :exempt [(rule DOEFF004 "替え玉は本物の codex と同じく家の場所を環境変数 CODEX_HOME から読む — handler が子の process に渡した家を子の側から見るのが検の要で、doeff の Program の外の process なので依存の注入で受ける入口が無い")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster doeff_codex])])
