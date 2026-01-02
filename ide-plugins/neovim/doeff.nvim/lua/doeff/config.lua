-- doeff.nvim configuration module
local M = {}

---@class DoeffKeymapConfig
---@field entrypoints string Keymap to open entrypoint picker
---@field run_cursor string Keymap to run entrypoint under cursor
---@field playlists string Keymap to open playlist picker
---@field run_last string Keymap to re-run last entrypoint

---@class DoeffFloatOpts
---@field border string Border style for floating window
---@field width number|float Width (fraction of screen if < 1)
---@field height number|float Height (fraction of screen if < 1)

---@class DoeffTerminalConfig
---@field direction string Terminal direction: 'float', 'horizontal', 'vertical'
---@field float_opts DoeffFloatOpts Floating terminal options

---@class DoeffIndexerConfig
---@field binary string Path to doeff-indexer binary
---@field auto_refresh boolean Auto-refresh index on file save
---@field cache_ttl number Cache TTL in milliseconds

---@class DoeffConfig
---@field keymaps DoeffKeymapConfig
---@field terminal DoeffTerminalConfig
---@field indexer DoeffIndexerConfig
---@field root_markers string[] Markers for finding project root

---@type DoeffConfig
M.defaults = {
  keymaps = {
    entrypoints = '<leader>de',    -- Program entrypoints
    run_cursor = '<leader>dr',     -- Run under cursor
    playlists = '<leader>dP',      -- Playlists (capital P)
    run_last = '<leader>dl',       -- Re-run last
    transforms = '<leader>dt',     -- Transforms
    interpreters = '<leader>di',   -- Interpreters
    kleisli = '<leader>dk',        -- Kleisli functions
    interceptors = '<leader>dc',   -- Interceptors
    all = '<leader>da',            -- All entries
  },
  terminal = {
    direction = 'float', -- 'float', 'horizontal', 'vertical'
    float_opts = {
      border = 'rounded',
      width = 0.8,
      height = 0.8,
    },
  },
  indexer = {
    binary = 'doeff-indexer',
    auto_refresh = true,
    cache_ttl = 5000, -- 5 seconds
  },
  root_markers = {
    'pyproject.toml',
    'setup.py',
    'setup.cfg',
    '.git',
    '.doeff',
  },
}

---@type DoeffConfig
M.values = vim.deepcopy(M.defaults)

---Setup configuration with user overrides
---@param opts DoeffConfig|nil
function M.setup(opts)
  M.values = vim.tbl_deep_extend('force', M.defaults, opts or {})
end

---Get current configuration
---@return DoeffConfig
function M.get()
  return M.values
end

return M
