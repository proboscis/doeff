# 型の穴の記事「公開APIの型の穴をテストで塞ぎ、黙って開かせない」の確かめ

11本目からの候補7の新しい記事`doeff-types.md`を、記事の書き方の決まり9項目に照らした記録。草稿は無く、2026-10-18に新しく書いた。公開順の表には足していない(公開計画の「候補」の節に1行)。

## 書き方の決まり9項目の照らし

| 項目 | 決まり | この記事 | 確かめ方 |
| --- | --- | --- | --- |
| 長さ | 本文3,000〜6,000字 | 4,225字 | `render_release.py doeff-types.md` |
| コード | 枠6つまで | 6つ(Python 4・bash 1・出力のtext 1) | 同上 |
| 図 | 2〜4枚・冒頭近くに全体の図 | 2枚(全体の図・84個の内訳の図)。全体の図は短いコードの次の節 | 同上と目視 |
| 言葉 | 初見の技術者が読める | strict・verifytypes・点(type completeness score)・核の4個・型の見本・印に、初出で一言を添えた。内部の造語と会話・cardのidは使わない | 通読と下の「秘密の確かめ」 |
| 口調 | です・ます、1文60字まで | 60字越え0・です・ます以外0 | `render_release.py` |
| 形 | 悩み→短いコード→全体の図→本文→まとめ→参考 | この順(冒頭の3段落→「まず短いコード」→「全体の図」→本文4節→まとめ→参考) | 見出しの順 |
| 数字 | 測った日と範囲を添える | 0.6.0と0.7.0の数は測った日・Pythonとpyrightの版・範囲(PyPIの2つの版を新しい環境に)を本文に書いた。commitの本文の数は「本文の測り(Python 3.12)」と書いた | 下の「数字の出どころ」 |
| 秘密 | 鍵・token・個人の情報・社内の機体の名を載せない | 当たり0 | 下の「秘密の確かめ」 |
| 公開 | `published: false` | false。案のZennのslugは`doeff-public-api-types` | front matterと書き出し |

## 掲載したコードの確かめ

doeffのmainの`afeeed4d7`(型の見本の道具の直し`732cb5756`を含む)の上の作業の木で、2026-10-18に走らせた。

```sh
uv run --no-sync python publications/zenn-use-cases-v0/reviews/types-check.py --probes  # 6つの枠を、記事の書いた形で走らせる(約4分)。
```

| 枠 | 走らせ方 | 結果 |
| --- | --- | --- |
| 1 `app.py`と2 bash | bashの枠の命令をそのまま走らせた(`uv run --with`でPyPIのdoeff 0.6.0・0.7.0とpyright 1.1.414を一時の環境に入れる) | 0.6.0は4 errors(`task_id`と`value`がUnknown)・0.7.0は0 errors。verifytypesは0.6.0が381個のうち84個・78%、0.7.0が406個のうち5個・98.8% |
| 1 `app.py`(今のmain) | 開発の環境のpyright 1.1.411のstrict | 0 errors |
| 3 tinylib | 枠を一時のpackageにして新しい環境に入れ、verifytypesにかけた。3つの直しを外した形も作った | 直した後は7個のうち0個・100%。直す前は8個のうち5個・37.5%(本文の数) |
| 4 表のテスト | 一時dirに`tests/test_public_types.py`として置き、今のmainの環境とPyPIの0.7.0の環境でpytest | どちらも2 passed。表から`doeff.cli.demo.live_world`の行を抜くと`test_no_new_gap_opens`だけ、`"doeff.run"`を足すと`test_closed_gaps_leave_the_table`だけが落ちた |
| 5 型の見本 | pyrightのstrictとmypy 2.4.0の`--strict`に、今のmainの環境とPyPIの0.7.0の環境でかけた | どちらの環境でも、両方の道具が`# expect-error`の21行目だけに誤りを出した |
| 6 道具の出力 | `tools/verify_type_probes.py --python 3.12`(核の4個をこのcheckoutから組んで新しいvenvへ) | 枠の行がすべて出力にあった(見本25個・印の行32・誤り73・開いている抜けは6の2行・READMEの例 doeff 7個とdoeff-vm 1個は誤り0) |

- `types-check.py`は、0.6.0の型の分からない84個を訳ごとに数え直し、図2の数(27・17・12・11・15・2)と照らす。0.7.0に残った名が`DoExpr`と`ProgramBase`だけで、新しく開いた名が`doeff.cli.demo`の3個だけである事も照らす。
- bashの枠ははじめ、`uv run …`を4行とも書き下していた。Zennのプレビューで行の末の結果の注釈が横にはみ出したので、共通の頭を`run=…`の変数にした。確かめの道具は、変数を決める行を各行の前で走らせる。
- 表のテストと型の見本の枠は、repoの本物(`tests/test_public_name_types.py`・`tests/type_probes/p10_scheduler.py`)を縮めた形。本物は、表を名ごとの訳の定数で持ち、doeff_core_effectsの頭の名と型の付いた4つのmoduleも守る。

### 確かめの途中で見つけて直した物

- 型の見本の道具`tools/verify_type_probes.py`が、mainで落ちていた。2026-10-17の`81aa4fd9d`が`tools/verify_core_install.py`の`install`と`install_problems`に引数`source`を足し、道具は古い2引数で呼んでいた。
- 別のbranchで、道具がこのcheckout(`Checkout()`)を渡す直しと、呼びの形を捕まえるテストを足し、mainへ入れた(`732cb5756`)。直す前の木では、そのテストがTypeErrorで赤だった。
- GitHubのActionsの記録(2026-10-18に読んだ)では、workflow Type probesは`81aa4fd9d`(2026-10-17 18:54 UTCの走り)から`a75011191`(2026-10-18 07:22 UTC)までの17回のpushで毎回failureだった。`732cb5756`から後はsuccess。本文の「約13時間・17回のpush」はこの記録の数。

## 数字の出どころ

| 数字 | 数え方 |
| --- | --- |
| 0.6.0: 公開の名381個・型の分からない84個(unknown 79・ambiguous 5)・78% | 2026-10-18に、`uv run --no-project --python 3.12 --with doeff==0.6.0 --with pyright==1.1.414 pyright --verifytypes doeff`(Python 3.12.14)。84個はverifytypesの`isTypeKnown`がfalseの公開の名 |
| 0.7.0: 406個・5個・98.8% | 同じ命令で`doeff==0.7.0` |
| doeff_core_effectsの92.3% | 同じ環境で`--verifytypes doeff_core_effects`(0.6.0は87.3%) |
| app.pyの4 errors | 同じ環境でpyrightのstrict。0.7.0は0 errors |
| 84個の内訳(27・17・15・12・11・2) | 0.6.0の84個の名を、`types-check.py`の`category`で分けた。27 = doeffの頭から公開し直した依頼と控え(`Ask`・`Tell`・`Task`・`Gather`など)、17 = 廃止した名の置き物、15 = `doeff.result`と`Some`・`Maybe`・`Nothing`、12 = ほかの関数とclass、11 = CLI(5個はambiguous)、2 = `DoExpr`・`ProgramBase` |
| 67個を1つのcommitで塞いだ・15個は先のcommit | `9fe8c330b`と`78c7c3811`の木の開発の環境(Python 3.14.3・pyright 1.1.411)で数えた。0.6.0から`9fe8c330b`の間に塞がった名は`Some`・`Maybe`の15個ちょうど(`6e2e3e986`)。`78c7c3811`が塞いだのは68個で、うち1個はPython 3.13から在るdataclassの`__replace__`(`McpToolDef.__replace__`)。Python 3.12の数では67個 |
| 69個→2個・81.9%→99.5% | commit `78c7c3811`の本文(核の4個だけの環境・pyright 1.1.414・Python 3.12・`9fe8c330b`と比べた値)。本文には「commitの本文の測り(Python 3.12)」と書いた |
| 0.7.0の3個は後で足した名 | `560bc92f4`(2026-10-17 16:27 UTC)が`python -m doeff demo`を足した。`71c68ad24`(同日22:30 UTC)が表に足した。その直前の木(`fec880067`)で`tests/test_public_name_types.py`を走らせ、1 failedで3個の名を挙げる事を確かめた |
| tinylibの8個のうち5個・37.5% | 上の「掲載したコードの確かめ」 |
| 見本25個・印の行32・誤り73 | `tools/verify_type_probes.py --python 3.12`の出力(2026-10-18) |

## 秘密の確かめ

- 新しく書いたfileと変えたfile(画像を除く)を正規表現で探した: 依頼・会話・card・マイルストーン・マージの列のidの形、社内の機体の名、作業の機体のpath、鍵やtokenの形、個人のメールの形。記事の本文の当たりは0だった。
- 台帳の文書(`imagegen/types-svg-source.txt`)には、他の回と同じく、図を撮る命令の名`agora-browser`だけがある。記事の本文には無い。
- 候補の報告のとおり、下流の非公開のリポジトリでの型の測りは載せていない。
- `zenn-check/check.mjs`で書き出しを読み、secretlintの秘密の形は0だった。
- 取引の研究には触れていない。

## Zennの形の確かめ

公開順の表に足さないので、他の候補と同じく、案のslug`doeff-public-api-types`の公開予定の行として`export_zenn.prepare`へ渡し、作業の外のdirへ書き出した。

- 書き出しの食い違い0。1本目と10本目へのリンクは「(公開予定)」になり、候補6へのリンクは題だけになった。
- `check.mjs --online`で、検査の誤り0・画像2枚の欠け0・リンク7個の切れ0・Markdownの崩れ0・秘密の形0(zenn-cli 0.5.6)。
- `agora-browser node screenshot.mjs`で、検査の警告の行0・本文のエラー0・画像2枚の読めない物0(chromium 151)。

## 変えた共有の物

- `doeff-main.md`と`README.md`の表に1行ずつ足した。
- `reviews/main-check.py`の記事の名の一覧に`types`を足した。`reviews/README.md`の表に1行を足した。
- `publication-plan.md`の「候補」の節に1行を足した。公開順の表と`verify_examples.py`の一覧は変えていない。
- 先にmainへ入った候補1の、メインの表の1つのセル(「…別の周期で回す。門の秒の…」)の句点を読点にした。`render_release.py doeff-main.md`が、です・ます以外で終わる文として数えていた。
