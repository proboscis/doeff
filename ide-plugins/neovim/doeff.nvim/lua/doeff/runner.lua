-- doeff.nvim runner module
-- Handles terminal execution of doeff programs

local M = {}

local config = require('doeff.config')
local indexer = require('doeff.indexer')

---@class DoeffRunOpts
---@field program string Qualified program name
---@field interpreter string|nil Interpreter qualified name
---@field transform string|nil Transform qualified name
---@field cwd string|nil Working directory
---@field args table|nil Additional arguments

-- Store last run info for re-run functionality
local last_run = nil

-- Active terminal buffer/window tracking
local terminals = {}

---Build the command to run a doeff program
---@param opts DoeffRunOpts
---@return string[] command
function M.build_command(opts)
  local cmd = { 'python', '-m', 'doeff', 'run' }

  table.insert(cmd, '--program')
  table.insert(cmd, opts.program)

  if opts.interpreter then
    table.insert(cmd, '--interpreter')
    table.insert(cmd, opts.interpreter)
  end

  if opts.transform then
    table.insert(cmd, '--transform')
    table.insert(cmd, opts.transform)
  end

  return cmd
end

---Create a floating terminal window
---@param opts DoeffFloatOpts
---@return number bufnr
---@return number winnr
local function create_float_terminal(opts)
  local width = math.floor(vim.o.columns * opts.width)
  local height = math.floor(vim.o.lines * opts.height)
  local row = math.floor((vim.o.lines - height) / 2)
  local col = math.floor((vim.o.columns - width) / 2)

  local buf = vim.api.nvim_create_buf(false, true)
  local win = vim.api.nvim_open_win(buf, true, {
    relative = 'editor',
    width = width,
    height = height,
    row = row,
    col = col,
    style = 'minimal',
    border = opts.border,
    title = ' doeff ',
    title_pos = 'center',
  })

  -- Set terminal-friendly options
  vim.api.nvim_set_option_value('winhl', 'Normal:Normal,FloatBorder:FloatBorder', { win = win })

  return buf, win
end

---Create a horizontal split terminal
---@return number bufnr
---@return number winnr
local function create_horizontal_terminal()
  vim.cmd('botright split')
  local win = vim.api.nvim_get_current_win()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_win_set_buf(win, buf)
  vim.cmd('resize 15')
  return buf, win
end

---Create a vertical split terminal
---@return number bufnr
---@return number winnr
local function create_vertical_terminal()
  vim.cmd('botright vsplit')
  local win = vim.api.nvim_get_current_win()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_win_set_buf(win, buf)
  return buf, win
end

---Create terminal based on direction
---@param direction string 'float', 'horizontal', or 'vertical'
---@return number bufnr
---@return number winnr
local function create_terminal(direction)
  local cfg = config.get()

  if direction == 'float' then
    return create_float_terminal(cfg.terminal.float_opts)
  elseif direction == 'horizontal' then
    return create_horizontal_terminal()
  elseif direction == 'vertical' then
    return create_vertical_terminal()
  else
    return create_float_terminal(cfg.terminal.float_opts)
  end
end

---Run a doeff program in a terminal
---@param opts DoeffRunOpts
---@param direction string|nil Terminal direction override
function M.run(opts, direction)
  local cfg = config.get()
  direction = direction or cfg.terminal.direction

  local cmd = M.build_command(opts)
  local cmd_str = table.concat(cmd, ' ')

  -- Store for re-run
  last_run = {
    opts = opts,
    direction = direction,
  }

  -- Create terminal
  local buf, win = create_terminal(direction)

  -- Run the command
  local cwd = opts.cwd or indexer.find_root() or vim.fn.getcwd()
  vim.fn.termopen(cmd_str, {
    cwd = cwd,
    on_exit = function(_, code, _)
      if code == 0 then
        vim.notify('doeff: Program completed successfully', vim.log.levels.INFO)
      else
        vim.notify('doeff: Program exited with code ' .. code, vim.log.levels.WARN)
      end
    end,
  })

  -- Enter insert mode for terminal interaction
  vim.cmd('startinsert')

  -- Track terminal
  terminals[buf] = {
    win = win,
    opts = opts,
    direction = direction,
  }

  -- Set up buffer-local keymaps for terminal
  vim.keymap.set('t', '<Esc>', [[<C-\><C-n>]], { buffer = buf, desc = 'Exit terminal mode' })
  vim.keymap.set('n', 'q', function()
    vim.api.nvim_win_close(win, true)
  end, { buffer = buf, desc = 'Close terminal' })

  return buf, win
end

---Run an entry from the indexer
---@param entry DoeffEntry
---@param direction string|nil Terminal direction override
function M.run_entry(entry, direction)
  local opts = {
    program = entry.qualified_name,
    cwd = indexer.find_root(),
  }

  -- Determine if this is an interpreter, transform, or kleisli
  local categories = entry.categories or {}
  local is_interpreter = vim.tbl_contains(categories, 'program_interpreter')
  local is_kleisli = vim.tbl_contains(categories, 'kleisli_program') or vim.tbl_contains(categories, 'do_function')

  if is_interpreter then
    -- Interpreters need a program to run - for now just run with default
    opts.interpreter = entry.qualified_name
    opts.program = nil
    -- We need to prompt for a program or use a default
    vim.notify('doeff: Running interpreter - select a program to interpret', vim.log.levels.INFO)
    return
  end

  M.run(opts, direction)
end

---Re-run the last executed program
---@param direction string|nil Override direction (nil uses last direction)
function M.run_last(direction)
  if not last_run then
    vim.notify('doeff: No previous run to repeat', vim.log.levels.WARN)
    return
  end

  M.run(last_run.opts, direction or last_run.direction)
end

---Run entrypoint under cursor
---@param direction string|nil Terminal direction
function M.run_cursor(direction)
  local file = vim.api.nvim_buf_get_name(0)
  local line = vim.api.nvim_win_get_cursor(0)[1]

  local entry = indexer.find_at_location(file, line)
  if not entry then
    vim.notify('doeff: No entrypoint found at cursor', vim.log.levels.WARN)
    return
  end

  M.run_entry(entry, direction)
end

---Get last run info
---@return table|nil
function M.get_last_run()
  return last_run
end

---Close all doeff terminals
function M.close_all()
  for buf, info in pairs(terminals) do
    if vim.api.nvim_buf_is_valid(buf) and vim.api.nvim_win_is_valid(info.win) then
      vim.api.nvim_win_close(info.win, true)
    end
  end
  terminals = {}
end

return M
