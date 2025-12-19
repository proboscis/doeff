export interface GitWorktreeInfo {
  worktreePath: string;
  head: string;
  branch: string | null;
  isDetached: boolean;
}

function branchFromRef(ref: string): string {
  const prefix = 'refs/heads/';
  return ref.startsWith(prefix) ? ref.slice(prefix.length) : ref;
}

export function parseGitWorktreeListPorcelain(stdout: string): GitWorktreeInfo[] {
  const entries: GitWorktreeInfo[] = [];
  const lines = stdout.split(/\r?\n/);

  let current: Partial<GitWorktreeInfo> | undefined;

  const flush = () => {
    if (!current?.worktreePath) {
      current = undefined;
      return;
    }
    entries.push({
      worktreePath: current.worktreePath,
      head: current.head ?? '',
      branch: current.branch ?? null,
      isDetached: current.isDetached ?? false
    });
    current = undefined;
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (!line) {
      continue;
    }

    if (line.startsWith('worktree ')) {
      flush();
      current = {
        worktreePath: line.slice('worktree '.length).trim(),
        isDetached: false
      };
      continue;
    }

    if (!current) {
      continue;
    }

    if (line.startsWith('HEAD ')) {
      current.head = line.slice('HEAD '.length).trim();
      continue;
    }

    if (line.startsWith('branch ')) {
      current.branch = branchFromRef(line.slice('branch '.length).trim());
      continue;
    }

    if (line === 'detached') {
      current.isDetached = true;
      current.branch = null;
      continue;
    }
  }

  flush();
  return entries;
}

