import pathlib, sys
p = pathlib.Path(sys.argv[1])
s = p.read_text()
def rep(old, new):
    global s
    assert s.count(old) == 1, old[:60]
    s = s.replace(old, new)
rep('(import doeff_cluster.shared.core.run_context_rules [worker-context-environ])\n',
    '(import doeff_cluster.shared.core.run_context_rules [worker-context-environ])\n'
    '(import doeff_cluster.shared.intent.runtime_env_model [EnvVar])\n')
rep(''';; この process の世代を書く Pod の中の file の名(無ければ書かない — readinessProbe が読む)。
(val BOOT-FILE-VAR "DOEFF_WORKER_BOOT_FILE")
''', ''';; この process の世代を書く Pod の中の file の名(無ければ書かない — readinessProbe が読む)。
(val BOOT-FILE-VAR "DOEFF_WORKER_BOOT_FILE")
;; --pass-env の「全部」の印(WORKER_PASS_ENV_EXTRA に `*` と書く)。worker の環境のうち、宣言の env-vars・:environ に置いてよい名
;; (EnvVar の規則 — 予約の名と頭・秘密の中身を名指す名を断る)を全部、子へ渡す。木の job(EXTEND)と実行環境の job(REPLACE)で worker の
;; 設定の届き方が違い、上に載る系が worker-env に値を足すたびに WORKER_PASS_ENV_EXTRA にも名を足す二重の記入を忘れて、実行環境へ移した
;; job が値を受けられなかった事を根から無くす。宣言で置けない名は worker からも渡さない(規則の定義点は EnvVar の 1 つ)。
(val PASS-ALL "*")
''')
rep('''  (<- found (ReadEnvironment :names names))
  (dfor entry found entry.name entry.value))
''', '''  ;; --pass-env の `*` が在れば全部の名を読む(接頭辞 "" — environment-mapping と同じ問い方)。
  (<- found (ReadEnvironment :names (tuple (gfor n names :if (!= n PASS-ALL) n)) :prefixes (if (in PASS-ALL names) #("") #())))
  (dfor entry found entry.name entry.value))
''')
rep('''  (<- wanted tuple (pass-env-names names))
  (val missing (lfor n wanted :if (not-in n environ) n))
  (when missing
    (raise (ValueError (+ "--pass-env の名が worker の環境に無い: " (.join "," missing)))))
  (dfor n wanted n (get environ n)))
''', '''  (<- wanted tuple (pass-env-names names))
  (val named (tuple (gfor n wanted :if (!= n PASS-ALL) n)))
  (val missing (lfor n named :if (not-in n environ) n))
  (when missing
    (raise (ValueError (+ "--pass-env の名が worker の環境に無い: " (.join "," missing)))))
  (| (if (in PASS-ALL wanted) (! (declarable-environment environ)) {})
     (dfor n named n (get environ n))))


(defk declarable-environment [environ]
  {:pre [(: environ dict)] :post [(: % dict)] :tags {:context "worker" :role "main"}}
  "--pass-env の `*`(PASS-ALL)の分を組むため: worker の環境のうち、宣言の env-vars・:environ に置いてよい名の全部。規則は EnvVar の 1 つ
   (予約の名 PATH・HOME・VIRTUAL_ENV と頭 PYTHON・HY_・UV_・LD_・DOEFF_・秘密の中身を名指す名 *_TOKEN・*_KEY 等を断る)。"
  (dfor #(k v) (.items environ) :if (is (EnvVar.environ-refusal {k v}) None) k v))
''')
rep('''  (.add-argument parser "--pass-env" :default ""
                 :help "子 process へ渡す worker の環境変数の名(`,` で並べる — 機体の設定の path や URL。無い名は起動を止める)")''',
'''  (.add-argument parser "--pass-env" :default ""
                 :help "子 process へ渡す worker の環境変数の名(`,` で並べる — 機体の設定の path や URL。無い名は起動を止める。`*` = 宣言で置ける名の全部)")''')
p.write_text(s)
