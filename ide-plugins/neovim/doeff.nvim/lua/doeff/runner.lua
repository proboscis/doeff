-- doeff.nvim runner module
-- Handles terminal execution of doeff programs via tmux or external terminal

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

---Check if value is a valid string (not nil, not vim.NIL)
---@param val any
---@return boolean
local function is_valid_string(val)
  return val ~= nil and val ~= vim.NIL and type(val) == 'string' and val ~= ''
end

---Find worktree path for a given branch
---@param branch string Branch name
---@return string|nil worktree_path
local function find_worktree_for_branch(branch)
  local result = vim.fn.system('git worktree list --porcelain')
  if vim.v.shell_error ~= 0 then
    return nil
  end

  local current_worktree = nil
  for line in result:gmatch('[^\r\n]+') do
    local worktree_path = line:match('^worktree%s+(.+)$')
    if worktree_path then
      current_worktree = worktree_path
    end
    local worktree_branch = line:match('^branch%s+refs/heads/(.+)$')
    if worktree_branch and worktree_branch == branch and current_worktree then
      return current_worktree
    end
  end

  return nil
end

---Resolve the working directory for a run
---@param opts table Options with cwd, worktree, branch fields
---@return string cwd
local function resolve_cwd(opts)
  -- 1. If explicit worktree path is specified, use it
  if is_valid_string(opts.worktree) then
    if vim.fn.isdirectory(opts.worktree) == 1 then
      return opts.worktree
    else
      vim.notify('doeff: Worktree path not found: ' .. opts.worktree, vim.log.levels.WARN)
    end
  end

  -- 2. If branch is specified, find worktree for that branch
  if is_valid_string(opts.branch) then
    local worktree_path = find_worktree_for_branch(opts.branch)
    if worktree_path then
      return worktree_path
    else
      vim.notify('doeff: No worktree found for branch: ' .. opts.branch, vim.log.levels.WARN)
    end
  end

  -- 3. If explicit cwd is specified, use it
  if is_valid_string(opts.cwd) then
    return opts.cwd
  end

  -- 4. Fall back to project root or current directory
  return indexer.find_root() or vim.fn.getcwd()
end

---Build the command to run a doeff program
---@param opts DoeffRunOpts
---@return string|nil command (shell string)
---@return string|nil error
function M.build_command(opts)
  if not opts then
    return nil, 'opts is nil'
  end
  if not is_valid_string(opts.program) then
    return nil, 'opts.program is missing or invalid'
  end

  local parts = { 'uv', 'run', 'doeff', 'run' }

  table.insert(parts, '--program')
  table.insert(parts, opts.program)

  if is_valid_string(opts.interpreter) then
    table.insert(parts, '--interpreter')
    table.insert(parts, opts.interpreter)
  end

  if is_valid_string(opts.transform) then
    table.insert(parts, '--transform')
    table.insert(parts, opts.transform)
  end

  return table.concat(parts, ' '), nil
end

---Check if we're running inside tmux
---@return boolean
local function in_tmux()
  return vim.env.TMUX ~= nil and vim.env.TMUX ~= ''
end

---Run command in a new tmux pane (split)
---@param cmd string Command to run
---@param cwd string Working directory
---@param direction string 'horizontal' or 'vertical'
local function run_in_tmux_pane(cmd, cwd, direction)
  local split_flag = direction == 'horizontal' and '-v' or '-h'
  -- Use tmux send-keys to run command in new pane, keeps pane open
  local tmux_cmd = string.format(
    'tmux split-window %s -c %s',
    split_flag,
    vim.fn.shellescape(cwd)
  )
  vim.fn.system(tmux_cmd)
  -- Send the command to the new pane
  vim.fn.system('tmux send-keys ' .. vim.fn.shellescape(cmd) .. ' Enter')
end

---Run command in a new tmux window
---@param cmd string Command to run
---@param cwd string Working directory
---@param name string|nil Window name
local function run_in_tmux_window(cmd, cwd, name)
  -- Create new window first
  local tmux_cmd = string.format(
    'tmux new-window -c %s -n %s',
    vim.fn.shellescape(cwd),
    vim.fn.shellescape(name or 'doeff')
  )
  vim.fn.system(tmux_cmd)
  -- Send the command to the new window
  vim.fn.system('tmux send-keys ' .. vim.fn.shellescape(cmd) .. ' Enter')
end

---Run command in an external terminal (macOS)
---@param cmd string Command to run
---@param cwd string Working directory
local function run_in_external_terminal(cmd, cwd)
  -- Use open with Terminal.app or iTerm2
  local script = string.format(
    [[tell application "Terminal"
      activate
      do script "cd %s && %s"
    end tell]],
    cwd:gsub('"', '\\"'),
    cmd:gsub('"', '\\"')
  )
  vim.fn.system({ 'osascript', '-e', script })
end

---Run a doeff program
---@param opts DoeffRunOpts
---@param direction string|nil 'tmux_pane', 'tmux_window', 'horizontal', 'vertical', or 'external'
function M.run(opts, direction)
  local cfg = config.get()
  direction = direction or cfg.terminal.direction

  local cmd, err = M.build_command(opts)
  if err then
    vim.notify('doeff: ' .. err, vim.log.levels.ERROR)
    return
  end

  -- Resolve working directory (handles worktree, branch, cwd)
  local cwd = resolve_cwd(opts)

  -- Store for re-run
  last_run = {
    opts = opts,
    direction = direction,
  }

  if in_tmux() then
    if direction == 'horizontal' then
      run_in_tmux_pane(cmd, cwd, 'horizontal')
      vim.notify('doeff: Running in tmux horizontal pane', vim.log.levels.INFO)
    elseif direction == 'vertical' then
      run_in_tmux_pane(cmd, cwd, 'vertical')
      vim.notify('doeff: Running in tmux vertical pane', vim.log.levels.INFO)
    else
      -- Default: new tmux window
      run_in_tmux_window(cmd, cwd, opts.program and opts.program:match('[^.]+$') or 'doeff')
      vim.notify('doeff: Running in new tmux window', vim.log.levels.INFO)
    end
  else
    -- Not in tmux - use external terminal
    run_in_external_terminal(cmd, cwd)
    vim.notify('doeff: Running in external terminal', vim.log.levels.INFO)
  end
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

return M
