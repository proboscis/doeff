# doeff.nvim

Neovim plugin for running [doeff](https://github.com/CyberAgentAILab/doeff) entrypoints with Telescope integration.

## Features

- Fuzzy search all doeff entrypoints (interpreters, kleisli, transforms)
- Preview function signatures and docstrings
- Run entrypoints in floating, horizontal, or vertical terminals
- Run entrypoint under cursor
- Re-run last executed entrypoint
- Playlist support for saved run configurations
- Auto-refresh index on file save

## Requirements

- Neovim >= 0.9.0
- [telescope.nvim](https://github.com/nvim-telescope/telescope.nvim)
- [plenary.nvim](https://github.com/nvim-lua/plenary.nvim)
- `doeff-indexer` binary in PATH

## Installation

### lazy.nvim

```lua
{
  'CyberAgentAILab/doeff.nvim',
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
  'CyberAgentAILab/doeff.nvim',
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
    entrypoints = '<leader>de',  -- Fuzzy search all entrypoints
    run_cursor = '<leader>dr',   -- Run entrypoint under cursor
    playlists = '<leader>dp',    -- Search and run playlists
    run_last = '<leader>dl',     -- Re-run last entrypoint
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
| `<leader>de` | doeff **e**ntrypoints | Fuzzy search all entrypoints |
| `<leader>dr` | doeff **r**un | Run entrypoint under cursor |
| `<leader>dp` | doeff **p**laylist | Search and run playlists |
| `<leader>dl` | doeff **l**ast | Re-run last entrypoint |

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
:DoeffEntrypoints     " Open entrypoints picker
:DoeffInterpreters    " Open interpreters picker
:DoeffKleisli         " Open kleisli functions picker
:DoeffTransforms      " Open transforms picker
:DoeffPlaylists       " Open playlists picker
:DoeffRunCursor       " Run entrypoint under cursor
:DoeffRunLast         " Re-run last entrypoint
:DoeffClearCache      " Clear indexer cache
:DoeffCloseTerminals  " Close all doeff terminals
```

### Telescope Extension

```vim
:Telescope doeff              " Alias for :Telescope doeff entrypoints
:Telescope doeff entrypoints  " Search all entrypoints
:Telescope doeff interpreters " Search interpreters only
:Telescope doeff kleisli      " Search kleisli functions only
:Telescope doeff transforms   " Search transforms only
:Telescope doeff playlists    " Search playlists
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

## Related

- [doeff](https://github.com/CyberAgentAILab/doeff) - The doeff framework
- [doeff-runner (VSCode)](../vscode/doeff-runner) - VSCode extension
- [doeff-indexer](../../packages/doeff-indexer) - CLI indexer for doeff

## License

MIT
