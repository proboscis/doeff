# VAULT — Spec-Driven Development Platform

このディレクトリは **Obsidian Vault** として設計されており、設計駆動開発（Spec-Driven Development）のためのナレッジベースです。

## セットアップ

### Obsidian で開く

1. [Obsidian](https://obsidian.md/) をインストール
2. "Open folder as vault" で `VAULT/` を選択
3. 設定は `.obsidian/` に含まれています

### 推奨プラグイン

以下のコアプラグインが有効化されています：

- **Bases** — データベースビュー（テーブル/カード）
- **Templates** — テンプレート挿入
- **Outline** — 目次表示
- **Graph view** — リンク可視化
- **Tags pane** — タグ管理
- **Properties** — Front matter 編集

推奨コミュニティプラグイン（任意）：

- **Kanban** — カンバンボード

## 構造

```
VAULT/
├── .obsidian/           # Obsidian 設定
├── Bases/               # Base 定義ファイル（.base）
├── Templates/           # ドキュメントテンプレート
├── Features/            # Feature（意図のコンテナ）
├── Specs/               # 仕様書・要件定義
├── Designs/             # アーキテクチャ・詳細設計
├── Tasks/               # タスク管理
├── Projects/            # プロジェクト管理
├── Issues/              # 問題・デバッグログ
├── Decisions/           # ADR（意思決定記録）
├── References/          # 参考資料
├── Development Flow.md  # ワークフローガイド
├── Index.md             # ダッシュボード
└── README.md            # このファイル
```

## ワークフロー

```
Feature (意図) → Specs/Designs (定義) → Tasks (実行) → Code
```

詳細は [[Development Flow]] を参照。

## ID システム

| Type | Format | 例 |
|------|--------|-----|
| Feature | FEAT-{MOD}-{NUM} | FEAT-CORE-001 |
| Spec | SPEC-{MOD}-{NUM} | SPEC-CORE-101 |
| Design | DES-{MOD}-{NUM} | DES-CORE-201 |
| Task | TASK-{MOD}-{NUM} | TASK-CORE-301 |
| Issue | ISSUE-{MOD}-{NUM} | ISSUE-CORE-401 |
| Decision | DEC-{MOD}-{NUM} | DEC-CORE-001 |

### Module 略称

| Module | 略称 |
|--------|------|
| core | CORE |
| effects | EFF |
| handlers | HAND |
| linter | LINT |
| cli | CLI |
| gemini | GEM |
| openai | OAI |
| pinjected | PINJ |

## 新しいドキュメントの作成

1. 適切なフォルダに移動
2. 新しいノートを作成
3. `Ctrl/Cmd + T` でテンプレートを挿入
4. Front matter を編集（ID, status, module 等）

## AI との連携

Cursor/Claude に Feature ID を伝えることで、構造化された情報で実装を指示できます：

> "Implement FEAT-CORE-001 according to its linked Specs and Tasks."

## 注意事項

- `.obsidian/` はユーザー固有の設定を含む場合があります
- Base 定義ファイル（`.base`）は YAML 形式です
- Obsidian 外でも Markdown ファイルとして読めます
- for Linking Tasks/Issues or anything, you cannot use ID. you need to specify the file name instead. Examples: Good:`[[README]]` Bad: `[[DES-CORE-001]]`

