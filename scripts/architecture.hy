;;; scripts の file の層の宣言(agora-redesign #3012)。
;;;
;;; 今は、規則の母集団から外す層 1 つだけを置く(packages/doeff-linter/scripts/architecture.hy と同じ形)。doeff-linter は
;;; Python の文ごとの規則の母集団を、file から上へ最も近い architecture.hy から決める(#2811)。ここで外すのは下の名指しの module だけで、
;;; ほかの scripts/ の file の母集団は変わらない。
;;;
;;; changed-tests = 変えた所の検を選んで走らせる入口 run_changed_tests(PEP 723 の script・依存は空 = stdlib だけ)。`make test-changed` が
;;;   `uv run --script` で呼び、repo の Python の環境が無くても動く — doeff の Program の外で走り、doeff の handler(ReadEnvironment に
;;;   答える subprocess_handler)を import できない。この module の環境の読みは入口の 1 か所(main(os.environ))だけで、そこで 1 度だけ
;;;   読んだ写像を下の関数へ渡す(下の関数は os.environ を読まない)。
;;;   - 外す規則: DOEFF004(os.environ を直に読む・参照する)— 名指しの module(run_changed_tests)に限る。
;;;   - 禁じる import: doeff の module。stdlib だけの約束が崩れると DOEFF032 が赤にする。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり入口の読みに当たる。この file を消すと、この dir の file の母集団は根の設定へ戻る。
(defarchitecture doeff-scripts
  :root "."
  :layers [(layer changed-tests
             :summary "変えた所の検を選んで走らせる stdlib だけの入口 — uv run --script で呼ぶ、Program の外の code"
             :knows "git の差分・検の契約の表・pytest の呼び方・環境変数の写像"
             :does-not-know "doeff の Program・effect・handler"
             :modules [run_changed_tests]
             :exempt [(rule DOEFF004 "PEP 723 の stdlib だけの script は doeff の Program の外で走り doeff の handler を import できない — この module の環境の読みは入口の 1 か所(main(os.environ))だけで、1 度だけ読んだ写像を下の関数へ渡す")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster])])
