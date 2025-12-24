export type DoeffOutputFormat = 'text' | 'json';

export interface PlaylistArgs {
  format?: DoeffOutputFormat;
  report?: boolean;
  reportVerbose?: boolean;
}

export type PlaylistItemType = 'doeff' | 'custom';

// Base fields shared by all playlist item types
export interface PlaylistItemBase {
  id: string;
  name: string;
  branch: string | null;
  commit: string | null;
  worktree: string | null;
  cwd: string | null; // Custom working directory (absolute or relative to workspace root)
}

// Doeff program playlist item (original type)
export interface DoeffPlaylistItem extends PlaylistItemBase {
  type: 'doeff';
  branch: string; // Required for doeff items
  program: string;
  apply: string | null;
  transform: string | null;
  args: PlaylistArgs;
}

// Custom command playlist item (new type)
export interface CustomPlaylistItem extends PlaylistItemBase {
  type: 'custom';
  cmd: string;
}

// Union type for all playlist items
export type PlaylistItemV2 = DoeffPlaylistItem | CustomPlaylistItem;

// Legacy interface for backwards compatibility (doeff items without explicit type)
export interface LegacyPlaylistItemV2 {
  id: string;
  name: string;
  branch: string;
  commit: string | null;
  program: string;
  apply: string | null;
  transform: string | null;
  args: PlaylistArgs;
}

// Type guards
export function isDoeffPlaylistItem(item: PlaylistItemV2): item is DoeffPlaylistItem {
  return item.type === 'doeff';
}

export function isCustomPlaylistItem(item: PlaylistItemV2): item is CustomPlaylistItem {
  return item.type === 'custom';
}

export interface PlaylistV2 {
  id: string;
  name: string;
  items: PlaylistItemV2[];
}

export interface PlaylistsFileV2 {
  version: 2;
  playlists: PlaylistV2[];
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isString(value: unknown): value is string {
  return typeof value === 'string';
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === 'boolean';
}

function normalizeArgs(value: unknown): PlaylistArgs {
  if (!isObject(value)) {
    return {};
  }

  const formatRaw = value.format;
  const format =
    formatRaw === 'text' || formatRaw === 'json' ? formatRaw : undefined;

  return {
    format,
    report: isBoolean(value.report) ? value.report : undefined,
    reportVerbose: isBoolean(value.reportVerbose) ? value.reportVerbose : undefined
  };
}

function normalizeItem(value: unknown): PlaylistItemV2 | undefined {
  if (!isObject(value)) {
    return undefined;
  }

  const id = value.id;
  const name = value.name;
  if (!isString(id) || !isString(name)) {
    return undefined;
  }

  const commit = value.commit;
  const worktree = value.worktree;
  const cwd = value.cwd;
  const type = value.type;

  // Handle custom playlist items
  if (type === 'custom') {
    const cmd = value.cmd;
    const branch = value.branch;
    if (!isString(cmd)) {
      return undefined;
    }
    return {
      type: 'custom',
      id,
      name,
      branch: branch === null ? null : isString(branch) ? branch : null,
      commit: commit === null ? null : isString(commit) ? commit : null,
      worktree: worktree === null ? null : isString(worktree) ? worktree : null,
      cwd: cwd === null ? null : isString(cwd) ? cwd : null,
      cmd
    };
  }

  // Handle doeff playlist items (including legacy items without type field)
  const branch = value.branch;
  const program = value.program;
  if (!isString(branch) || !isString(program)) {
    return undefined;
  }

  const apply = value.apply;
  const transform = value.transform;

  return {
    type: 'doeff',
    id,
    name,
    branch,
    commit: commit === null ? null : isString(commit) ? commit : null,
    worktree: worktree === null ? null : isString(worktree) ? worktree : null,
    cwd: cwd === null ? null : isString(cwd) ? cwd : null,
    program,
    apply: apply === null ? null : isString(apply) ? apply : null,
    transform: transform === null ? null : isString(transform) ? transform : null,
    args: normalizeArgs(value.args)
  };
}

function normalizePlaylist(value: unknown): PlaylistV2 | undefined {
  if (!isObject(value)) {
    return undefined;
  }

  const id = value.id;
  const name = value.name;
  if (!isString(id) || !isString(name)) {
    return undefined;
  }

  const itemsRaw = Array.isArray(value.items) ? value.items : [];
  const items = itemsRaw
    .map(normalizeItem)
    .filter((item): item is PlaylistItemV2 => item !== undefined);

  return { id, name, items };
}

export function normalizePlaylistsFileV2(value: unknown): PlaylistsFileV2 {
  if (!isObject(value)) {
    return { version: 2, playlists: [] };
  }

  const playlistsRaw = Array.isArray(value.playlists) ? value.playlists : [];
  const playlists = playlistsRaw
    .map(normalizePlaylist)
    .filter((playlist): playlist is PlaylistV2 => playlist !== undefined);

  return { version: 2, playlists };
}

export function parsePlaylistsJsonV2(content: string): { data: PlaylistsFileV2; error?: string } {
  try {
    const raw = JSON.parse(content) as unknown;
    const version = isObject(raw) ? raw.version : undefined;
    if (version !== undefined && version !== 2) {
      return {
        data: normalizePlaylistsFileV2(raw),
        error: `Unsupported playlists schema version: ${String(version)}`
      };
    }
    return { data: normalizePlaylistsFileV2(raw) };
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Invalid JSON';
    return { data: { version: 2, playlists: [] }, error: message };
  }
}

export function formatBranchCommitTag(branch: string | null, commit: string | null): string {
  if (!branch && !commit) {
    return '';
  }
  if (!branch) {
    return `[@ ${commit!.slice(0, 6)}]`;
  }
  if (!commit) {
    return `[${branch}]`;
  }
  return `[${branch} @ ${commit.slice(0, 6)}]`;
}

export function playlistArgsToDoeffRunArgs(args: PlaylistArgs | undefined): string[] {
  const result: string[] = [];
  if (!args) {
    return result;
  }

  if (args.format) {
    result.push('--format', args.format);
  }
  if (args.report) {
    result.push('--report');
  }
  if (args.reportVerbose) {
    result.push('--report-verbose');
  }

  return result;
}

