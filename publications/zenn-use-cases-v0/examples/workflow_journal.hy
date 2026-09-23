"時刻と乱数を保存し、同じ実行IDで再利用する。エージェントは使わない。"

(require doeff-hy.conductor [defworkflow time! random! <-])  ;; ワークフローと時刻・乱数の依頼を使う。
(import doeff_conductor.dsl [artifact ref])  ;; 記録した値を参照して最終結果へまとめる。

(defworkflow document-run  ;; 開始時刻と乱数を取得するワークフローを定義する。
  :params {}  ;; この例は外部パラメータを受け取らない。
  :roles {}  ;; エージェントを使う役割を設けない。
  (<- started (time!))  ;; 初回は現在時刻、再生時は記録済みの時刻を受け取る。
  (<- seed (random!))  ;; 初回は乱数値、再生時は記録済みの値を受け取る。
  (artifact {"started" (ref "started") "seed" (ref "seed")}))  ;; 2つの値を辞書として返す。

(setv WORKFLOW document-run)  ;; Conductorが読み込むワークフローの入口を公開する。
