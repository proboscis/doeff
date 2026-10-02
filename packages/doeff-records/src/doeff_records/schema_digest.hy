;;; 置き場の宣言の表ごとの要約(純粋 — #2742)。
;;;
;;; 要約 = 表の宣言(TableDecl)の repr を utf-8 にした sha256(64 字)。宣言は凍らせた値なので、同じ宣言なら同じ綴りになる。
;;; 評価はこの 1 か所: 記録の service の GET /served(動いている process が配っている表の要約)と、使い手の木の data
;;; (使い手の repo が木ごとに書く表の要約の file — 使い手の側の見張りが、動いている物と木を比べる)が同じ関数を使う。
;;; 2 か所に分かれると、同じ宣言の要約が食い違って全部の表が「宣言が変わった」に見える。
;;; TableDecl の形(欄・repr の綴り)が doeff の版で変わると、全部の表の要約が一度に変わる — 使い手は宣言し直すまで前の版を選ぶ。
(require doeff-hy.macros [defk val])
(import hashlib)
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [RecordsSchema])

(val MODULE-TAGS {:context "records" :role "judgment"})


(defk schema-digests [schema]
  {:pre [(: schema RecordsSchema)] :post [(: % FrozenMap)] :tags {:context "records" :role "judgment"}}
  "動いている置き場と使い手の木が、表の宣言が同じかを比べられるようにするため: 表の名 → 表の宣言の repr の sha256(64 字の 16 進)。"
  (FrozenMap (gfor #(table declaration) (.items schema.tables)
                   #((str table) (.hexdigest (hashlib.sha256 (.encode (repr declaration) "utf-8")))))))
