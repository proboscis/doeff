# doeff入門 - Pythonで代数的エフェクト

Zenn本の構成案。

---

## 想定読者

- Pythonに慣れた開発者
- async/await は使ったことがある
- 関数型プログラミングに興味があるが、モナドで挫折した人
- テスト容易性、アーキテクチャに関心がある人

---

## 構成

### 第1部: なぜdoeffが必要か（新規執筆）

| 章 | タイトル | 内容 | ステータス |
|----|---------|------|-----------|
| 01 | 従来のPythonの問題点 | コールバック地獄、状態とIOの混在、テストの難しさ | ✅ 完了 |
| 02 | 代数的エフェクトという解決策 | 「何をしたいか」と「どうやるか」の分離 | ✅ 完了 |
| 03 | doeffの設計思想 | ジェネレータベースのエフェクトシステム、Pythonイディオムの尊重 | ✅ 完了 |

### 第2部: 基本を学ぶ（docs/から再構成）

| 章 | タイトル | 元ドキュメント | ステータス |
|----|---------|---------------|-----------|
| 04 | インストールと最初のプログラム | docs/01-getting-started.md | ✅ 完了 |
| 05 | ProgramとEffectの概念 | docs/02-core-concepts.md | ✅ 完了 |
| 06 | 基本エフェクト: Reader, State, Writer | docs/03-basic-effects.md | ✅ 完了 |
| 07 | エラーハンドリング | docs/05-error-handling.md | ✅ 完了 |

### 第3部: 実践的な使い方（docs/から再構成）

| 章 | タイトル | 元ドキュメント | ステータス |
|----|---------|---------------|-----------|
| 08 | 非同期処理 | docs/04-async-effects.md | ✅ 完了 |
| 09 | キャッシュシステム | docs/07-cache-system.md | ✅ 完了 |
| 10 | 実用パターン集 | docs/12-patterns.md | ✅ 完了 |
| 11 | Kleisli Arrowと合成 | docs/11-kleisli-arrows.md | ✅ 完了 |

### 第4部: アーキテクチャの深層（新規執筆）

| 章 | タイトル | 内容 | ステータス |
|----|---------|------|-----------|
| 12 | ランタイムとスケジューラ | docs/20-runtime-scheduler.md + 解説 | ✅ 完了 |
| 13 | Pure Coreパターン | ロジックとIOの完全分離 | ✅ 完了 |
| 14 | 構造化ログと実行トレース | ログからの可視化、Kleisli/transformによる後処理 | ✅ 完了 |
| 15 | card_game_2026での実例 | 実際のゲームでの適用例 | ✅ 完了 |

> **Note**: doeffのgeneratorはPythonの制約上シリアライズ不可。
> ただし、構造化ログ（StructuredLog）を通じて実行トレースを記録・再現できる。

### 第5部: 産業利用パターン（placementプロジェクトから）

| 章 | タイトル | 内容 | ステータス |
|----|---------|------|-----------|
| 16 | Pipeline Oriented Programming | データフロー中心設計、Pathを避ける、純粋関数の連鎖 | ✅ 完了 |
| 17 | Protocol-Based Injection | 型安全な依存性注入、`@impl`パターン | ✅ 完了 |
| 18 | エラーハンドリング: recoverパターン | 例外を投げる→呼び出し側でrecover、first_success | ✅ 完了 |
| 19 | 構造化ログとトレース | slog、listen、後処理での可視化 | ✅ 完了 |
| 20 | Kleisli Toolsとtransform | IDE連携、CLIでの後処理チェーン | ✅ 完了 |

### 第6部: 応用と展望（新規執筆）

| 章 | タイトル | 内容 | ステータス |
|----|---------|------|-----------|
| 21 | 他の言語との比較 | OCaml 5, Koka, Eff, SimPy | ✅ 完了 |
| 22 | 応用領域 | シミュレーション、バックテスト、ゲーム | ✅ 完了 |
| 23 | 今後の展望 | 型システム、ドメイン制約、コミュニティ | ✅ 完了 |

---

## 執筆方針

1. **ストーリー性**: 「問題→解決→深掘り」の流れ
2. **コード例重視**: 説明より動くコードを見せる
3. **図解**: アーキテクチャ図で視覚的に理解
4. **比較**: 従来手法との違いを明確に

---

## ファイル一覧

| ファイル | 内容 |
|---------|------|
| `01-why-doeff.md` | 第1章: 従来のPythonの問題点 |
| `02-algebraic-effects.md` | 第2章: 代数的エフェクトという解決策 |
| `03-design-philosophy.md` | 第3章: doeffの設計思想 |
| `04-getting-started.md` | 第4章: インストールと最初のプログラム |
| `05-program-and-effect.md` | 第5章: ProgramとEffectの概念 |
| `06-basic-effects.md` | 第6章: 基本エフェクト |
| `07-error-handling.md` | 第7章: エラーハンドリング |
| `08-async-effects.md` | 第8章: 非同期処理 |
| `09-cache-system.md` | 第9章: キャッシュシステム |
| `10-patterns.md` | 第10章: 実用パターン集 |
| `11-kleisli-arrows.md` | 第11章: Kleisli Arrowと合成 |
| `12-runtime-scheduler.md` | 第12章: ランタイムとスケジューラ |
| `13-pure-core.md` | 第13章: Pure Coreパターン |
| `14-structured-logging.md` | 第14章: 構造化ログと実行トレース |
| `15-card-game-example.md` | 第15章: card_game_2026での実例 |
| `16-pipeline-oriented-programming.md` | 第16章: Pipeline Oriented Programming |
| `17-protocol-based-injection.md` | 第17章: Protocol-Based Injection |
| `18-recover-pattern.md` | 第18章: recoverパターン |
| `19-industrial-logging.md` | 第19章: 構造化ログとトレース（産業利用編） |
| `20-kleisli-tools.md` | 第20章: Kleisli Toolsとtransform |
| `21-language-comparison.md` | 第21章: 他の言語との比較 |
| `22-applications.md` | 第22章: 応用領域 |
| `23-future.md` | 第23章: 今後の展望 |

---

## 参考資料

- docs/（既存ドキュメント）
- card_game_2026/docs/CONCEPT.md（日本語の哲学説明）
- Dan Abramov "Algebraic Effects for the Rest of Us"
