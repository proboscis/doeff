# doeff.nvim

Neovim plugin for running [doeff](https://github.com/proboscis/doeff) entrypoints with Telescope integration.

## Features

- Fuzzy search all doeff entrypoints (interpreters, kleisli, transforms)
- Preview function signatures and docstrings
- Run entrypoints in floating, horizontal, or vertical terminals
- Run entrypoint under cursor
- Re-run last executed entrypoint
- Playlist support for saved run configurations
- Auto-refresh index on file save
- **Workflow monitoring** - List, watch, and attach to agentic workflows

## Requirements

- Neovim >= 0.9.0
- [telescope.nvim](https://github.com/nvim-telescope/telescope.nvim)
- [plenary.nvim](https://github.com/nvim-lua/plenary.nvim)
- `doeff-indexer` binary in PATH
- `doeff-agentic` binary in PATH (optional, for workflow features)

## Installation

### lazy.nvim

```lua
{
    'proboscis/doeff.nvim',
  dependencies = {
    'nvim-telescope/telescope.nvim',
    'nvim-lua/plenary.nvim',
  },
  opts = {
    -- your configuration here
  },
}
```

### packer.nvim

```lua
use {
    'proboscis/doeff.nvim',
  requires = {
    'nvim-telescope/telescope.nvim',
    'nvim-lua/plenary.nvim',
  },
  config = function()
    require('doeff').setup({
      -- your configuration here
    })
  end,
}
```

## Configuration

```lua
require('doeff').setup({
  -- Keybindings (defaults shown)
  keymaps = {
    entrypoints = '<leader>de',     -- Fuzzy search all entrypoints
    run_cursor = '<leader>dr',      -- Run entrypoint under cursor
    playlists = '<leader>dP',       -- Search and run playlists
    run_last = '<leader>dl',        -- Re-run last entrypoint
    transforms = '<leader>dt',      -- Search transforms
    interpreters = '<leader>di',    -- Search interpreters
    kleisli = '<leader>dk',         -- Search kleisli functions
    interceptors = '<leader>dc',    -- Search interceptors
    all = '<leader>dA',             -- Search all entries
    workflows = '<leader>dw',       -- Workflow picker
    workflow_attach = '<leader>da', -- Attach to workflow agent
  },

  -- Terminal settings
  terminal = {
    direction = 'float',  -- 'float', 'horizontal', 'vertical'
    float_opts = {
      border = 'rounded',
      width = 0.8,
      height = 0.8,
    },
  },

  -- Indexer settings
  indexer = {
    binary = 'doeff-indexer',  -- or full path to binary
    auto_refresh = true,        -- Clear cache on file save
    cache_ttl = 5000,          -- Cache TTL in milliseconds
  },

  -- Workflow settings (for doeff-agentic integration)
  workflows = {
    binary = 'doeff-agentic',  -- or full path to binary
  },

  -- Project root detection markers
  root_markers = {
    'pyproject.toml',
    'setup.py',
    'setup.cfg',
    '.git',
    '.doeff',
  },
})
```

## Usage

### Keybindings

| Binding | Action | Description |
|---------|--------|-------------|
| `<leader>de` | doeff **e**ntrypoints | Fuzzy search Program entrypoints |
| `<leader>dr` | doeff **r**un | Run entrypoint under cursor |
| `<leader>dP` | doeff **P**laylist | Search and run playlists |
| `<leader>dl` | doeff **l**ast | Re-run last entrypoint |
| `<leader>dt` | doeff **t**ransforms | Search transforms |
| `<leader>di` | doeff **i**nterpreters | Search interpreters |
| `<leader>dk` | doeff **k**leisli | Search kleisli functions |
| `<leader>dc` | doeff inter**c**eptors | Search interceptors |
| `<leader>dA` | doeff **A**ll | Search all entry types |
| `<leader>dw` | doeff **w**orkflows | List and manage workflows |
| `<leader>da` | doeff **a**ttach | Attach to workflow agent |

### Telescope Picker Actions

| Key | Action |
|-----|--------|
| `<CR>` | Run selected entrypoint in floating terminal |
| `<C-x>` | Run in horizontal split terminal |
| `<C-v>` | Run in vertical split terminal |
| `<C-f>` | Run in floating terminal |
| `<C-e>` | Edit/jump to entrypoint source |

### Commands

```vim
:DoeffEntrypoints      " Open entrypoints picker
:DoeffInterpreters     " Open interpreters picker
:DoeffKleisli          " Open kleisli functions picker
:DoeffTransforms       " Open transforms picker
:DoeffInterceptors     " Open interceptors picker
:DoeffAll              " Open all entries picker
:DoeffPlaylists        " Open playlists picker
:DoeffRunCursor        " Run entrypoint under cursor
:DoeffRunLast          " Re-run last entrypoint
:DoeffClearCache       " Clear indexer cache
:DoeffCloseTerminals   " Close all doeff terminals

" Workflow commands
:DoeffWorkflows        " Open workflows picker
:DoeffWorkflowAttach   " Attach to active workflow agent
:DoeffWorkflowStop {id} " Stop a workflow by ID/prefix
```

### Telescope Extension

```vim
:Telescope doeff              " Alias for :Telescope doeff entrypoints
:Telescope doeff entrypoints  " Search Program entrypoints
:Telescope doeff interpreters " Search interpreters only
:Telescope doeff kleisli      " Search kleisli functions only
:Telescope doeff transforms   " Search transforms only
:Telescope doeff playlists    " Search playlists
:Telescope doeff workflows    " List and manage workflows
```

### Lua API

```lua
local doeff = require('doeff')

-- Pickers
doeff.pick_entrypoints()
doeff.pick_interpreters()
doeff.pick_kleisli()
doeff.pick_transforms()
doeff.pick_playlists()
doeff.pick_workflows()

-- Run commands
doeff.run_cursor()           -- Run entrypoint at cursor
doeff.run_cursor('vertical') -- Run in vertical split
doeff.run_last()             -- Re-run last

-- Run a specific program
doeff.run({
  program = 'src.pipelines.main_pipeline',
  interpreter = 'src.interpreters.default',
  transform = 'src.transforms.optimize',
  cwd = '/path/to/project',
})

-- Get all entrypoints
local entries, err = doeff.get_entries()

-- Clear cache
doeff.clear_cache()

-- Workflow operations
doeff.workflow_attach()              -- Attach to active workflow
local wfs, err = doeff.list_workflows()    -- List all workflows
local wf, err = doeff.get_workflow('a3f')  -- Get workflow by ID/prefix
doeff.stop_workflow('a3f')                 -- Stop a workflow
```

## Entry Categories

The picker displays badges for each entry type:

| Badge | Category |
|-------|----------|
| `[I]` | Program Interpreter (`Program -> T`) |
| `[T]` | Program Transformer (`Program -> Program`) |
| `[K]` | Kleisli Program (`() -> Program[T]`) |
| `[@do]` | Function with `@do` decorator |
| `[IC]` | Interceptor (`Effect -> Effect/Program`) |

## Playlist Support

doeff.nvim reads playlist files from these locations:

- `.doeff-runner.playlists.json`
- `doeff-runner.playlists.json`
- `playlists.json`
- `.vscode/.doeff-runner.playlists.json`
- `.vscode/doeff-runner.playlists.json`
- `.vscode/playlists.json`

Playlists are compatible with the VSCode doeff-runner extension format.

## Workflow Monitoring

doeff.nvim integrates with `doeff-agentic` for monitoring and interacting with agent-based workflows.

### Workflow Picker

The workflow picker (`<leader>dw` or `:DoeffWorkflows`) displays:

```
┌─ Doeff Workflows [CR:attach C-w:watch C-k:kill C-s:send] ──────┐
│ > search...                                                     │
├─────────────────────────────────────────────────────────────────┤
│   a3f8b2c  pr-review-main       [blocked]   review-agent        │
│ > b7e1d4f  pr-review-feat-x     [running]   fix-agent           │
│   c9a2e6d  data-pipeline        [done]      -                   │
└─────────────────────────────────────────────────────────────────┘
```

### Workflow Picker Actions

| Key | Action |
|-----|--------|
| `<CR>` | Attach to workflow's agent (tmux session) |
| `<C-w>` | Watch workflow updates in terminal |
| `<C-k>` | Stop/kill workflow |
| `<C-s>` | Send message to agent |

### Quick Attach

`<leader>da` (or `:DoeffWorkflowAttach`) attaches to the current workflow:
- If only one active workflow exists, attaches directly
- If multiple workflows are running, opens the picker

### Requirements

The workflow features require `doeff-agentic` CLI to be installed:

```bash
# From the doeff repository
cd packages/doeff-agentic-cli
cargo build --release
# Add to PATH or configure in setup
```

## Related

- [doeff](https://github.com/proboscis/doeff) - The doeff framework
- [doeff-runner (VSCode)](../vscode/doeff-runner) - VSCode extension
- [doeff-indexer](../../packages/doeff-indexer) - CLI indexer for doeff
- [doeff-agentic](../../packages/doeff-agentic) - Agent workflow orchestration
- [doeff-agentic-cli](../../packages/doeff-agentic-cli) - Fast Rust CLI for workflows

## License

MIT
