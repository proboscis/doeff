;;; Executable ADR: doeff-effect-analyzer の閉じの解析の答えの置き場(result_cache)の鍵は、答えの依る module の組だけ — 解析が source を
;;; 読んだ module・解析そのものの module・そこから値を作った module を辿った組。process が読み込んだ全部の module ではない。
;;;
;;; 動機 = 置き場の鍵が外れると、closure の検 10 本の本体の和で約 240 秒かかる。前の鍵(ebb060dec・733107dae・094738fb3)は
;;; 「答えを作った process が読み込んでいた全部の module の名と中身」で、ebb060dec が「import の時に他の module の値を書き換える module」の
;;; 数え漏れを恐れて広く取った。この ADR はその決め直し。
;;;
;;; 戻し方: この ADR を足した commit を revert する(ADR の file 1 つと、result_cache の鍵が読み込んだ全部の module に戻る。置き場の
;;; 形の印 v4 の entry は読まれなくなるだけ)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import importlib)
(import os)
(import sys)
(import tempfile)
(import pathlib [Path])
(import unittest.mock [patch])


;; analyzer の Python の前の部分(Rust の拡張を組まずに読める — root の venv には入らない。analyzer の検の conftest と同じ置き方)。
(val ANALYZER-PYTHON (str (/ (. (Path __file__) parent parent parent) "packages" "doeff-effect-analyzer" "python")))


(defadr ADR-DOE-ANALYZER-001
  :title "閉じの解析の答えの置き場の鍵は、答えの依る module の組だけ — 解析が source を読んだ module・解析そのものの module・そこから値を作った module(親の package・名前空間の値の定義元・Hy の macro・関数の本体の import・点つきの名)を辿った組。読み込んだ全部の module ではない"
  :status "accepted"
  :scope ["packages/doeff-effect-analyzer/python/doeff_effect_analyzer/_result_cache.py"
          "packages/doeff-effect-analyzer/python/doeff_effect_analyzer/_program_effects.py"
          "docs/adr/defadr_doeff_analyzer_001_result_key.hy"]
  :problem
    [(fact
       "agora-controllers の日次の pytest の段は 951 file を 3 つの session に配る。どの session も closure の検と関係の無い検の file を数百読み込み、置き場の鍵は「答えを作った process が読み込んでいた全部の module の名と中身」だったので、4 時間の間にどれか 1 つが変わると外れた。名の重なる conftest と、起こし方で変わる __mp_main__ も鍵に入った。")
     (fact
       "当たれば closure の検 1 本は 0.05〜1.2 秒、外れると 20〜60 秒。日次 08:00 の回の closure の検 10 本の本体の和は約 240 秒で、外れた時の大きさ。"
       :evidence "手元の測り")
     (fact
       "前の鍵が数えた「import の時に他の module の値を書き換える module」は、答えを作った process が読み込んでいた時に中身だけを数え、読む側の process が読み込んでいるかは数えなかった(読み込んでいない module は import の路が見つける file の中身で照らす)。読む側にだけ在る書き換えの module は ebb060dec の時から鍵の外。"
       :evidence "result_cache.py の module の説明(この変更の前の版)・_current_material")]
  :context
    [(interpretation
       "答えは、解析が読む source と、それを import してできた実行時の値で決まる。読む source の module は解析の口(program_effects._module_source)を通るので、解析を走らせる間に名を数えられる(reading_sources)。値はその module の名前空間に在り、値を作った module は値から辿れる: 関数と class の定義元(__module__ と code の file)・instance の class の定義元・名に束ねた module・Hy の macro の表・関数の本体の import(解析は関数の中の import も追う)・__import__ に渡す点つきの名・関数が包む関数(defk の __wrapped__)と閉包の中の関数。これを、辿り着いた module ごとに繰り返す。")
     (interpretation
       "package は、誰が import したかに依らず読み込んだ submodule を全部属性に持つ。だから package から submodule へは、辿り着いた module の code がその submodule の名を使う時だけ渡る(import a.b の後に a.b.x を使う)。code の中の素の名(属性や変数の名 conftest・test)を module と取らない — pytest の code が名 conftest を使うだけで、session ごとに違う conftest が鍵に入った(この変更の途中の測り)。")
     (interpretation
       "狭めても失う物は前と同じ種類に留まる: 答えの依る組から辿れない module が import の時に値を書き換える事(前も、読む側の process での有無は数えなかった)・module でない file と環境変数から import の時に決める値・辿れる参照を残さない module が import の時に作った素の値(数・文字列)。agora-controllers は import の時にどれもしない。")]
  :decision
    [(rule R1 "答えの置き場の鍵は、解析が source を読んだ module(program_effects.reading_sources)と解析そのものの module(cached_result の compute の定義元)から、親の package・名前空間の値の定義元・Hy の macro・関数の本体の import と点つきの名・包む関数と閉包の関数の定義元を辿った module の組の、名と中身。process が読み込んだ全部の module を鍵にしない。")
     (rule R2 "package から submodule へは、辿り着いた module の code がその submodule の名を使う時だけ渡る。code の中の素の名を module と取らない(import の命令の名と点つきの文字列だけを取る)。")
     (rule R3 "鍵に入らない物(答えの依る組から辿れない書き換えの module・module でない file と環境変数・参照の残らない素の値)は result_cache の module の説明に書き、変える時はこの ADR を改める。")]
  :laws
    [(law an-unrelated-module-does-not-take-part-in-the-key
       :statement "for_all module m loaded but neither read by the analysis nor reached from what it read: change(m) => the stored answer is reused"
       :counterexamples
         [(counterexample "鍵が読み込んだ全部の module(この変更の前の形)— 同じ session の関係の無い検の file 1 つの変更で、closure の検が 20〜60 秒の解析をやり直す")
          (counterexample "code の素の名(属性の名)が読み込み済みの module の名と同じなら鍵に入れる — conftest が鍵に入り、session ごとに外れる")])
     (law a-module-the-answer-depends-on-is-in-the-key
       :statement "for_all module m read by the analysis, or whose values a read module holds, or imported in a read function's body: change(m) => the analysis runs again"
       :counterexamples
         [(counterexample "鍵を解析が読んだ module だけにする — 読んだ module の値を import の時に作った module(handler の工場)の変更で古い答えを返す")])]
  :enforcement
    [(deftest test-adr-doe-analyzer-001-an-unrelated-module-does-not-take-part-in-the-key
       ;; 解析は job の module の source を読み、答えに関係の無い module の file の中身を入れる(使い回せば古い中身が返る)。
       ;; 反例: job の関数の code が素の名(属性 result_key_adr_other)で関係の無い module の名を使っても、その module は鍵に入らない。
       (val other-name (.join "_" #("result_key_adr" "other")))
       (with [scratch (tempfile.TemporaryDirectory)
              _env (patch.dict os.environ {"DOEFF_EFFECT_ANALYZER_CACHE" (str (/ (Path scratch) "trees"))})
              _modules (patch.dict sys.modules)
              _path (patch.object sys "path" [scratch ANALYZER-PYTHON #* sys.path])]
         (os.environ.pop "DOEFF_EFFECT_ANALYZER_RESULT_CACHE" None)
         (val program-effects (importlib.import-module "doeff_effect_analyzer._program_effects"))
         (val result-cache (importlib.import-module "doeff_effect_analyzer._result_cache"))
         (.write-text (/ (Path scratch) "result_key_adr_job.py") f"def job(config):\n    return config.{other-name}\n" :encoding "utf-8")
         (val other (/ (Path scratch) f"{other-name}.py"))
         (.write-text other "VALUE = 1\n" :encoding "utf-8")
         (importlib.import-module "result_key_adr_job")
         (importlib.import-module other-name)
         (val analysis (fn []
                         (program-effects.module-ast (get sys.modules "result_key_adr_job"))
                         (.read-text other :encoding "utf-8")))
         (val first (result-cache.cached-result #("adr.analysis.unrelated") analysis))
         (.write-text other "VALUE = 2\n" :encoding "utf-8")
         (val second (result-cache.cached-result #("adr.analysis.unrelated") analysis))
         (assert (= first second "VALUE = 1\n")
                 (+ "関係の無い module の変更で解析し直した(ADR-DOE-ANALYZER-001 R1・R2): " (repr second)))))
     (deftest test-adr-doe-analyzer-001-a-module-the-answer-depends-on-is-in-the-key
       ;; 解析は made の module だけを読むが、made の値 HANDLER は工場の module が import の時に作った — 工場の変更で解析し直す
       ;; (答えに工場の file の中身を入れ、解析し直せば新しい中身が返る)。
       (with [scratch (tempfile.TemporaryDirectory)
              _env (patch.dict os.environ {"DOEFF_EFFECT_ANALYZER_CACHE" (str (/ (Path scratch) "trees"))})
              _modules (patch.dict sys.modules)
              _path (patch.object sys "path" [scratch ANALYZER-PYTHON #* sys.path])]
         (os.environ.pop "DOEFF_EFFECT_ANALYZER_RESULT_CACHE" None)
         (val program-effects (importlib.import-module "doeff_effect_analyzer._program_effects"))
         (val result-cache (importlib.import-module "doeff_effect_analyzer._result_cache"))
         (val factory (/ (Path scratch) "result_key_adr_factory.py"))
         (.write-text factory "def make():\n    def handler():\n        return 1\n    return handler\n" :encoding "utf-8")
         (.write-text (/ (Path scratch) "result_key_adr_made.py") "from result_key_adr_factory import make\nHANDLER = make()\n"
                      :encoding "utf-8")
         (importlib.import-module "result_key_adr_made")
         (val analysis (fn []
                         (program-effects.module-ast (get sys.modules "result_key_adr_made"))
                         (.read-text factory :encoding "utf-8")))
         (result-cache.cached-result #("adr.analysis.depended") analysis)
         (.write-text factory "def make():\n    def handler():\n        return 22\n    return handler\n" :encoding "utf-8")
         (val second (result-cache.cached-result #("adr.analysis.depended") analysis))
         (assert (in "return 22" second)
                 (+ "値を作った module の変更で解析し直さなかった(ADR-DOE-ANALYZER-001 R1): " (repr second)))))]
  :plans [])
