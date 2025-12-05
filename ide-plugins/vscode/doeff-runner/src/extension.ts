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

class ProgramCodeLensProvider implements vscode.CodeLensProvider, vscode.Disposable {
  private readonly emitter = new vscode.EventEmitter<void>();
  public readonly onDidChangeCodeLenses = this.emitter.event;
  private kleisliCache = new Map<string, ToolCache>();
  private transformCache = new Map<string, ToolCache>();
  private pendingFetches = new Set<string>();
  private readonly CACHE_TTL_MS = 30000; // 30 seconds before background refresh

  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    const lenses: vscode.CodeLens[] = [];
    const declarations = extractProgramDeclarations(document);

    const workspaceFolder =
      vscode.workspace.getWorkspaceFolder(document.uri) ??
      vscode.workspace.workspaceFolders?.[0];

    for (const decl of declarations) {
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

      if (workspaceFolder) {
        const rootPath = workspaceFolder.uri.fsPath;

        // Add Kleisli tool buttons
        const kleisliTools = this.getToolsSync('kleisli', rootPath, decl.typeArg);
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

        // Add Transform tool buttons
        const transformTools = this.getToolsSync('transform', rootPath, decl.typeArg);
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

  /**
   * Synchronously returns cached tool entries.
   * Triggers background refresh if cache is stale.
   */
  private getToolsSync(
    toolType: 'kleisli' | 'transform',
    rootPath: string,
    typeArg: string
  ): IndexEntry[] {
    const cache = toolType === 'kleisli' ? this.kleisliCache : this.transformCache;
    const cacheKey = `${toolType}:${rootPath}:${typeArg}`;
    const cached = cache.get(cacheKey);
    const now = Date.now();

    // Return cached data if available (even if stale)
    if (cached) {
      // Trigger background refresh if stale
      if (now - cached.timestamp > this.CACHE_TTL_MS) {
        this.refreshToolsInBackground(toolType, rootPath, typeArg, cacheKey);
      }
      return cached.entries;
    }

    // No cache - trigger background fetch and return empty for now
    this.refreshToolsInBackground(toolType, rootPath, typeArg, cacheKey);
    return [];
  }

  /**
   * Fetches tool data in background and refreshes CodeLens when done.
   */
  private refreshToolsInBackground(
    toolType: 'kleisli' | 'transform',
    rootPath: string,
    typeArg: string,
    cacheKey: string
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
          typeArg
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
  const codeLensProvider = new ProgramCodeLensProvider();

  context.subscriptions.push(
    output,
    codeLensProvider,
    vscode.languages.registerCodeLensProvider(
      { language: 'python' },
      codeLensProvider
    ),
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
    vscode.workspace.onDidChangeTextDocument((event) => {
      if (vscode.window.activeTextEditor?.document === event.document) {
        codeLensProvider.refresh();
      }
    }),
    vscode.window.onDidChangeActiveTextEditor(() => {
      codeLensProvider.refresh();
    })
  );
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

    // Find default interpreter
    const interpreters = await fetchEntries(
      indexerPath,
      workspaceFolder.uri.fsPath,
      'find-interpreters',
      declaration.typeArg
    );
    if (!interpreters.length) {
      vscode.window.showErrorMessage(
        `No doeff interpreters were found. Cannot run with ${toolType}.`
      );
      return;
    }

    // Use the first interpreter as default
    const defaultInterpreter = interpreters[0];

    // Find the tool entry for validation
    const toolCommand = toolType === 'kleisli' ? 'find-kleisli' : 'find-transforms';
    const tools = await fetchEntries(
      indexerPath,
      workspaceFolder.uri.fsPath,
      toolCommand,
      declaration.typeArg
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

  const interpreters = await fetchEntries(
    indexerPath,
    rootPath,
    'find-interpreters',
    programType
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
    programType
  );
  const transformers = await fetchEntries(
    indexerPath,
    rootPath,
    'find-transforms',
    programType
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
  const typeArg = match[2]?.trim() || 'Any';
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

async function fetchEntries(
  indexerPath: string,
  rootPath: string,
  command: string,
  typeArg: string
): Promise<IndexEntry[]> {
  const trimmedType = typeArg.trim();
  const cacheKey = `${command}:${rootPath}:${trimmedType || 'Any'}`;
  const args = [command, '--root', rootPath];
  const supportsTypeArg =
    command === 'find-kleisli' || command === 'find-interceptors';
  if (supportsTypeArg && trimmedType && trimmedType.toLowerCase() !== 'any') {
    args.push('--type-arg', trimmedType);
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
  if (!normalizedType || normalizedType.toLowerCase() === 'any') {
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
