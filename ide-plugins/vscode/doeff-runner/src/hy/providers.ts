// VS Code の provider — 索引の store と解決の論理の結果を VS Code の型へ写すだけの薄い層。
// どの provider も同じ store から読み、自分で索引を読み込まない。

import * as vscode from 'vscode';
import type { HyRange } from './contract';
import { symbolAt } from './cursor';
import type { ExternalFileView, ExternalModuleSource } from './external';
import type { HyLog } from './indexService';
import {
  buildOutline,
  hoverMarkdown,
  outlineKindOf,
  searchWorkspaceSymbols,
  type OutlineNode,
  type OutlineSymbolKind
} from './outline';
import type { PythonModuleSource } from './python';
import { collectReferences, resolveDefinition, type DefinitionResolution, type DefinitionTarget } from './resolve';
import type { HyIndexView } from './store';

const WORKSPACE_SYMBOL_LIMIT = 500;

/** 契約の範囲を VS Code の Range にする。 */
function toRange(range: HyRange): vscode.Range {
  return new vscode.Range(range.start.line, range.start.character, range.end.line, range.end.character);
}

/** 目次の記号の種類を VS Code の SymbolKind にする(網羅を compiler が確かめる)。 */
function toSymbolKind(kind: OutlineSymbolKind): vscode.SymbolKind {
  switch (kind) {
    case 'Function':
      return vscode.SymbolKind.Function;
    case 'Method':
      return vscode.SymbolKind.Method;
    case 'Class':
      return vscode.SymbolKind.Class;
    case 'Enum':
      return vscode.SymbolKind.Enum;
    case 'EnumMember':
      return vscode.SymbolKind.EnumMember;
    case 'Field':
      return vscode.SymbolKind.Field;
    case 'Object':
      return vscode.SymbolKind.Object;
    case 'Event':
      return vscode.SymbolKind.Event;
    case 'Variable':
      return vscode.SymbolKind.Variable;
    case 'Module':
      return vscode.SymbolKind.Module;
    case 'Namespace':
      return vscode.SymbolKind.Namespace;
    case 'Property':
      return vscode.SymbolKind.Property;
    case 'Constant':
      return vscode.SymbolKind.Constant;
    case 'TypeParameter':
      return vscode.SymbolKind.TypeParameter;
    default: {
      const unreachable: never = kind;
      throw new Error(`網羅されていない種類: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 行き先 1 件を VS Code の Location にする(module そのものは file の先頭)。 */
function targetLocation(target: DefinitionTarget): vscode.Location {
  const uri = vscode.Uri.file(target.path);
  switch (target.tag) {
    case 'hy-definition':
      return new vscode.Location(uri, toRange(target.definition.range));
    case 'python-definition':
      return new vscode.Location(uri, toRange(target.range));
    case 'hy-module':
    case 'python-module':
      return new vscode.Location(uri, new vscode.Position(0, 0));
    default: {
      const unreachable: never = target;
      throw new Error(`網羅されていない行き先: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 目次の項目を VS Code の DocumentSymbol にする(子も再帰で)。 */
function toDocumentSymbol(node: OutlineNode): vscode.DocumentSymbol {
  const symbol = new vscode.DocumentSymbol(
    node.name,
    node.detail,
    toSymbolKind(node.kind),
    toRange(node.range),
    toRange(node.selectionRange)
  );
  symbol.children = node.children.map(toDocumentSymbol);
  return symbol;
}

/** Hy の定義へ移動・参照・目次・記号の検索・hover を 1 つの store から答える provider。 */
export class HyNavigationProvider
  implements
    vscode.DefinitionProvider,
    vscode.ReferenceProvider,
    vscode.DocumentSymbolProvider,
    vscode.WorkspaceSymbolProvider,
    vscode.HoverProvider
{
  constructor(
    private readonly index: HyIndexView,
    private readonly python: PythonModuleSource,
    private readonly external: ExternalModuleSource & ExternalFileView,
    private readonly log: HyLog
  ) {}

  /** カーソルの記号を解決する(provider 共通の入口)。記号でない所なら undefined。 */
  private async resolveAt(
    document: vscode.TextDocument,
    position: vscode.Position
  ): Promise<{ readonly name: string; readonly resolution: DefinitionResolution } | undefined> {
    const symbol = symbolAt(document.lineAt(position.line).text, position.character);
    if (symbol === undefined) {
      return undefined;
    }
    const resolution = await resolveDefinition(this.index, this.python, this.external, {
      filePath: document.uri.fsPath,
      name: symbol.name,
      qualifier: symbol.qualifier
    });
    for (const problem of resolution.problems) {
      this.log.appendLine(`[hy] ${problem}`);
    }
    return { name: symbol.name, resolution };
  }

  /** 定義へ移動 — 解決の段(同じ file → import 先 → Python → workspace の外 → workspace 全体)の結果を返す。 */
  async provideDefinition(document: vscode.TextDocument, position: vscode.Position): Promise<vscode.Location[]> {
    const resolved = await this.resolveAt(document, position);
    return resolved === undefined ? [] : resolved.resolution.targets.map(targetLocation);
  }

  /** 参照の一覧 — 定義の module が定まれば、それで絞って全 file から集める。 */
  async provideReferences(
    document: vscode.TextDocument,
    position: vscode.Position,
    context: vscode.ReferenceContext
  ): Promise<vscode.Location[]> {
    const resolved = await this.resolveAt(document, position);
    if (resolved === undefined) {
      return [];
    }
    return collectReferences(this.index, resolved.name, resolved.resolution.module, context.includeDeclaration).map(
      (ref) => new vscode.Location(vscode.Uri.file(ref.path), toRange(ref.range))
    );
  }

  /** file の目次 — 索引の definitions を container で入れ子にした物(workspace の外の file は移動の時に取った索引から)。 */
  provideDocumentSymbols(document: vscode.TextDocument): vscode.DocumentSymbol[] {
    const file = this.index.get(document.uri.fsPath)?.file ?? this.external.cachedFile(document.uri.fsPath);
    return file === undefined ? [] : buildOutline(file).map(toDocumentSymbol);
  }

  /** workspace の記号の検索 — 全定義から query で絞る。 */
  provideWorkspaceSymbols(query: string): vscode.SymbolInformation[] {
    return searchWorkspaceSymbols(this.index, query, WORKSPACE_SYMBOL_LIMIT).map((hit) => {
      const def = hit.definition;
      return new vscode.SymbolInformation(
        def.name,
        toSymbolKind(outlineKindOf(def.kind)),
        def.container ?? '',
        new vscode.Location(vscode.Uri.file(hit.path), toRange(def.range))
      );
    });
  }

  /** hover — 行き先の Hy の定義の kind・引数・docstring を出す(候補が多い時は先頭の 3 件)。 */
  async provideHover(document: vscode.TextDocument, position: vscode.Position): Promise<vscode.Hover | undefined> {
    const resolved = await this.resolveAt(document, position);
    if (resolved === undefined) {
      return undefined;
    }
    const parts: string[] = [];
    for (const target of resolved.resolution.targets) {
      if (target.tag === 'hy-definition' && parts.length < 3) {
        parts.push(hoverMarkdown(target.definition, target.module));
      }
    }
    if (parts.length === 0) {
      return undefined;
    }
    const extra = resolved.resolution.targets.length - parts.length;
    const text = parts.join('\n\n---\n\n') + (extra > 0 ? `\n\n(他に ${extra} 件)` : '');
    return new vscode.Hover(new vscode.MarkdownString(text));
  }
}
