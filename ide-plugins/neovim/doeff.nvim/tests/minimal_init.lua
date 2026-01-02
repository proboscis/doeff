-- Minimal init for running tests
-- Usage: nvim --headless -u tests/minimal_init.lua -c "PlenaryBustedDirectory tests/ {minimal_init = 'tests/minimal_init.lua'}"

local plenary_dir = os.getenv('PLENARY_DIR') or '/tmp/plenary.nvim'
local telescope_dir = os.getenv('TELESCOPE_DIR') or '/tmp/telescope.nvim'

-- Clone plenary if not exists
if vim.fn.isdirectory(plenary_dir) == 0 then
  vim.fn.system({
    'git',
    'clone',
    '--depth=1',
    'https://github.com/nvim-lua/plenary.nvim',
    plenary_dir,
  })
end

-- Clone telescope if not exists
if vim.fn.isdirectory(telescope_dir) == 0 then
  vim.fn.system({
    'git',
    'clone',
    '--depth=1',
    'https://github.com/nvim-telescope/telescope.nvim',
    telescope_dir,
  })
end

-- Add to runtimepath
vim.opt.runtimepath:append('.')
vim.opt.runtimepath:append(plenary_dir)
vim.opt.runtimepath:append(telescope_dir)

-- Setup plenary test harness
vim.cmd([[runtime plugin/plenary.vim]])
