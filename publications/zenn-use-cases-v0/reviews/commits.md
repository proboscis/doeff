# 開発の記録「ライブラリのcommitの68%を、Claude Codeと書いた1年」の確かめ

11本目からの候補6の新しい記事`doeff-commits.md`を、記事の書き方の決まり9項目に照らした記録。草稿は無く、2026-10-18に新しく書いた。公開順の表には足していない(公開計画の「候補」の節に1行)。

## 書き方の決まり9項目の照らし

| 項目 | 決まり | この記事 | 確かめ方 |
| --- | --- | --- | --- |
| 長さ | 本文3,000〜6,000字 | 5,301字 | `render_release.py doeff-commits.md` |
| コード | 枠6つまで | 4つ(Python 3・bash 1) | 同上 |
| 図 | 2〜4枚・冒頭近くに全体の図 | 3枚(処理図・全体の図・月ごとの数の図) | 同上と目視 |
| 言葉 | 初見の技術者が読める | 共著の行・エフェクト・ハンドラ・PyPI・pyright・mypy・verifytypes・ADR・semgrepに初出で一言を添えた。内部の造語と会話・cardのidは使わない | 通読と下の「秘密の確かめ」 |
| 口調 | です・ます、1文60字まで | 60字越え0・です・ます以外0 | `render_release.py` |
| 形 | 悩み→短いコード→全体の図→本文→まとめ→参考 | この順(短いコードの節の末尾に処理図、次の節が全体の図) | 見出しの順 |
| 数字 | 測った日と範囲を添える | 表の各行・図・本文に日と範囲。Codexの分・網が止めた件数・不具合との関係は「分からない」と書いた | 下の「数字の出どころ」 |
| 秘密 | 鍵・token・個人の情報・社内の機体の名を載せない | 当たり0 | 下の「秘密の確かめ」 |
| 公開 | `published: false` | false。案のZennのslugは`doeff-claude-code-commits` | front matterと書き出し |

## 掲載したコードの確かめ

doeffのmainの`c0dfc3f24`の上の作業の木で、2026-10-18に走らせた。

```sh
uv run --no-sync python -W error publications/zenn-use-cases-v0/reviews/commits-check.py  # 本文の枠を順に実行し、表示と型の誤りの行を照らす。
uv run --no-sync python -W error publications/zenn-use-cases-v0/examples/commit_share.py  # 完全な例を、開発の環境で走らせる。
uv run --no-project --python 3.12 --with "doeff==0.7.0" python -W error publications/zenn-use-cases-v0/examples/commit_share.py  # PyPIのdoeffだけで走らせる。
```

- 枠1は`(2, 3)`を表示した(決めた3つの本文のうち、大文字の行と小文字の行の2つを共著と数えた)。
- 枠2は、このリポジトリの根で`(3811, 5566)`を表示した。
- 枠3は定義だけで表示は無い。pyright 1.1.411(strict)とmypy 2.4.0(--strict)の誤りは、どちらも印を付けた11行目だけだった。
- 完全な例は、開発の環境とPyPIのdoeff 0.7.0(Python 3.12)の両方で`(2, 3)`と`(3811, 5566)`を表示した。
- 型の検査の枠は、PyPIのdoeff 0.7.0だけの環境でも、pyright・mypyとも11行目だけが誤りだった。
- bashの枠の1行目(`git clone`)は走らせていない。2行目は、上の3つ目の命令と同じ物をこの木の根で走らせた。
- 最初の版の本番のハンドラは`RunProcess`と`subprocess_handler`を使った。開発の環境では動いたが、PyPIのdoeff 0.7.0では`doeff_core_effects.os_process`がHyで書かれていて`doeff[hy]`を要した。読者が`pip install doeff`だけで試せるよう、ハンドラの中で`subprocess.run`を呼ぶ形に替えた。

## 数字の出どころ

| 数字 | 数え方 |
| --- | --- |
| commit 5,566個・共著3,811個(68.5%) | `git log c0dfc3f24`の全commit(mergeを含む)。本文に行頭の`Co-Authored-By: Claude`(大文字と小文字を問わない)を持つcommitをcommit単位で数えた |
| mergeを除いた5,106個のうち3,799個(74.4%) | 同じ範囲で、親が2つ以上のcommit 460個を除いた |
| 月ごとの数(2026年10月の2,114個のうち2,070個・2026年1〜2月の949個のうち117個・2025年11月の44個のうち0個) | author dateの秒をUTC+9に直して月で分けた。表は`commits-months.csv` |
| mergeのcommit 258個(2026年1〜2月) | 同じ分け方で、親が2つ以上のcommitを数えた |
| 共著の行のモデル名(Opus 5.5の2,549行・全3,841行) | 同じ範囲の共著の行を、`(1M context)`と大文字と小文字の違いをまとめて数えた。1つのcommitに2行ある物を含む |
| ほかの道具の名の共著の行13個 | 同じ範囲で、`Co-authored-by:`の後がClaudeでない行(13行とも同じ名) |
| PyPIの版7つ・0.5.0〜0.7.0の時刻 | `https://pypi.org/pypi/doeff/json`の各版の最初のuploadの時刻(UTC)を、2026-10-18に読んでUTC+9に直した。0.5.0は10-17 03:37、0.6.0は10-17 17:11、0.7.0は10-18 08:10 |
| 19個・19.5MB → 4個・3.5MB、`async def`と終了コード0 | `CHANGELOG.md`の0.6.0の節 |
| 137個のうち25個が落ちた・httpx.getの例 | `tests/test_docs_page_examples.py`の頭の説明(0.5.0の時点) |
| 「Why Effects?」の16個の例 | `CHANGELOG.md`の0.7.0の節 |
| 型の分からない公開の名381個のうち69個→2個・81.9%→99.5% | commit `78c7c3811`(2026-10-17)の本文。核の4個だけの環境・pyright 1.1.414・Python 3.12で測った値 |
| 印を付けたファイル25個 | `tests/type_probes/`のファイルの数 |
| ADRのファイル44本・semgrepの規則54個 | `git grep`で、`docs/adr/*.hy`のうち`(defadr `を持つファイルと、`docs/adr/*.hy`・`packages/doeff-agents/`の`(defsemgrep `の数。記事の例・試験の固定データ・macroの定義の中の物は数えない |
| 設計の検証の記録50件・2026-09-25〜10-17・49件に返答 | `docs/design-checks/`の下のディレクトリの数と、各ディレクトリを足したcommitの日。2つのAIの返答のファイル(`blind-a-reply`・`blind-a-return`など)を持つ物が49件 |
| 毎朝・12の組 | `.github/workflows/core-install-from-pypi.yml`の`cron: "17 21 * * *"`(UTC。日本時間の6時17分)と、3つのOS × Python 3.11〜3.14 |

最初は`TZ=Asia/Tokyo`で月を分けたが、作業の機体に時刻帯の表が無く、UTCで分けていた(10月が2,075個になった)。author dateの秒をUTC+9に直して数え直し、表・図・本文を揃えた。

## 秘密の確かめ

- 新しく書いたfileと変えたfile(画像を除く)で、依頼・会話・card・マイルストーンのidの形、社内の機体の名、作業の機体のpath、鍵やtokenの形、個人のメールの形を正規表現で探した。当たりは0だった(共著の行の`noreply@anthropic.com`は除いた)。
- `zenn-check/check.mjs`で書き出しを読み、secretlintの秘密の形は0だった。
- `docs/design-checks`の記録の名は依頼のidなので、本文にはディレクトリの名だけを書き、記録ごとの名は載せない。
- 取引の研究には触れていない。

## Zennの形の確かめ

公開順の表に足さないので、5本目と同じく、候補の行を`export_zenn.prepare`へ渡して書き出した(案のslug`doeff-claude-code-commits`・公開予定と同じ扱い)。

- 書き出しの食い違い0。5本目と10本目へのリンクは「(公開予定)」になり、完全な例はGitHubのURLになった。
- `check.mjs --online`で、検査の誤り0・画像3枚の欠け0・Markdownの崩れ0・秘密の形0。リンク6個のうち、完全な例のGitHubのURL 1個がmainに入る前なので404だった(mainに入った後に測り直す)。zenn-cliは0.5.6。
- `agora-browser node screenshot.mjs`で、検査の警告の行0・本文のエラー0・画像3枚の読めない物0(chromium 151)。

## 変えた共有の物

- `doeff-main.md`と`README.md`の表に1行ずつ足した(`verify_publication.py`と`verify_examples.py`が、メインから全原稿へ案内する事を照らす)。
- `reviews/main-check.py`の記事の名の一覧に`commits`を足した。
- `publication-plan.md`に「候補」の節を作り、1行を足した。公開順の表と`verify_examples.py`の一覧は変えていない。
- 照らしの道具は変えていない。`verify_publication.py`は28記事で通った。
