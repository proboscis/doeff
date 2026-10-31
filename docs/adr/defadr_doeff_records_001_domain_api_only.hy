;;; Executable ADR: 記録の service(doeff-records)の生の操作(表と列の名と鍵を呼び手に選ばせて直に読み書きする操作)は、
;;; cluster の外へ提供しない。外の呼び手(cluster の外の網・CLI・webapp)はドメインの API だけを使う。
;;;
;;; この ADR は決定の記録で、doeff-records の code と wire の振る舞いは変えない。cluster の中の job が doeff-records の効果で
;;; 読み書きする形がこの決定に当たるかは利用者に確かめている最中で、中の側の形は未定(R3)。
;;; 外へ出ていないことの検査は doeff の外が持つ: dotfiles の linter(defjevrule)と、agora の repo の宣言のテスト。
;;; doeff の側には、wire が出す生の操作の一覧を固定し、足すとテストが赤になる形だけを置く(R4)。
;;;
;;; 戻し方: この file を revert する(code と wire は変えていないので、それだけで戻る)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import doeff_records._wire [OPERATIONS PATH-PREFIX])


;; wire が /v1/records/ の下に出す生の操作の一覧(2026-10-07 の doeff の main で 10)。足す変更はこの一覧に無い物として赤になる。
;; 減らす変更は、同じ変更でこの一覧から削る。
;; 2026-10-22: append-events(追記の束を 1 往復で積む — append-event の束ね。冪等キー・上限・断りは append-event と同じ答え)を足した。
;; 日次の root の failing を直す変更で、AppendEvents の追加(README の記述あり)が一覧を更新しないまま本線に入っていたのを揃える。
(val RAW-OPERATIONS
  (frozenset #("read-row" "list-rows" "put-row" "put-rows" "watch-changes"
               "append-event" "append-events" "read-events" "read-event-by-key" "read-stream-end" "watch-events")))


(defadr ADR-DOE-RECORDS-001
  :title "記録の service の生の操作(表と列を直に読み書きする操作)は cluster の外へ提供しない。外の呼び手はドメインの API だけを使う。cluster の中の形は利用者に確かめている最中で未定"
  :status "accepted"
  :scope ["packages/doeff-records/src/doeff_records/_wire.hy"
          "packages/doeff-records/src/doeff_records/_service.hy"
          "docs/adr/defadr_doeff_records_001_domain_api_only.hy"]
  :problem
    [(fact
       "2026-10-07 に決めた事: 汎用の DB の API を提供してはならない。read-row や list-rows のような生の API は出さず、すべてドメインの API だけにする。生の API の提供は defjevrule(dotfiles のルールの宣言)や linter で見つけて禁じ、赤にして直す。")
     (fact
       "2026-10-07 の doeff の main で、doeff-records の wire は /v1/records/ の下に 10 の生の操作を出している: read-row・list-rows・put-row・put-rows・watch-changes・append-event・read-events・read-event-by-key・read-stream-end・watch-events(_wire.hy の OPERATIONS)。HTTP で受けるのは service.hy。"
       :evidence "packages/doeff-records/src/doeff_records/_wire.hy:26-42")]
  :context
    [(interpretation
       "生の操作は、表と列の名と鍵を呼び手に選ばせる汎用の読み書きで、ドメインの意味(誰が何をしてよいか・どの形の値か)を持たない。外の呼び手がこれを直に使うと、ドメインの決まりを通らずに記録を読み書きできる。")
     (interpretation
       "「外」は cluster の外の網(tailnet を含む)・CLI・webapp を指す。cluster の中の job が doeff-records の効果(ReadRow など)で読み書きする事が生の API の提供に当たるかは、この決定からは決まらない — 利用者に確かめている最中。")]
  :decision
    [(rule R1 "記録の service の生の操作は、cluster の外(外の網・CLI・webapp)へ提供しない。")
     (rule R2 "外の呼び手は、ドメインの API(用途ごとの名と型を持つ API)だけを使う。生の操作を外から呼ぶ経路を新しく作らない。")
     (rule R3 "cluster の中の job が doeff-records の効果で読み書きする形がこの決定に当たるかは未定。確かめの答えが出るまで、中の側の形はこの ADR で決めない(答えが出たら規則を足すか、別の ADR にする)。")
     (rule R4 "doeff の側では、wire が出す生の操作の一覧を RAW-OPERATIONS に固定する。一覧に無い操作を足す変更はテストで赤になる。操作を減らす変更は、同じ変更で一覧から削る。")
     (rule R5 "外へ出ていないことの検査は doeff の外が持つ: dotfiles の linter(defjevrule の宣言・ADR-DOTFILES-027 の側)と、agora の repo の宣言のテスト(記録の service を tailnet へ出す宣言を赤にする)。")]
  :laws
    [(law no-raw-operation-is-offered-outside
       :statement "for_all 記録の service の公開 p: p が cluster の外から届く ⇒ p は生の操作に答えない"
       :counterexamples
         [(counterexample "記録の service のエンドポイント(8875)を tailnet へ出す宣言を置き、Mac の CLI や webapp が read-row・list-rows を直に呼ぶ")]
       :enforced-by ["agora の repo の宣言のテスト"
                     "dotfiles の linter(defjevrule)"]
       :wiring "未配線(2026-10-07)— 検査は doeff の外の 2 つが作る。この file は決定の記録")
     (law the-raw-operation-list-does-not-grow
       :statement "_wire.hy の OPERATIONS の集合 = RAW-OPERATIONS"
       :counterexamples
         [(counterexample "wire に生の操作を 1 つ足し、外の呼び手がそれを使う — 一覧と比べない形では、決定の後も生の操作が黙って増える")]
       :enforced-by ["docs/adr/defadr_doeff_records_001_domain_api_only.hy::test-adr-doe-records-001-raw-operations-do-not-grow"]
       :wiring "配線済み(2026-10-07)— この file のテスト")]
  :enforcement
    [(deftest test-adr-doe-records-001-raw-operations-do-not-grow
       ;; wire が出す生の操作は一覧のとおり(足すと赤・減らす変更は同じ変更で一覧を削る — R4)。
       (assert (= PATH-PREFIX "/v1/records/") PATH-PREFIX)
       (assert (= (frozenset OPERATIONS) RAW-OPERATIONS)
               (+ "wire の生の操作が一覧と違う(ADR-DOE-RECORDS-001 R4 — 生の操作を足さない。減らす変更は RAW-OPERATIONS を同じ変更で削る): "
                  (str (sorted OPERATIONS)))))]
  :plans [])
