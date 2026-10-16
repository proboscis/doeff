# メイン記事の図の制作記録

記事ごとの制作記録。はじめ[imagegen/README.md](../README.md)の末尾へ書き足していた節を、2026-10-17に文を変えずにこのファイルへ移した。文中の`manifest.json`・`captions.json`は、今は記事ごとの`../manifest/main.json`・`../../visuals/captions/main.json`である。

## メインの最小ハンドラを図へ反映（2026-09-16追記）

main-conceptとmain-flowをv3へ更新した。組み込みimagegenで生成・編集し、@handler・@do・handle_ask_effect、Resumeによる再開、with_handlersによる設置を本文と揃えた。旧図はarchiveへ保持。採用プロンプトはmain-*-flat-code-v3.txt、編集はmain-*-flat-code-v3-edit1.txt。実行結果はrunで得られることも図へ明記した。

## メイン冒頭のキービジュアル

[main-hero.png](/images/zenn-use-cases-v0/generated/main-hero.png)を追加した。[生成プロンプト](../main-hero-flat-code-v1.txt)を組み込みimagegenへ渡して制作し、コードと往復矢印を目視確認した。タイトル直下に配置し、定義と最小コードへ続く。画像47枚の寸法・容量・採用ハッシュはmanifest.jsonを参照。

## メイン記事の3枚をSVGで描き直す（2026-10-16追記）

- メインの3枚（main-hero・main-concept・main-flow）も、週1の仕上げに合わせて手で書いたSVGから作り直した。
- 正本と作り方は[main-svg-source.txt](../main-svg-source.txt)に書いた。manifest.jsonの`tool`は時間の記事と同じ。
- これで残りは40枚。
