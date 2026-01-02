-- doeff.nvim playlists telescope picker
local M = {}

local indexer = require('doeff.indexer')
local runner = require('doeff.runner')

local pickers = require('telescope.pickers')
local finders = require('telescope.finders')
local conf = require('telescope.config').values
local actions = require('telescope.actions')
local action_state = require('telescope.actions.state')
local entry_display = require('telescope.pickers.entry_display')

-- Playlist file paths to search for (in order of priority)
local PLAYLIST_RELPATHS = {
  'doeff/playlists.json',                -- Inside .git or gitdir
}

local PLAYLIST_ROOT_PATHS = {
  '.doeff-runner.playlists.json',
  'doeff-runner.playlists.json',
  'playlists.json',
  '.vscode/.doeff-runner.playlists.json',
  '.vscode/doeff-runner.playlists.json',
  '.vscode/playlists.json',
}

---Resolve gitdir (handles worktrees where .git is a file)
---@param root string Project root
---@return string|nil gitdir
local function resolve_gitdir(root)
  local git_path = root .. '/.git'

  -- Check if .git is a directory
  if vim.fn.isdirectory(git_path) == 1 then
    return git_path
  end

  -- Check if .git is a file (worktree)
  if vim.fn.filereadable(git_path) == 1 then
    local content = vim.fn.readfile(git_path)
    if content and content[1] then
      local gitdir = content[1]:match('gitdir:%s*(.+)')
      if gitdir and vim.fn.isdirectory(gitdir) == 1 then
        return gitdir
      end
    end
  end

  return nil
end

---Find playlist file in project
---@param root string Project root
---@return string|nil path Path to playlist file
local function find_playlist_file(root)
  -- First check gitdir (handles worktrees)
  local gitdir = resolve_gitdir(root)
  if gitdir then
    for _, relpath in ipairs(PLAYLIST_RELPATHS) do
      local path = gitdir .. '/' .. relpath
      if vim.fn.filereadable(path) == 1 then
        return path
      end
    end
  end

  -- Then check root-level paths
  for _, relpath in ipairs(PLAYLIST_ROOT_PATHS) do
    local path = root .. '/' .. relpath
    if vim.fn.filereadable(path) == 1 then
      return path
    end
  end

  return nil
end

---Parse playlist file
---@param path string Path to playlist file
---@return table|nil playlists
---@return string|nil error
local function parse_playlists(path)
  local content = vim.fn.readfile(path)
  if not content or #content == 0 then
    return nil, 'Empty playlist file'
  end

  local json_str = table.concat(content, '\n')
  local ok, data = pcall(vim.json.decode, json_str)
  if not ok then
    return nil, 'Failed to parse JSON: ' .. tostring(data)
  end

  return data, nil
end

---Flatten playlists into items with playlist name
---@param data table Playlist data
---@return table[] items
local function flatten_playlists(data)
  local items = {}

  local playlists = data.playlists or {}
  for _, playlist in ipairs(playlists) do
    for _, item in ipairs(playlist.items or {}) do
      table.insert(items, {
        playlist_name = playlist.name,
        playlist_id = playlist.id,
        item = item,
      })
    end
  end

  return items
end

---Create entry maker for playlist items
---@return function
local function make_entry_maker()
  local displayer = entry_display.create({
    separator = ' ',
    items = {
      { width = 8 },      -- Type badge
      { width = 25 },     -- Item name
      { width = 20 },     -- Playlist name
      { remaining = true }, -- Program/command
    },
  })

  return function(entry)
    local item = entry.item
    local type_badge = item.type == 'doeff' and '[doeff]' or '[cmd]'
    local detail = item.type == 'doeff' and (item.program or '') or (item.cmd or '')

    return {
      value = entry,
      display = function()
        return displayer({
          { type_badge, 'TelescopeResultsComment' },
          { item.name, 'TelescopeResultsIdentifier' },
          { entry.playlist_name, 'TelescopeResultsSpecialComment' },
          { detail, 'TelescopeResultsComment' },
        })
      end,
      ordinal = item.name .. ' ' .. entry.playlist_name .. ' ' .. detail,
    }
  end
end

-- Note: Previewer disabled due to Telescope buffer timing issues
-- The picker list shows item name, playlist, and type which is sufficient

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

---Resolve cwd for a playlist item
---@param item table Playlist item
---@return string cwd
local function resolve_item_cwd(item)
  -- 1. If explicit worktree path is specified, use it
  if is_valid_string(item.worktree) then
    if vim.fn.isdirectory(item.worktree) == 1 then
      return item.worktree
    end
  end

  -- 2. If branch is specified, find worktree for that branch
  if is_valid_string(item.branch) then
    local worktree_path = find_worktree_for_branch(item.branch)
    if worktree_path then
      return worktree_path
    else
      vim.notify('doeff: No worktree found for branch: ' .. item.branch, vim.log.levels.WARN)
    end
  end

  -- 3. If explicit cwd is specified, use it
  if is_valid_string(item.cwd) then
    return item.cwd
  end

  -- 4. Fall back to project root or current directory
  return indexer.find_root() or vim.fn.getcwd()
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

---Run a custom command in tmux or external terminal
---@param cmd string Command to run
---@param cwd string Working directory
---@param name string|nil Name for tmux window
---@param direction string Terminal direction
local function run_custom_command(cmd, cwd, name, direction)
  if in_tmux() then
    if direction == 'horizontal' then
      run_in_tmux_pane(cmd, cwd, 'horizontal')
      vim.notify('doeff: Running in tmux horizontal pane', vim.log.levels.INFO)
    elseif direction == 'vertical' then
      run_in_tmux_pane(cmd, cwd, 'vertical')
      vim.notify('doeff: Running in tmux vertical pane', vim.log.levels.INFO)
    else
      run_in_tmux_window(cmd, cwd, name or 'doeff')
      vim.notify('doeff: Running in new tmux window', vim.log.levels.INFO)
    end
  else
    run_in_external_terminal(cmd, cwd)
    vim.notify('doeff: Running in external terminal', vim.log.levels.INFO)
  end
end

---Run a playlist item
---@param entry table Entry from picker
---@param direction string Terminal direction
local function run_item(entry, direction)
  local item = entry.item

  if item.type == 'doeff' then
    runner.run({
      program = item.program,
      interpreter = item.interpreter,
      transform = item.transform,
      cwd = item.cwd,
      branch = item.branch,
      worktree = item.worktree,
      args = item.args,
    }, direction)
  else
    -- Custom command
    local cmd = item.cmd
    if not cmd or cmd == '' then
      vim.notify('doeff: Empty command', vim.log.levels.WARN)
      return
    end

    -- Resolve cwd (handles worktree, branch, cwd)
    local cwd = resolve_item_cwd(item)

    run_custom_command(cmd, cwd, item.name, direction)
  end
end

---Create action to run playlist item
---@param direction string Terminal direction
---@return function
local function run_action(direction)
  return function(prompt_bufnr)
    local selection = action_state.get_selected_entry()
    actions.close(prompt_bufnr)
    if selection then
      run_item(selection.value, direction)
    end
  end
end

---Create action to edit the playlist file
---@return function
local function edit_action()
  return function(prompt_bufnr)
    local selection = action_state.get_selected_entry()
    actions.close(prompt_bufnr)
    if selection and selection.value and selection.value.playlist_path then
      vim.cmd('edit ' .. vim.fn.fnameescape(selection.value.playlist_path))
      -- Try to search for the item name to jump to it
      local item_name = selection.value.item.name or selection.value.item.id
      if item_name then
        vim.fn.search('"name":\\s*"' .. vim.fn.escape(item_name, '\\/'))
      end
    end
  end
end

---Create action to copy the run command to clipboard
---@return function
local function copy_command_action()
  return function(prompt_bufnr)
    local selection = action_state.get_selected_entry()
    if selection and selection.value then
      local item = selection.value.item
      local cmd
      if item.type == 'doeff' then
        cmd = 'uv run doeff run --program ' .. (item.program or '')
        if is_valid_string(item.transform) then
          cmd = cmd .. ' --transform ' .. item.transform
        end
      else
        cmd = item.cmd or ''
      end
      vim.fn.setreg('+', cmd)
      vim.notify('doeff: Copied command to clipboard', vim.log.levels.INFO)
    end
  end
end

---Main playlists picker
---@param opts table|nil Telescope picker options
function M.picker(opts)
  opts = opts or {}

  local root = indexer.find_root()
  if not root then
    vim.notify('doeff: Could not find project root', vim.log.levels.ERROR)
    return
  end

  local playlist_path = find_playlist_file(root)
  if not playlist_path then
    vim.notify('doeff: No playlist file found', vim.log.levels.INFO)
    return
  end

  local data, err = parse_playlists(playlist_path)
  if err then
    vim.notify('doeff: ' .. err, vim.log.levels.ERROR)
    return
  end

  local items = flatten_playlists(data)
  if #items == 0 then
    vim.notify('doeff: No playlist items found', vim.log.levels.INFO)
    return
  end

  -- Store playlist path in each item for edit action
  for _, item in ipairs(items) do
    item.playlist_path = playlist_path
  end

  pickers.new(opts, {
    prompt_title = 'Doeff Playlists [Enter:run C-e:edit C-y:copy ?:help]',
    finder = finders.new_table({
      results = items,
      entry_maker = make_entry_maker(),
    }),
    sorter = conf.generic_sorter(opts),
    previewer = false,
    attach_mappings = function(prompt_bufnr, map)
      -- Default action: run in new tmux window
      actions.select_default:replace(run_action('float'))

      -- Run mappings
      map('i', '<C-x>', run_action('horizontal'), { desc = 'Run in horizontal pane' })
      map('n', '<C-x>', run_action('horizontal'), { desc = 'Run in horizontal pane' })
      map('i', '<C-v>', run_action('vertical'), { desc = 'Run in vertical pane' })
      map('n', '<C-v>', run_action('vertical'), { desc = 'Run in vertical pane' })

      -- Edit playlist file
      map('i', '<C-e>', edit_action(), { desc = 'Edit playlist file' })
      map('n', '<C-e>', edit_action(), { desc = 'Edit playlist file' })

      -- Copy command to clipboard
      map('i', '<C-y>', copy_command_action(), { desc = 'Copy command to clipboard' })
      map('n', '<C-y>', copy_command_action(), { desc = 'Copy command to clipboard' })

      return true
    end,
  }):find()
end

---Get all playlists (for programmatic access)
---@return table|nil playlists
---@return string|nil error
function M.get_playlists()
  local root = indexer.find_root()
  if not root then
    return nil, 'Could not find project root'
  end

  local playlist_path = find_playlist_file(root)
  if not playlist_path then
    return nil, 'No playlist file found'
  end

  return parse_playlists(playlist_path)
end

return M
