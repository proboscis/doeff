;;; packages/doeff-linter/scripts の file の層の宣言(agora-redesign #3012)。
;;;
;;; 今は、規則の母集団から外す層 1 つだけを置く(tools/architecture.hy・packages/doeff-indexer/architecture.hy と同じ形)。doeff-linter は
;;; Python の文ごとの規則の母集団を、file から上へ最も近い architecture.hy から決める(#2811)。ここで外すのは下の名指しの module だけ。
;;;
;;; snapshot = linter の断面を組む道具 linter_snapshot(PEP 723 の script・依存は空 = stdlib だけ)。commit の hook と pin の便が
;;;   `uv run --script` で呼び、repo の Python の環境が無くても動く — doeff の Program の外で走り、doeff の handler(ReadEnvironment に
;;;   答える subprocess_handler)を import できない。環境変数(置き場の名・XDG_CACHE_HOME・HOME・git の GIT_*)は、入口の main が
;;;   1 度だけ読んで写像として下の関数へ渡す(下の関数は os.environ を読まない)。
;;;   - 外す規則: DOEFF004(os.environ を直に読む・参照する)— 名指しの module(linter_snapshot)に限る。
;;;   - 禁じる import: doeff の module。stdlib だけの約束が崩れると DOEFF032 が赤にする。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり入口の読みに当たる。この file を消すと、この dir の file の母集団は根の設定へ戻る。
(defarchitecture doeff-linter-scripts
  :root "."
  :layers [(layer snapshot
             :summary "linter の断面を組む stdlib だけの道具 — uv run --script で呼ぶ、Program の外の code"
             :knows "git・cargo・断面の置き場・環境変数の写像"
             :does-not-know "doeff の Program・effect・handler"
             :modules [linter_snapshot]
             :exempt [(rule DOEFF004 "PEP 723 の stdlib だけの script は doeff の Program の外で走り doeff の handler を import できないので、入口の main が環境変数の写像を 1 度だけ読んで下の関数へ渡す")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster])])
