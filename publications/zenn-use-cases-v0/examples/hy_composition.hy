;; Hyでハンドラ・契約・テスト・コレクション処理を共有する実行例。

(require doeff-hy.macros [defk <- do! defhandler deftest for/do]) ; 計算・結合・検査の構文を使えるようにする。
(import doeff [do :as _doeff-do run]) ; for/doの展開に使うdoと、確認用のrunを読み込む。
(import doeff-core-effects [Ask]) ; 挨拶の接頭辞をハンドラへ問い合わせる。
(import doeff-traverse [Traverse :as _doeff_traverse_Traverse ; Fromが展開されるTraverseの参照名を用意する。
                       Skip :as _doeff_traverse_Skip ; Whenが偽のときに使うSkipの参照名を用意する。
                       Reduce]) ; 有効な項目だけを順に畳み込む依頼を読み込む。
(import doeff-traverse.handlers [sequential]) ; TraverseとReduceを順番に解釈するハンドラを読み込む。

(defhandler greeting-source [] ; 呼ぶとProgramを包む関数を返す、引数なしのハンドラ工場。
  (Ask [key] ; Askが持つkeyを受け取り、対応する値を選ぶ。
    (resume (get {"prefix" "こんにちは、"} key)))) ; prefixには挨拶を返して続行し、未登録キーはKeyErrorにする。

(defk greet [name] ; 名前から挨拶を作るProgramを返す。
  {:pre [(: name str)] :post [(: % str)]} ; 実行時に入力と結果が文字列であることを検査する。
  (+ (! (Ask "prefix")) name)) ; 接頭辞を受け取った位置で名前と結合する。

(setv greeting ; 固定した宛先を使うProgramを、実行せず値として保存する。
  (do! ; 複数の式を、後から実行できるひとつのProgramへまとめる。
    {:post [(: % str)]} ; 最終結果が文字列であることを検査する。
    (<- text (greet "読者のみなさん")) ; 子Programを実行し「こんにちは、読者のみなさん」を受け取る。
    (+ text "！"))) ; 感嘆符を加えた挨拶を返す。

(deftest test-greeting ; doeff_interpreterを受け取るpytest用のテストを生成する。
  (<- text greeting) ; テストに渡されたハンドラの下で挨拶Programを実行する。
  (assert (= text "こんにちは、読者のみなさん！"))) ; 接頭辞・名前・感嘆符が揃った結果を検査する。

(defk add [total value] ; Reduceから呼ばれ、累積値へ次の値を足すProgramを返す。
  {:pre [(: total int) (: value int)] :post [(: % int)]} ; 引数2個と返り値が整数であることを検査する。
  (+ total value)) ; 0と2なら2、2と6なら8を返す。

(defk eligible-total [values] ; 入力の正の値を2倍し、合計するProgramを返す。
  {:pre [(: values list)] :post [(: % int)]} ; 入力はリスト、結果は整数と検査する。
  (<- selected ; Traverseの結果を、履歴を保持するCollectionとして受け取る。
    (for/do ; 項目ごとの処理をTraverseとして組み立てる。
      (<- value (From values)) ; 入力[1 -1 3]の各値を、ハンドラが選ぶ順序で処理する。
      (When (> value 0)) ; -1をSkipにし、後続の2倍処理には進ませない。
      (* value 2))) ; 有効な項目の値を2と6にする。
  (<- total (Reduce add 0 selected)) ; 除外した項目を飛ばし、0→2→8と集計する。
  total) ; 計算済みの整数8を親Programへ返す。

(assert (= (run ((greeting-source) greeting)) "こんにちは、読者のみなさん！")) ; 挨拶ハンドラを取り付けて結果を確認する。
(assert (= (run ((sequential) (eligible-total [1 -1 3]))) 8)) ; 順次ハンドラで、正の値だけの倍数合計が8と確認する。
(assert (= (run ((sequential) (eligible-total [-3 0]))) 0)) ; 全件除外ならReduceの初期値0が返ると確認する。
