;;; Executable ADR: invariants of doeff-runner's reading plane (ide-plugins/vscode/doeff-runner/src/read/) —
;;; the plane reads only the index, does not rewrite source, does not imitate syntax, and takes its text from the labels table.
;;; Design record = docs/design/hy-reading-plane/ (the design and decision files of v1–v7).

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-RUNNER-001
  :title "doeff-runner's reading plane reads only the index and does not rewrite source — the unit is the definition, the entry is multiple tag axes, only bodies are shown as text, and the plane's text comes from the labels table"
  :status "accepted"
  :scope ["ide-plugins/vscode/doeff-runner/src/read/"
          "ide-plugins/vscode/doeff-runner/src/read/labels.ts"
          "packages/doeff-indexer(hy-index — source of truth for the definitions × attributes the plane reads)"
          "packages/doeff-linter/src/project/body_view.rs(the single place that defines how body text is read)"]
  :problem
    [(fact
       "Reading doeff-hy code by file and tree collapses the system's structure (tag axes such as context and role, effects, calls, types) onto the single axis of where files are placed. Binding the scope of variables and such to files and file structure makes no sense. A tree structure cannot cover all axes of the domain, so the system should be organized not by a single file tree but by multiple tag axes."
       :evidence "docs/design/hy-reading-plane/records/decision-05be5e9de565.json")
     (fact
       "If the reading plane walks files itself to collect definitions, a second list of definitions separate from hy-index (doeff-indexer) appears, and the index and the plane disagree on the set, positions, and attributes of definitions. The plane's modules currently receive only the index and the linter output (measured 2026-09-29: 0 occurrences of fs / child_process / findFiles / workspace.fs in the 9 files of src/read/), but there was no check stopping that."
       :evidence "ide-plugins/vscode/doeff-runner/src/read/*.ts(2026-09-29 origin/main 8bae6543)")
     (fact
       "Source is not edited by hand. If the reading plane had editing features, a second entry point for rewriting Hy source would appear inside the plane, and its character as a plane for reading would break down."
       :evidence "docs/design/hy-reading-plane/records/design-c718ca28a562.json")]
  :context
    [(interpretation
       "A Hy file is a place where definitions are stored; the system's structure lives in the definition × attribute plane (= hy-index). For this use, Hy is an excellent language because it can store arbitrary data and code in free form, and it is merely a place to store data. The structure of a doeff-hy system is put on a separate plane. Therefore the plane reads only the structure layer (the index) and does not read the storage layer (files) directly.")
     (interpretation
       "How Hy forms are read (assembling the body text) lives in one place in doeff-linter (src/project/body_view.rs, the bodies of editor-json); the plane only draws and holds no copy of the reading logic. The plane not reading files also protects this single place.")
     (interpretation
       "The reading plane does not imitate Scala syntax (using, /** */, =), and shows entities such as defk with better HTML components. Not imitating syntax cannot be bound by a static check (it is a rendering judgment), so it is kept as the text of a law.")]
  :decision
    [(rule R1 "The unit of the reading plane is the definition, not the file, and the entry is switching and intersecting multiple tag axes (plus effects, calls, types, tests, location), not a tree. One definition = one card (decision-05be5e9de565 — option D was adopted. D is much more readable than plain lisp, and the target is the whole plane for reading doeff-hy code, not only defk).")
     (rule R2 "The plane (the modules in src/read/) does not read files itself. It receives the list of definitions, axis attributes, and positions from the hy-index store, and types, effects, and body lines from doeff-linter's editor-json. Imports of fs / node:fs / child_process and vscode.workspace.findFiles / vscode.workspace.fs are placed outside the plane (the readers in hy/ and lint/).")
     (rule R3 "The plane is read-only: APIs that rewrite documents (WorkspaceEdit, applyEdit, TextEditor.edit, document save, saveAll) are not placed in the plane. To fix source, open the editor via each card's open in editor (v1 R2).")
     (rule R4 "The plane does not imitate Scala or other syntax and shows entities with HTML components. Only bodies are shown as text, and the shape of body text is approved (the val / var shape for match — 2026-09-28, decision-751eb42b8152). The source of truth for the shape is section 20 of packages/doeff-linter/docs/SPECIFICATION.md (decision-ca54efb91260, v2 2.2, v3 2).")
     (rule R5 "Label text shown on the plane is taken from one string table in labels.ts (labels are args / return type rather than the Japanese words for argument / answer, decision-ef207b6c19ad). Entity-kind badges, tag keys, docstrings, and bodies are as in the source. No static check is placed: banning Japanese string literals would turn even internal exception messages red, and English literals cannot be distinguished from CSS classes or ids. This is checked in review.")
     (rule R6 "Rollback: when removing R2 / R3, delete doeff-runner-reading-plane-reads-only-the-index / doeff-runner-reading-plane-is-read-only in .semgrep.yaml and this ADR's enforcement in the same commit, and rebuild docs/adr/enforcement-ledger.json (ADR-DOE-ENFORCE-001 R5). The reading plane itself can be turned off with the reading-plane switch in the doeffRunner settings.")]
  :laws
    [(law reading-plane-reads-only-the-index
       :statement "module ∈ src/read/ => not reads_files(module) and definitions(module) ⊆ hy_index"
       :counterexamples
         [(counterexample "The plane collects .hy files with vscode.workspace.findFiles and makes cards for definitions not in the index (the index and the plane disagree on the set of definitions)")
          (counterexample "The plane re-reads source with fs.readFileSync and assembles body text (a copy of the linter's single reading logic appears)")])
     (law reading-plane-is-read-only
       :statement "module ∈ src/read/ => no document mutation (WorkspaceEdit / applyEdit / TextEditor.edit / save)"
       :counterexamples
         [(counterexample "Adding an edit field that lets body text in a card be fixed in place, writing back to source with WorkspaceEdit")
          (counterexample "Calling document.save() when the plane closes")])
     (law reading-plane-labels-come-from-one-table
       :statement "label shown on the plane => LABELS[key] (labels.ts)"
       :counterexamples
         [(counterexample "Writing strings such as 'callers' or a Japanese 'argument' label directly in render.ts, disagreeing with the labels.ts table")])]
  :enforcement
    ;; The installed rules are the 2 in .semgrep.yaml (paths.include = **/doeff-runner/src/read/** — they hit both the temporary tree of the samples and
    ;; the real tree). That the real src/read/ stays green is checked by tests/semgrep/test_reading_plane_rules.py and
    ;; semgrep-reading-plane in .pre-commit-config.yaml.
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
  :plans ["docs/design/hy-reading-plane/"])
