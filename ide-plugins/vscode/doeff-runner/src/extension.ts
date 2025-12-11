import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as fs from 'fs';
import { promisify } from 'util';
import * as path from 'path';

const execFileAsync = promisify(cp.execFile);

const CACHE_TTL_MS = 5000;
const PROGRAM_REGEX =
  /^\s*([A-Za-z_]\w*)\s*:\s*(?:["']?Program(?:\s*\[\s*([^\]]+)\s*\])?["']?)/;
// Fallback paths for doeff-indexer binary (used if not found in Python env)
const INDEXER_FALLBACK_CANDIDATES = [
  '/usr/local/bin/doeff-indexer',
  '/usr/bin/doeff-indexer',
  `${process.env.HOME ?? ''}/.cargo/bin/doeff-indexer`,
  `${process.env.HOME ?? ''}/.local/bin/doeff-indexer`,
  '/opt/homebrew/bin/doeff-indexer'
];

const output = vscode.window.createOutputChannel('doeff-runner');

// Terminal management for non-debug runs
function createTerminal(name: string, cwd?: string): vscode.Terminal {
  return vscode.window.createTerminal({ name, cwd });
}

// Build a descriptive name for run/debug sessions
// Format: <Run/Debug>-<entrypoint>-><kleisli>-><transform>
function buildSessionName(
  mode: 'Run' | 'Debug',
  programPath: string,
  kleisli?: string,
  transformer?: string
): string {
  // Extract just the entrypoint name from qualified path
  const entrypoint = programPath.split('.').pop() ?? programPath;
  let name = `${mode}-${entrypoint}`;

  if (kleisli) {
    const kleisliName = kleisli.split('.').pop() ?? kleisli;
    name += `->${kleisliName}`;
  }
  if (transformer) {
    const transformName = transformer.split('.').pop() ?? transformer;
    name += `->${transformName}`;
  }

  return name;
}

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
  debug: '🐛',
  runWithOptions: '▶⚙',
  kleisli: '🔗',
  transform: '🔀',
  toggleOn: '[🐛]',
  toggleOff: '[▶]'
};

// Maximum number of tools to show directly in CodeLens before showing "+X more" button
const MAX_VISIBLE_TOOLS = 3;

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

  constructor(private context: vscode.ExtensionContext) { }

  // Debug mode state
  getDebugMode(): boolean {
    return this.context.workspaceState.get<boolean>('debugMode', true);
  }

  async setDebugMode(enabled: boolean): Promise<void> {
    await this.context.workspaceState.update('debugMode', enabled);
    this._onStateChange.fire();
  }

  async toggleDebugMode(): Promise<boolean> {
    const current = this.getDebugMode();
    await this.setDebugMode(!current);
    return !current;
  }

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

type TreeNode = ModuleNode | EntrypointNode | ActionNode | EnvChainNode | EnvSourceNode | EnvKeyNode;

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

// =============================================================================
// Env Chain Types (for Implicit Environment Inspector)
// =============================================================================

interface EnvChainEntry {
  qualifiedName: string;
  filePath: string;
  line: number;
  keys: string[];
  staticValues?: Record<string, unknown>;
  isUserConfig?: boolean;
}

interface EnvChainResult {
  program: string;
  envChain: EnvChainEntry[];
}

interface EnvChainNode {
  type: 'envChain';
  parentEntry: IndexEntry;
  entries: EnvChainEntry[];
}

interface EnvSourceNode {
  type: 'envSource';
  entry: EnvChainEntry;
  parentEntry: IndexEntry;
  allEnvEntries: EnvChainEntry[]; // For override detection
}

interface EnvKeyNode {
  type: 'envKey';
  key: string;
  value: unknown | null;
  isFinal: boolean;
  overriddenBy?: string;
  envEntry: EnvChainEntry;
  parentEntry: IndexEntry;
}

// =============================================================================
// Key Inspector Types
// =============================================================================

interface KeyResolution {
  key: string;
  finalValue: unknown | null;
  chain: Array<{
    envQualifiedName: string;
    value: unknown | null;
    isOverridden: boolean;
  }>;
  runtimeValue?: unknown;
  runtimeError?: string;
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
  ) { }

  getTreeItem(element: TreeNode): vscode.TreeItem {
    switch (element.type) {
      case 'module':
        return this.createModuleTreeItem(element);
      case 'entrypoint':
        return this.createEntrypointTreeItem(element);
      case 'action':
        return this.createActionTreeItem(element);
      case 'envChain':
        return this.createEnvChainTreeItem(element);
      case 'envSource':
        return this.createEnvSourceTreeItem(element);
      case 'envKey':
        return this.createEnvKeyTreeItem(element);
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
    const debugMode = this.stateStore.getDebugMode();
    const label = this.getActionLabel(node.actionType, debugMode);
    const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.None);

    const defaultAction = this.stateStore.getDefaultAction(node.parentEntry.qualifiedName);
    const isDefault = this.actionsEqual(defaultAction, node.actionType);

    switch (node.actionType.kind) {
      case 'run':
        // Use 'debug' icon when in debug mode, 'play' when in run mode
        item.iconPath = new vscode.ThemeIcon(debugMode ? 'debug-start' : 'play');
        item.command = {
          command: 'doeff-runner.runFromTree',
          title: debugMode ? 'Debug' : 'Run',
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

  private createEnvChainTreeItem(node: EnvChainNode): vscode.TreeItem {
    const keyCount = node.entries.reduce((sum, e) => sum + e.keys.length, 0);
    const sourceCount = node.entries.length;
    const label = keyCount > 0
      ? `📦 Environment (${keyCount} keys, ${sourceCount} sources)`
      : `📦 Environment (${sourceCount} sources)`;

    const item = new vscode.TreeItem(
      label,
      vscode.TreeItemCollapsibleState.Collapsed
    );
    item.iconPath = new vscode.ThemeIcon('package');
    item.contextValue = 'envChain';
    item.tooltip = 'Click to expand environment chain';
    return item;
  }

  private createEnvSourceTreeItem(node: EnvSourceNode): vscode.TreeItem {
    const entry = node.entry;
    const icon = entry.isUserConfig ? '🏠' : '📄';
    const keyInfo = entry.keys.length > 0 ? ` (${entry.keys.length} keys)` : '';
    const label = `${icon} ${entry.qualifiedName}${keyInfo}`;

    const item = new vscode.TreeItem(
      label,
      entry.keys.length > 0
        ? vscode.TreeItemCollapsibleState.Collapsed
        : vscode.TreeItemCollapsibleState.None
    );
    item.iconPath = entry.isUserConfig
      ? new vscode.ThemeIcon('home')
      : new vscode.ThemeIcon('file-code');
    item.contextValue = 'envSource';
    item.tooltip = entry.filePath;
    item.command = {
      command: 'vscode.open',
      title: 'Go to File',
      arguments: [
        vscode.Uri.file(entry.filePath),
        { selection: new vscode.Range(entry.line - 1, 0, entry.line - 1, 0) }
      ]
    };
    return item;
  }

  private createEnvKeyTreeItem(node: EnvKeyNode): vscode.TreeItem {
    const valueDisplay = node.value !== null
      ? JSON.stringify(node.value)
      : '<dynamic>';
    const marker = node.isFinal ? '★' : `⚠️↓ overridden by ${node.overriddenBy}`;
    const label = `🔑 ${node.key} = ${valueDisplay} ${marker}`;

    const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.None);
    item.iconPath = new vscode.ThemeIcon('key');
    item.contextValue = 'envKey';
    item.tooltip = node.isFinal
      ? `Final value from ${node.envEntry.qualifiedName}`
      : `Overridden by ${node.overriddenBy}`;
    return item;
  }

  private getActionLabel(action: ActionType, debugMode?: boolean): string {
    switch (action.kind) {
      case 'run': {
        const useDebug = debugMode ?? this.stateStore.getDebugMode();
        const prefix = useDebug ? TOOL_PREFIX.debug : TOOL_PREFIX.run;
        const label = useDebug ? 'Debug' : 'Run';
        return `${prefix} ${label}`;
      }
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
      case 'envChain':
        return element.entries.map(entry => ({
          type: 'envSource' as const,
          entry,
          parentEntry: element.parentEntry,
          allEnvEntries: element.entries
        }));
      case 'envSource':
        return this.getEnvKeyNodes(element);
      case 'envKey':
        return [];
    }
  }

  private getEnvKeyNodes(node: EnvSourceNode): EnvKeyNode[] {
    const keys = node.entry.keys;
    const staticValues = node.entry.staticValues ?? {};

    return keys.map(key => {
      const value = staticValues[key] ?? null;

      // Check if this key is overridden by a later env in the chain
      const thisEnvIndex = node.allEnvEntries.findIndex(
        e => e.qualifiedName === node.entry.qualifiedName
      );

      let overriddenBy: string | undefined;
      for (let i = thisEnvIndex + 1; i < node.allEnvEntries.length; i++) {
        const laterEnv = node.allEnvEntries[i];
        if (laterEnv.keys.includes(key)) {
          overriddenBy = laterEnv.qualifiedName;
          break;
        }
      }

      return {
        type: 'envKey' as const,
        key,
        value,
        isFinal: !overriddenBy,
        overriddenBy,
        envEntry: node.entry,
        parentEntry: node.parentEntry
      };
    });
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

  private async getActionNodes(entry: IndexEntry): Promise<TreeNode[]> {
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

    // Add environment chain node
    const result: TreeNode[] = [...actions];
    try {
      const envChain = await this.getEnvChain(rootPath, entry.qualifiedName);
      if (envChain.length > 0) {
        result.push({
          type: 'envChain',
          parentEntry: entry,
          entries: envChain
        });
      }
    } catch (error) {
      output.appendLine(`[warning] Failed to load env chain for ${entry.qualifiedName}: ${error}`);
    }

    return result;
  }

  private envChainCache = new Map<string, EnvChainEntry[]>();

  private async getEnvChain(rootPath: string, programQualifiedName: string): Promise<EnvChainEntry[]> {
    const cacheKey = `envchain:${programQualifiedName}`;
    const cached = this.envChainCache.get(cacheKey);
    if (cached) {
      return cached;
    }

    try {
      const indexerPath = await locateIndexer();
      const result = await queryEnvChain(indexerPath, rootPath, programQualifiedName);
      this.envChainCache.set(cacheKey, result);
      return result;
    } catch {
      return [];
    }
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
  private fileIndexCache = new Map<string, { entries: IndexEntry[]; timestamp: number }>();
  private pendingFetches = new Set<string>();
  private readonly CACHE_TTL_MS = 30000; // 30 seconds before background refresh

  constructor(private stateStore: DoeffStateStore) { }

  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    const lenses: vscode.CodeLens[] = [];

    const workspaceFolder =
      vscode.workspace.getWorkspaceFolder(document.uri) ??
      vscode.workspace.workspaceFolders?.[0];

    if (!workspaceFolder) {
      return lenses;
    }

    // Use indexer output instead of regex-based detection
    const entries = this.getFileEntriesSync(workspaceFolder.uri.fsPath, document.uri.fsPath);

    // Filter to only Program assignments (not function parameters)
    const programEntries = entries.filter(entry =>
      entry.itemKind === 'assignment' &&
      entry.typeUsages.some(usage => usage.kind === 'program')
    );

    // Get current debug mode
    const debugMode = this.stateStore.getDebugMode();

    for (const entry of programEntries) {
      const lineNumber = entry.line - 1; // Convert 1-indexed to 0-indexed
      const range = new vscode.Range(
        new vscode.Position(lineNumber, 0),
        new vscode.Position(lineNumber, 1)
      );

      // Extract type argument from type_usages
      const typeArg = this.extractTypeArg(entry);

      // Check if there's a default action set via TreeView
      const defaultAction = this.stateStore.getDefaultAction(entry.qualifiedName);

      // Debug mode toggle button (first)
      lenses.push(
        new vscode.CodeLens(range, {
          title: debugMode ? TOOL_PREFIX.toggleOn : TOOL_PREFIX.toggleOff,
          tooltip: debugMode ? 'Debug mode ON (click to switch to Run mode)' : 'Run mode (click to switch to Debug mode)',
          command: 'doeff-runner.toggleDebugMode',
          arguments: []
        })
      );

      // Show default action first if set
      if (defaultAction) {
        lenses.push(this.createDefaultActionLensFromEntry(entry, range, defaultAction, document.uri, debugMode));
      }

      // Standard Run/Debug button (label changes based on debug mode)
      const runLabel = debugMode ? `${TOOL_PREFIX.debug} Debug` : `${TOOL_PREFIX.run} Run`;
      const runTooltip = debugMode ? 'Debug with default interpreter' : 'Run with default interpreter';
      lenses.push(
        new vscode.CodeLens(range, {
          title: runLabel,
          tooltip: runTooltip,
          command: 'doeff-runner.runDefault',
          arguments: [document.uri, lineNumber]
        })
      );
      // Run with options button
      lenses.push(
        new vscode.CodeLens(range, {
          title: `${TOOL_PREFIX.runWithOptions} Options`,
          tooltip: 'Run with custom interpreter, kleisli, and transformer selection',
          command: 'doeff-runner.runOptions',
          arguments: [document.uri, lineNumber]
        })
      );

      // Only show kleisli/transform tools if the Program has a type argument
      // Untyped Program shouldn't show tools since we don't know the output type
      if (typeArg) {
        const rootPath = workspaceFolder.uri.fsPath;

        // Use program location for proximity-based sorting
        const proximity: ProximityContext = {
          filePath: document.uri.fsPath,
          line: entry.line
        };

        // Add Kleisli tool buttons (sorted by proximity, limited to MAX_VISIBLE_TOOLS)
        const kleisliTools = this.getToolsSync('kleisli', rootPath, typeArg, proximity);
        const visibleKleisli = kleisliTools.slice(0, MAX_VISIBLE_TOOLS);
        const hiddenKleisliCount = kleisliTools.length - visibleKleisli.length;

        for (const kleisli of visibleKleisli) {
          lenses.push(
            new vscode.CodeLens(range, {
              title: `${TOOL_PREFIX.kleisli} ${kleisli.name}`,
              tooltip: kleisli.docstring
                ? `[Kleisli] ${kleisli.qualifiedName}\n\n${kleisli.docstring}`
                : `[Kleisli] ${kleisli.qualifiedName}`,
              command: 'doeff-runner.runWithKleisli',
              arguments: [
                document.uri,
                lineNumber,
                kleisli.qualifiedName
              ]
            })
          );
        }

        // Show "+X more" button if there are hidden Kleisli tools
        if (hiddenKleisliCount > 0) {
          lenses.push(
            new vscode.CodeLens(range, {
              title: `${TOOL_PREFIX.kleisli} +${hiddenKleisliCount} more`,
              tooltip: `Show ${hiddenKleisliCount} more Kleisli tools`,
              command: 'doeff-runner.showMoreKleisli',
              arguments: [document.uri, lineNumber, typeArg]
            })
          );
        }

        // Add Transform tool buttons (sorted by proximity, limited to MAX_VISIBLE_TOOLS)
        const transformTools = this.getToolsSync('transform', rootPath, typeArg, proximity);
        const visibleTransform = transformTools.slice(0, MAX_VISIBLE_TOOLS);
        const hiddenTransformCount = transformTools.length - visibleTransform.length;

        for (const transform of visibleTransform) {
          lenses.push(
            new vscode.CodeLens(range, {
              title: `${TOOL_PREFIX.transform} ${transform.name}`,
              tooltip: transform.docstring
                ? `[Transform] ${transform.qualifiedName}\n\n${transform.docstring}`
                : `[Transform] ${transform.qualifiedName}`,
              command: 'doeff-runner.runWithTransform',
              arguments: [
                document.uri,
                lineNumber,
                transform.qualifiedName
              ]
            })
          );
        }

        // Show "+X more" button if there are hidden Transform tools
        if (hiddenTransformCount > 0) {
          lenses.push(
            new vscode.CodeLens(range, {
              title: `${TOOL_PREFIX.transform} +${hiddenTransformCount} more`,
              tooltip: `Show ${hiddenTransformCount} more Transform tools`,
              command: 'doeff-runner.showMoreTransforms',
              arguments: [document.uri, lineNumber, typeArg]
            })
          );
        }
      }
    }
    return lenses;
  }

  private extractTypeArg(entry: IndexEntry): string {
    for (const usage of entry.typeUsages) {
      if (usage.kind === 'program' && usage.typeArguments.length > 0) {
        return usage.typeArguments[0];
      }
    }
    return '';
  }

  /**
   * Synchronously returns cached index entries for a file.
   * Triggers background refresh if cache is stale.
   */
  private getFileEntriesSync(rootPath: string, filePath: string): IndexEntry[] {
    const cacheKey = `file:${filePath}`;
    const cached = this.fileIndexCache.get(cacheKey);
    const now = Date.now();

    // Return cached data if available (even if stale)
    if (cached) {
      // Trigger background refresh if stale
      if (now - cached.timestamp > this.CACHE_TTL_MS) {
        this.refreshFileIndexInBackground(rootPath, filePath, cacheKey);
      }
      return cached.entries;
    }

    // No cache - trigger background fetch and return empty for now
    this.refreshFileIndexInBackground(rootPath, filePath, cacheKey);
    return [];
  }

  /**
   * Fetches file index in background and refreshes CodeLens when done.
   */
  private refreshFileIndexInBackground(
    rootPath: string,
    filePath: string,
    cacheKey: string
  ): void {
    // Avoid duplicate fetches
    if (this.pendingFetches.has(cacheKey)) {
      return;
    }
    this.pendingFetches.add(cacheKey);

    // Fire and forget - fetch in background
    (async () => {
      try {
        const indexerPath = await locateIndexer();
        const entries = await queryIndexer(indexerPath, cacheKey, rootPath, [
          'index',
          '--root',
          rootPath,
          '--file',
          filePath
        ]);
        const oldCached = this.fileIndexCache.get(cacheKey);
        this.fileIndexCache.set(cacheKey, {
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

  private createDefaultActionLensFromEntry(
    entry: IndexEntry,
    range: vscode.Range,
    action: ActionType,
    uri: vscode.Uri,
    debugMode: boolean
  ): vscode.CodeLens {
    const lineNumber = entry.line - 1; // Convert 1-indexed to 0-indexed
    let title: string;
    let command: string;
    let args: unknown[];

    switch (action.kind) {
      case 'run': {
        const runPrefix = debugMode ? TOOL_PREFIX.debug : TOOL_PREFIX.run;
        const runLabel = debugMode ? 'Debug' : 'Run';
        title = `★ ${runPrefix} ${runLabel}`;
        command = 'doeff-runner.runDefault';
        args = [uri, lineNumber];
        break;
      }
      case 'runWithOptions':
        title = `★ ${TOOL_PREFIX.runWithOptions} Options`;
        command = 'doeff-runner.runOptions';
        args = [uri, lineNumber];
        break;
      case 'kleisli': {
        const kleisliName = action.toolQualifiedName.split('.').pop() ?? action.toolQualifiedName;
        title = `★ ${TOOL_PREFIX.kleisli} ${kleisliName}`;
        command = 'doeff-runner.runWithKleisli';
        args = [uri, lineNumber, action.toolQualifiedName];
        break;
      }
      case 'transform': {
        const transformName = action.toolQualifiedName.split('.').pop() ?? action.toolQualifiedName;
        title = `★ ${TOOL_PREFIX.transform} ${transformName}`;
        command = 'doeff-runner.runWithTransform';
        args = [uri, lineNumber, action.toolQualifiedName];
        break;
      }
    }

    return new vscode.CodeLens(range, {
      title,
      tooltip: 'Default action (set via Explorer)',
      command,
      arguments: args
    });
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

  // Store extension context for bundled binary access
  extensionContext = context;

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

  // Helper to update TreeView message based on debug mode and filter
  const updateTreeViewMessage = () => {
    const debugMode = stateStore.getDebugMode();
    const filterText = treeProvider.getFilterText();
    const modeIndicator = debugMode ? '🐛 Debug' : '▶ Run';

    if (filterText) {
      treeView.message = `${modeIndicator} | 🔍 "${filterText}"`;
    } else {
      treeView.message = `${modeIndicator} mode`;
    }
  };

  // Subscribe to filter changes to update TreeView message
  treeProvider.onFilterChange(() => {
    updateTreeViewMessage();
  });

  // Initialize TreeView message
  updateTreeViewMessage();

  // Subscribe to state changes to refresh CodeLens and TreeView
  stateStore.onStateChange(() => {
    codeLensProvider.refresh();
    treeProvider.refresh();
    updateTreeViewMessage();
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
          codeLensProvider,
          stateStore
        )
    ),
    vscode.commands.registerCommand(
      'doeff-runner.runOptions',
      (resource?: vscode.Uri | string, lineNumber?: number) =>
        runProgram(
          resource,
          lineNumber,
          'options',
          codeLensProvider,
          stateStore
        )
    ),
    vscode.commands.registerCommand(
      'doeff-runner.runConfig',
      (selection: RunSelection) => runSelection(selection, undefined, stateStore.getDebugMode())
    ),
    vscode.commands.registerCommand(
      'doeff-runner.runWithKleisli',
      (resource?: vscode.Uri | string, lineNumber?: number, kleisliQualifiedName?: string) =>
        runProgramWithTool(
          resource,
          lineNumber,
          'kleisli',
          kleisliQualifiedName,
          codeLensProvider,
          stateStore
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
          codeLensProvider,
          stateStore
        )
    ),
    // "Show more" commands for CodeLens overflow
    vscode.commands.registerCommand(
      'doeff-runner.showMoreKleisli',
      async (uri: vscode.Uri, lineNumber: number, typeArg: string) => {
        await showMoreTools(uri, lineNumber, typeArg, 'kleisli', codeLensProvider, stateStore);
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.showMoreTransforms',
      async (uri: vscode.Uri, lineNumber: number, typeArg: string) => {
        await showMoreTools(uri, lineNumber, typeArg, 'transform', codeLensProvider, stateStore);
      }
    ),
    // Toggle debug mode command
    vscode.commands.registerCommand(
      'doeff-runner.toggleDebugMode',
      async () => {
        const newMode = await stateStore.toggleDebugMode();
        const modeLabel = newMode ? 'Debug' : 'Run';
        vscode.window.showInformationMessage(`doeff: Switched to ${modeLabel} mode`);
      }
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
        await runFromTreeView(entry, actionType, codeLensProvider, stateStore);
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
    // Key Inspector commands
    vscode.commands.registerCommand(
      'doeff-runner.inspectEnvKey',
      async (entryArg?: IndexEntry | EnvSourceNode) => {
        const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
        if (!workspaceFolder) {
          vscode.window.showErrorMessage('No workspace folder open');
          return;
        }

        // Determine program and env chain
        let programEntry: IndexEntry | undefined;
        let envChain: EnvChainEntry[] = [];

        if (entryArg && 'type' in entryArg && entryArg.type === 'envSource') {
          programEntry = entryArg.parentEntry;
          envChain = entryArg.allEnvEntries;
        } else if (entryArg && 'qualifiedName' in entryArg) {
          programEntry = entryArg as IndexEntry;
        }

        // If no entry provided, ask user to select a program
        if (!programEntry) {
          const indexerPath = await locateIndexer();
          const allEntries = await fetchEntries(indexerPath, workspaceFolder.uri.fsPath, 'index', '', undefined);
          const programs = allEntries.filter(e =>
            e.itemKind === 'assignment' &&
            e.typeUsages.some(u => u.kind === 'program')
          );

          const selected = await vscode.window.showQuickPick(
            programs.map(p => ({
              label: p.name,
              description: p.qualifiedName,
              entry: p
            })),
            { placeHolder: 'Select a program to inspect environment' }
          );

          if (!selected) return;
          programEntry = selected.entry;
        }

        // Fetch env chain if not already provided
        if (envChain.length === 0) {
          const indexerPath = await locateIndexer();
          envChain = await queryEnvChain(indexerPath, workspaceFolder.uri.fsPath, programEntry.qualifiedName);
        }

        // Collect all keys from the env chain
        const allKeys = new Set<string>();
        for (const env of envChain) {
          for (const key of env.keys) {
            allKeys.add(key);
          }
        }

        if (allKeys.size === 0) {
          vscode.window.showInformationMessage(
            'No keys found in environment chain. Keys may be dynamic.',
            'Refresh Keys'
          ).then(action => {
            if (action === 'Refresh Keys') {
              vscode.commands.executeCommand('doeff-runner.refreshEnvKeys', programEntry);
            }
          });
          return;
        }

        // Show QuickPick with all keys
        interface KeyQuickPickItem extends vscode.QuickPickItem {
          key: string;
          resolution: KeyResolution;
        }

        const items: KeyQuickPickItem[] = Array.from(allKeys).map(key => {
          // Build resolution chain for this key
          const chain: KeyResolution['chain'] = [];
          let finalValue: unknown | null = null;
          let finalEnv = '';

          for (let i = 0; i < envChain.length; i++) {
            const env = envChain[i];
            if (env.keys.includes(key)) {
              const value = env.staticValues?.[key] ?? null;
              const isLast = !envChain.slice(i + 1).some(e => e.keys.includes(key));
              chain.push({
                envQualifiedName: env.qualifiedName,
                value,
                isOverridden: !isLast
              });
              if (isLast) {
                finalValue = value;
                finalEnv = env.qualifiedName;
              }
            }
          }

          const resolution: KeyResolution = { key, finalValue, chain };

          const valueStr = finalValue !== null ? JSON.stringify(finalValue) : '<dynamic>';
          const markers = chain.length > 1 ? ` (${chain.length} sources)` : '';

          return {
            label: `🔑 ${key}`,
            description: `= ${valueStr}${markers}`,
            detail: `Final from: ${finalEnv}`,
            key,
            resolution
          };
        });

        const selected = await vscode.window.showQuickPick(items, {
          placeHolder: 'Select a key to see resolution chain',
          matchOnDescription: true
        });

        if (!selected) return;

        // Show resolution details in a new document
        const content = formatKeyResolution(selected.resolution, programEntry.qualifiedName);
        const doc = await vscode.workspace.openTextDocument({
          content,
          language: 'markdown'
        });
        await vscode.window.showTextDocument(doc, { preview: true });
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.resolveEnvKey',
      async (keyNode?: EnvKeyNode) => {
        if (!keyNode) {
          vscode.window.showErrorMessage('No key selected');
          return;
        }

        const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
        if (!workspaceFolder) {
          vscode.window.showErrorMessage('No workspace folder open');
          return;
        }

        // Execute ask(key) at runtime
        const key = keyNode.key;

        vscode.window.withProgress(
          { location: vscode.ProgressLocation.Notification, title: `Resolving ${key}...` },
          async () => {
            try {
              const askCode = `from doeff.core import ask; print(ask("${key}"))`;
              const { stdout, stderr } = await execFileAsync('python3', ['-c', askCode], {
                cwd: workspaceFolder.uri.fsPath,
                timeout: 10000
              });

              if (stderr.trim()) {
                output.appendLine(`[warn] ask("${key}") stderr: ${stderr.trim()}`);
              }

              const runtimeValue = stdout.trim();
              vscode.window.showInformationMessage(`${key} = ${runtimeValue}`);
            } catch (error) {
              const message = error instanceof Error ? error.message : 'Failed to resolve key';
              vscode.window.showErrorMessage(`Failed to resolve ${key}: ${message}`);
            }
          }
        );
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
  codeLensProvider: ProgramCodeLensProvider,
  stateStore: DoeffStateStore
): Promise<void> {
  const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
  if (!workspaceFolder) {
    vscode.window.showErrorMessage('Open a workspace folder to run doeff.');
    return;
  }

  const debugMode = stateStore.getDebugMode();

  try {
    await vscode.workspace.saveAll();

    switch (actionType.kind) {
      case 'run':
        await runDefault(entry.qualifiedName, workspaceFolder, debugMode);
        break;
      case 'runWithOptions': {
        // Open the file and trigger runOptions
        const uri = vscode.Uri.file(entry.filePath);
        const document = await vscode.workspace.openTextDocument(uri);
        await vscode.window.showTextDocument(document);
        await runProgram(uri, entry.line - 1, 'options', codeLensProvider, stateStore);
        break;
      }
      case 'kleisli':
        await runWithToolFromTree(
          entry,
          'kleisli',
          actionType.toolQualifiedName,
          workspaceFolder,
          debugMode
        );
        break;
      case 'transform':
        await runWithToolFromTree(
          entry,
          'transform',
          actionType.toolQualifiedName,
          workspaceFolder,
          debugMode
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
  workspaceFolder: vscode.WorkspaceFolder,
  debugMode: boolean = true
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

  await runSelection(selection, workspaceFolder, debugMode);
}

export function deactivate() {
  output.dispose();
}

async function runProgram(
  resource: vscode.Uri | string | undefined,
  lineNumber: number | undefined,
  mode: RunMode,
  codeLensProvider: ProgramCodeLensProvider,
  stateStore: DoeffStateStore
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

    const debugMode = stateStore.getDebugMode();

    if (mode === 'default') {
      await runDefault(programPath, workspaceFolder, debugMode);
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
      await runSelection(selection, workspaceFolder, debugMode);
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
  codeLensProvider: ProgramCodeLensProvider,
  stateStore: DoeffStateStore
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

    const debugMode = stateStore.getDebugMode();
    await runSelection(selection, workspaceFolder, debugMode);
    codeLensProvider.refresh();
  } catch (error) {
    const message =
      error instanceof Error ? error.message : 'Unknown error running doeff.';
    output.appendLine(`[error] ${message}`);
    vscode.window.showErrorMessage(`doeff runner failed: ${message}`);
  }
}

async function showMoreTools(
  uri: vscode.Uri,
  lineNumber: number,
  typeArg: string,
  toolType: 'kleisli' | 'transform',
  codeLensProvider: ProgramCodeLensProvider,
  stateStore: DoeffStateStore
): Promise<void> {
  const workspaceFolder =
    vscode.workspace.getWorkspaceFolder(uri) ??
    vscode.workspace.workspaceFolders?.[0];
  if (!workspaceFolder) {
    vscode.window.showErrorMessage('Open a workspace folder to run doeff.');
    return;
  }

  try {
    const indexerPath = await locateIndexer();
    const rootPath = workspaceFolder.uri.fsPath;

    // Use the CodeLens location for proximity-based sorting
    const proximity: ProximityContext = {
      filePath: uri.fsPath,
      line: lineNumber + 1 // Convert 0-indexed to 1-indexed
    };

    // Fetch all tools
    const command = toolType === 'kleisli' ? 'find-kleisli' : 'find-transforms';
    const tools = await fetchEntries(indexerPath, rootPath, command, typeArg, proximity);

    if (tools.length === 0) {
      vscode.window.showInformationMessage(`No ${toolType} tools found for type ${typeArg}.`);
      return;
    }

    // Show QuickPick with all tools
    const toolLabel = toolType === 'kleisli' ? 'Kleisli' : 'Transform';
    const prefix = toolType === 'kleisli' ? TOOL_PREFIX.kleisli : TOOL_PREFIX.transform;
    const items = tools.map((tool) => ({
      label: `${prefix} ${tool.name}`,
      description: tool.qualifiedName,
      detail: tool.docstring,
      tool
    }));

    const selected = await vscode.window.showQuickPick(items, {
      placeHolder: `Select ${toolLabel} tool to run`,
      matchOnDescription: true,
      matchOnDetail: true
    });

    if (!selected) {
      return; // User cancelled
    }

    // Run with selected tool
    await runProgramWithTool(
      uri,
      lineNumber,
      toolType,
      selected.tool.qualifiedName,
      codeLensProvider,
      stateStore
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    output.appendLine(`[error] ${message}`);
    vscode.window.showErrorMessage(`Failed to show ${toolType} tools: ${message}`);
  }
}

async function runDefault(
  programPath: string,
  workspaceFolder?: vscode.WorkspaceFolder,
  debugMode: boolean = true
) {
  const folder =
    workspaceFolder ?? vscode.workspace.workspaceFolders?.[0];
  const args = ['run', '--program', programPath];
  const commandDisplay = `python -m doeff ${args.join(' ')}`;
  const sessionName = buildSessionName(debugMode ? 'Debug' : 'Run', programPath);

  if (debugMode) {
    // Debug mode: use VSCode debug infrastructure with debugpy
    const debugConfig: vscode.DebugConfiguration = {
      type: 'python',
      request: 'launch',
      name: sessionName,
      module: 'doeff',
      args,
      cwd: folder?.uri.fsPath,
      console: 'integratedTerminal',
      justMyCode: false
    };

    if (folder) {
      persistLaunchConfig(debugConfig, folder);
    }

    vscode.window.showInformationMessage(`Debugging: ${commandDisplay}`);
    output.appendLine(`[info] Debugging: ${commandDisplay}`);
    await vscode.debug.startDebugging(folder, debugConfig);
  } else {
    // Run mode: use terminal directly without debugpy
    const terminal = createTerminal(sessionName, folder?.uri.fsPath);
    vscode.window.showInformationMessage(`Running: ${commandDisplay}`);
    output.appendLine(`[info] Running: ${commandDisplay}`);
    terminal.sendText(commandDisplay);
    terminal.show();
  }
}

async function runSelection(
  selection: RunSelection | undefined,
  workspaceFolder?: vscode.WorkspaceFolder,
  debugMode: boolean = true
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

  const commandDisplay = `python -m doeff ${args.join(' ')}`;
  const modeLabel = debugMode ? 'Debugging' : 'Running';
  const sessionName = buildSessionName(
    debugMode ? 'Debug' : 'Run',
    selection.programPath,
    selection.kleisli?.qualifiedName,
    selection.transformer?.qualifiedName
  );
  output.appendLine(
    `[info] ${modeLabel} doeff for ${selection.programPath} with interpreter ${selection.interpreter.qualifiedName}`
  );

  if (debugMode) {
    // Debug mode: use VSCode debug infrastructure with debugpy
    const debugConfig: vscode.DebugConfiguration = {
      type: 'python',
      request: 'launch',
      name: sessionName,
      module: 'doeff',
      args,
      cwd: folder?.uri.fsPath,
      console: 'integratedTerminal',
      justMyCode: false
    };

    if (folder) {
      persistLaunchConfig(debugConfig, folder);
    }

    vscode.window.showInformationMessage(`Debugging: ${commandDisplay}`);
    output.appendLine(`[info] Command: ${commandDisplay}`);
    await vscode.debug.startDebugging(folder, debugConfig);
  } else {
    // Run mode: use terminal directly without debugpy
    const terminal = createTerminal(sessionName, folder?.uri.fsPath);
    vscode.window.showInformationMessage(`Running: ${commandDisplay}`);
    output.appendLine(`[info] Command: ${commandDisplay}`);
    terminal.sendText(commandDisplay);
    terminal.show();
  }
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

  // Skip if this looks like a function parameter
  // 1. Check if line ends with ',' or ')' after the annotation WITHOUT an '=' sign
  //    (typical for function args like `def foo(arg: Program[T])`)
  //    But allow assignments where RHS ends with ')' like `x: Program[T] = foo()`
  const afterAnnotation = code.slice(match.index + match[0].length).trim();
  const hasAssignment = afterAnnotation.includes('=');
  if (!hasAssignment && (afterAnnotation.endsWith(',') || afterAnnotation.endsWith(')'))) {
    return;
  }

  // 2. Check if there are unmatched opening parens before the variable name
  //    This indicates we're inside a function signature like: def foo(arg: Program[T])
  const beforeMatch = code.slice(0, match.index);
  const openParens = (beforeMatch.match(/\(/g) || []).length;
  const closeParens = (beforeMatch.match(/\)/g) || []).length;
  if (openParens > closeParens) {
    return; // Inside parentheses, likely a function parameter
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

/**
 * Get the path to the bundled doeff-indexer binary for the current platform.
 */
function getBundledIndexerPath(context: vscode.ExtensionContext): string | undefined {
  const platform = process.platform;
  const arch = process.arch;

  let binaryName: string;
  if (platform === 'win32') {
    binaryName = 'doeff-indexer-windows-x64.exe';
  } else if (platform === 'darwin') {
    binaryName = arch === 'arm64'
      ? 'doeff-indexer-darwin-arm64'
      : 'doeff-indexer-darwin-x64';
  } else if (platform === 'linux') {
    binaryName = arch === 'arm64'
      ? 'doeff-indexer-linux-arm64'
      : 'doeff-indexer-linux-x64';
  } else {
    return undefined;
  }

  const bundledPath = path.join(context.extensionPath, 'bin', binaryName);
  if (isExecutable(bundledPath)) {
    return bundledPath;
  }
  return undefined;
}

// Extension context stored for access to bundled binaries
let extensionContext: vscode.ExtensionContext | undefined;

async function locateIndexer(): Promise<string> {
  // 1. Check for bundled binary first (fastest, no dependencies)
  if (extensionContext) {
    const bundledPath = getBundledIndexerPath(extensionContext);
    if (bundledPath) {
      output.appendLine(`[info] Using bundled indexer: ${bundledPath}`);
      return bundledPath;
    }
  }

  // 2. Check environment variable
  const envPath = process.env.DOEFF_INDEXER_PATH;
  if (envPath && isExecutable(envPath)) {
    output.appendLine(`[info] Using indexer from DOEFF_INDEXER_PATH: ${envPath}`);
    return envPath;
  }

  // 3. Try to find indexer in Python environment
  const pythonEnvIndexer = await findIndexerInPythonEnv();
  if (pythonEnvIndexer) {
    output.appendLine(`[info] Using indexer from Python environment: ${pythonEnvIndexer}`);
    return pythonEnvIndexer;
  }

  // 4. Fall back to system paths
  for (const candidate of INDEXER_FALLBACK_CANDIDATES) {
    if (candidate && isExecutable(candidate)) {
      output.appendLine(`[info] Using indexer from system path: ${candidate}`);
      return candidate;
    }
  }

  vscode.window.showErrorMessage(
    'doeff-indexer not found. The bundled binary may be missing for your platform.'
  );
  throw new Error('doeff-indexer not found');
}

/**
 * Find doeff-indexer binary in the Python environment.
 * This looks for the binary in the same directory as the Python interpreter.
 */
async function findIndexerInPythonEnv(): Promise<string | undefined> {
  const pythonPath = await getPythonInterpreter();
  if (!pythonPath) {
    return undefined;
  }

  const binDir = path.dirname(pythonPath);
  const indexerPath = path.join(binDir, 'doeff-indexer');

  if (isExecutable(indexerPath)) {
    return indexerPath;
  }

  // Windows: check for .exe extension
  const indexerPathExe = indexerPath + '.exe';
  if (isExecutable(indexerPathExe)) {
    return indexerPathExe;
  }

  return undefined;
}

/**
 * Get the Python interpreter path from VSCode Python extension or settings.
 */
async function getPythonInterpreter(): Promise<string | undefined> {
  try {
    // Try to get from VSCode Python extension
    const pythonExt = vscode.extensions.getExtension('ms-python.python');
    if (pythonExt) {
      if (!pythonExt.isActive) {
        await pythonExt.activate();
      }
      // Try the newer API first
      const execDetails = pythonExt.exports?.settings?.getExecutionDetails?.(
        vscode.workspace.workspaceFolders?.[0]?.uri
      );
      if (execDetails?.execCommand?.[0]) {
        return execDetails.execCommand[0];
      }
    }

    // Fall back to workspace settings
    const config = vscode.workspace.getConfiguration('python');
    const pythonPath = config.get<string>('defaultInterpreterPath');
    if (pythonPath && fs.existsSync(pythonPath)) {
      return pythonPath;
    }

    // Try common virtual env locations relative to workspace
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (workspaceFolder) {
      const venvCandidates = [
        path.join(workspaceFolder.uri.fsPath, '.venv', 'bin', 'python'),
        path.join(workspaceFolder.uri.fsPath, '.venv', 'Scripts', 'python.exe'),
        path.join(workspaceFolder.uri.fsPath, 'venv', 'bin', 'python'),
        path.join(workspaceFolder.uri.fsPath, 'venv', 'Scripts', 'python.exe'),
      ];
      for (const candidate of venvCandidates) {
        if (fs.existsSync(candidate)) {
          return candidate;
        }
      }
    }
  } catch (error) {
    output.appendLine(`[warn] Failed to get Python interpreter: ${error}`);
  }

  return undefined;
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

function formatKeyResolution(resolution: KeyResolution, programName: string): string {
  const lines: string[] = [
    `# Key Resolution: \`${resolution.key}\``,
    '',
    `**Program:** \`${programName}\``,
    '',
    `**Final Value:** ${resolution.finalValue !== null ? `\`${JSON.stringify(resolution.finalValue)}\`` : '`<dynamic>`'}`,
    ''
  ];

  if (resolution.chain.length > 0) {
    lines.push('## Override Chain');
    lines.push('');
    for (const entry of resolution.chain) {
      const valueStr = entry.value !== null ? JSON.stringify(entry.value) : '<dynamic>';
      const marker = entry.isOverridden ? '⚠️↓ overridden' : '★ final';
      lines.push(`- \`${entry.envQualifiedName}\`: ${valueStr} (${marker})`);
    }
    lines.push('');
  }

  if (resolution.runtimeValue !== undefined) {
    lines.push('## Runtime Value');
    lines.push('');
    lines.push(`\`\`\``);
    lines.push(JSON.stringify(resolution.runtimeValue, null, 2));
    lines.push(`\`\`\``);
  } else if (resolution.runtimeError) {
    lines.push('## Runtime Error');
    lines.push('');
    lines.push(`\`\`\``);
    lines.push(resolution.runtimeError);
    lines.push(`\`\`\``);
  }

  return lines.join('\n');
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

async function queryEnvChain(
  indexerPath: string,
  rootPath: string,
  programQualifiedName: string
): Promise<EnvChainEntry[]> {
  const args = [
    'find-env-chain',
    '--root', rootPath,
    '--program', programQualifiedName
  ];

  try {
    const stdout = await executeIndexer(indexerPath, args, rootPath);
    const result: EnvChainResult = JSON.parse(stdout);
    // Convert snake_case from Rust to camelCase for TypeScript
    return result.envChain?.map(entry => ({
      qualifiedName: entry.qualifiedName ?? (entry as unknown as Record<string, string>).qualified_name,
      filePath: entry.filePath ?? (entry as unknown as Record<string, string>).file_path,
      line: entry.line,
      keys: entry.keys ?? [],
      staticValues: entry.staticValues ?? (entry as unknown as Record<string, unknown>).static_values as Record<string, unknown> | undefined,
      isUserConfig: entry.isUserConfig ?? (entry as unknown as Record<string, boolean>).is_user_config
    })) ?? [];
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to query env chain';
    output.appendLine(`[error] queryEnvChain failed: ${message}`);
    return [];
  }
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
