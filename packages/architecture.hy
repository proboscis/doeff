;;; packages の下の、自分の宣言を持たない package に共通の層の宣言(agora-redesign #2859)。
;;;
;;; 今は、規則の母集団から外す層 1 つだけを置く。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い
;;; architecture.hy から決める(#2811)。自分の宣言を持つ package(doeff-cluster・doeff-hy)はそちらを読み、ほかの package の file は
;;; この宣言を読む — ここで外すのは下の名指しの module だけで、ほかの file の母集団は変わらない。
;;;
;;; build = Rust の package の PEP 517 の build の入口 doeff_cargo_backend(tools/doeff_cargo_backend.py の写しが
;;;   doeff-agentic-cli・doeff-effect-analyzer・doeff-indexer・doeff-linter・doeff-vm の根に在る — 同じ module の名)。uv・pip が wheel を
;;;   組む時に呼び、doeff の Program の外で走る。cargo と maturin への設定(CARGO_TARGET_DIR ほか)は環境変数そのものが口で、
;;;   依存の注入(doeff の Ask)で受ける入口が無い。
;;;   - 外す規則: DOEFF004(os.environ を直に読む)— 名指しの module(doeff_cargo_backend)に限る。
;;;   - 禁じる import: doeff の業務の module。外した層に業務の code が入ると DOEFF032 が赤にする。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり build の入口に当たる。この file を消すと、ほかの package の file の母集団は
;;;   根の設定へ戻る(今と同じ)。
(defarchitecture doeff-packages
  :root "."
  :layers [(layer build
             :summary "Rust の package の PEP 517 の build の入口 — uv・pip が wheel を組む時に呼ぶ、Program の外の code"
             :knows "cargo・maturin・wheel・PEP 517 の hook"
             :does-not-know "doeff の Program・effect・handler"
             :modules [doeff_cargo_backend]
             :exempt [(rule DOEFF004 "PEP 517 の build の入口は uv・pip が呼び doeff の Program の外で走るので、cargo と maturin への設定(CARGO_TARGET_DIR ほか)は環境変数そのものが口で、設定を Ask で受ける入口が無い")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster])])
