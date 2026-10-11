# 盲検の入力(A・B 共通の材料)

## 対象の版と読める範囲

- repo: doeff
- 読み取り専用の作業木: `/home/ubuntu/.worktrees/doeff-wt-doeff002-design-check`
  - 版 = 4ccab04aca929df800e6d33d36b8e31c8f87fcea(直しの後の main)
  - 直す前の版 = 61f0600093debe4c04c6550985bc4b26a3b6e4dc。`git -C <作業木> show 61f060009:<path>` で読める。
- 読んでよい物: 上の作業木の file と git の履歴。
- 読まない物: `docs/design-checks/lt-JAMKZ0NP8ZTQ52AHNGW67SGKPP/` の下の、自分の入力 file(この file と blind-a-input.md か blind-b-input.md)以外。
- linter の binary(直しの後の版から組んだ物): `/home/ubuntu/.cache/doeff-linter-snapshots/02fca1e258949b32bb62c7ad708aab9873e0b252/doeff-linter`
  - 使い方の例: `<binary> --no-log --output-format json <file.py>`
  - 当ててよいのは、自分で作る一時の dir(`/tmp/doeff002-blind-<A か B>/`)の下の file だけ。
- してはいけない事:
  - repo の file を書き換える。
  - repo 全体に linter や pytest を当てる。
  - 他の会話へ連絡する。

## 要件

- doeff には自前の linter(Rust 製・doeff-linter)がある。
- 日次の検証が、規則 × file ごとの所見の数を基点(baseline)と比べ、増えれば赤にする。
- 規則 DOEFF002(Mutable Attribute Naming)は、class の属性に `__init__` 等の初期化の外で代入するなら、名を `mut_`(公開)/ `_mut`(非公開)で始めるよう強いる。
  - 目的: object が作られた後に変わる状態を、名で見えるようにする。

## 変更の中身(直しの後の版で入った物)

1. DOEFF002 が初期化と見る method に `__set_name__` を足した。
   - 前は `__init__` と `__post_init__` だけだった。
   - `__set_name__` は、descriptor が置かれた class を作る時に Python が呼ぶ(PEP 487)。
2. DOEFF002 の説明文を、実装に合わせた。
   - 前の説明文: 「list・dict・set の可変な属性」
   - 今の説明文: 値の型を問わず、初期化の外で代入される属性
3. 基点の file の `version` を、今の linter の組み立ての入力の鍵へ替えた。
   - 前は 10-03 の linter の鍵だった。
   - 日次は、基点の file を最後に書いた commit の linter で測る。
4. 鍵を替えた事で判定に入る DOEFF004(`os.environ` への参照)の所見を直した。
   - 検の 4 file: 子の process へ `{**os.environ, …}` を渡す形を、pytest の `monkeypatch.setenv` に替えた。
   - `tools/rust_wheel_release.py`: `tools/architecture.hy` に層 `release` を足し、名指しの module に限って DOEFF004 を外した。
5. `program_effects.py` の `_Reader` の、遅れて作る属性 `_absent` を `_mut_absent` に改名した。

## 責務(module)

### M1 DOEFF002 の規則

- path: `packages/doeff-linter/src/rules/doeff002_mutable_attribute_naming.rs`
- 持つ判断:
  - 初期化の method の集合(`is_init_method`)
  - 初期化の外の `self.X` への代入を数える事
  - 名の決まり
- 隠す事: 値の型を問わない事。
- 口: `LintRule::check(&RuleContext) -> Vec<Violation>`
- effect: なし。

### M2 規則の説明文

- path: `packages/doeff-linter/src/rule_info.rs`・`packages/doeff-linter/src/report.rs`
- 持つ物: 説明文。M1 と食い違わない事。

### M3 基点の比べ

- path:
  - `scripts/hook_finding_baseline.py`
  - `scripts/hook_finding_baseline/doeff-linter.json`
  - `scripts/doeff_linter_locked.py`
  - `scripts/gate_tools.sh`(`linter`)
- 持つ物: 基点の数と、数えた linter の鍵。
- 不変条件:
  - 基点は下がる向きにだけ動く。
  - 鍵の合う binary が無ければ「測れない」。

### M4 tools の層の宣言

- path: `tools/architecture.hy`
- 持つ物: 規則の母集団から外す、名指しの module と理由。

### M5 閉じの解析の reader

- path: `packages/doeff-effect-analyzer/python/doeff_effect_analyzer/program_effects.py` の `_Reader` と `_ClauseEmits`
- 持つ物: 遅れて作る `:absent` の受け手。

### M6 子の process を起こす検

- path:
  - `packages/doeff-hy/tests/test_dataclass_code_store.py`
  - `packages/doeff-hy/tests/test_import_gc.py`
  - `tests/test_hook_finding_baseline.py`
  - `tests/test_wheel_remote_store.py`
- 持つ物: 子へ渡す環境(`monkeypatch.setenv`)。

## 将来の変更についての主張(前提つき)

### S1(concurrency)

- 変更: thread をまたいで共有される object に、後から作って持つ状態を足す。または、descriptor が自分の名を `__set_name__` で覚える。
- 主張:
  - M1 は、後から代入する状態を型に依らず `_mut` の名に強いる。
  - `__set_name__` だけで代入する descriptor の状態は強いない。
  - 初期化の集合を変えるのは、M1 の `is_init_method` の 1 か所だけ。
- 予想の範囲: M1(と、その検・M2 の文)。M3 は `version` の 1 行。M4〜M6 の code は変わらない。
- 前提: 代入は `self.<名> = …` / `+=` / 注釈つきの代入の形で書かれる。

### S2(storage)

- 変更: linter の規則の意味を変え、日次が新しい linter で測るよう基点の鍵を替える。
- 主張:
  - 鍵の替えは、基点の file の `version` の 1 行と、下がる向きの数だけを変える。M3 の code は変えない。
  - 鍵の合わない状態では、日次は黙って測らず「測れない」と名乗る。
  - 鍵の替えで判定に入る所見は、基点に足さずに直す。
- 予想の範囲: M3 の data 1 行と、判定に入る所見の持ち主。

### S3(effects)

- 変更: 検が子の process に、もう 1 つ環境変数を渡す。または、tools の下に環境変数を読む道具を足す。
- 主張:
  - 検は `monkeypatch.setenv` で置き(M6 だけ)、DOEFF004 は鳴らない。
  - tools の新しい道具は、M4 に名指して理由を書かない限り DOEFF004 が当たる。
  - 名指しは module 単位で、層のほかの file へ広がらない。
- 予想の範囲: M6 か、M4 の 1 項目。

### 外したシナリオ

- hardware・distribution・simulation の軸は外した。
- 理由: 静的な解析であり、binary を配る仕組みは変えていない。
