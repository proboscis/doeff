;; 記事「設計の記録をテストとして走らせる」の最初のコードと同じADR。読者のprojectでは docs/adr/defadr_billing_clock.hy に置く。
;; この例はdoeffのリポジトリの中に置くため、doeff自身のADRの集め手が拾わない名前(adr_*.hy)にし、pyproject.tomlで集めさせる。
(require doeff-adr.macros [defadr rule law deftest defsemgrep]) ; ADR・規則・性質・2種類の検査の構文を使う。
(require doeff-hy.macros [val]) ; 一度だけ決める名前を作る。
(import doeff-adr.macros [fact counterexample]) ; 問題の事実と、許さない反例を値として書く。
(import datetime [datetime timezone]) ; 期限と、固定する時刻を作る。
(import doeff-time [sim-time-handler]) ; 開始時刻を決めた仮想の時計のハンドラ。
(import billing [is-overdue]) ; 守られる側の業務のコード。

(val today (datetime 2026 10 17 :tzinfo timezone.utc)) ; テストの「今日」を固定する。

(defadr ADR-BILLING-001 ; ADRの番号。この名でテストの名前が決まる。
  :title "業務のコードは時刻を直に読まない" ; 何を決めたか。
  :status "accepted" ; 採用済み。採用済みのADRは、検査を1つ以上持つ必要がある。
  :problem [(fact "datetime.now()を直に読むと、テストの結果が走らせた日で変わる")] ; 決めた訳。
  :decision [(rule R1 "今の時刻はGetTimeの依頼で頼み、外側の時計のハンドラが答える")] ; 決めた規則。
  :laws [(law same-clock-same-answer ; 規則が守られた時に成り立つ性質。
           :statement "同じ時刻を答える時計の下では、同じ期限に同じ答えを返す" ; 性質の文。
           :counterexamples [(counterexample "業務の関数の中でdatetime.now()を呼ぶ")])] ; 許さない例。
  :enforcement ; ここから下が、pytestで走る検査。
  [(deftest test-overdue-follows-the-clock ; 1つ目:時計を固定して、答えが決まる事を試す。
     (<- late ((sim-time-handler :start-time today) ; 今日を10月17日に固定した時計を付ける。
               (is-overdue (datetime 2026 10 1 :tzinfo timezone.utc)))) ; 期限10月1日は過ぎている。
     (<- early ((sim-time-handler :start-time today) ; 同じ時計をもう一度付ける。
                (is-overdue (datetime 2026 10 31 :tzinfo timezone.utc)))) ; 期限10月31日はまだ先。
     (assert (and late (not early)))) ; 走らせた日によらず、TrueとFalseになる。
   (defsemgrep no-direct-now ; 2つ目:Semgrepの規則が、例どおりに検出するかを試す。
     "billing-no-direct-now" ; .semgrep.yamlにある規則の名。
     [{"relative-path" "billing.py" ; 検出すべき例:業務のファイルでOSの時計を読む。
       "source" "from datetime import datetime\nnow = datetime.now()\n"}]
     [{"relative-path" "billing.py" ; 検出しない例:時刻をGetTimeの依頼で頼む。
       "source" "def f():\n    now = yield GetTime()\n    return now\n"}])])
