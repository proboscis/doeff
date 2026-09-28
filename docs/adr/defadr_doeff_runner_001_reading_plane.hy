;;; Executable ADR: doeff-runner の読む面(ide-plugins/vscode/doeff-runner/src/read/)の不変 —
;;; 面は索引だけを読み、source を書き換えず、構文をなぞらず、面の文字は labels の表から出す。
;;; 設計の記録 = docs/design/hy-reading-plane/(v1〜v7 の design と decision)・実装の追跡 = agora-redesign #910(U11)。

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-RUNNER-001
  :title "doeff-runner の読む面は索引だけを読み、source を書き換えない — 単位は定義・入口は tag の複数の軸・文字で出すのは本体だけ・面の文字は labels の表から"
  :status "accepted"
  :scope ["ide-plugins/vscode/doeff-runner/src/read/"
          "ide-plugins/vscode/doeff-runner/src/read/labels.ts"
          "packages/doeff-indexer(hy-index — 面が読む定義 × 属性の正本)"
          "packages/doeff-linter/src/project/body_view.rs(本体の文字の読み方の 1 か所)"]
  :problem
    [(fact
       "doeff-hy の code を file と木で読むと、系の構造(context・role などの tag の軸・effect・呼び出し・型)が file の置き場の 1 軸に潰れる。operator 2026-09-28 22:1x〜23:1x の逐語: \"I feel it's nonesense to make a scope of variable or anything bound to a file and file structure\" / \"because a tree structure can never cover the entire axis of domain\" / \"the system should be more organized across multiple axis of tags rather than a single file structure tree\"。"
       :evidence "docs/design/hy-reading-plane/records/decision-05be5e9de565.json(会話 defk-view-discuss・herdr pane w3J:pA)")
     (fact
       "読む面が自分で file を歩いて定義を集めると、hy-index(doeff-indexer)と別の 2 つ目の定義の一覧ができ、索引と面で定義の集合・位置・属性が食い違う。面の module は今は索引と linter の出力だけを受け取っている(2026-09-29 実測: src/read/ の 9 file に fs / child_process / findFiles / workspace.fs は 0 件)が、それを止める検査が無かった。"
       :evidence "ide-plugins/vscode/doeff-runner/src/read/*.ts(2026-09-29 origin/main 8bae6543)・agora-redesign #910 受け入れ条件 3 の制約 5・V8")
     (fact
       "operator は source を手で編集しない(#849 の逐語 \"I will never edit the source manually\")。読む面に編集の機能があると、Hy の source を書き換える 2 つ目の入口が面の中に生まれ、読むための面という性格が崩れる。"
       :evidence "docs/design/hy-reading-plane/records/design-c718ca28a562.json(ADR-DOTFILES-027 R-28bb3a77・#849 逐語 6)・#910 V9")]
  :context
    [(interpretation
       "Hy の file は定義の置き場で、系の構造は定義 × 属性の面(= hy-index)にある。operator 逐語 \"so hy lang is just a way to store data but we want our doeff-hy to be in different plane as a system structure\"(2026-09-28)・\"in my understanding hy is great at storing arbitrary data and code in freely structured manner, for our purpose\"(2026-09-28 23:1x)。したがって面は構造の層(索引)だけを読み、置き場の層(file)を直に読まない。")
     (interpretation
       "Hy の form の読み方(本体の文字の組み立て)は doeff-linter の 1 か所(src/project/body_view.rs・editor-json の bodies)に置き、面は描くだけにする(#849 の決定「読み方の写しを持たない」)。面が file を読まないことは、この 1 か所を守ることでもある。")
     (interpretation
       "読む面は Scala の構文(using・/** */・=)をなぞらず、実体を HTML の部品で見せる。operator 逐語 \"so we are not to follow syntax like using. we want better html visualization of entity like defk\"(2026-09-28 23:1x)。構文をなぞらないことは静的な検査で縛れない(描き方の判断)ので、law の文として残す。")]
  :decision
    [(rule R1 "読む面の単位は file ではなく定義で、入口は木ではなく tag の複数の軸(と effect・呼び出し・型・テスト・置き場)の切り替えと交差。1 つの定義 = 1 枚のカード。operator 2026-09-28 22:1x〜23:1x の裁定(decision-05be5e9de565 — \"perhaps D is the way to go\" / \"to me D reads far better, than pure lisp\" / \"actually we are not only talking about defk but the whole doeff-hy code reading plane\")。")
     (rule R2 "面(src/read/ の module)は file を自分で読まない。定義の一覧・軸の属性・位置は hy-index の置き場から、型・effect・本体の行は doeff-linter の editor-json から受け取る。fs / node:fs / child_process の import と vscode.workspace.findFiles・vscode.workspace.fs は面の外(hy/・lint/ の読み手)に置く(#910 受け入れ条件 3 の制約 5・V8)。")
     (rule R3 "面は読み取り専用: document を書き換える API(WorkspaceEdit・applyEdit・TextEditor.edit・document の save・saveAll)を面に置かない。source を直す時は各カードの open in editor で editor を開く(v1 R2・#910 V9)。")
     (rule R4 "面は Scala などの構文をなぞらず、実体を HTML の部品で見せる。文字で出すのは本体だけで、本体の文字の形は operator が承認済み(\"yeah val var when match is perfect.\" — 2026-09-28 23:4x・decision-751eb42b8152)。形の正本は packages/doeff-linter/docs/SPECIFICATION.md 20 節(decision-ca54efb91260・v2 2.2・v3 2)。")
     (rule R5 "面に出るラベルの文字は labels.ts の文字列の表 1 つから引く(operator 2026-09-29 00:0x \"instead of 引数/答え use args / return type\" — decision-ef207b6c19ad)。実体の種類のバッジ・tags の key・docstring・本体は source のまま。静的な検査は置かない: 日本語の文字列 literal を禁じると内部の例外文まで赤になり、英語の literal は CSS の class や id と区別できないため。レビューで見る。")
     (rule R6 "戻し方: R2 / R3 を外す時は .semgrep.yaml の doeff-runner-reading-plane-reads-only-the-index / doeff-runner-reading-plane-is-read-only と、この ADR の enforcement を同じ commit で消し、docs/adr/enforcement-ledger.json を作り直す(ADR-DOE-ENFORCE-001 R5)。読む面そのものは設定 doeffRunner の読む面の切り替えで切れる(#910 受け入れ条件 6)。")]
  :laws
    [(law reading-plane-reads-only-the-index
       :statement "module ∈ src/read/ => not reads_files(module) and definitions(module) ⊆ hy_index"
       :counterexamples
         [(counterexample "面が vscode.workspace.findFiles で .hy を集め、索引に無い定義のカードを作る(索引と面で定義の集合が食い違う)")
          (counterexample "面が fs.readFileSync で source を読み直して本体の文字を組み立てる(linter の 1 か所の読み方の写しができる)")])
     (law reading-plane-is-read-only
       :statement "module ∈ src/read/ => no document mutation (WorkspaceEdit / applyEdit / TextEditor.edit / save)"
       :counterexamples
         [(counterexample "カードの中の本体の文字をその場で直せる編集欄を足し、WorkspaceEdit で source へ書き戻す")
          (counterexample "面を閉じる時に document.save() を呼ぶ")])
     (law reading-plane-labels-come-from-one-table
       :statement "label shown on the plane => LABELS[key] (labels.ts)"
       :counterexamples
         [(counterexample "render.ts に 'callers' や '引数' の文字列を直に書き、labels.ts の表と食い違う")])]
  :enforcement
    ;; installed 版の規則は .semgrep.yaml の 2 本(paths.include = **/doeff-runner/src/read/** — 検体の一時の木と
    ;; 本物の木の両方で当たる)。本物の src/read/ が緑であり続けることは tests/semgrep/test_reading_plane_rules.py と
    ;; .pre-commit-config.yaml の semgrep-reading-plane が検める。
    [(defsemgrep doeff-runner-reading-plane-reads-only-the-index
       "doeff-runner-reading-plane-reads-only-the-index"
       [{"relative-path" "ide-plugins/vscode/doeff-runner/src/read/bad_node_fs.ts"
         "source" "import * as fs from 'fs';\n\nexport function body(path: string): string {\n  return fs.readFileSync(path, 'utf8');\n}\n"}
        {"relative-path" "ide-plugins/vscode/doeff-runner/src/read/bad_fs_promises.ts"
         "source" "import { readFile } from 'node:fs/promises';\n\nexport const read = readFile;\n"}
        {"relative-path" "ide-plugins/vscode/doeff-runner/src/read/bad_child_process.ts"
         "source" "import { execFile } from 'child_process';\n\nexport const run = execFile;\n"}
        {"relative-path" "ide-plugins/vscode/doeff-runner/src/read/bad_find_files.ts"
         "source" "import * as vscode from 'vscode';\n\nexport async function all(): Promise<number> {\n  const uris = await vscode.workspace.findFiles('**/*.hy');\n  return uris.length;\n}\n"}
        {"relative-path" "ide-plugins/vscode/doeff-runner/src/read/bad_workspace_fs.ts"
         "source" "import * as vscode from 'vscode';\n\nexport async function bytes(uri: vscode.Uri): Promise<Uint8Array> {\n  return vscode.workspace.fs.readFile(uri);\n}\n"}]
       [{"relative-path" "ide-plugins/vscode/doeff-runner/src/read/clean_index_only.ts"
         "source" "import * as path from 'path';\nimport * as vscode from 'vscode';\nimport type { HyIndexStore } from '../hy/store';\n\nexport function label(store: HyIndexStore, uri: vscode.Uri): string {\n  const enabled = vscode.workspace.getConfiguration().get<boolean>('doeffRunner.readingPlane');\n  return `${path.basename(uri.fsPath)} ${String(store.get(uri.fsPath))} ${String(enabled)}`;\n}\n"}
        {"relative-path" "ide-plugins/vscode/doeff-runner/src/hy/reader_outside_the_plane.ts"
         "source" "import * as fs from 'fs';\n\nexport const text = (p: string): string => fs.readFileSync(p, 'utf8');\n"}])
     (defsemgrep doeff-runner-reading-plane-is-read-only
       "doeff-runner-reading-plane-is-read-only"
       [{"relative-path" "ide-plugins/vscode/doeff-runner/src/read/bad_workspace_edit.ts"
         "source" "import * as vscode from 'vscode';\n\nexport async function put(document: vscode.TextDocument): Promise<void> {\n  const edit = new vscode.WorkspaceEdit();\n  edit.insert(document.uri, new vscode.Position(0, 0), ';; x\\n');\n  await vscode.workspace.applyEdit(edit);\n}\n"}
        {"relative-path" "ide-plugins/vscode/doeff-runner/src/read/bad_editor_edit.ts"
         "source" "import * as vscode from 'vscode';\n\nexport async function put(editor: vscode.TextEditor): Promise<boolean> {\n  return editor.edit((builder) => builder.insert(new vscode.Position(0, 0), 'x'));\n}\n"}
        {"relative-path" "ide-plugins/vscode/doeff-runner/src/read/bad_save.ts"
         "source" "import * as vscode from 'vscode';\n\nexport async function close(document: vscode.TextDocument): Promise<boolean> {\n  return document.save();\n}\n"}]
       [{"relative-path" "ide-plugins/vscode/doeff-runner/src/read/clean_fold_memory.ts"
         "source" "import * as vscode from 'vscode';\n\nexport function remember(memory: { save(key: string, value: string): void }, uri: vscode.Uri): void {\n  memory.save(uri.fsPath, 'folded');\n  void vscode.commands.executeCommand('vscode.open', uri);\n}\n"}
        {"relative-path" "ide-plugins/vscode/doeff-runner/src/defk/editor_outside_the_plane.ts"
         "source" "import * as vscode from 'vscode';\n\nexport async function put(document: vscode.TextDocument): Promise<boolean> {\n  return document.save();\n}\n"}])]
  :plans ["docs/design/hy-reading-plane/"
          "https://github.com/proboscis/agora-redesign/issues/910"])
