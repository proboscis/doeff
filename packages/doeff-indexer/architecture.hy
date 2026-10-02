;;; packages/doeff-indexer の file の層の宣言(agora-redesign #3012)。
;;;
;;; 今は、規則の母集団から外す層 1 つだけを置く(tools/architecture.hy と同じ形)。doeff-linter は Python の文ごとの規則の母集団を、
;;; file から上へ最も近い architecture.hy から決める(#2811)。ここで外すのは下の名指しの module だけで、doeff-indexer のほかの file
;;; (python/ の下の package)の母集団は変わらない。根の doeff_cargo_backend.py は tools への symlink で、本物の場所の宣言
;;; (tools/architecture.hy)が当たる。
;;;
;;; build = doeff-indexer の PEP 517 の build backend doeff_indexer_build_backend。uv・pip が wheel を組む時に呼び、doeff の Program の外で
;;;   走る。CLI の binary を組む cargo への設定(CARGO・CARGO_BUILD_TARGET・DOEFF_INDEXER_SKIP_CLI_BUILD)は環境変数そのものが口で、
;;;   依存の注入(doeff の Ask)で受ける入口が無い。
;;;   - 外す規則: DOEFF004(os.environ を直に読む)— 名指しの module(doeff_indexer_build_backend)に限る。
;;;   - 禁じる import: doeff の業務の module。外した層に業務の code が入ると DOEFF032 が赤にする。
;;; 以前は根の pyproject.toml の exclude で file ごと全部の規則の外にしていたが、linter の exclude の部分一致の誤りで、どちらにしても
;;;   判定されていなかった(#3012)。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり build backend に当たる。この file を消すと、doeff-indexer の file の母集団は
;;;   根の設定へ戻る。
(defarchitecture doeff-indexer-build
  :root "."
  :layers [(layer build
             :summary "doeff-indexer の PEP 517 の build backend — uv・pip が wheel を組む時に呼ぶ、Program の外の code"
             :knows "cargo・maturin・wheel・PEP 517 の hook"
             :does-not-know "doeff の Program・effect・handler"
             :modules [doeff_indexer_build_backend]
             :exempt [(rule DOEFF004 "PEP 517 の build backend は uv・pip が呼び doeff の Program の外で走るので、cargo への設定(CARGO・CARGO_BUILD_TARGET・DOEFF_INDEXER_SKIP_CLI_BUILD)は環境変数そのものが口で、設定を Ask で受ける入口が無い")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster])])
