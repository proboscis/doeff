import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as fs from 'fs';
import { promisify } from 'util';
import * as path from 'path';

const execFileAsync = promisify(cp.execFile);

const CACHE_TTL_MS = 5000;
const PROGRAM_REGEX =
  /^\s*([A-Za-z_]\w*)\s*:\s*(?:["']?Program(?:\s*\[\s*([^\]]+)\s*\])?["']?)/;
const INDEXER_CANDIDATES = [
  '/usr/local/bin/doeff-indexer',
  '/usr/bin/doeff-indexer',
  `${process.env.HOME ?? ''}/.cargo/bin/doeff-indexer`,
  `${process.env.HOME ?? ''}/.local/bin/doeff-indexer`,
  '/opt/homebrew/bin/doeff-indexer'
];

const output = vscode.window.createOutputChannel('doeff-runner');

interface IndexParameter {
  name: string;
  annotation?: string;
  isRequired: boolean;
  position: number;
  kind: string;
}

interface ProgramTypeUsage {
  kind: string;
  raw: string;
  typeArguments: string[];
}

interface IndexEntry {
  name: string;
  qualifiedName: string;
  filePath: string;
  line: number;
  itemKind: string; // 'function' | 'async_function' | 'assignment'
  categories: string[];
  programParameters: IndexParameter[];
  interpreterParameters: IndexParameter[];
  typeUsages: ProgramTypeUsage[];
  docstring?: string;
  markers?: string[];
}

interface RunSelection {
  programPath: string;
  programType: string;
  interpreter: IndexEntry;
  kleisli?: IndexEntry;
  transformer?: IndexEntry;
}

interface ProgramDeclaration {
  name: string;
  typeArg: string;
  range: vscode.Range;
}

interface CacheEntry<T> {
  timestamp: number;
  data: T;
}

type RunMode = 'default' | 'options';

const entryCache = new Map<string, CacheEntry<IndexEntry[]>>();

interface ToolCache {
  typeArg: string;
  entries: IndexEntry[];
  timestamp: number;
}

// Visual prefixes for different tool categories
const TOOL_PREFIX = {
  run: '▶',
  runWithOptions: '▶⚙',
  kleisli: '🔗',
  transform: '🔀'
};

// =============================================================================
// State Store for sharing state between TreeView and CodeLens
// =============================================================================

type ActionType =
  | { kind: 'run' }
  | { kind: 'runWithOptions' }
  | { kind: 'kleisli'; toolQualifiedName: string }
  | { kind: 'transform'; toolQualifiedName: string };

interface ActionPreference {
  entrypointQualifiedName: string;
  defaultAction: ActionType;
}

class DoeffStateStore {
  private _onStateChange = new vscode.EventEmitter<void>();
  readonly onStateChange = this._onStateChange.event;

  constructor(private context: vscode.ExtensionContext) {}

  getPreferences(): ActionPreference[] {
    return this.context.workspaceState.get<ActionPreference[]>('actionPreferences', []);
  }

  getDefaultAction(qualifiedName: string): ActionType | undefined {
    const prefs = this.getPreferences();
    return prefs.find(p => p.entrypointQualifiedName === qualifiedName)?.defaultAction;
  }

  async setDefaultAction(qualifiedName: string, action: ActionType): Promise<void> {
    const prefs = this.getPreferences();
    const updated = prefs.filter(p => p.entrypointQualifiedName !== qualifiedName);
    updated.push({ entrypointQualifiedName: qualifiedName, defaultAction: action });
    await this.context.workspaceState.update('actionPreferences', updated);
    this._onStateChange.fire();
  }

  async clearDefaultAction(qualifiedName: string): Promise<void> {
    const prefs = this.getPreferences();
    const updated = prefs.filter(p => p.entrypointQualifiedName !== qualifiedName);
    await this.context.workspaceState.update('actionPreferences', updated);
    this._onStateChange.fire();
  }

  dispose(): void {
    this._onStateChange.dispose();
  }
}

// =============================================================================
// TreeView Types and Provider
// =============================================================================

type TreeNode = ModuleNode | EntrypointNode | ActionNode;

interface ModuleNode {
  type: 'module';
  modulePath: string;
  displayName: string;
  entries: IndexEntry[];
}

interface EntrypointNode {
  type: 'entrypoint';
  entry: IndexEntry;
}

interface ActionNode {
  type: 'action';
  actionType: ActionType;
  parentEntry: IndexEntry;
  tool?: IndexEntry;
}

class DoeffProgramsProvider implements vscode.TreeDataProvider<TreeNode>, vscode.Disposable {
  private _onDidChangeTreeData = new vscode.EventEmitter<TreeNode | undefined>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private _onFilterChange = new vscode.EventEmitter<string>();
  readonly onFilterChange = this._onFilterChange.event;

  private indexCache: IndexEntry[] = [];
  private kleisliCache = new Map<string, IndexEntry[]>();
  private filterText = '';
  private transformCache = new Map<string, IndexEntry[]>();
  private cacheTimestamp = 0;
  private readonly CACHE_TTL_MS = 30000;
  private refreshing = false;

  constructor(
    private stateStore: DoeffStateStore
  ) {}

  getTreeItem(element: TreeNode): vscode.TreeItem {
    switch (element.type) {
      case 'module':
        return this.createModuleTreeItem(element);
      case 'entrypoint':
        return this.createEntrypointTreeItem(element);
      case 'action':
        return this.createActionTreeItem(element);
    }
  }

  private createModuleTreeItem(node: ModuleNode): vscode.TreeItem {
    const item = new vscode.TreeItem(
      node.displayName,
      vscode.TreeItemCollapsibleState.Expanded
    );
    item.iconPath = new vscode.ThemeIcon('folder');
    item.contextValue = 'module';
    return item;
  }

  private createEntrypointTreeItem(node: EntrypointNode): vscode.TreeItem {
    const entry = node.entry;
    const typeArg = this.extractTypeArg(entry);
    const label = typeArg ? `${entry.name}: Program[${typeArg}]` : entry.name;

    const defaultAction = this.stateStore.getDefaultAction(entry.qualifiedName);
    const item = new vscode.TreeItem(
      label,
      vscode.TreeItemCollapsibleState.Collapsed
    );
    item.iconPath = new vscode.ThemeIcon('symbol-function');
    item.contextValue = 'entrypoint';
    item.tooltip = entry.docstring
      ? `${entry.qualifiedName}\n\n${entry.docstring}`
      : entry.qualifiedName;
    item.description = defaultAction ? this.getActionLabel(defaultAction) : undefined;
    item.command = {
      command: 'doeff-runner.revealEntrypoint',
      title: 'Go to Definition',
      arguments: [entry]
    };
    return item;
  }

  private createActionTreeItem(node: ActionNode): vscode.TreeItem {
    const label = this.getActionLabel(node.actionType);
    const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.None);

    const defaultAction = this.stateStore.getDefaultAction(node.parentEntry.qualifiedName);
    const isDefault = this.actionsEqual(defaultAction, node.actionType);

    switch (node.actionType.kind) {
      case 'run':
        item.iconPath = new vscode.ThemeIcon('play');
        item.command = {
          command: 'doeff-runner.runFromTree',
          title: 'Run',
          arguments: [node.parentEntry, node.actionType]
        };
        break;
      case 'runWithOptions':
        item.iconPath = new vscode.ThemeIcon('settings-gear');
        item.command = {
          command: 'doeff-runner.runFromTree',
          title: 'Run with Options',
          arguments: [node.parentEntry, node.actionType]
        };
        break;
      case 'kleisli':
        item.iconPath = new vscode.ThemeIcon('link');
        item.tooltip = node.tool?.docstring
          ? `[Kleisli] ${node.tool.qualifiedName}\n\n${node.tool.docstring}`
          : `[Kleisli] ${node.actionType.toolQualifiedName}`;
        item.command = {
          command: 'doeff-runner.runFromTree',
          title: 'Run with Kleisli',
          arguments: [node.parentEntry, node.actionType]
        };
        break;
      case 'transform':
        item.iconPath = new vscode.ThemeIcon('arrow-swap');
        item.tooltip = node.tool?.docstring
          ? `[Transform] ${node.tool.qualifiedName}\n\n${node.tool.docstring}`
          : `[Transform] ${node.actionType.toolQualifiedName}`;
        item.command = {
          command: 'doeff-runner.runFromTree',
          title: 'Run with Transform',
          arguments: [node.parentEntry, node.actionType]
        };
        break;
    }

    item.contextValue = 'action';
    if (isDefault) {
      item.description = '★ default';
    }
    return item;
  }

  private getActionLabel(action: ActionType): string {
    switch (action.kind) {
      case 'run':
        return `${TOOL_PREFIX.run} Run`;
      case 'runWithOptions':
        return `${TOOL_PREFIX.runWithOptions} Options`;
      case 'kleisli': {
        const kleisliName = action.toolQualifiedName.split('.').pop() ?? action.toolQualifiedName;
        return `${TOOL_PREFIX.kleisli} ${kleisliName}`;
      }
      case 'transform': {
        const transformName = action.toolQualifiedName.split('.').pop() ?? action.toolQualifiedName;
        return `${TOOL_PREFIX.transform} ${transformName}`;
      }
    }
  }

  private actionsEqual(a: ActionType | undefined, b: ActionType): boolean {
    if (!a) return false;
    if (a.kind !== b.kind) return false;
    if (a.kind === 'kleisli' && b.kind === 'kleisli') {
      return a.toolQualifiedName === b.toolQualifiedName;
    }
    if (a.kind === 'transform' && b.kind === 'transform') {
      return a.toolQualifiedName === b.toolQualifiedName;
    }
    return true;
  }

  async getChildren(element?: TreeNode): Promise<TreeNode[]> {
    if (!element) {
      // Root: return module nodes
      return this.getModuleNodes();
    }

    switch (element.type) {
      case 'module':
        return element.entries.map(entry => ({
          type: 'entrypoint' as const,
          entry
        }));
      case 'entrypoint':
        return this.getActionNodes(element.entry);
      case 'action':
        return [];
    }
  }

  private async getModuleNodes(): Promise<ModuleNode[]> {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) {
      return [];
    }

    await this.ensureIndexLoaded(workspaceFolder.uri.fsPath);

    // Filter to only entrypoints: global variables (assignments) with Program[T] type
    let entrypoints = this.indexCache.filter(entry =>
      entry.itemKind === 'assignment' &&
      entry.typeUsages.some(usage => usage.kind === 'program')
    );

    // Apply text filter if set (searches name, qualifiedName, and type arguments)
    if (this.filterText) {
      entrypoints = entrypoints.filter(entry => {
        // Check name and qualifiedName
        if (entry.name.toLowerCase().includes(this.filterText) ||
            entry.qualifiedName.toLowerCase().includes(this.filterText)) {
          return true;
        }
        // Check type arguments (e.g., Program[MyType] -> matches "mytype")
        for (const usage of entry.typeUsages) {
          if (usage.kind === 'program') {
            for (const typeArg of usage.typeArguments) {
              if (typeArg.toLowerCase().includes(this.filterText)) {
                return true;
              }
            }
            // Also check the raw type string
            if (usage.raw.toLowerCase().includes(this.filterText)) {
              return true;
            }
          }
        }
        return false;
      });
    }

    // Group by module (directory path)
    const grouped = new Map<string, IndexEntry[]>();
    for (const entry of entrypoints) {
      const modulePath = this.getModulePath(entry, workspaceFolder.uri.fsPath);
      const existing = grouped.get(modulePath) ?? [];
      existing.push(entry);
      grouped.set(modulePath, existing);
    }

    // Convert to ModuleNodes
    const modules: ModuleNode[] = [];
    for (const [modulePath, entries] of grouped) {
      modules.push({
        type: 'module',
        modulePath,
        displayName: modulePath || '(root)',
        entries: entries.sort((a, b) => a.name.localeCompare(b.name))
      });
    }

    return modules.sort((a, b) => a.displayName.localeCompare(b.displayName));
  }

  private async getActionNodes(entry: IndexEntry): Promise<ActionNode[]> {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) {
      return [];
    }

    const rootPath = workspaceFolder.uri.fsPath;
    const typeArg = this.extractTypeArg(entry);
    const actions: ActionNode[] = [];

    // Use entry location for proximity-based sorting
    const proximity: ProximityContext = {
      filePath: entry.filePath,
      line: entry.line
    };

    // Standard actions
    actions.push({
      type: 'action',
      actionType: { kind: 'run' },
      parentEntry: entry
    });
    actions.push({
      type: 'action',
      actionType: { kind: 'runWithOptions' },
      parentEntry: entry
    });

    // Only show kleisli/transform tools if the Program has a type argument
    // Untyped Program (no type arg) shouldn't show tools since we don't know the output type
    if (typeArg) {
      // Load kleisli tools (sorted by proximity)
      const kleisliTools = await this.getKleisliTools(rootPath, typeArg, proximity);
      for (const tool of kleisliTools) {
        actions.push({
          type: 'action',
          actionType: { kind: 'kleisli', toolQualifiedName: tool.qualifiedName },
          parentEntry: entry,
          tool
        });
      }

      // Load transform tools (sorted by proximity)
      const transformTools = await this.getTransformTools(rootPath, typeArg, proximity);
      for (const tool of transformTools) {
        actions.push({
          type: 'action',
          actionType: { kind: 'transform', toolQualifiedName: tool.qualifiedName },
          parentEntry: entry,
          tool
        });
      }
    }

    return actions;
  }

  private async ensureIndexLoaded(rootPath: string): Promise<void> {
    const now = Date.now();
    if (this.indexCache.length > 0 && now - this.cacheTimestamp < this.CACHE_TTL_MS) {
      return;
    }

    if (this.refreshing) {
      return;
    }

    this.refreshing = true;
    try {
      const indexerPath = await locateIndexer();
      const entries = await this.fetchAllEntries(indexerPath, rootPath);
      this.indexCache = entries;
      this.cacheTimestamp = now;
    } catch (error) {
      output.appendLine(`[error] Failed to load index: ${error}`);
    } finally {
      this.refreshing = false;
    }
  }

  private async fetchAllEntries(indexerPath: string, rootPath: string): Promise<IndexEntry[]> {
    const cacheKey = `index:${rootPath}`;
    return queryIndexer(indexerPath, cacheKey, rootPath, [
      'index',
      '--root',
      rootPath
    ]);
  }

  private async getKleisliTools(
    rootPath: string,
    typeArg: string,
    proximity?: ProximityContext
  ): Promise<IndexEntry[]> {
    const proxKey = proximity ? `:${proximity.filePath}:${proximity.line}` : '';
    const cacheKey = `kleisli:${typeArg}${proxKey}`;
    const cached = this.kleisliCache.get(cacheKey);
    if (cached) {
      return cached;
    }

    try {
      const indexerPath = await locateIndexer();
      const entries = await fetchEntries(indexerPath, rootPath, 'find-kleisli', typeArg, proximity);
      this.kleisliCache.set(cacheKey, entries);
      return entries;
    } catch {
      return [];
    }
  }

  private async getTransformTools(
    rootPath: string,
    typeArg: string,
    proximity?: ProximityContext
  ): Promise<IndexEntry[]> {
    const proxKey = proximity ? `:${proximity.filePath}:${proximity.line}` : '';
    const cacheKey = `transform:${typeArg}${proxKey}`;
    const cached = this.transformCache.get(cacheKey);
    if (cached) {
      return cached;
    }

    try {
      const indexerPath = await locateIndexer();
      const entries = await fetchEntries(indexerPath, rootPath, 'find-transforms', typeArg, proximity);
      this.transformCache.set(cacheKey, entries);
      return entries;
    } catch {
      return [];
    }
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  private getModulePath(entry: IndexEntry, _rootPath: string): string {
    // Extract module path from qualified name (e.g., "src.module.func" -> "src.module")
    const parts = entry.qualifiedName.split('.');
    parts.pop(); // Remove function name
    return parts.join('.');
  }

  private extractTypeArg(entry: IndexEntry): string {
    // Extract type argument from type_usages
    // Returns empty string if no type argument (untyped Program)
    // Returns the actual type if specified (e.g., 'MyType', 'Any')
    for (const usage of entry.typeUsages) {
      if (usage.kind === 'program' && usage.typeArguments.length > 0) {
        return usage.typeArguments[0];
      }
    }
    return '';  // No type argument specified
  }

  refresh(): void {
    this.indexCache = [];
    this.kleisliCache.clear();
    this.transformCache.clear();
    this.cacheTimestamp = 0;
    this._onDidChangeTreeData.fire(undefined);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  invalidateFile(_filePath: string): void {
    // For now, just refresh everything
    // Could be optimized to only refresh affected entries
    this.refresh();
  }

  setFilter(text: string): void {
    this.filterText = text.toLowerCase();
    this._onFilterChange.fire(this.filterText);
    this._onDidChangeTreeData.fire(undefined);
  }

  clearFilter(): void {
    this.filterText = '';
    this._onFilterChange.fire('');
    this._onDidChangeTreeData.fire(undefined);
  }

  getFilterText(): string {
    return this.filterText;
  }

  async getAllEntrypoints(): Promise<IndexEntry[]> {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) {
      return [];
    }
    await this.ensureIndexLoaded(workspaceFolder.uri.fsPath);
    return this.indexCache.filter(entry =>
      entry.itemKind === 'assignment' &&
      entry.typeUsages.some(usage => usage.kind === 'program')
    );
  }

  dispose(): void {
    this._onDidChangeTreeData.dispose();
    this._onFilterChange.dispose();
  }
}

class ProgramCodeLensProvider implements vscode.CodeLensProvider, vscode.Disposable {
  private readonly emitter = new vscode.EventEmitter<void>();
  public readonly onDidChangeCodeLenses = this.emitter.event;
  private kleisliCache = new Map<string, ToolCache>();
  private transformCache = new Map<string, ToolCache>();
  private pendingFetches = new Set<string>();
  private readonly CACHE_TTL_MS = 30000; // 30 seconds before background refresh

  constructor(private stateStore?: DoeffStateStore) {}

  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    const lenses: vscode.CodeLens[] = [];
    const declarations = extractProgramDeclarations(document);

    const workspaceFolder =
      vscode.workspace.getWorkspaceFolder(document.uri) ??
      vscode.workspace.workspaceFolders?.[0];

    for (const decl of declarations) {
      // Check if there's a default action set via TreeView
      const qualifiedName = this.getQualifiedNameForDeclaration(document, decl);
      const defaultAction = qualifiedName ? this.stateStore?.getDefaultAction(qualifiedName) : undefined;

      // Show default action first if set
      if (defaultAction) {
        lenses.push(this.createDefaultActionLens(decl, defaultAction, document.uri));
      }

      // Standard Run button
      lenses.push(
        new vscode.CodeLens(decl.range, {
          title: `${TOOL_PREFIX.run} Run`,
          tooltip: 'Run with default interpreter',
          command: 'doeff-runner.runDefault',
          arguments: [document.uri, decl.range.start.line]
        })
      );
      // Run with options button
      lenses.push(
        new vscode.CodeLens(decl.range, {
          title: `${TOOL_PREFIX.runWithOptions} Options`,
          tooltip: 'Run with custom interpreter, kleisli, and transformer selection',
          command: 'doeff-runner.runOptions',
          arguments: [document.uri, decl.range.start.line]
        })
      );

      // Only show kleisli/transform tools if the Program has a type argument
      // Untyped Program shouldn't show tools since we don't know the output type
      if (workspaceFolder && decl.typeArg) {
        const rootPath = workspaceFolder.uri.fsPath;

        // Use program location for proximity-based sorting
        const proximity: ProximityContext = {
          filePath: document.uri.fsPath,
          line: decl.range.start.line + 1  // Convert 0-indexed to 1-indexed
        };

        // Add Kleisli tool buttons (sorted by proximity)
        const kleisliTools = this.getToolsSync('kleisli', rootPath, decl.typeArg, proximity);
        for (const kleisli of kleisliTools) {
          lenses.push(
            new vscode.CodeLens(decl.range, {
              title: `${TOOL_PREFIX.kleisli} ${kleisli.name}`,
              tooltip: kleisli.docstring
                ? `[Kleisli] ${kleisli.qualifiedName}\n\n${kleisli.docstring}`
                : `[Kleisli] ${kleisli.qualifiedName}`,
              command: 'doeff-runner.runWithKleisli',
              arguments: [
                document.uri,
                decl.range.start.line,
                kleisli.qualifiedName
              ]
            })
          );
        }

        // Add Transform tool buttons (sorted by proximity)
        const transformTools = this.getToolsSync('transform', rootPath, decl.typeArg, proximity);
        for (const transform of transformTools) {
          lenses.push(
            new vscode.CodeLens(decl.range, {
              title: `${TOOL_PREFIX.transform} ${transform.name}`,
              tooltip: transform.docstring
                ? `[Transform] ${transform.qualifiedName}\n\n${transform.docstring}`
                : `[Transform] ${transform.qualifiedName}`,
              command: 'doeff-runner.runWithTransform',
              arguments: [
                document.uri,
                decl.range.start.line,
                transform.qualifiedName
              ]
            })
          );
        }
      }
    }
    return lenses;
  }

  private createDefaultActionLens(
    decl: ProgramDeclaration,
    action: ActionType,
    uri: vscode.Uri
  ): vscode.CodeLens {
    let title: string;
    let command: string;
    let args: unknown[];

    switch (action.kind) {
      case 'run':
        title = `★ ${TOOL_PREFIX.run} Run`;
        command = 'doeff-runner.runDefault';
        args = [uri, decl.range.start.line];
        break;
      case 'runWithOptions':
        title = `★ ${TOOL_PREFIX.runWithOptions} Options`;
        command = 'doeff-runner.runOptions';
        args = [uri, decl.range.start.line];
        break;
      case 'kleisli': {
        const kleisliName = action.toolQualifiedName.split('.').pop() ?? action.toolQualifiedName;
        title = `★ ${TOOL_PREFIX.kleisli} ${kleisliName}`;
        command = 'doeff-runner.runWithKleisli';
        args = [uri, decl.range.start.line, action.toolQualifiedName];
        break;
      }
      case 'transform': {
        const transformName = action.toolQualifiedName.split('.').pop() ?? action.toolQualifiedName;
        title = `★ ${TOOL_PREFIX.transform} ${transformName}`;
        command = 'doeff-runner.runWithTransform';
        args = [uri, decl.range.start.line, action.toolQualifiedName];
        break;
      }
    }

    return new vscode.CodeLens(decl.range, {
      title,
      tooltip: 'Default action (set via Explorer)',
      command,
      arguments: args
    });
  }

  private getQualifiedNameForDeclaration(
    document: vscode.TextDocument,
    decl: ProgramDeclaration
  ): string | undefined {
    // Try to compute qualified name from file path
    const workspaceFolder = vscode.workspace.getWorkspaceFolder(document.uri);
    if (!workspaceFolder) {
      return undefined;
    }

    const relativePath = path.relative(workspaceFolder.uri.fsPath, document.uri.fsPath);
    const modulePath = relativePath
      .replace(/\.py$/, '')
      .replace(/\//g, '.')
      .replace(/\\/g, '.');

    return `${modulePath}.${decl.name}`;
  }

  /**
   * Synchronously returns cached tool entries.
   * Triggers background refresh if cache is stale.
   */
  private getToolsSync(
    toolType: 'kleisli' | 'transform',
    rootPath: string,
    typeArg: string,
    proximity?: ProximityContext
  ): IndexEntry[] {
    const cache = toolType === 'kleisli' ? this.kleisliCache : this.transformCache;
    const proxKey = proximity ? `:${proximity.filePath}:${proximity.line}` : '';
    const cacheKey = `${toolType}:${rootPath}:${typeArg}${proxKey}`;
    const cached = cache.get(cacheKey);
    const now = Date.now();

    // Return cached data if available (even if stale)
    if (cached) {
      // Trigger background refresh if stale
      if (now - cached.timestamp > this.CACHE_TTL_MS) {
        this.refreshToolsInBackground(toolType, rootPath, typeArg, cacheKey, proximity);
      }
      return cached.entries;
    }

    // No cache - trigger background fetch and return empty for now
    this.refreshToolsInBackground(toolType, rootPath, typeArg, cacheKey, proximity);
    return [];
  }

  /**
   * Fetches tool data in background and refreshes CodeLens when done.
   */
  private refreshToolsInBackground(
    toolType: 'kleisli' | 'transform',
    rootPath: string,
    typeArg: string,
    cacheKey: string,
    proximity?: ProximityContext
  ): void {
    // Avoid duplicate fetches
    if (this.pendingFetches.has(cacheKey)) {
      return;
    }
    this.pendingFetches.add(cacheKey);

    const cache = toolType === 'kleisli' ? this.kleisliCache : this.transformCache;
    const command = toolType === 'kleisli' ? 'find-kleisli' : 'find-transforms';

    // Fire and forget - fetch in background
    (async () => {
      try {
        const indexerPath = await locateIndexer();
        const entries = await fetchEntries(
          indexerPath,
          rootPath,
          command,
          typeArg,
          proximity
        );
        const oldCached = cache.get(cacheKey);
        cache.set(cacheKey, {
          typeArg,
          entries,
          timestamp: Date.now()
        });
        // Only refresh if entries changed
        if (!oldCached || JSON.stringify(oldCached.entries) !== JSON.stringify(entries)) {
          this.refresh();
        }
      } catch {
        // Silently ignore errors - keep old cache if available
      } finally {
        this.pendingFetches.delete(cacheKey);
      }
    })();
  }

  refresh() {
    this.emitter.fire();
  }

  dispose() {
    this.emitter.dispose();
  }
}

export function activate(context: vscode.ExtensionContext) {
  output.appendLine('doeff-runner activated');

  // Create state store for sharing state between TreeView and CodeLens
  const stateStore = new DoeffStateStore(context);

  // Create providers
  const codeLensProvider = new ProgramCodeLensProvider(stateStore);
  const treeProvider = new DoeffProgramsProvider(stateStore);

  // Create TreeView
  const treeView = vscode.window.createTreeView('doeff-programs', {
    treeDataProvider: treeProvider,
    showCollapseAll: true
  });

  // Subscribe to filter changes to update TreeView message
  treeProvider.onFilterChange((filterText) => {
    if (filterText) {
      treeView.message = `🔍 Filter: "${filterText}"`;
    } else {
      treeView.message = undefined;
    }
  });

  // Subscribe to state changes to refresh CodeLens
  stateStore.onStateChange(() => {
    codeLensProvider.refresh();
    treeProvider.refresh();
  });

  // File watcher for auto-refresh
  const fileWatcher = vscode.workspace.createFileSystemWatcher('**/*.py');
  fileWatcher.onDidChange(uri => treeProvider.invalidateFile(uri.fsPath));
  fileWatcher.onDidCreate(uri => treeProvider.invalidateFile(uri.fsPath));
  fileWatcher.onDidDelete(uri => treeProvider.invalidateFile(uri.fsPath));

  context.subscriptions.push(
    output,
    stateStore,
    codeLensProvider,
    treeProvider,
    treeView,
    fileWatcher,
    vscode.languages.registerCodeLensProvider(
      { language: 'python' },
      codeLensProvider
    ),
    // Existing commands
    vscode.commands.registerCommand(
      'doeff-runner.runDefault',
      (resource?: vscode.Uri | string, lineNumber?: number) =>
        runProgram(
          resource,
          lineNumber,
          'default',
          codeLensProvider
        )
    ),
    vscode.commands.registerCommand(
      'doeff-runner.runOptions',
      (resource?: vscode.Uri | string, lineNumber?: number) =>
        runProgram(
          resource,
          lineNumber,
          'options',
          codeLensProvider
        )
    ),
    vscode.commands.registerCommand(
      'doeff-runner.runConfig',
      (selection: RunSelection) => runSelection(selection)
    ),
    vscode.commands.registerCommand(
      'doeff-runner.runWithKleisli',
      (resource?: vscode.Uri | string, lineNumber?: number, kleisliQualifiedName?: string) =>
        runProgramWithTool(
          resource,
          lineNumber,
          'kleisli',
          kleisliQualifiedName,
          codeLensProvider
        )
    ),
    vscode.commands.registerCommand(
      'doeff-runner.runWithTransform',
      (resource?: vscode.Uri | string, lineNumber?: number, transformQualifiedName?: string) =>
        runProgramWithTool(
          resource,
          lineNumber,
          'transform',
          transformQualifiedName,
          codeLensProvider
        )
    ),
    // New TreeView commands
    vscode.commands.registerCommand(
      'doeff-runner.refreshExplorer',
      () => treeProvider.refresh()
    ),
    vscode.commands.registerCommand(
      'doeff-runner.revealEntrypoint',
      async (entry: IndexEntry) => {
        const uri = vscode.Uri.file(entry.filePath);
        const document = await vscode.workspace.openTextDocument(uri);
        const editor = await vscode.window.showTextDocument(document);
        const position = new vscode.Position(entry.line - 1, 0);
        editor.selection = new vscode.Selection(position, position);
        editor.revealRange(
          new vscode.Range(position, position),
          vscode.TextEditorRevealType.InCenter
        );
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.runFromTree',
      async (entry: IndexEntry, actionType: ActionType) => {
        await runFromTreeView(entry, actionType, codeLensProvider);
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.setDefaultAction',
      async (node: ActionNode) => {
        await stateStore.setDefaultAction(
          node.parentEntry.qualifiedName,
          node.actionType
        );
        vscode.window.showInformationMessage(
          `Set default action for ${node.parentEntry.name}`
        );
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.clearDefaultAction',
      async (node: EntrypointNode) => {
        await stateStore.clearDefaultAction(node.entry.qualifiedName);
        vscode.window.showInformationMessage(
          `Cleared default action for ${node.entry.name}`
        );
      }
    ),
    // Search/filter commands
    vscode.commands.registerCommand(
      'doeff-runner.searchEntrypoints',
      async () => {
        const entrypoints = await treeProvider.getAllEntrypoints();
        if (entrypoints.length === 0) {
          vscode.window.showInformationMessage('No entrypoints found in workspace.');
          return;
        }

        const items = entrypoints.map(entry => ({
          label: entry.name,
          description: entry.qualifiedName,
          detail: entry.docstring,
          entry
        }));

        const selected = await vscode.window.showQuickPick(items, {
          placeHolder: 'Search entrypoints...',
          matchOnDescription: true,
          matchOnDetail: true
        });

        if (selected) {
          // Reveal the entrypoint in editor
          const uri = vscode.Uri.file(selected.entry.filePath);
          const document = await vscode.workspace.openTextDocument(uri);
          const editor = await vscode.window.showTextDocument(document);
          const position = new vscode.Position(selected.entry.line - 1, 0);
          editor.selection = new vscode.Selection(position, position);
          editor.revealRange(
            new vscode.Range(position, position),
            vscode.TextEditorRevealType.InCenter
          );
        }
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.filterEntrypoints',
      async () => {
        const currentFilter = treeProvider.getFilterText();
        const input = await vscode.window.showInputBox({
          prompt: 'Filter entrypoints by name',
          value: currentFilter,
          placeHolder: 'Enter filter text (leave empty to clear)'
        });

        if (input === undefined) {
          return; // Cancelled
        }

        if (input === '') {
          treeProvider.clearFilter();
          vscode.window.showInformationMessage('Filter cleared');
        } else {
          treeProvider.setFilter(input);
          vscode.window.showInformationMessage(`Filtering by: ${input}`);
        }
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.clearFilter',
      () => {
        treeProvider.clearFilter();
        vscode.window.showInformationMessage('Filter cleared');
      }
    ),
    // Document/editor change handlers
    vscode.workspace.onDidChangeTextDocument((event) => {
      if (vscode.window.activeTextEditor?.document === event.document) {
        codeLensProvider.refresh();
      }
    }),
    vscode.window.onDidChangeActiveTextEditor(() => {
      codeLensProvider.refresh();
    }),
    // Sync TreeView selection with editor
    vscode.window.onDidChangeTextEditorSelection((event) => {
      const document = event.textEditor.document;
      if (document.languageId !== 'python') {
        return;
      }
      const line = event.selections[0].active.line;
      const declaration = findDeclarationAtLine(document, line);
      if (declaration) {
        // Could reveal in tree view, but requires finding the node
        // Skipped for now to avoid performance impact
      }
    })
  );
}

async function runFromTreeView(
  entry: IndexEntry,
  actionType: ActionType,
  codeLensProvider: ProgramCodeLensProvider
): Promise<void> {
  const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
  if (!workspaceFolder) {
    vscode.window.showErrorMessage('Open a workspace folder to run doeff.');
    return;
  }

  try {
    await vscode.workspace.saveAll();

    switch (actionType.kind) {
      case 'run':
        await runDefault(entry.qualifiedName, workspaceFolder);
        break;
      case 'runWithOptions': {
        // Open the file and trigger runOptions
        const uri = vscode.Uri.file(entry.filePath);
        const document = await vscode.workspace.openTextDocument(uri);
        await vscode.window.showTextDocument(document);
        await runProgram(uri, entry.line - 1, 'options', codeLensProvider);
        break;
      }
      case 'kleisli':
        await runWithToolFromTree(
          entry,
          'kleisli',
          actionType.toolQualifiedName,
          workspaceFolder
        );
        break;
      case 'transform':
        await runWithToolFromTree(
          entry,
          'transform',
          actionType.toolQualifiedName,
          workspaceFolder
        );
        break;
    }

    codeLensProvider.refresh();
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    output.appendLine(`[error] ${message}`);
    vscode.window.showErrorMessage(`doeff runner failed: ${message}`);
  }
}

async function runWithToolFromTree(
  entry: IndexEntry,
  toolType: 'kleisli' | 'transform',
  toolQualifiedName: string,
  workspaceFolder: vscode.WorkspaceFolder
): Promise<void> {
  const indexerPath = await locateIndexer();
  const typeArg = entry.typeUsages.find(u => u.kind === 'program')?.typeArguments[0] ?? 'Any';

  // Use entry's location for proximity-based sorting
  const proximity: ProximityContext = {
    filePath: entry.filePath,
    line: entry.line
  };

  // Find interpreter (sorted by proximity to the entrypoint)
  const interpreters = await fetchEntries(
    indexerPath,
    workspaceFolder.uri.fsPath,
    'find-interpreters',
    typeArg,
    proximity
  );

  if (!interpreters.length) {
    vscode.window.showErrorMessage('No doeff interpreters found.');
    return;
  }

  // First interpreter is now the closest one
  const interpreter = interpreters[0];

  // Find tool (sorted by proximity)
  const toolCommand = toolType === 'kleisli' ? 'find-kleisli' : 'find-transforms';
  const tools = await fetchEntries(
    indexerPath,
    workspaceFolder.uri.fsPath,
    toolCommand,
    typeArg,
    proximity
  );

  const tool = tools.find(t => t.qualifiedName === toolQualifiedName);
  if (!tool) {
    vscode.window.showErrorMessage(`${toolType} '${toolQualifiedName}' not found.`);
    return;
  }

  const selection: RunSelection = {
    programPath: entry.qualifiedName,
    programType: typeArg,
    interpreter,
    kleisli: toolType === 'kleisli' ? tool : undefined,
    transformer: toolType === 'transform' ? tool : undefined
  };

  await runSelection(selection, workspaceFolder);
}

export function deactivate() {
  output.dispose();
}

async function runProgram(
  resource: vscode.Uri | string | undefined,
  lineNumber: number | undefined,
  mode: RunMode,
  codeLensProvider: ProgramCodeLensProvider
) {
  try {
    await vscode.workspace.saveAll();
    const document = await resolveDocument(resource);
    if (!document) {
      vscode.window.showErrorMessage('No active Python document to run.');
      return;
    }

    const targetLine =
      typeof lineNumber === 'number'
        ? lineNumber
        : vscode.window.activeTextEditor?.selection.active.line ?? 0;

    const declaration = findDeclarationAtLine(document, targetLine);
    if (!declaration) {
      vscode.window.showErrorMessage(
        'No doeff Program annotation found on this line.'
      );
      return;
    }

    const workspaceFolder =
      vscode.workspace.getWorkspaceFolder(document.uri) ??
      vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) {
      vscode.window.showErrorMessage('Open a workspace folder to run doeff.');
      return;
    }

    const indexerPath = await locateIndexer();
    const programEntry = await findProgramEntry(
      indexerPath,
      workspaceFolder.uri.fsPath,
      document.uri.fsPath,
      declaration.name
    );
    if (!programEntry) {
      vscode.window.showErrorMessage(
        `doeff-indexer could not find symbol '${declaration.name}' in this file.`
      );
      return;
    }
    const programPath = programEntry.qualifiedName || declaration.name;

    if (mode === 'default') {
      await runDefault(programPath, workspaceFolder);
    } else {
      const selection = await buildSelection(
        document,
        declaration,
        workspaceFolder,
        programPath
      );
      if (!selection) {
        return;
      }
      await runSelection(selection, workspaceFolder);
    }

    codeLensProvider.refresh();
  } catch (error) {
    const message =
      error instanceof Error ? error.message : 'Unknown error running doeff.';
    output.appendLine(`[error] ${message}`);
    vscode.window.showErrorMessage(`doeff runner failed: ${message}`);
  }
}

async function runProgramWithTool(
  resource: vscode.Uri | string | undefined,
  lineNumber: number | undefined,
  toolType: 'kleisli' | 'transform',
  toolQualifiedName: string | undefined,
  codeLensProvider: ProgramCodeLensProvider
) {
  try {
    await vscode.workspace.saveAll();
    const document = await resolveDocument(resource);
    if (!document) {
      vscode.window.showErrorMessage('No active Python document to run.');
      return;
    }

    const targetLine =
      typeof lineNumber === 'number'
        ? lineNumber
        : vscode.window.activeTextEditor?.selection.active.line ?? 0;

    const declaration = findDeclarationAtLine(document, targetLine);
    if (!declaration) {
      vscode.window.showErrorMessage(
        'No doeff Program annotation found on this line.'
      );
      return;
    }

    const workspaceFolder =
      vscode.workspace.getWorkspaceFolder(document.uri) ??
      vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) {
      vscode.window.showErrorMessage('Open a workspace folder to run doeff.');
      return;
    }

    const indexerPath = await locateIndexer();
    const programEntry = await findProgramEntry(
      indexerPath,
      workspaceFolder.uri.fsPath,
      document.uri.fsPath,
      declaration.name
    );
    if (!programEntry) {
      vscode.window.showErrorMessage(
        `doeff-indexer could not find symbol '${declaration.name}' in this file.`
      );
      return;
    }
    const programPath = programEntry.qualifiedName || declaration.name;

    // Use program location for proximity-based sorting
    const proximity: ProximityContext = {
      filePath: document.uri.fsPath,
      line: targetLine + 1  // Convert 0-indexed to 1-indexed
    };

    // Find interpreter (sorted by proximity to the program)
    const interpreters = await fetchEntries(
      indexerPath,
      workspaceFolder.uri.fsPath,
      'find-interpreters',
      declaration.typeArg,
      proximity
    );
    if (!interpreters.length) {
      vscode.window.showErrorMessage(
        `No doeff interpreters were found. Cannot run with ${toolType}.`
      );
      return;
    }

    // First interpreter is now the closest one
    const defaultInterpreter = interpreters[0];

    // Find the tool entry for validation (sorted by proximity)
    const toolCommand = toolType === 'kleisli' ? 'find-kleisli' : 'find-transforms';
    const tools = await fetchEntries(
      indexerPath,
      workspaceFolder.uri.fsPath,
      toolCommand,
      declaration.typeArg,
      proximity
    );
    const toolEntry = tools.find(
      (t) => t.qualifiedName === toolQualifiedName
    );
    if (!toolEntry && toolQualifiedName) {
      vscode.window.showErrorMessage(
        `${toolType} tool '${toolQualifiedName}' not found.`
      );
      return;
    }

    const selection: RunSelection = {
      programPath,
      programType: declaration.typeArg,
      interpreter: defaultInterpreter,
      kleisli: toolType === 'kleisli' ? toolEntry : undefined,
      transformer: toolType === 'transform' ? toolEntry : undefined
    };

    await runSelection(selection, workspaceFolder);
    codeLensProvider.refresh();
  } catch (error) {
    const message =
      error instanceof Error ? error.message : 'Unknown error running doeff.';
    output.appendLine(`[error] ${message}`);
    vscode.window.showErrorMessage(`doeff runner failed: ${message}`);
  }
}

async function runDefault(
  programPath: string,
  workspaceFolder?: vscode.WorkspaceFolder
) {
  const folder =
    workspaceFolder ?? vscode.workspace.workspaceFolders?.[0];
  const args = ['run', '--program', programPath];

  const debugConfig: vscode.DebugConfiguration = {
    type: 'python',
    request: 'launch',
    name: `doeff: ${programPath}`,
    module: 'doeff',
    args,
    cwd: folder?.uri.fsPath,
    console: 'integratedTerminal',
    justMyCode: false
  };

  if (folder) {
    persistLaunchConfig(debugConfig, folder);
  }

  const commandDisplay = `python -m doeff ${args.join(' ')}`;
  vscode.window.showInformationMessage(`Running: ${commandDisplay}`);
  output.appendLine(`[info] Command: ${commandDisplay}`);
  await vscode.debug.startDebugging(folder, debugConfig);
}

async function runSelection(
  selection: RunSelection | undefined,
  workspaceFolder?: vscode.WorkspaceFolder
) {
  if (!selection) {
    vscode.window.showErrorMessage('No doeff selection to run.');
    return;
  }
  const folder =
    workspaceFolder ?? vscode.workspace.workspaceFolders?.[0];
  const args = [
    'run',
    '--program',
    selection.programPath,
    '--interpreter',
    selection.interpreter.qualifiedName
  ];

  if (selection.kleisli) {
    args.push('--apply', selection.kleisli.qualifiedName);
  }
  if (selection.transformer) {
    args.push('--transform', selection.transformer.qualifiedName);
  }

  const debugConfig: vscode.DebugConfiguration = {
    type: 'python',
    request: 'launch',
    name: `doeff: ${selection.programPath}`,
    module: 'doeff',
    args,
    cwd: folder?.uri.fsPath,
    console: 'integratedTerminal',
    justMyCode: false
  };

  if (folder) {
    persistLaunchConfig(debugConfig, folder);
  }

  output.appendLine(
    `[info] Launching doeff run for ${selection.programPath} with interpreter ${selection.interpreter.qualifiedName}`
  );
  const commandDisplay = `python -m doeff ${args.join(' ')}`;
  vscode.window.showInformationMessage(`Running: ${commandDisplay}`);
  output.appendLine(`[info] Command: ${commandDisplay}`);
  await vscode.debug.startDebugging(folder, debugConfig);
}

function persistLaunchConfig(
  config: vscode.DebugConfiguration,
  folder: vscode.WorkspaceFolder
) {
  try {
    const vscodeDir = path.join(folder.uri.fsPath, '.vscode');
    if (!fs.existsSync(vscodeDir)) {
      fs.mkdirSync(vscodeDir, { recursive: true });
    }
    const launchPath = path.join(vscodeDir, 'launch.json');
    const existing = fs.existsSync(launchPath)
      ? JSON.parse(fs.readFileSync(launchPath, 'utf8'))
      : { version: '0.2.0', configurations: [] };
    const configurations: vscode.DebugConfiguration[] =
      Array.isArray(existing.configurations) ? existing.configurations : [];
    const index = configurations.findIndex(
      (item) => item && item.name === config.name
    );
    if (index >= 0) {
      configurations[index] = config;
    } else {
      configurations.push(config);
    }
    const payload = {
      version: existing.version ?? '0.2.0',
      configurations
    };
    fs.writeFileSync(launchPath, JSON.stringify(payload, null, 2), 'utf8');
    output.appendLine(`[info] Saved launch config: ${config.name}`);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : 'Failed to write launch.json';
    output.appendLine(`[warn] ${message}`);
  }
}

async function buildSelection(
  document: vscode.TextDocument,
  declaration: ProgramDeclaration,
  workspaceFolder: vscode.WorkspaceFolder,
  programPath: string
): Promise<RunSelection | undefined> {
  const indexerPath = await locateIndexer();
  const rootPath = workspaceFolder.uri.fsPath;

  const programType = declaration.typeArg;

  // Use program location for proximity-based sorting
  const proximity: ProximityContext = {
    filePath: document.uri.fsPath,
    line: declaration.range.start.line + 1  // Convert 0-indexed to 1-indexed
  };

  const interpreters = await fetchEntries(
    indexerPath,
    rootPath,
    'find-interpreters',
    programType,
    proximity
  );
  if (!interpreters.length) {
    vscode.window.showErrorMessage(
      'No doeff interpreters were found. Check the "doeff-runner" output for indexer details.'
    );
    return;
  }
  const kleisli = await fetchEntries(
    indexerPath,
    rootPath,
    'find-kleisli',
    programType,
    proximity
  );
  const transformers = await fetchEntries(
    indexerPath,
    rootPath,
    'find-transforms',
    programType,
    proximity
  );

  const interpreterChoice = await selectEntry(
    'Select interpreter',
    interpreters,
    false
  );
  if (!interpreterChoice) {
    return;
  }
  const kleisliChoice = kleisli.length
    ? await selectEntry('Select Kleisli (optional)', kleisli, true)
    : undefined;
  const transformerChoice = transformers.length
    ? await selectEntry('Select transformer (optional)', transformers, true)
    : undefined;

  return {
    programPath,
    programType,
    interpreter: interpreterChoice,
    kleisli: kleisliChoice,
    transformer: transformerChoice
  };
}

async function selectEntry(
  title: string,
  entries: IndexEntry[],
  allowNone: boolean
): Promise<IndexEntry | undefined> {
  const items: (vscode.QuickPickItem & { entry?: IndexEntry })[] = entries.map(
    (entry) => ({
      label: entry.name,
      description: entry.qualifiedName,
      detail: entry.docstring,
      entry
    })
  );
  if (allowNone) {
    items.unshift({
      label: 'None',
      description: 'Skip selection',
      detail: 'Leave this input empty'
    });
  }
  const choice = await vscode.window.showQuickPick(items, {
    title,
    placeHolder: allowNone
      ? 'Select an entry or choose None'
      : 'Select an interpreter'
  });
  return choice?.entry;
}

function findDeclarationAtLine(
  document: vscode.TextDocument,
  lineNumber: number
): ProgramDeclaration | undefined {
  const declarations = extractProgramDeclarations(document);
  return declarations.find((decl) => decl.range.start.line === lineNumber);
}

function extractProgramDeclarations(
  document: vscode.TextDocument
): ProgramDeclaration[] {
  const declarations: ProgramDeclaration[] = [];
  for (let i = 0; i < document.lineCount; i += 1) {
    const declaration = parseProgramDeclaration(document.lineAt(i).text, i);
    if (declaration) {
      declarations.push(declaration);
    }
  }
  return declarations;
}

function parseProgramDeclaration(
  line: string,
  lineNumber: number
): ProgramDeclaration | undefined {
  const code = line.split('#')[0];
  const match = PROGRAM_REGEX.exec(code);
  if (!match) {
    return;
  }
  const name = match[1];
  // Return empty string for untyped Program, actual type for Program[T]
  const typeArg = match[2]?.trim() || '';
  const range = new vscode.Range(
    new vscode.Position(lineNumber, 0),
    new vscode.Position(lineNumber, line.length)
  );
  return { name, typeArg, range };
}

async function resolveDocument(
  resource?: vscode.Uri | string
): Promise<vscode.TextDocument | undefined> {
  if (resource instanceof vscode.Uri) {
    return vscode.workspace.openTextDocument(resource);
  }
  if (typeof resource === 'string') {
    return vscode.workspace.openTextDocument(vscode.Uri.file(resource));
  }
  return vscode.window.activeTextEditor?.document;
}

async function locateIndexer(): Promise<string> {
  const envPath = process.env.DOEFF_INDEXER_PATH;
  if (envPath && isExecutable(envPath)) {
    return envPath;
  }
  for (const candidate of INDEXER_CANDIDATES) {
    if (candidate && isExecutable(candidate)) {
      return candidate;
    }
  }
  vscode.window.showErrorMessage(
    'doeff-indexer not found. Install it (cargo install --path packages/doeff-indexer) or set DOEFF_INDEXER_PATH.'
  );
  throw new Error('doeff-indexer not found');
}

function isExecutable(target: string): boolean {
  try {
    fs.accessSync(target, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

async function findProgramEntry(
  indexerPath: string,
  rootPath: string,
  filePath: string,
  name: string
): Promise<IndexEntry | undefined> {
  const cacheKey = `index:${rootPath}:${filePath}`;
  const entries = await queryIndexer(indexerPath, cacheKey, rootPath, [
    'index',
    '--root',
    rootPath,
    '--file',
    filePath
  ]);
  return entries.find((entry) => entry.name === name);
}

interface ProximityContext {
  filePath: string;
  line: number;
}

async function fetchEntries(
  indexerPath: string,
  rootPath: string,
  command: string,
  typeArg: string,
  proximity?: ProximityContext
): Promise<IndexEntry[]> {
  const trimmedType = typeArg.trim();
  const proxKey = proximity ? `:${proximity.filePath}:${proximity.line}` : '';
  const cacheKey = `${command}:${rootPath}:${trimmedType || 'Any'}${proxKey}`;
  const args = [command, '--root', rootPath];
  const supportsTypeArg =
    command === 'find-kleisli' || command === 'find-transforms' || command === 'find-interceptors';
  if (supportsTypeArg && trimmedType && trimmedType.toLowerCase() !== 'any') {
    args.push('--type-arg', trimmedType);
  }
  // Pass proximity information for sorting by closest match
  if (proximity) {
    args.push('--proximity-file', proximity.filePath);
    args.push('--proximity-line', String(proximity.line));
  }
  const entries = await queryIndexer(indexerPath, cacheKey, rootPath, args);
  return filterEntriesForType(entries, trimmedType);
}

async function queryIndexer(
  indexerPath: string,
  cacheKey: string,
  cwd: string,
  args: string[]
): Promise<IndexEntry[]> {
  const cached = entryCache.get(cacheKey);
  if (cached && Date.now() - cached.timestamp < CACHE_TTL_MS) {
    return cached.data;
  }

  const stdout = await executeIndexer(indexerPath, args, cwd);
  let parsed: IndexEntry[] = [];
  try {
    const payload = JSON.parse(stdout);
    const rawEntries = Array.isArray(payload?.entries)
      ? payload.entries
      : Array.isArray(payload)
        ? payload
        : [];
    parsed = normalizeEntries(rawEntries);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : 'Invalid JSON from indexer';
    output.appendLine(`[error] Failed to parse indexer output: ${message}`);
  }

  entryCache.set(cacheKey, { timestamp: Date.now(), data: parsed });
  return parsed;
}

async function executeIndexer(
  indexerPath: string,
  args: string[],
  cwd: string
): Promise<string> {
  output.appendLine(
    `[info] Running doeff-indexer: ${indexerPath} ${args.join(' ')}`
  );
  try {
    const { stdout, stderr } = await execFileAsync(indexerPath, args, {
      cwd,
      maxBuffer: 10 * 1024 * 1024
    });
    if (stderr.trim()) {
      output.appendLine(`[warn] doeff-indexer stderr:\n${stderr.trim()}`);
    }
    return stdout;
  } catch (error) {
    const message =
      error instanceof Error ? error.message : 'Failed to execute doeff-indexer';
    output.appendLine(`[error] ${message}`);
    throw error;
  }
}

function normalizeEntries(entries: any[]): IndexEntry[] {
  return entries.map((entry) => ({
    name: entry.name ?? '',
    qualifiedName: entry.qualified_name ?? entry.qualifiedName ?? '',
    filePath: entry.file_path ?? entry.filePath ?? '',
    line: entry.line ?? 0,
    itemKind: entry.item_kind ?? entry.itemKind ?? 'function',
    categories: entry.categories ?? [],
    programParameters: normalizeParams(
      entry.program_parameters ?? entry.programParameters ?? []
    ),
    interpreterParameters: normalizeParams(
      entry.program_interpreter_parameters ??
        entry.interpreter_parameters ??
        entry.interpreterParameters ??
        []
    ),
    typeUsages: normalizeTypeUsages(entry.type_usages ?? entry.typeUsages ?? []),
    docstring: entry.docstring ?? undefined,
    markers: entry.markers ?? undefined
  }));
}

function normalizeParams(params: any[]): IndexParameter[] {
  return params.map((param) => ({
    name: param.name ?? '',
    annotation: param.annotation ?? undefined,
    isRequired: param.is_required ?? param.isRequired ?? false,
    position: param.position ?? 0,
    kind: param.kind ?? ''
  }));
}

function normalizeTypeUsages(usages: any[]): ProgramTypeUsage[] {
  return usages.map((usage) => ({
    kind: usage.kind ?? '',
    raw: usage.raw ?? '',
    typeArguments: usage.type_arguments ?? usage.typeArguments ?? []
  }));
}

function filterEntriesForType(
  entries: IndexEntry[],
  typeArg: string
): IndexEntry[] {
  const normalizedType = typeArg.trim();
  // Empty type means unspecified - return empty (caller should handle this case)
  if (!normalizedType) {
    return [];
  }
  // Explicit 'Any' means match all entries
  if (normalizedType.toLowerCase() === 'any') {
    return entries;
  }
  const filtered = entries.filter((entry) => matchesType(entry, normalizedType));
  return filtered.length > 0 ? filtered : entries;
}

function matchesType(entry: IndexEntry, typeArg: string): boolean {
  const lower = typeArg.toLowerCase();
  const parameterMatches = [...entry.programParameters, ...entry.interpreterParameters].some(
    (param) => (param.annotation ?? '').toLowerCase().includes(lower)
  );
  const usageMatches = entry.typeUsages.some(
    (usage) =>
      usage.raw.toLowerCase() === lower ||
      usage.typeArguments.some(
        (argument: string) => argument.toLowerCase() === lower
      )
  );
  return parameterMatches || usageMatches;
}
