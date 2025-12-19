import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as crypto from 'crypto';
import * as fs from 'fs';
import { promisify } from 'util';
import * as path from 'path';
import { parseGitWorktreeListPorcelain, type GitWorktreeInfo } from './worktrees';
import {
  type PlaylistItemV2,
  type PlaylistV2,
  type PlaylistsFileV2,
  formatBranchCommitTag,
  parsePlaylistsJsonV2,
  playlistArgsToDoeffRunArgs
} from './playlists';
import { multiTokenFuzzyMatch } from './search';

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

let uvAvailableCache: boolean | undefined;

async function isUvAvailable(): Promise<boolean> {
  if (uvAvailableCache !== undefined) {
    return uvAvailableCache;
  }
  try {
    await execFileAsync('uv', ['--version'], { maxBuffer: 1024 * 1024 });
    uvAvailableCache = true;
  } catch {
    uvAvailableCache = false;
  }
  return uvAvailableCache;
}

function findGitWorktreeRootSync(startPath: string): string | undefined {
  // Best-effort: walk upwards looking for a `.git` file/dir (worktree root).
  // This enables CodeLens + run actions for files opened outside the VSCode workspace.
  let current = startPath;
  try {
    if (fs.existsSync(startPath) && !fs.statSync(startPath).isDirectory()) {
      current = path.dirname(startPath);
    }
  } catch {
    current = path.dirname(startPath);
  }

  let parent = path.dirname(current);
  while (current !== parent) {
    const gitMarker = path.join(current, '.git');
    if (fs.existsSync(gitMarker)) {
      return current;
    }
    current = parent;
    parent = path.dirname(current);
  }

  // Check filesystem root once as well.
  if (fs.existsSync(path.join(current, '.git'))) {
    return current;
  }

  return undefined;
}

function resolveRootPathForUri(uri: vscode.Uri): string | undefined {
  const folder = vscode.workspace.getWorkspaceFolder(uri);
  if (folder) {
    return folder.uri.fsPath;
  }
  if (uri.scheme === 'file') {
    const gitRoot = findGitWorktreeRootSync(uri.fsPath);
    if (gitRoot) {
      return gitRoot;
    }
  }
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

// Build a descriptive name for run/debug sessions
// Format: <Run/Debug>-<entrypoint>-><kleisli>-><transform>
function buildSessionName(
  mode: 'Run' | 'Debug',
  programPath: string,
  kleisli?: string,
  transformer?: string,
  branch?: string
): string {
  // Extract just the entrypoint name from qualified path
  const entrypoint = programPath.split('.').pop() ?? programPath;
  const prefix = mode === 'Debug' ? 'd' : 'p';
  let name = `${prefix}_${entrypoint}`;

  if (kleisli) {
    const kleisliName = kleisli.split('.').pop() ?? kleisli;
    name += `->${kleisliName}`;
  }
  if (transformer) {
    const transformName = transformer.split('.').pop() ?? transformer;
    name += `->${transformName}`;
  }

  return branch ? `[${branch}](${name})` : name;
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

type RawEnvChainEntry = Partial<{
  qualifiedName: string;
  qualified_name: string;
  filePath: string;
  file_path: string;
  line: number;
  keys: string[];
  staticValues: Record<string, unknown> | null;
  static_values: Record<string, unknown> | null;
  isUserConfig: boolean;
  is_user_config: boolean;
}>;

type RawEnvChainResult = Partial<{
  program: string;
  envChain: RawEnvChainEntry[];
  env_chain: RawEnvChainEntry[];
}>;

interface EnvChainNode {
  type: 'envChain';
  rootPath: string;
  parentEntry: IndexEntry;
  entries: EnvChainEntry[];
}

interface EnvSourceNode {
  type: 'envSource';
  rootPath: string;
  entry: EnvChainEntry;
  parentEntry: IndexEntry;
  allEnvEntries: EnvChainEntry[]; // For override detection
}

interface EnvKeyNode {
  type: 'envKey';
  rootPath: string;
  key: string;
  value: unknown | null;
  isFinal: boolean;
  overriddenBy?: string;
  envEntry: EnvChainEntry;
  parentEntry: IndexEntry;
}

function createEnvChainTreeItem(node: EnvChainNode): vscode.TreeItem {
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

function createEnvSourceTreeItem(node: EnvSourceNode): vscode.TreeItem {
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

function createEnvKeyTreeItem(node: EnvKeyNode): vscode.TreeItem {
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

function getEnvKeyNodes(node: EnvSourceNode): EnvKeyNode[] {
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
      rootPath: node.rootPath,
      key,
      value,
      isFinal: !overriddenBy,
      overriddenBy,
      envEntry: node.entry,
      parentEntry: node.parentEntry
    };
  });
}

function modulePathFromQualifiedName(qualifiedName: string): string {
  const lastDot = qualifiedName.lastIndexOf('.');
  return lastDot >= 0 ? qualifiedName.slice(0, lastDot) : '';
}

function modulePrefixesForProgram(programQualifiedName: string): Set<string> {
  const programModule = modulePathFromQualifiedName(programQualifiedName);
  const parts = programModule.split('.').filter(Boolean);
  const prefixes = new Set<string>();

  if (parts.length === 0) {
    prefixes.add('');
    return prefixes;
  }

  for (let i = 1; i <= parts.length; i++) {
    prefixes.add(parts.slice(0, i).join('.'));
  }
  return prefixes;
}

function filterEnvChain(
  programQualifiedName: string,
  envChain: EnvChainEntry[],
  indexEntries: IndexEntry[]
): EnvChainEntry[] {
  if (envChain.length === 0) {
    return envChain;
  }

  const showUserConfig = vscode.workspace
    .getConfiguration()
    .get<boolean>('doeff-runner.envInspector.showUserConfig', true);

  const modulePrefixes = modulePrefixesForProgram(programQualifiedName);
  const byQualifiedName = new Map(indexEntries.map(entry => [entry.qualifiedName, entry]));

  return envChain.filter(entry => {
    if (entry.isUserConfig) {
      return showUserConfig;
    }

    const indexed = byQualifiedName.get(entry.qualifiedName);
    if (indexed && indexed.itemKind !== 'assignment') {
      return false;
    }

    const envModule = modulePathFromQualifiedName(entry.qualifiedName);
    return modulePrefixes.has(envModule);
  });
}

const ENV_CHAIN_CACHE_TTL_MS = 5000;
const envChainCache = new Map<string, CacheEntry<EnvChainEntry[]>>();

async function getEnvChainForRoot(rootPath: string, programQualifiedName: string): Promise<EnvChainEntry[]> {
  const cacheKey = `envchain:${rootPath}:${programQualifiedName}`;
  const cached = envChainCache.get(cacheKey);
  if (cached && Date.now() - cached.timestamp < ENV_CHAIN_CACHE_TTL_MS) {
    return cached.data;
  }

  const indexerPath = await locateIndexer();

  const [indexEntries, rawEnvChain] = await Promise.all([
    queryIndexer(indexerPath, `index:${rootPath}`, rootPath, ['index', '--root', rootPath]),
    queryEnvChain(indexerPath, rootPath, programQualifiedName)
  ]);

  const filtered = filterEnvChain(programQualifiedName, rawEnvChain, indexEntries);
  envChainCache.set(cacheKey, { timestamp: Date.now(), data: filtered });
  return filtered;
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
        return createEnvChainTreeItem(element);
      case 'envSource':
        return createEnvSourceTreeItem(element);
      case 'envKey':
        return createEnvKeyTreeItem(element);
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
    const uri = vscode.Uri.file(entry.filePath);
    item.iconPath = new vscode.ThemeIcon('symbol-function');
    item.contextValue = 'entrypoint';
    item.tooltip = entry.docstring
      ? `${entry.qualifiedName}\n\n${entry.docstring}`
      : entry.qualifiedName;
    item.description = defaultAction ? this.getActionLabel(defaultAction) : undefined;
    item.command = {
      command: 'vscode.open',
      title: 'Go to Definition',
      arguments: [
        uri,
        { selection: new vscode.Range(entry.line - 1, 0, entry.line - 1, 0) }
      ]
    };
    item.resourceUri = uri;
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
          rootPath: element.rootPath,
          entry,
          parentEntry: element.parentEntry,
          allEnvEntries: element.entries
        }));
      case 'envSource':
        return getEnvKeyNodes(element);
      case 'envKey':
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
          rootPath,
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
      await this.ensureIndexLoaded(rootPath);
      const indexerPath = await locateIndexer();
      const result = await queryEnvChain(indexerPath, rootPath, programQualifiedName);
      const filtered = filterEnvChain(programQualifiedName, result, this.indexCache);
      this.envChainCache.set(cacheKey, filtered);
      return filtered;
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

    if (document.uri.scheme !== 'file') {
      return lenses;
    }

    const rootPath = resolveRootPathForUri(document.uri);
    if (!rootPath) {
      return lenses;
    }

    // Use indexer output instead of regex-based detection
    const entries = this.getFileEntriesSync(rootPath, document.uri.fsPath);

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

      // Add to playlist button (leftmost)
      lenses.push(
        new vscode.CodeLens(range, {
          title: '[+]',
          tooltip: 'Add this Program to a Playlist',
          command: 'doeff-runner.addToPlaylist',
          arguments: [{
            entry,
            worktreePath: rootPath
          }]
        })
      );

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

      // Debug mode toggle button
      lenses.push(
        new vscode.CodeLens(range, {
          title: debugMode ? TOOL_PREFIX.toggleOn : TOOL_PREFIX.toggleOff,
          tooltip: debugMode ? 'Debug mode ON (click to switch to Run mode)' : 'Run mode (click to switch to Debug mode)',
          command: 'doeff-runner.toggleDebugMode',
          arguments: []
        })
      );

      // Show default action if set
      if (defaultAction) {
        lenses.push(this.createDefaultActionLensFromEntry(entry, range, defaultAction, document.uri, debugMode));
      }

      // Only show kleisli/transform tools if the Program has a type argument
      // Untyped Program shouldn't show tools since we don't know the output type
      if (typeArg) {
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

// =============================================================================
// VSCode 002: Worktree-aware playlists
// =============================================================================

interface ProgramTarget {
  branch: string;
  worktreePath: string;
  entry: IndexEntry;
}

function uuid(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }
}

function isProgramEntrypoint(entry: IndexEntry): boolean {
  return (
    entry.itemKind === 'assignment' &&
    entry.typeUsages.some((usage) => usage.kind === 'program')
  );
}

function extractProgramTypeArg(entry: IndexEntry): string {
  for (const usage of entry.typeUsages) {
    if (usage.kind === 'program' && usage.typeArguments.length > 0) {
      return usage.typeArguments[0];
    }
  }
  return '';
}

function shortQualifiedName(value: string): string {
  return value.split('.').pop() ?? value;
}

function formatPlaylistToolsTag(apply: string | null, transform: string | null): string {
  const parts: string[] = [];
  if (apply) {
    parts.push(`🔗 ${shortQualifiedName(apply)}`);
  }
  if (transform) {
    parts.push(`🔀 ${shortQualifiedName(transform)}`);
  }
  return parts.join(' ');
}

class DoeffPlaylistsStore implements vscode.Disposable {
  private readonly _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChange = this._onDidChange.event;

  private repoRootPromise: Promise<string> | undefined;
  private filePathPromise: Promise<string> | undefined;
  private cache: PlaylistsFileV2 | undefined;
  private watcher: fs.FSWatcher | undefined;
  private migrationAttempted = false;

  constructor(private workspacePath: string) { }

  dispose(): void {
    this.watcher?.close();
    this._onDidChange.dispose();
  }

  private async getRepoRoot(): Promise<string> {
    if (!this.repoRootPromise) {
      this.repoRootPromise = (async () => {
        const repoRoot = await resolveRepoRoot(this.workspacePath);
        return repoRoot ?? this.workspacePath;
      })();
    }
    return this.repoRootPromise;
  }

  async getFilePath(): Promise<string> {
    if (!this.filePathPromise) {
      this.filePathPromise = (async () => {
        const repoRoot = await this.getRepoRoot();
        const gitCommonDir = await resolveGitCommonDir(repoRoot);
        const filePath = gitCommonDir
          ? path.join(gitCommonDir, 'doeff', 'playlists.json')
          : path.join(repoRoot, '.vscode', 'doeff-runner.playlists.json');
        this.ensureWatcher(filePath);
        return filePath;
      })();
    }
    return this.filePathPromise;
  }

  private ensureWatcher(filePath: string): void {
    if (this.watcher) {
      return;
    }

    const dir = path.dirname(filePath);
    try {
      fs.mkdirSync(dir, { recursive: true });
    } catch (error) {
      output.appendLine(`[warn] Failed to create playlists dir ${dir}: ${String(error)}`);
    }

    try {
      this.watcher = fs.watch(dir, { persistent: false }, (_eventType, filename) => {
        if (!filename || filename.toString() === path.basename(filePath)) {
          this.cache = undefined;
          this._onDidChange.fire();
        }
      });
    } catch (error) {
      output.appendLine(`[warn] Failed to watch playlists dir ${dir}: ${String(error)}`);
    }
  }

  private empty(): PlaylistsFileV2 {
    return { version: 2, playlists: [] };
  }

  private writeFileSync(filePath: string, data: PlaylistsFileV2): void {
    const dir = path.dirname(filePath);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(filePath, JSON.stringify(data, null, 2) + '\n', 'utf8');
  }

  private async maybeMigrateV1(v2Path: string): Promise<void> {
    if (this.migrationAttempted) {
      return;
    }
    this.migrationAttempted = true;

    const repoRoot = await this.getRepoRoot();
    const v1Path = path.join(repoRoot, '.vscode', 'doeff-runner.playlists.json');
    if (path.resolve(v1Path) === path.resolve(v2Path)) {
      return;
    }
    if (!fs.existsSync(v1Path) || fs.existsSync(v2Path)) {
      return;
    }

    let raw: unknown;
    try {
      raw = JSON.parse(fs.readFileSync(v1Path, 'utf8')) as unknown;
    } catch (error) {
      output.appendLine(`[warn] Failed to parse v1 playlists JSON: ${String(error)}`);
      return;
    }

    const currentBranch = await resolveCurrentBranch(repoRoot);
    const defaultBranch = currentBranch ?? 'main';

    const playlistsRaw = Array.isArray((raw as any)?.playlists)
      ? (raw as any).playlists as unknown[]
      : Array.isArray(raw)
        ? raw as unknown[]
        : [];

    const migrated: PlaylistsFileV2 = { version: 2, playlists: [] };
    for (const pl of playlistsRaw) {
      const name = typeof (pl as any)?.name === 'string' ? (pl as any).name as string : 'Playlist';
      const itemsRaw = Array.isArray((pl as any)?.items) ? (pl as any).items as unknown[] : [];
      const items: PlaylistItemV2[] = [];

      for (const it of itemsRaw) {
        const program =
          typeof (it as any)?.program === 'string'
            ? (it as any).program as string
            : typeof (it as any)?.programPath === 'string'
              ? (it as any).programPath as string
              : undefined;
        if (!program) {
          continue;
        }

        const itemName =
          typeof (it as any)?.name === 'string'
            ? (it as any).name as string
            : program.split('.').pop() ?? program;

        const apply =
          typeof (it as any)?.apply === 'string'
            ? (it as any).apply as string
            : typeof (it as any)?.kleisli === 'string'
              ? (it as any).kleisli as string
              : null;
        const transform =
          typeof (it as any)?.transform === 'string'
            ? (it as any).transform as string
            : typeof (it as any)?.transformer === 'string'
              ? (it as any).transformer as string
              : null;

        items.push({
          id: uuid(),
          name: itemName,
          branch: defaultBranch,
          commit: null,
          program,
          apply,
          transform,
          args: {}
        });
      }

      migrated.playlists.push({ id: uuid(), name, items });
    }

    try {
      this.writeFileSync(v2Path, migrated);
      vscode.window.showInformationMessage('doeff-runner: Playlists migrated to v2 format');
      output.appendLine(`[info] Migrated playlists from ${v1Path} -> ${v2Path}`);
    } catch (error) {
      output.appendLine(`[warn] Failed to write migrated playlists: ${String(error)}`);
    }
  }

  async load(): Promise<PlaylistsFileV2> {
    if (this.cache) {
      return this.cache;
    }

    const filePath = await this.getFilePath();
    await this.maybeMigrateV1(filePath);

    if (!fs.existsSync(filePath)) {
      this.cache = this.empty();
      return this.cache;
    }

    const content = fs.readFileSync(filePath, 'utf8');
    const parsed = parsePlaylistsJsonV2(content);
    if (parsed.error) {
      output.appendLine(`[warn] Playlists file parse warning: ${parsed.error}`);
      vscode.window.showWarningMessage(`doeff-runner playlists: ${parsed.error}`);
    }
    this.cache = parsed.data;
    return parsed.data;
  }

  async save(data: PlaylistsFileV2): Promise<void> {
    const filePath = await this.getFilePath();
    this.writeFileSync(filePath, data);
    this.cache = data;
    this._onDidChange.fire();
  }

  async update(mutator: (data: PlaylistsFileV2) => void): Promise<PlaylistsFileV2> {
    const data = await this.load();
    mutator(data);
    await this.save(data);
    return data;
  }

  async ensureFileExists(): Promise<string> {
    const filePath = await this.getFilePath();
    if (!fs.existsSync(filePath)) {
      this.writeFileSync(filePath, this.empty());
    }
    return filePath;
  }
}

type PlaylistsTreeNode =
  | PlaylistNode
  | PlaylistBranchNode
  | PlaylistItemNode
  | PlaylistActionNode
  | EnvChainNode
  | EnvSourceNode
  | EnvKeyNode;

interface PlaylistNode {
  type: 'playlist';
  playlist: PlaylistV2;
  branches: PlaylistBranchNode[];
  parent?: undefined;
}

interface PlaylistBranchNode {
  type: 'playlistBranch';
  playlist: PlaylistNode;
  branch: string;
  isCurrent: boolean;
  items: PlaylistItemNode[];
}

interface PlaylistItemNode {
  type: 'playlistItem';
  playlist: PlaylistNode;
  branchNode: PlaylistBranchNode;
  item: PlaylistItemV2;
  actions: PlaylistActionNode[];
}

interface PlaylistActionNode {
  type: 'playlistAction';
  playlistItem: PlaylistItemNode;
  action: 'run' | 'edit' | 'remove';
}

class DoeffPlaylistsProvider implements vscode.TreeDataProvider<PlaylistsTreeNode>, vscode.Disposable {
  private _onDidChangeTreeData = new vscode.EventEmitter<PlaylistsTreeNode | undefined>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private tree: PlaylistNode[] = [];
  private nodeById = new Map<string, PlaylistsTreeNode>();
  private loaded = false;

  constructor(
    private store: DoeffPlaylistsStore,
    private stateStore: DoeffStateStore,
    private workspacePath: string
  ) {
    this.store.onDidChange(() => {
      this.loaded = false;
      this._onDidChangeTreeData.fire(undefined);
    });
  }

  dispose(): void {
    this._onDidChangeTreeData.dispose();
  }

  refresh(): void {
    this.loaded = false;
    this._onDidChangeTreeData.fire(undefined);
  }

  getParent(element: PlaylistsTreeNode): PlaylistsTreeNode | undefined {
    if (element.type === 'playlist') {
      return undefined;
    }
    if (element.type === 'playlistBranch') {
      return element.playlist;
    }
    if (element.type === 'playlistItem') {
      return element.branchNode;
    }
    if (element.type === 'playlistAction') {
      return element.playlistItem;
    }
    return undefined;
  }

  private async ensureLoaded(): Promise<void> {
    if (this.loaded) {
      return;
    }

    const data = await this.store.load();
    const repoRoot = await resolveRepoRoot(this.workspacePath) ?? this.workspacePath;
    const currentBranch = repoRoot ? await resolveCurrentBranch(repoRoot) : undefined;
    this.nodeById.clear();

    this.tree = data.playlists
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((playlist) => {
        const playlistNode: PlaylistNode = {
          type: 'playlist',
          playlist,
          branches: []
        };
        this.nodeById.set(`playlist:${playlist.id}`, playlistNode);

        const branchesByName = new Map<string, PlaylistBranchNode>();

        const ensureBranchNode = (branch: string): PlaylistBranchNode => {
          const existing = branchesByName.get(branch);
          if (existing) {
            return existing;
          }
          const node: PlaylistBranchNode = {
            type: 'playlistBranch',
            playlist: playlistNode,
            branch,
            isCurrent: currentBranch === branch,
            items: []
          };
          branchesByName.set(branch, node);
          this.nodeById.set(`branch:${playlist.id}:${branch}`, node);
          return node;
        };

        for (const item of playlist.items) {
          const branchNode = ensureBranchNode(item.branch);
          const itemNode: PlaylistItemNode = {
            type: 'playlistItem',
            playlist: playlistNode,
            branchNode,
            item,
            actions: []
          };
          this.nodeById.set(`item:${playlist.id}:${item.id}`, itemNode);
          itemNode.actions = (['run', 'edit', 'remove'] as const).map((action) => {
            const actionNode: PlaylistActionNode = {
              type: 'playlistAction',
              playlistItem: itemNode,
              action
            };
            this.nodeById.set(`action:${playlist.id}:${item.id}:${action}`, actionNode);
            return actionNode;
          });
          branchNode.items.push(itemNode);
        }

        playlistNode.branches = Array.from(branchesByName.values()).sort((a, b) => {
          if (a.isCurrent && !b.isCurrent) return -1;
          if (!a.isCurrent && b.isCurrent) return 1;
          return a.branch.localeCompare(b.branch);
        });

        return playlistNode;
      });

    this.loaded = true;
  }

  getTreeItem(element: PlaylistsTreeNode): vscode.TreeItem {
    switch (element.type) {
      case 'playlist': {
        const item = new vscode.TreeItem(element.playlist.name, vscode.TreeItemCollapsibleState.Expanded);
        item.contextValue = 'playlist';
        item.iconPath = new vscode.ThemeIcon('list-unordered');
        item.id = `playlist:${element.playlist.id}`;
        item.description = `${element.playlist.items.length} item(s)`;
        return item;
      }
      case 'playlistBranch': {
        const label = element.isCurrent ? `${element.branch} ✓ current` : element.branch;
        const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.Expanded);
        item.contextValue = 'playlistBranch';
        item.iconPath = new vscode.ThemeIcon('git-branch');
        item.id = `branch:${element.playlist.playlist.id}:${element.branch}`;
        item.description = `${element.items.length} item(s)`;
        return item;
      }
      case 'playlistItem': {
        const commitTag = element.item.commit ? `@ ${element.item.commit.slice(0, 6)}` : '';
        const toolsTag = formatPlaylistToolsTag(element.item.apply, element.item.transform);
        const itemLabel = element.item.name;
        const item = new vscode.TreeItem(itemLabel, vscode.TreeItemCollapsibleState.Collapsed);
        item.contextValue = 'playlistItem';
        item.iconPath = new vscode.ThemeIcon('symbol-function');
        item.id = `item:${element.playlist.playlist.id}:${element.item.id}`;
        item.description = [commitTag, toolsTag].filter(Boolean).join(' ');
        item.tooltip = [
          element.item.program,
          element.item.apply ? `apply: ${element.item.apply}` : undefined,
          element.item.transform ? `transform: ${element.item.transform}` : undefined
        ].filter(Boolean).join('\n');
        item.command = {
          command: 'doeff-runner.revealPlaylistItem',
          title: 'Go to Definition',
          arguments: [element.playlist.playlist.id, element.item.id]
        };
        return item;
      }
      case 'playlistAction': {
        const debugMode = this.stateStore.getDebugMode();
        const label =
          element.action === 'run'
            ? (debugMode ? '🐛 Debug' : '▶ Run')
            : element.action === 'edit'
              ? '✎ Edit'
              : '✕ Remove';
        const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.None);
        item.contextValue = 'playlistAction';
        item.id = `action:${element.playlistItem.playlist.playlist.id}:${element.playlistItem.item.id}:${element.action}`;

        if (element.action === 'run') {
          item.iconPath = new vscode.ThemeIcon(debugMode ? 'debug-start' : 'play');
          item.command = {
            command: 'doeff-runner.runPlaylistItem',
            title: 'Run Playlist Item',
            arguments: [element.playlistItem.playlist.playlist.id, element.playlistItem.item.id]
          };
        } else if (element.action === 'edit') {
          item.iconPath = new vscode.ThemeIcon('edit');
          item.command = {
            command: 'doeff-runner.editPlaylistItem',
            title: 'Edit Playlist Item',
            arguments: [element.playlistItem.playlist.playlist.id, element.playlistItem.item.id]
          };
        } else {
          item.iconPath = new vscode.ThemeIcon('trash');
          item.command = {
            command: 'doeff-runner.removePlaylistItem',
            title: 'Remove Playlist Item',
            arguments: [element.playlistItem.playlist.playlist.id, element.playlistItem.item.id]
          };
        }
        return item;
      }
      case 'envChain':
        return createEnvChainTreeItem(element);
      case 'envSource':
        return createEnvSourceTreeItem(element);
      case 'envKey':
        return createEnvKeyTreeItem(element);
    }
  }

  async getChildren(element?: PlaylistsTreeNode): Promise<PlaylistsTreeNode[]> {
    await this.ensureLoaded();

    if (!element) {
      return this.tree;
    }
    if (element.type === 'playlist') {
      return element.branches;
    }
    if (element.type === 'playlistBranch') {
      return element.items;
    }
    if (element.type === 'playlistItem') {
      const children: PlaylistsTreeNode[] = [...element.actions];
      try {
        const repoRoot = await resolveRepoRoot(this.workspacePath) ?? this.workspacePath;
        if (repoRoot) {
          const stdout = await executeGit(['worktree', 'list', '--porcelain'], repoRoot);
          const worktrees = parseGitWorktreeListPorcelain(stdout);
          const worktreePath = worktrees.find((wt) => wt.branch === element.item.branch)?.worktreePath;
          if (worktreePath) {
            const envChain = await getEnvChainForRoot(worktreePath, element.item.program);
            const parentEntry: IndexEntry = {
              name: element.item.program.split('.').pop() ?? element.item.program,
              qualifiedName: element.item.program,
              filePath: '',
              line: 0,
              itemKind: 'assignment',
              categories: [],
              programParameters: [],
              interpreterParameters: [],
              typeUsages: []
            };

            children.push({
              type: 'envChain',
              rootPath: worktreePath,
              parentEntry,
              entries: envChain
            });
          }
        }
      } catch (error) {
        output.appendLine(`[warn] Failed to load env chain for playlist item: ${String(error)}`);
      }
      return children;
    }
    if (element.type === 'envChain') {
      return element.entries.map((entry) => ({
        type: 'envSource' as const,
        rootPath: element.rootPath,
        entry,
        parentEntry: element.parentEntry,
        allEnvEntries: element.entries
      }));
    }
    if (element.type === 'envSource') {
      return getEnvKeyNodes(element);
    }
    return [];
  }

  getPlaylistNode(playlistId: string): PlaylistNode | undefined {
    const node = this.nodeById.get(`playlist:${playlistId}`);
    return node?.type === 'playlist' ? node : undefined;
  }

  getPlaylistItemNode(playlistId: string, itemId: string): PlaylistItemNode | undefined {
    const node = this.nodeById.get(`item:${playlistId}:${itemId}`);
    return node?.type === 'playlistItem' ? node : undefined;
  }
}

type WorktreesProgramsNode =
  | WorktreeBranchNode
  | WorktreeModuleNode
  | WorktreeProgramNode
  | EnvChainNode
  | EnvSourceNode
  | EnvKeyNode;

interface WorktreeBranchNode {
  type: 'wtBranch';
  branch: string;
  worktreePath: string;
  head: string;
  isCurrent: boolean;
  modules: WorktreeModuleNode[];
  parent?: undefined;
}

interface WorktreeModuleNode {
  type: 'wtModule';
  branch: WorktreeBranchNode;
  modulePath: string;
  programs: WorktreeProgramNode[];
}

interface WorktreeProgramNode {
  type: 'wtProgram';
  module: WorktreeModuleNode;
  entry: IndexEntry;
}

class DoeffWorktreeProgramsProvider implements vscode.TreeDataProvider<WorktreesProgramsNode>, vscode.Disposable {
  private _onDidChangeTreeData = new vscode.EventEmitter<WorktreesProgramsNode | undefined>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private branches: WorktreeBranchNode[] = [];
  private nodeByKey = new Map<string, WorktreesProgramsNode>();
  private cacheTimestamp = 0;
  private refreshing = false;
  private readonly CACHE_TTL_MS = 30000;

  constructor(private workspacePath: string) { }

  dispose(): void {
    this._onDidChangeTreeData.dispose();
  }

  refresh(): void {
    this.cacheTimestamp = 0;
    this.branches = [];
    this.nodeByKey.clear();
    this._onDidChangeTreeData.fire(undefined);
  }

  getParent(element: WorktreesProgramsNode): WorktreesProgramsNode | undefined {
    if (element.type === 'wtBranch') {
      return undefined;
    }
    if (element.type === 'wtModule') {
      return element.branch;
    }
    if (element.type === 'wtProgram') {
      return element.module;
    }
    return undefined;
  }

  private async ensureLoaded(): Promise<void> {
    if (this.refreshing) {
      return;
    }
    if (this.cacheTimestamp && Date.now() - this.cacheTimestamp < this.CACHE_TTL_MS) {
      return;
    }

    this.refreshing = true;
    try {
      const indexerPath = await locateIndexer();
      const repoRoot = await resolveRepoRoot(this.workspacePath) ?? this.workspacePath;
      const stdout = await executeGit(['worktree', 'list', '--porcelain'], repoRoot);
      const worktrees = parseGitWorktreeListPorcelain(stdout)
        .filter((wt) => wt.branch);

      const currentResolved = path.resolve(this.workspacePath);

      const byBranch = new Map<string, GitWorktreeInfo>();
      for (const wt of worktrees) {
        if (wt.branch) {
          byBranch.set(wt.branch, wt);
        }
      }

      const branchNames = Array.from(byBranch.keys()).sort((a, b) => a.localeCompare(b));

      const branchNodes: WorktreeBranchNode[] = [];
      const nodeByKey = new Map<string, WorktreesProgramsNode>();

      await Promise.all(branchNames.map(async (branchName) => {
        const wt = byBranch.get(branchName);
        if (!wt) {
          return;
        }

        const worktreePath = wt.worktreePath;
        const isCurrent = path.resolve(worktreePath) === currentResolved;

        const entries = await queryIndexer(
          indexerPath,
          `index:${worktreePath}`,
          worktreePath,
          ['index', '--root', worktreePath]
        );
        const programs = entries.filter(isProgramEntrypoint);

        const grouped = new Map<string, IndexEntry[]>();
        for (const entry of programs) {
          const modulePath = entry.qualifiedName.split('.').slice(0, -1).join('.');
          const existing = grouped.get(modulePath) ?? [];
          existing.push(entry);
          grouped.set(modulePath, existing);
        }

        const branchNode: WorktreeBranchNode = {
          type: 'wtBranch',
          branch: branchName,
          worktreePath,
          head: wt.head,
          isCurrent,
          modules: []
        };

        nodeByKey.set(`branch:${branchName}`, branchNode);

        const moduleNodes = Array.from(grouped.entries())
          .map(([modulePath, moduleEntries]) => {
            const moduleNode: WorktreeModuleNode = {
              type: 'wtModule',
              branch: branchNode,
              modulePath: modulePath || '(root)',
              programs: []
            };
            nodeByKey.set(`module:${branchName}:${modulePath}`, moduleNode);

            moduleNode.programs = moduleEntries
              .slice()
              .sort((a, b) => a.name.localeCompare(b.name))
              .map((entry) => {
                const programNode: WorktreeProgramNode = {
                  type: 'wtProgram',
                  module: moduleNode,
                  entry
                };
                nodeByKey.set(`program:${branchName}:${entry.qualifiedName}`, programNode);
                return programNode;
              });
            return moduleNode;
          })
          .sort((a, b) => a.modulePath.localeCompare(b.modulePath));

        branchNode.modules = moduleNodes;
        branchNodes.push(branchNode);
      }));

      branchNodes.sort((a, b) => {
        if (a.isCurrent && !b.isCurrent) return -1;
        if (!a.isCurrent && b.isCurrent) return 1;
        return a.branch.localeCompare(b.branch);
      });

      this.branches = branchNodes;
      this.nodeByKey = nodeByKey;
      this.cacheTimestamp = Date.now();
    } catch (error) {
      output.appendLine(`[warn] Failed to load worktrees programs: ${String(error)}`);
      this.branches = [];
      this.nodeByKey.clear();
    } finally {
      this.refreshing = false;
    }
  }

  async getProgramTargets(): Promise<ProgramTarget[]> {
    await this.ensureLoaded();
    const targets: ProgramTarget[] = [];
    for (const branch of this.branches) {
      for (const mod of branch.modules) {
        for (const prog of mod.programs) {
          targets.push({
            branch: branch.branch,
            worktreePath: branch.worktreePath,
            entry: prog.entry
          });
        }
      }
    }
    return targets;
  }

  getProgramNode(branch: string, qualifiedName: string): WorktreeProgramNode | undefined {
    const node = this.nodeByKey.get(`program:${branch}:${qualifiedName}`);
    return node?.type === 'wtProgram' ? node : undefined;
  }

  getTreeItem(element: WorktreesProgramsNode): vscode.TreeItem {
    switch (element.type) {
      case 'wtBranch': {
        const label = element.isCurrent ? `${element.branch} ✓ current` : element.branch;
        const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.Expanded);
        item.contextValue = 'worktreeBranch';
        item.iconPath = new vscode.ThemeIcon('git-branch');
        item.id = `branch:${element.branch}`;
        return item;
      }
      case 'wtModule': {
        const item = new vscode.TreeItem(element.modulePath, vscode.TreeItemCollapsibleState.Expanded);
        item.contextValue = 'worktreeModule';
        item.iconPath = new vscode.ThemeIcon('folder');
        item.id = `module:${element.branch.branch}:${element.modulePath}`;
        return item;
      }
      case 'wtProgram': {
        const typeArg = extractProgramTypeArg(element.entry);
        const label = typeArg ? `${element.entry.name}: Program[${typeArg}]` : element.entry.name;
        const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.Collapsed);
        const uri = vscode.Uri.file(element.entry.filePath);
        item.contextValue = 'worktreeEntrypoint';
        item.iconPath = new vscode.ThemeIcon('symbol-function');
        item.id = `program:${element.module.branch.branch}:${element.entry.qualifiedName}`;
        item.tooltip = element.entry.docstring
          ? `${element.entry.qualifiedName}\n\n${element.entry.docstring}`
          : element.entry.qualifiedName;
        item.command = {
          command: 'vscode.open',
          title: 'Go to Definition',
          arguments: [
            uri,
            { selection: new vscode.Range(element.entry.line - 1, 0, element.entry.line - 1, 0) }
          ]
        };
        item.resourceUri = uri;
        return item;
      }
      case 'envChain':
        return createEnvChainTreeItem(element);
      case 'envSource':
        return createEnvSourceTreeItem(element);
      case 'envKey':
        return createEnvKeyTreeItem(element);
    }
  }

  async getChildren(element?: WorktreesProgramsNode): Promise<WorktreesProgramsNode[]> {
    await this.ensureLoaded();
    if (!element) {
      return this.branches;
    }
    if (element.type === 'wtBranch') {
      return element.modules;
    }
    if (element.type === 'wtModule') {
      return element.programs;
    }
    if (element.type === 'wtProgram') {
      const rootPath = element.module.branch.worktreePath;
      const envChain = await getEnvChainForRoot(rootPath, element.entry.qualifiedName);
      return [{
        type: 'envChain',
        rootPath,
        parentEntry: element.entry,
        entries: envChain
      }];
    }
    if (element.type === 'envChain') {
      return element.entries.map((entry) => ({
        type: 'envSource' as const,
        rootPath: element.rootPath,
        entry,
        parentEntry: element.parentEntry,
        allEnvEntries: element.entries
      }));
    }
    if (element.type === 'envSource') {
      return getEnvKeyNodes(element);
    }
    return [];
  }
}

export function activate(context: vscode.ExtensionContext) {
  output.appendLine('doeff-runner activated');

  // Store extension context for bundled binary access
  extensionContext = context;

  // Create state store for sharing state between TreeView and CodeLens
  const stateStore = new DoeffStateStore(context);

  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';

  // Create providers
  const codeLensProvider = new ProgramCodeLensProvider(stateStore);
  const treeProvider = new DoeffProgramsProvider(stateStore);
  const worktreeProgramsProvider = new DoeffWorktreeProgramsProvider(workspaceRoot);
  const playlistsStore = new DoeffPlaylistsStore(workspaceRoot);
  const playlistsProvider = new DoeffPlaylistsProvider(playlistsStore, stateStore, workspaceRoot);

  // Create TreeView
  const treeView = vscode.window.createTreeView('doeff-programs', {
    treeDataProvider: treeProvider,
    showCollapseAll: true
  });
  const worktreesTreeView = vscode.window.createTreeView('doeff-programs-all', {
    treeDataProvider: worktreeProgramsProvider,
    showCollapseAll: true
  });
  const playlistsTreeView = vscode.window.createTreeView('doeff-playlists', {
    treeDataProvider: playlistsProvider,
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

    worktreesTreeView.message = `${modeIndicator} mode`;
    playlistsTreeView.message = `${modeIndicator} mode`;
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
    playlistsProvider.refresh();
    updateTreeViewMessage();
  });

  // File watcher for auto-refresh
  const fileWatcher = vscode.workspace.createFileSystemWatcher('**/*.py');
  fileWatcher.onDidChange(uri => {
    treeProvider.invalidateFile(uri.fsPath);
    worktreeProgramsProvider.refresh();
  });
  fileWatcher.onDidCreate(uri => {
    treeProvider.invalidateFile(uri.fsPath);
    worktreeProgramsProvider.refresh();
  });
  fileWatcher.onDidDelete(uri => {
    treeProvider.invalidateFile(uri.fsPath);
    worktreeProgramsProvider.refresh();
  });

  async function pickProgramTarget(): Promise<ProgramTarget | undefined> {
    const targets = await worktreeProgramsProvider.getProgramTargets();
    if (targets.length === 0) {
      vscode.window.showInformationMessage('No Programs found across worktrees.');
      return undefined;
    }

    const grouped = new Map<string, ProgramTarget[]>();
    for (const target of targets) {
      const existing = grouped.get(target.branch) ?? [];
      existing.push(target);
      grouped.set(target.branch, existing);
    }

    interface ProgramQuickPickItem extends vscode.QuickPickItem {
      target?: ProgramTarget;
      searchText?: string;
    }

    const branchGroups = Array.from(grouped.keys())
      .sort((a, b) => a.localeCompare(b))
      .map((branch) => {
        const branchTargets = (grouped.get(branch) ?? [])
          .slice()
          .sort((a, b) => a.entry.name.localeCompare(b.entry.name));
        const items = branchTargets.map((target): ProgramQuickPickItem => {
          const modulePath = target.entry.qualifiedName.split('.').slice(0, -1).join('.');
          return {
            label: target.entry.name,
            description: modulePath,
            detail: `[${branch}]`,
            target,
            searchText: `${target.entry.name} ${target.entry.qualifiedName} ${modulePath} ${branch}`
          };
        });
        return { branch, items };
      });

    const buildItems = (query: string): ProgramQuickPickItem[] => {
      const flattened: ProgramQuickPickItem[] = [];
      for (const group of branchGroups) {
        const filtered = query
          ? group.items.filter((item) => multiTokenFuzzyMatch(query, item.searchText ?? item.label))
          : group.items;
        if (filtered.length === 0) {
          continue;
        }
        flattened.push({ label: group.branch, kind: vscode.QuickPickItemKind.Separator });
        flattened.push(...filtered);
      }
      return flattened;
    };

    return await new Promise<ProgramTarget | undefined>((resolve) => {
      const qp = vscode.window.createQuickPick<ProgramQuickPickItem>();
      qp.title = 'Pick a program';
      qp.placeholder = 'Search (supports multi-token: "abc fg")';

      const refreshItems = () => {
        qp.items = buildItems(qp.value);
      };

      refreshItems();
      qp.onDidChangeValue(refreshItems);
      qp.onDidAccept(() => {
        const selection = qp.selectedItems[0];
        if (selection?.target) {
          resolve(selection.target);
        } else {
          resolve(undefined);
        }
        qp.hide();
      });
      qp.onDidHide(() => {
        resolve(undefined);
        qp.dispose();
      });

      qp.show();
    });
  }

  async function revealInWorktreesTree(target: ProgramTarget): Promise<void> {
    const node = worktreeProgramsProvider.getProgramNode(target.branch, target.entry.qualifiedName);
    if (node) {
      await worktreesTreeView.reveal(node, { select: true, focus: true, expand: true });
    }
    await vscode.commands.executeCommand('doeff-runner.revealEntrypoint', target.entry);
  }

  async function pickPlaylistId(): Promise<string | undefined> {
    const data = await playlistsStore.load();

    interface PlaylistQuickPickItem extends vscode.QuickPickItem {
      playlistId?: string;
      isCreate?: boolean;
    }

    const playlists = data.playlists.slice().sort((a, b) => a.name.localeCompare(b.name));
    const items: PlaylistQuickPickItem[] = [];

    // Default selection should be an existing playlist (if any), not "create new".
    for (const playlist of playlists) {
      items.push({
        label: playlist.name,
        description: `${playlist.items.length} item(s)`,
        playlistId: playlist.id
      });
    }
    if (items.length > 0) {
      items.push({ label: 'Actions', kind: vscode.QuickPickItemKind.Separator });
    }
    items.push({ label: 'Create new playlist...', isCreate: true });

    const selected = await vscode.window.showQuickPick(items, { title: 'Select playlist' });
    if (!selected) {
      return undefined;
    }
    if (selected.isCreate) {
      const name = await vscode.window.showInputBox({
        prompt: 'New playlist name',
        placeHolder: 'e.g., Auth Experiments'
      });
      if (!name?.trim()) {
        return undefined;
      }
      const id = uuid();
      await playlistsStore.update((payload) => {
        payload.playlists.push({ id, name: name.trim(), items: [] });
      });
      return id;
    }
    return selected.playlistId;
  }

  async function pickPlaylistItemIds(
    title: string
  ): Promise<{ playlistId: string; itemId: string } | undefined> {
    const data = await playlistsStore.load();

    interface PlaylistItemQuickPickItem extends vscode.QuickPickItem {
      playlistId?: string;
      itemId?: string;
      searchText?: string;
    }

    const playlistGroups = data.playlists
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((playlist) => {
        const items: PlaylistItemQuickPickItem[] = playlist.items.map((item) => {
          const programName = item.program.split('.').pop() ?? item.program;
          const detail = [
            formatBranchCommitTag(item.branch, item.commit),
            formatPlaylistToolsTag(item.apply, item.transform)
          ].filter(Boolean).join(' ');

          return {
            label: item.name,
            description: programName,
            detail,
            playlistId: playlist.id,
            itemId: item.id,
            searchText: [
              item.name,
              item.program,
              programName,
              item.branch,
              item.commit ?? '',
              item.apply ?? '',
              item.transform ?? ''
            ].join(' ')
          };
        });
        return { name: playlist.name, items };
      });

    const buildItems = (query: string): PlaylistItemQuickPickItem[] => {
      const flattened: PlaylistItemQuickPickItem[] = [];
      for (const group of playlistGroups) {
        const filtered = query
          ? group.items.filter((item) => multiTokenFuzzyMatch(query, item.searchText ?? item.label))
          : group.items;
        if (filtered.length === 0) {
          continue;
        }
        flattened.push({ label: group.name, kind: vscode.QuickPickItemKind.Separator });
        flattened.push(...filtered);
      }
      return flattened;
    };

    const selected = await new Promise<PlaylistItemQuickPickItem | undefined>((resolve) => {
      const qp = vscode.window.createQuickPick<PlaylistItemQuickPickItem>();
      qp.title = title;
      qp.placeholder = 'Search (supports multi-token: "abc fg")';

      const refreshItems = () => {
        qp.items = buildItems(qp.value);
      };

      refreshItems();
      qp.onDidChangeValue(refreshItems);
      qp.onDidAccept(() => {
        resolve(qp.selectedItems[0]);
        qp.hide();
      });
      qp.onDidHide(() => {
        resolve(undefined);
        qp.dispose();
      });
      qp.show();
    });

    if (!selected?.playlistId || !selected.itemId) {
      return undefined;
    }
    return { playlistId: selected.playlistId, itemId: selected.itemId };
  }

  function resolvePlaylistItemIds(
    arg1?: unknown,
    arg2?: unknown
  ): { playlistId: string; itemId: string } | undefined {
    if (typeof arg1 === 'string' && typeof arg2 === 'string') {
      return { playlistId: arg1, itemId: arg2 };
    }

    if (arg1 && typeof arg1 === 'object') {
      const maybeNode = arg1 as Partial<PlaylistItemNode>;
      if (
        maybeNode.type === 'playlistItem' &&
        maybeNode.playlist &&
        maybeNode.item &&
        typeof maybeNode.playlist.playlist?.id === 'string' &&
        typeof maybeNode.item.id === 'string'
      ) {
        return { playlistId: maybeNode.playlist.playlist.id, itemId: maybeNode.item.id };
      }
    }

    return undefined;
  }

  async function addToPlaylistFlow(initial?: ProgramTarget): Promise<void> {
    const repoRoot = await resolveRepoRoot(workspaceRoot) ?? workspaceRoot;
    if (!repoRoot) {
      vscode.window.showErrorMessage('No workspace folder open.');
      return;
    }

    const target = initial ?? await pickProgramTarget();
    if (!target) {
      return;
    }

    const programQualifiedName = target.entry.qualifiedName;

    const currentBranch = await resolveCurrentBranch(repoRoot);
    const branches = await listLocalBranches(repoRoot);
    const defaultBranch = target.branch || currentBranch || branches[0] || 'main';

    const indexedTargets = await worktreeProgramsProvider.getProgramTargets();
    const branchesWithEntrypoint = new Set(
      indexedTargets
        .filter((t) => t.entry.qualifiedName === programQualifiedName)
        .map((t) => t.branch)
    );

    interface BranchQuickPickItem extends vscode.QuickPickItem {
      branch?: string;
      showAll?: boolean;
    }

    let branch: string | undefined;

    if (branchesWithEntrypoint.size > 0) {
      const verified = Array.from(branchesWithEntrypoint.values()).sort((a, b) => a.localeCompare(b));
      if (verified.includes(defaultBranch)) {
        verified.splice(verified.indexOf(defaultBranch), 1);
        verified.unshift(defaultBranch);
      }

      const branchItems: BranchQuickPickItem[] = [
        { label: 'Branches with this entrypoint', kind: vscode.QuickPickItemKind.Separator },
        ...verified.map((b) => ({
          label: b,
          description: currentBranch === b ? 'current' : undefined,
          branch: b
        })),
        { label: 'Other branches...', description: 'Show all local branches', showAll: true }
      ];

      const selected = await vscode.window.showQuickPick(branchItems, { title: 'Target branch' });
      if (!selected) {
        return;
      }

      if (selected.showAll) {
        const allBranchItems: BranchQuickPickItem[] = [];
        const seen = new Set<string>();

        const addBranchItem = (b: string) => {
          if (seen.has(b)) return;
          seen.add(b);
          allBranchItems.push({
            label: b,
            description: branchesWithEntrypoint.has(b) ? 'indexed' : 'unverified',
            branch: b
          });
        };

        addBranchItem(defaultBranch);
        for (const b of branches) {
          addBranchItem(b);
        }

        const chosen = await vscode.window.showQuickPick(allBranchItems, {
          title: 'Target branch (unverified branches may fail)'
        });
        branch = chosen?.branch;
      } else {
        branch = selected.branch;
      }
    } else {
      const branchItems: BranchQuickPickItem[] = [];
      const seen = new Set<string>();

      const addBranchItem = (b: string) => {
        if (seen.has(b)) return;
        seen.add(b);
        const isCurrent = currentBranch === b;
        branchItems.push({
          label: b,
          description: isCurrent ? 'current' : undefined,
          branch: b
        });
      };

      addBranchItem(defaultBranch);
      for (const b of branches) {
        addBranchItem(b);
      }

      const selected = await vscode.window.showQuickPick(branchItems, { title: 'Target branch' });
      branch = selected?.branch;
    }

    if (!branch) {
      return;
    }

    const worktreePath = await ensureWorktreeForBranch(repoRoot, branch);
    if (!worktreePath) {
      return;
    }

    const indexerPath = await locateIndexer();
    const programEntry =
      branch === target.branch && path.resolve(worktreePath) === path.resolve(target.worktreePath)
        ? target.entry
        : await findProgramByQualifiedName(indexerPath, worktreePath, programQualifiedName);

    if (!programEntry) {
      vscode.window.showErrorMessage(`Program '${programQualifiedName}' not found in branch '${branch}'.`);
      return;
    }

    const pinnedCommit = await promptPinnedCommit(repoRoot, branch);
    if (pinnedCommit === undefined) {
      return;
    }

    const playlistId = await pickPlaylistId();
    if (!playlistId) {
      return;
    }

    const defaultName = programEntry.name;

    const nameInput = await vscode.window.showInputBox({
      prompt: 'Playlist item name (optional)',
      value: defaultName,
      placeHolder: 'e.g., Login v1.0 baseline'
    });
    if (nameInput === undefined) {
      return;
    }
    const itemName = nameInput.trim() || defaultName;

    const newItem: PlaylistItemV2 = {
      id: uuid(),
      name: itemName,
      branch,
      commit: pinnedCommit,
      program: programQualifiedName,
      apply: null,
      transform: null,
      args: {}
    };

    await playlistsStore.update((payload) => {
      const playlist = payload.playlists.find((p) => p.id === playlistId);
      if (!playlist) {
        payload.playlists.push({ id: playlistId, name: 'Playlist', items: [newItem] });
        return;
      }
      playlist.items.push(newItem);
    });

    vscode.window.showInformationMessage(`Added '${programEntry.name}' to playlist.`);
  }

  async function runPlaylistItemFlow(playlistId: string, itemId: string): Promise<void> {
    const data = await playlistsStore.load();
    const playlist = data.playlists.find((p) => p.id === playlistId);
    const item = playlist?.items.find((i) => i.id === itemId);
    if (!playlist || !item) {
      vscode.window.showErrorMessage('Playlist item not found.');
      return;
    }

    const repoRoot = await resolveRepoRoot(workspaceRoot) ?? workspaceRoot;
    if (!repoRoot) {
      vscode.window.showErrorMessage('No workspace folder open.');
      return;
    }

    const branchWorktreePath = await ensureWorktreeForBranch(repoRoot, item.branch);
    if (!branchWorktreePath) {
      return;
    }

    let runCwd = branchWorktreePath;
    if (item.commit) {
      const head = await executeGit(['rev-parse', 'HEAD'], branchWorktreePath);
      if (head.trim() !== item.commit.trim()) {
        const choice = await vscode.window.showWarningMessage(
          `Worktree is at ${head.slice(0, 6)}, but item is pinned to ${item.commit.slice(0, 6)}.`,
          { modal: true },
          'Run at current HEAD',
          `Create temp worktree at ${item.commit.slice(0, 6)}`,
          'Cancel'
        );
        if (choice === 'Cancel' || !choice) {
          return;
        }
        if (choice.startsWith('Create temp')) {
          const temp = await ensureDetachedWorktreeAtCommit(repoRoot, item.branch, item.commit);
          if (!temp) {
            return;
          }
          runCwd = temp;
        }
      }
    }

    const indexerPath = await locateIndexer();
    const programEntry = await findProgramByQualifiedName(indexerPath, runCwd, item.program);
    if (!programEntry) {
      vscode.window.showErrorMessage(
        `Program '${item.program}' not found in branch '${item.branch}'.`
      );
      return;
    }

    const proximity: ProximityContext = { filePath: programEntry.filePath, line: programEntry.line };
    const interpreters = await fetchEntries(indexerPath, runCwd, 'find-interpreters', '', proximity);
    if (interpreters.length === 0) {
      vscode.window.showErrorMessage('No interpreters found in target worktree.');
      return;
    }

    const programTypeArg = extractProgramTypeArg(programEntry);
    let kleisli: IndexEntry | undefined;
    if (item.apply) {
      const kleisliEntries = await fetchEntries(indexerPath, runCwd, 'find-kleisli', programTypeArg, proximity);
      kleisli = kleisliEntries.find((e) => e.qualifiedName === item.apply);
      if (!kleisli) {
        vscode.window.showErrorMessage(`Kleisli '${item.apply}' not found in branch '${item.branch}'.`);
        return;
      }
    }

    let transformer: IndexEntry | undefined;
    if (item.transform) {
      const transformEntries = await fetchEntries(indexerPath, runCwd, 'find-transforms', programTypeArg, proximity);
      transformer = transformEntries.find((e) => e.qualifiedName === item.transform);
      if (!transformer) {
        vscode.window.showErrorMessage(`Transformer '${item.transform}' not found in branch '${item.branch}'.`);
        return;
      }
    }

    const selection: RunSelection = {
      programPath: item.program,
      programType: programTypeArg,
      interpreter: interpreters[0],
      kleisli,
      transformer
    };

    const extraArgs = playlistArgsToDoeffRunArgs(item.args);
    await runSelection(selection, undefined, stateStore.getDebugMode(), {
      cwd: runCwd,
      persistFolderPath: runCwd,
      branch: item.branch,
      extraArgs
    });
  }

  async function revealPlaylistItemFlow(playlistId: string, itemId: string): Promise<void> {
    const data = await playlistsStore.load();
    const playlist = data.playlists.find((p) => p.id === playlistId);
    const item = playlist?.items.find((i) => i.id === itemId);
    if (!playlist || !item) {
      vscode.window.showErrorMessage('Playlist item not found.');
      return;
    }

    const repoRoot = await resolveRepoRoot(workspaceRoot) ?? workspaceRoot;
    if (!repoRoot) {
      vscode.window.showErrorMessage('No workspace folder open.');
      return;
    }

    const branchWorktreePath = await ensureWorktreeForBranch(repoRoot, item.branch);
    if (!branchWorktreePath) {
      return;
    }

    let openCwd = branchWorktreePath;
    if (item.commit) {
      const head = await executeGit(['rev-parse', 'HEAD'], branchWorktreePath);
      if (head.trim() !== item.commit.trim()) {
        const choice = await vscode.window.showWarningMessage(
          `Worktree is at ${head.slice(0, 6)}, but item is pinned to ${item.commit.slice(0, 6)}.`,
          { modal: true },
          'Open at current HEAD',
          `Open at pinned commit ${item.commit.slice(0, 6)}`,
          'Cancel'
        );
        if (!choice || choice === 'Cancel') {
          return;
        }
        if (choice.startsWith('Open at pinned')) {
          const temp = await ensureDetachedWorktreeAtCommit(repoRoot, item.branch, item.commit);
          if (!temp) {
            return;
          }
          openCwd = temp;
        }
      }
    }

    const indexerPath = await locateIndexer();
    const programEntry = await findProgramByQualifiedName(indexerPath, openCwd, item.program);
    if (!programEntry) {
      vscode.window.showErrorMessage(
        `Program '${item.program}' not found in branch '${item.branch}'.`
      );
      return;
    }

    await vscode.commands.executeCommand('doeff-runner.revealEntrypoint', programEntry);
  }

  async function removePlaylistItemFlow(playlistId: string, itemId: string): Promise<void> {
    const data = await playlistsStore.load();
    const playlist = data.playlists.find((p) => p.id === playlistId);
    const item = playlist?.items.find((i) => i.id === itemId);
    if (!playlist || !item) {
      vscode.window.showErrorMessage('Playlist item not found.');
      return;
    }

    const confirm = await vscode.window.showWarningMessage(
      `Remove '${item.name}' from '${playlist.name}'?`,
      { modal: true },
      'Remove'
    );
    if (confirm !== 'Remove') {
      return;
    }

    await playlistsStore.update((payload) => {
      const pl = payload.playlists.find((p) => p.id === playlistId);
      if (!pl) return;
      pl.items = pl.items.filter((i) => i.id !== itemId);
    });
  }

  async function editPlaylistItemFlow(playlistId: string, itemId: string): Promise<void> {
    const data = await playlistsStore.load();
    const playlist = data.playlists.find((p) => p.id === playlistId);
    const item = playlist?.items.find((i) => i.id === itemId);
    if (!playlist || !item) {
      vscode.window.showErrorMessage('Playlist item not found.');
      return;
    }

    const action = await vscode.window.showQuickPick(
      [
        { label: 'Rename', key: 'rename' },
        { label: 'Pin / unpin commit', key: 'pin' },
        { label: 'Change Kleisli', key: 'kleisli' },
        { label: 'Change Transform', key: 'transform' },
        { label: 'Move to playlist', key: 'move' }
      ],
      { title: `Edit: ${item.name}` }
    );
    if (!action) {
      return;
    }

    const repoRoot = await resolveRepoRoot(workspaceRoot) ?? workspaceRoot;
    const worktreePath = await ensureWorktreeForBranch(repoRoot, item.branch);
    if (!worktreePath) {
      return;
    }

    const indexerPath = await locateIndexer();
    const programEntry = await findProgramByQualifiedName(indexerPath, worktreePath, item.program);
    const programTypeArg = programEntry ? extractProgramTypeArg(programEntry) : '';
    const proximity: ProximityContext = programEntry
      ? { filePath: programEntry.filePath, line: programEntry.line }
      : { filePath: '', line: 0 };

    if (action.key === 'rename') {
      const name = await vscode.window.showInputBox({ prompt: 'New item name', value: item.name });
      if (name === undefined) return;
      await playlistsStore.update((payload) => {
        const pl = payload.playlists.find((p) => p.id === playlistId);
        const it = pl?.items.find((i) => i.id === itemId);
        if (it) it.name = name.trim() || it.name;
      });
      return;
    }

    if (action.key === 'pin') {
      const pinnedCommit = await promptPinnedCommit(repoRoot, item.branch);
      if (pinnedCommit === undefined) return;
      await playlistsStore.update((payload) => {
        const pl = payload.playlists.find((p) => p.id === playlistId);
        const it = pl?.items.find((i) => i.id === itemId);
        if (it) it.commit = pinnedCommit;
      });
      return;
    }

    if (action.key === 'kleisli') {
      if (!programEntry) {
        vscode.window.showErrorMessage('Program not found in target branch; cannot edit tools.');
        return;
      }
      const kleisliEntries = await fetchEntries(indexerPath, worktreePath, 'find-kleisli', programTypeArg, proximity);
      const choice = kleisliEntries.length
        ? await selectEntry('Select Kleisli (optional)', kleisliEntries, true)
        : undefined;
      await playlistsStore.update((payload) => {
        const pl = payload.playlists.find((p) => p.id === playlistId);
        const it = pl?.items.find((i) => i.id === itemId);
        if (it) it.apply = choice?.qualifiedName ?? null;
      });
      return;
    }

    if (action.key === 'transform') {
      if (!programEntry) {
        vscode.window.showErrorMessage('Program not found in target branch; cannot edit tools.');
        return;
      }
      const transformEntries = await fetchEntries(
        indexerPath,
        worktreePath,
        'find-transforms',
        programTypeArg,
        proximity
      );
      const choice = transformEntries.length
        ? await selectEntry('Select transformer (optional)', transformEntries, true)
        : undefined;
      await playlistsStore.update((payload) => {
        const pl = payload.playlists.find((p) => p.id === playlistId);
        const it = pl?.items.find((i) => i.id === itemId);
        if (it) it.transform = choice?.qualifiedName ?? null;
      });
      return;
    }

    if (action.key === 'move') {
      const targetPlaylistId = await pickPlaylistId();
      if (!targetPlaylistId) return;
      if (targetPlaylistId === playlistId) return;
      await playlistsStore.update((payload) => {
        const src = payload.playlists.find((p) => p.id === playlistId);
        const dst = payload.playlists.find((p) => p.id === targetPlaylistId);
        if (!src || !dst) return;
        const index = src.items.findIndex((i) => i.id === itemId);
        if (index < 0) return;
        const [moved] = src.items.splice(index, 1);
        dst.items.push(moved);
      });
      return;
    }
  }

  context.subscriptions.push(
    output,
    stateStore,
    codeLensProvider,
    treeProvider,
    worktreeProgramsProvider,
    playlistsStore,
    playlistsProvider,
    treeView,
    worktreesTreeView,
    playlistsTreeView,
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
      () => {
        treeProvider.refresh();
        worktreeProgramsProvider.refresh();
        playlistsProvider.refresh();
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.revealEntrypoint',
      async (entry: IndexEntry) => {
        const uri = vscode.Uri.file(entry.filePath);
        await vscode.commands.executeCommand('vscode.open', uri, {
          selection: new vscode.Range(entry.line - 1, 0, entry.line - 1, 0)
        });
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

        interface EntrypointQuickPickItem extends vscode.QuickPickItem {
          entry: IndexEntry;
          searchText: string;
        }

        const allItems: EntrypointQuickPickItem[] = entrypoints.map(entry => ({
          label: entry.name,
          description: entry.qualifiedName,
          detail: entry.docstring,
          entry,
          searchText: `${entry.name} ${entry.qualifiedName} ${entry.docstring ?? ''}`
        }));

        const selected = await new Promise<EntrypointQuickPickItem | undefined>((resolve) => {
          const qp = vscode.window.createQuickPick<EntrypointQuickPickItem>();
          qp.placeholder = 'Search entrypoints (supports multi-token: "abc fg")';

          const refreshItems = () => {
            qp.items = qp.value
              ? allItems.filter((item) => multiTokenFuzzyMatch(qp.value, item.searchText))
              : allItems;
          };

          refreshItems();
          qp.onDidChangeValue(refreshItems);
          qp.onDidAccept(() => {
            resolve(qp.selectedItems[0]);
            qp.hide();
          });
          qp.onDidHide(() => {
            resolve(undefined);
            qp.dispose();
          });
          qp.show();
        });

        if (!selected) {
          return;
        }

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
    // Playlists + worktree-aware program pickers
    vscode.commands.registerCommand(
      'doeff-runner.addToPlaylist',
      async (arg?: unknown) => {
        let initial: ProgramTarget | undefined;

        if (arg && typeof arg === 'object') {
          const anyArg = arg as any;

          if (anyArg.type === 'wtProgram' && anyArg.entry && anyArg.module?.branch) {
            const node = arg as WorktreeProgramNode;
            initial = {
              branch: node.module.branch.branch,
              worktreePath: node.module.branch.worktreePath,
              entry: node.entry
            };
          } else if (anyArg.type === 'entrypoint' && anyArg.entry) {
            const repoRoot = await resolveRepoRoot(workspaceRoot) ?? workspaceRoot;
            const branch = await resolveCurrentBranch(repoRoot) ?? 'main';
            initial = { branch, worktreePath: workspaceRoot, entry: anyArg.entry as IndexEntry };
          } else if (anyArg.entry && typeof anyArg.worktreePath === 'string') {
            const repoRoot = await resolveRepoRoot(workspaceRoot) ?? workspaceRoot;
            const worktreePath = anyArg.worktreePath as string;
            const branch =
              await resolveCurrentBranch(worktreePath) ??
              await resolveCurrentBranch(repoRoot) ??
              'main';
            initial = { branch, worktreePath, entry: anyArg.entry as IndexEntry };
          } else if (typeof anyArg.qualifiedName === 'string') {
            const repoRoot = await resolveRepoRoot(workspaceRoot) ?? workspaceRoot;
            const branch = await resolveCurrentBranch(repoRoot) ?? 'main';
            initial = { branch, worktreePath: workspaceRoot, entry: arg as IndexEntry };
          }
        }

        await addToPlaylistFlow(initial);
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.pickProgram',
      async () => {
        const target = await pickProgramTarget();
        if (!target) return;
        await revealInWorktreesTree(target);
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.pickAndRun',
      async () => {
        const target = await pickProgramTarget();
        if (!target) return;
        await runDefault(target.entry.qualifiedName, undefined, stateStore.getDebugMode(), {
          cwd: target.worktreePath,
          persistFolderPath: target.worktreePath,
          branch: target.branch
        });
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.pickAndAddToPlaylist',
      async () => {
        const target = await pickProgramTarget();
        if (!target) return;
        await addToPlaylistFlow(target);
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.pickPlaylist',
      async () => {
        const data = await playlistsStore.load();
        const items = data.playlists
          .slice()
          .sort((a, b) => a.name.localeCompare(b.name))
          .map((pl) => ({
            label: pl.name,
            description: `${pl.items.length} item(s)`,
            playlistId: pl.id
          }));
        const selected = await vscode.window.showQuickPick(items, { title: 'Pick a playlist' });
        if (!selected) return;
        const node = playlistsProvider.getPlaylistNode(selected.playlistId);
        if (node) {
          await playlistsTreeView.reveal(node, { select: true, focus: true, expand: true });
        }
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.pickPlaylistItem',
      async () => {
        const picked = await pickPlaylistItemIds('Pick a playlist item');
        if (!picked) {
          return;
        }

        const node = playlistsProvider.getPlaylistItemNode(picked.playlistId, picked.itemId);
        if (node) {
          await playlistsTreeView.reveal(node, { select: true, focus: true, expand: true });
        }
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.pickAndRunPlaylistItem',
      async () => {
        const picked = await pickPlaylistItemIds('Pick a playlist item to run');
        if (!picked) {
          return;
        }
        await runPlaylistItemFlow(picked.playlistId, picked.itemId);
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.runPlaylistItem',
      async (arg1?: unknown, arg2?: unknown) => {
        const ids = resolvePlaylistItemIds(arg1, arg2)
          ?? await pickPlaylistItemIds('Pick a playlist item to run');
        if (!ids) {
          return;
        }
        await runPlaylistItemFlow(ids.playlistId, ids.itemId);
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.revealPlaylistItem',
      async (arg1?: unknown, arg2?: unknown) => {
        const ids = resolvePlaylistItemIds(arg1, arg2)
          ?? await pickPlaylistItemIds('Pick a playlist item to reveal');
        if (!ids) {
          return;
        }
        await revealPlaylistItemFlow(ids.playlistId, ids.itemId);
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.editPlaylistItem',
      async (playlistId: string, itemId: string) => {
        await editPlaylistItemFlow(playlistId, itemId);
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.removePlaylistItem',
      async (playlistId: string, itemId: string) => {
        await removePlaylistItemFlow(playlistId, itemId);
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.createPlaylist',
      async () => {
        const name = await vscode.window.showInputBox({
          prompt: 'New playlist name',
          placeHolder: 'e.g., Daily'
        });
        if (!name?.trim()) {
          return;
        }
        const id = uuid();
        await playlistsStore.update((payload) => {
          payload.playlists.push({ id, name: name.trim(), items: [] });
        });
        const node = playlistsProvider.getPlaylistNode(id);
        if (node) {
          await playlistsTreeView.reveal(node, { select: true, focus: true, expand: true });
        }
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.renamePlaylist',
      async (node?: PlaylistNode) => {
        const data = await playlistsStore.load();
        const playlistId =
          node?.type === 'playlist'
            ? node.playlist.id
            : (await vscode.window.showQuickPick(
              data.playlists
                .slice()
                .sort((a, b) => a.name.localeCompare(b.name))
                .map((pl) => ({ label: pl.name, playlistId: pl.id })),
              { title: 'Pick a playlist to rename' }
            ))?.playlistId;

        if (!playlistId) {
          return;
        }

        const playlist = data.playlists.find((p) => p.id === playlistId);
        const name = await vscode.window.showInputBox({
          prompt: 'New playlist name',
          value: playlist?.name ?? ''
        });
        if (name === undefined) return;

        await playlistsStore.update((payload) => {
          const pl = payload.playlists.find((p) => p.id === playlistId);
          if (pl && name.trim()) {
            pl.name = name.trim();
          }
        });
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.deletePlaylist',
      async (node?: PlaylistNode) => {
        const data = await playlistsStore.load();
        const playlistId =
          node?.type === 'playlist'
            ? node.playlist.id
            : (await vscode.window.showQuickPick(
              data.playlists
                .slice()
                .sort((a, b) => a.name.localeCompare(b.name))
                .map((pl) => ({
                  label: pl.name,
                  description: `${pl.items.length} item(s)`,
                  playlistId: pl.id
                })),
              { title: 'Pick a playlist to delete' }
            ))?.playlistId;

        if (!playlistId) return;
        const playlist = data.playlists.find((p) => p.id === playlistId);
        if (!playlist) return;

        const confirm = await vscode.window.showWarningMessage(
          playlist.items.length
            ? `Delete playlist '${playlist.name}' (${playlist.items.length} items)?`
            : `Delete playlist '${playlist.name}'?`,
          { modal: true },
          'Delete'
        );
        if (confirm !== 'Delete') return;

        await playlistsStore.update((payload) => {
          payload.playlists = payload.playlists.filter((p) => p.id !== playlistId);
        });
      }
    ),
    vscode.commands.registerCommand(
      'doeff-runner.openPlaylistsFile',
      async () => {
        const filePath = await playlistsStore.ensureFileExists();
        const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(filePath));
        await vscode.window.showTextDocument(doc, { preview: false });
      }
    ),
    // Key Inspector commands
    vscode.commands.registerCommand(
      'doeff-runner.inspectEnvKey',
      async (entryArg?: IndexEntry | EnvChainNode | EnvSourceNode) => {
        const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
        const defaultRoot = workspaceFolder?.uri.fsPath;

        // Determine program, root path, and env chain
        let rootPath = defaultRoot;
        let programEntry: IndexEntry | undefined;
        let envChain: EnvChainEntry[] = [];

        if (entryArg && typeof entryArg === 'object' && 'type' in entryArg) {
          if (entryArg.type === 'envSource') {
            rootPath = entryArg.rootPath;
            programEntry = entryArg.parentEntry;
            envChain = entryArg.allEnvEntries;
          } else if (entryArg.type === 'envChain') {
            rootPath = entryArg.rootPath;
            programEntry = entryArg.parentEntry;
            envChain = entryArg.entries;
          }
        } else if (entryArg && typeof entryArg === 'object' && 'qualifiedName' in entryArg) {
          programEntry = entryArg as IndexEntry;
        }

        if (!rootPath) {
          vscode.window.showErrorMessage('No workspace folder open');
          return;
        }

        // If no entry provided, ask user to select a program
        if (!programEntry) {
          const indexerPath = await locateIndexer();
          const allEntries = await fetchEntries(indexerPath, rootPath, 'index', '', undefined);
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
          envChain = await getEnvChainForRoot(rootPath, programEntry.qualifiedName);
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

        const rootPath = keyNode.rootPath || vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!rootPath) {
          vscode.window.showErrorMessage('No workspace folder open');
          return;
        }

        // Execute ask(key) at runtime
        const key = keyNode.key;

        vscode.window.withProgress(
          { location: vscode.ProgressLocation.Notification, title: `Resolving ${key}...` },
          async () => {
            try {
              const askCode = `from doeff.core import ask; print(ask(${JSON.stringify(key)}))`;
              const timeout = vscode.workspace
                .getConfiguration()
                .get<number>('doeff-runner.envInspector.askTimeout', 10000);

              const { stdout, stderr } = (await isUvAvailable())
                ? await execFileAsync('uv', ['run', 'python', '-c', askCode], {
                  cwd: rootPath,
                  timeout
                })
                : await execFileAsync(await getPythonInterpreter() ?? 'python3', ['-c', askCode], {
                  cwd: rootPath,
                  timeout
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

    const rootPath = resolveRootPathForUri(document.uri);
    if (!rootPath) {
      vscode.window.showErrorMessage(
        'Could not resolve project root for this file. Open a workspace folder or a Git repo.'
      );
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
      vscode.workspace.getWorkspaceFolder(vscode.Uri.file(rootPath)) ??
      vscode.workspace.workspaceFolders?.[0];

    const indexerPath = await locateIndexer();
    const programEntry = await findProgramEntry(
      indexerPath,
      rootPath,
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
      await runDefault(programPath, workspaceFolder, debugMode, {
        cwd: rootPath,
        persistFolderPath: rootPath
      });
    } else {
      const selection = await buildSelection(
        document,
        declaration,
        rootPath,
        programPath
      );
      if (!selection) {
        return;
      }
      await runSelection(selection, workspaceFolder, debugMode, {
        cwd: rootPath,
        persistFolderPath: rootPath
      });
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

    const rootPath = resolveRootPathForUri(document.uri);
    if (!rootPath) {
      vscode.window.showErrorMessage(
        'Could not resolve project root for this file. Open a workspace folder or a Git repo.'
      );
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
      vscode.workspace.getWorkspaceFolder(vscode.Uri.file(rootPath)) ??
      vscode.workspace.workspaceFolders?.[0];

    const indexerPath = await locateIndexer();
    const programEntry = await findProgramEntry(
      indexerPath,
      rootPath,
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
      rootPath,
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
      rootPath,
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
    await runSelection(selection, workspaceFolder, debugMode, {
      cwd: rootPath,
      persistFolderPath: rootPath
    });
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
  const rootPath = resolveRootPathForUri(uri);
  if (!rootPath) {
    vscode.window.showErrorMessage(
      'Could not resolve project root for this file. Open a workspace folder or a Git repo.'
    );
    return;
  }

  try {
    const indexerPath = await locateIndexer();

    // Use the CodeLens location for proximity-based sorting
    const proximity: ProximityContext = {
      filePath: uri.fsPath,
      line: lineNumber + 1 // Convert 0-indexed to 1-indexed
    };

    // Fetch all tools
    const command = toolType === 'kleisli' ? 'find-kleisli' : 'find-transforms';
    const tools = await fetchEntries(indexerPath, rootPath, command, typeArg, proximity);

    if (tools.length === 0) {
      const suffix = typeArg.trim() ? `for type ${typeArg}` : 'in workspace';
      vscode.window.showInformationMessage(`No ${toolType} tools found ${suffix}.`);
      return;
    }

    if (!typeArg.trim()) {
      vscode.window.showInformationMessage(
        `No type argument specified on this Program. Showing all ${toolType} tools; add Program[T] to enable filtering.`
      );
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

interface RunInvocationOptions {
  cwd?: string;
  persistFolderPath?: string;
  extraArgs?: string[];
  branch?: string;
}

async function runDefault(
  programPath: string,
  workspaceFolder?: vscode.WorkspaceFolder,
  debugMode: boolean = true,
  options: RunInvocationOptions = {}
) {
  const folder =
    workspaceFolder ?? vscode.workspace.workspaceFolders?.[0];
  const cwd = options.cwd ?? folder?.uri.fsPath;
  const persistFolderPath = options.persistFolderPath ?? cwd ?? folder?.uri.fsPath;
  const args = ['run', '--program', programPath, ...(options.extraArgs ?? [])];
  const branch = options.branch ?? (cwd ? await resolveCurrentBranch(cwd) : undefined);
  const sessionName = buildSessionName(debugMode ? 'Debug' : 'Run', programPath, undefined, undefined, branch);

  if (debugMode) {
    // Debug mode: use VSCode debug infrastructure with debugpy
    const commandDisplay = `python -m doeff ${args.join(' ')}`;
    const debugConfig: vscode.DebugConfiguration = {
      type: 'python',
      request: 'launch',
      name: sessionName,
      module: 'doeff',
      args,
      cwd,
      console: 'integratedTerminal',
      justMyCode: false
    };

    if (persistFolderPath) {
      persistLaunchConfigToPath(debugConfig, persistFolderPath);
    }

    vscode.window.showInformationMessage(`Debugging: ${commandDisplay}`);
    output.appendLine(`[info] Debugging: ${commandDisplay}`);
    await vscode.debug.startDebugging(folder, debugConfig);
  } else {
    // Run mode: use terminal directly without debugpy, respecting IDE's Python selection
    const command = (await isUvAvailable())
      ? `uv run doeff ${args.join(' ')}`
      : `"${await getPythonInterpreter() ?? 'python'}" -m doeff ${args.join(' ')}`;
    const terminal = createTerminal(sessionName, cwd);
    vscode.window.showInformationMessage(`Running: ${command}`);
    output.appendLine(`[info] Command: ${command}`);
    terminal.sendText(command);
    terminal.show();
  }
}

async function runSelection(
  selection: RunSelection | undefined,
  workspaceFolder?: vscode.WorkspaceFolder,
  debugMode: boolean = true,
  options: RunInvocationOptions = {}
) {
  if (!selection) {
    vscode.window.showErrorMessage('No doeff selection to run.');
    return;
  }
  const folder =
    workspaceFolder ?? vscode.workspace.workspaceFolders?.[0];
  const cwd = options.cwd ?? folder?.uri.fsPath;
  const persistFolderPath = options.persistFolderPath ?? cwd ?? folder?.uri.fsPath;
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
  if (options.extraArgs?.length) {
    args.push(...options.extraArgs);
  }

  const modeLabel = debugMode ? 'Debugging' : 'Running';
  const branch = options.branch ?? (cwd ? await resolveCurrentBranch(cwd) : undefined);
  const sessionName = buildSessionName(
    debugMode ? 'Debug' : 'Run',
    selection.programPath,
    selection.kleisli?.qualifiedName,
    selection.transformer?.qualifiedName,
    branch
  );
  output.appendLine(
    `[info] ${modeLabel} doeff for ${selection.programPath} with interpreter ${selection.interpreter.qualifiedName}`
  );

  if (debugMode) {
    // Debug mode: use VSCode debug infrastructure with debugpy
    const commandDisplay = `python -m doeff ${args.join(' ')}`;
    const debugConfig: vscode.DebugConfiguration = {
      type: 'python',
      request: 'launch',
      name: sessionName,
      module: 'doeff',
      args,
      cwd,
      console: 'integratedTerminal',
      justMyCode: false
    };

    if (persistFolderPath) {
      persistLaunchConfigToPath(debugConfig, persistFolderPath);
    }

    vscode.window.showInformationMessage(`Debugging: ${commandDisplay}`);
    output.appendLine(`[info] Command: ${commandDisplay}`);
    await vscode.debug.startDebugging(folder, debugConfig);
  } else {
    // Run mode: use terminal directly without debugpy, respecting IDE's Python selection
    const command = (await isUvAvailable())
      ? `uv run doeff ${args.join(' ')}`
      : `"${await getPythonInterpreter() ?? 'python'}" -m doeff ${args.join(' ')}`;
    const terminal = createTerminal(sessionName, cwd);
    vscode.window.showInformationMessage(`Running: ${command}`);
    output.appendLine(`[info] Command: ${command}`);
    terminal.sendText(command);
    terminal.show();
  }
}

function persistLaunchConfigToPath(config: vscode.DebugConfiguration, folderPath: string) {
  try {
    const vscodeDir = path.join(folderPath, '.vscode');
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
  rootPath: string,
  programPath: string
): Promise<RunSelection | undefined> {
  const indexerPath = await locateIndexer();

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
  if (!programType.trim()) {
    vscode.window.showInformationMessage(
      'No type argument specified on this Program. Kleisli/Transform tools will be shown unfiltered. Add Program[T] to enable filtering.'
    );
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
  if (afterAnnotation.endsWith(',')) {
    return;
  }
  // Handle multi-line function signatures that close the parens and add return type on the same line:
  //   def foo(
  //     program: Program[T]) -> str:
  if (!hasAssignment && afterAnnotation.startsWith(')')) {
    return;
  }
  if (!hasAssignment && afterAnnotation.endsWith(')')) {
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

// =============================================================================
// Git helpers (worktrees + playlists storage)
// =============================================================================

async function executeGit(args: string[], cwd: string): Promise<string> {
  const safeCwd =
    cwd ||
    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ||
    process.cwd();

  try {
    const { stdout, stderr } = await execFileAsync('git', args, {
      cwd: safeCwd,
      maxBuffer: 10 * 1024 * 1024
    });
    if (stderr.trim()) {
      output.appendLine(`[warn] git stderr:\n${stderr.trim()}`);
    }
    return stdout.trim();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    output.appendLine(`[error] git ${args.join(' ')} failed: ${message}`);
    throw error;
  }
}

async function resolveRepoRoot(cwd: string): Promise<string | undefined> {
  try {
    const root = await executeGit(['rev-parse', '--show-toplevel'], cwd);
    return root.trim() || undefined;
  } catch {
    return undefined;
  }
}

async function resolveGitCommonDir(repoRoot: string): Promise<string | undefined> {
  try {
    const raw = await executeGit(['rev-parse', '--git-common-dir'], repoRoot);
    const resolved = path.resolve(repoRoot, raw.trim());
    return resolved || undefined;
  } catch {
    return undefined;
  }
}

async function resolveCurrentBranch(repoRoot: string): Promise<string | undefined> {
  try {
    const branch = await executeGit(['rev-parse', '--abbrev-ref', 'HEAD'], repoRoot);
    const trimmed = branch.trim();
    return trimmed && trimmed !== 'HEAD' ? trimmed : undefined;
  } catch {
    return undefined;
  }
}

async function listLocalBranches(repoRoot: string): Promise<string[]> {
  try {
    const stdout = await executeGit(['branch', '--format=%(refname:short)'], repoRoot);
    return stdout
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
  } catch {
    return [];
  }
}

function sanitizeBranchForPath(branch: string): string {
  return branch.replace(/[\\/]/g, '__');
}

async function ensureWorktreeForBranch(repoRoot: string, branch: string): Promise<string | undefined> {
  try {
    const stdout = await executeGit(['worktree', 'list', '--porcelain'], repoRoot);
    const worktrees = parseGitWorktreeListPorcelain(stdout);
    const existing = worktrees.find((wt) => wt.branch === branch);
    if (existing) {
      return existing.worktreePath;
    }

    const decision = await vscode.window.showWarningMessage(
      `No worktree for '${branch}'. Create one?`,
      { modal: true },
      'Create Worktree',
      'Cancel'
    );
    if (decision !== 'Create Worktree') {
      return undefined;
    }

    const baseDir = path.join(path.dirname(repoRoot), `${path.basename(repoRoot)}-worktrees`);
    const defaultPath = path.join(baseDir, sanitizeBranchForPath(branch));
    const worktreePathInput = await vscode.window.showInputBox({
      prompt: `Worktree path for '${branch}'`,
      value: defaultPath
    });
    if (!worktreePathInput?.trim()) {
      return undefined;
    }
    const worktreePath = worktreePathInput.trim();

    await executeGit(['worktree', 'add', worktreePath, branch], repoRoot);
    return worktreePath;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    vscode.window.showErrorMessage(`Failed to create worktree for '${branch}': ${message}`);
    return undefined;
  }
}

async function ensureDetachedWorktreeAtCommit(
  repoRoot: string,
  branch: string,
  commit: string
): Promise<string | undefined> {
  try {
    const baseDir = path.join(
      path.dirname(repoRoot),
      `${path.basename(repoRoot)}-worktrees`,
      '.doeff-temp'
    );
    const defaultPath = path.join(baseDir, `${sanitizeBranchForPath(branch)}-${commit.slice(0, 6)}`);
    const worktreePathInput = await vscode.window.showInputBox({
      prompt: `Temp worktree path for ${branch} @ ${commit.slice(0, 6)}`,
      value: defaultPath
    });
    if (!worktreePathInput?.trim()) {
      return undefined;
    }
    const worktreePath = worktreePathInput.trim();

    await executeGit(['worktree', 'add', '--detach', worktreePath, commit], repoRoot);
    return worktreePath;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    vscode.window.showErrorMessage(`Failed to create temp worktree: ${message}`);
    return undefined;
  }
}

async function findProgramByQualifiedName(
  indexerPath: string,
  worktreePath: string,
  qualifiedName: string
): Promise<IndexEntry | undefined> {
  const entries = await queryIndexer(
    indexerPath,
    `index:${worktreePath}`,
    worktreePath,
    ['index', '--root', worktreePath]
  );
  return entries.find((entry) => entry.qualifiedName === qualifiedName && isProgramEntrypoint(entry));
}

async function promptPinnedCommit(repoRoot: string, branch: string): Promise<string | null | undefined> {
  const head = await executeGit(['rev-parse', branch], repoRoot);
  const headShort = head.slice(0, 6);

  const options = [
    { label: 'No - always use latest (HEAD)', value: null as string | null },
    { label: `Yes - pin to ${headShort} (current)`, value: head },
    { label: 'Yes - select from history...', value: 'history' }
  ];

  const choice = await vscode.window.showQuickPick(options, { title: 'Pin to commit?' });
  if (!choice) {
    return undefined;
  }
  if (choice.value === 'history') {
    return await pickCommitFromHistory(repoRoot, branch);
  }
  return choice.value;
}

async function pickCommitFromHistory(repoRoot: string, branch: string): Promise<string | undefined> {
  const stdout = await executeGit(['log', '--format=%H%x09%s', '-n', '50', branch], repoRoot);
  const lines = stdout.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);

  const items = lines.map((line) => {
    const [hash, subject] = line.split('\t', 2);
    return {
      label: `${hash.slice(0, 6)} ${subject ?? ''}`.trim(),
      description: hash,
      commit: hash
    };
  });

  const selected = await vscode.window.showQuickPick(items, {
    title: `Select commit from ${branch}`,
    matchOnDescription: true
  });
  return selected?.commit;
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
  // Only apply client-side type filtering for tool-like commands.
  // Interpreters should be discoverable even for untyped Programs.
  if (supportsTypeArg && trimmedType && trimmedType.toLowerCase() !== 'any') {
    return filterEntriesForType(entries, trimmedType);
  }
  return entries;
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
    const result = JSON.parse(stdout) as RawEnvChainResult;

    // Convert snake_case from Rust to camelCase for TypeScript.
    // Note: doeff-indexer currently emits `env_chain` (snake_case) at the top level.
    const rawEnvChain = result.envChain ?? result.env_chain ?? [];
    return rawEnvChain.map((entry): EnvChainEntry => {
      const staticValuesRaw = entry.staticValues ?? entry.static_values ?? undefined;
      const staticValues =
        staticValuesRaw && typeof staticValuesRaw === 'object'
          ? (staticValuesRaw as Record<string, unknown>)
          : undefined;

      return {
        qualifiedName: entry.qualifiedName ?? entry.qualified_name ?? '',
        filePath: entry.filePath ?? entry.file_path ?? '',
        line: entry.line ?? 0,
        keys: entry.keys ?? [],
        staticValues,
        isUserConfig: entry.isUserConfig ?? entry.is_user_config
      };
    }).filter(entry => entry.qualifiedName && entry.filePath);
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
