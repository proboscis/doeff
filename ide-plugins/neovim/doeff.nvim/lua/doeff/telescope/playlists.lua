-- doeff.nvim playlists telescope picker
local M = {}

local indexer = require('doeff.indexer')
local runner = require('doeff.runner')
local config = require('doeff.config')

local pickers = require('telescope.pickers')
local finders = require('telescope.finders')
local conf = require('telescope.config').values
local actions = require('telescope.actions')
local action_state = require('telescope.actions.state')
local previewers = require('telescope.previewers')
local entry_display = require('telescope.pickers.entry_display')

-- Playlist file names to search for
local PLAYLIST_FILES = {
  '.doeff-runner.playlists.json',
  'doeff-runner.playlists.json',
  'playlists.json',
}

---Find playlist file in project
---@param root string Project root
---@return string|nil path Path to playlist file
local function find_playlist_file(root)
  for _, filename in ipairs(PLAYLIST_FILES) do
    local path = root .. '/' .. filename
    if vim.fn.filereadable(path) == 1 then
      return path
    end

    -- Also check .vscode directory
    local vscode_path = root .. '/.vscode/' .. filename
    if vim.fn.filereadable(vscode_path) == 1 then
      return vscode_path
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

---Create previewer for playlist items
---@return table
local function make_previewer()
  return previewers.new_buffer_previewer({
    title = 'Playlist Item Preview',
    define_preview = function(self, entry, status)
      local value = entry.value
      local item = value.item
      local lines = {}

      table.insert(lines, '# ' .. item.name)
      table.insert(lines, '')
      table.insert(lines, 'Playlist: ' .. value.playlist_name)
      table.insert(lines, 'Type: ' .. item.type)
      table.insert(lines, '')

      if item.type == 'doeff' then
        table.insert(lines, '## Doeff Configuration')
        table.insert(lines, '')
        if item.program then
          table.insert(lines, 'Program: ' .. item.program)
        end
        if item.interpreter then
          table.insert(lines, 'Interpreter: ' .. item.interpreter)
        end
        if item.transform then
          table.insert(lines, 'Transform: ' .. item.transform)
        end
        if item.apply then
          table.insert(lines, 'Apply: ' .. item.apply)
        end

        if item.args then
          table.insert(lines, '')
          table.insert(lines, '## Arguments')
          for k, v in pairs(item.args) do
            table.insert(lines, '  ' .. k .. ': ' .. vim.inspect(v))
          end
        end
      else
        table.insert(lines, '## Command')
        table.insert(lines, '')
        table.insert(lines, '```bash')
        table.insert(lines, item.cmd or '')
        table.insert(lines, '```')
      end

      -- Additional options
      table.insert(lines, '')
      table.insert(lines, '## Options')
      if item.branch then
        table.insert(lines, 'Branch: ' .. item.branch)
      end
      if item.commit then
        table.insert(lines, 'Commit: ' .. item.commit)
      end
      if item.worktree then
        table.insert(lines, 'Worktree: ' .. item.worktree)
      end
      if item.cwd then
        table.insert(lines, 'CWD: ' .. item.cwd)
      end

      vim.api.nvim_buf_set_lines(self.state.bufnr, 0, -1, false, lines)
      vim.api.nvim_set_option_value('filetype', 'markdown', { buf = self.state.bufnr })
    end,
  })
end

---Run a playlist item
---@param entry table Entry from picker
---@param direction string Terminal direction
local function run_item(entry, direction)
  local item = entry.item
  local cfg = config.get()

  if item.type == 'doeff' then
    runner.run({
      program = item.program,
      interpreter = item.interpreter,
      transform = item.transform,
      cwd = item.cwd,
      args = item.args,
    }, direction)
  else
    -- Custom command
    local cmd = item.cmd
    if not cmd or cmd == '' then
      vim.notify('doeff: Empty command', vim.log.levels.WARN)
      return
    end

    local root = indexer.find_root()
    local cwd = item.cwd or root or vim.fn.getcwd()

    -- Create terminal and run command
    local buf
    if direction == 'float' then
      local opts = cfg.terminal.float_opts
      local width = math.floor(vim.o.columns * opts.width)
      local height = math.floor(vim.o.lines * opts.height)
      local row = math.floor((vim.o.lines - height) / 2)
      local col = math.floor((vim.o.columns - width) / 2)

      buf = vim.api.nvim_create_buf(false, true)
      vim.api.nvim_open_win(buf, true, {
        relative = 'editor',
        width = width,
        height = height,
        row = row,
        col = col,
        style = 'minimal',
        border = opts.border,
        title = ' ' .. item.name .. ' ',
        title_pos = 'center',
      })
    elseif direction == 'horizontal' then
      vim.cmd('botright split')
      buf = vim.api.nvim_create_buf(false, true)
      vim.api.nvim_win_set_buf(0, buf)
      vim.cmd('resize 15')
    else
      vim.cmd('botright vsplit')
      buf = vim.api.nvim_create_buf(false, true)
      vim.api.nvim_win_set_buf(0, buf)
    end

    vim.fn.termopen(cmd, { cwd = cwd })
    vim.cmd('startinsert')
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

  pickers.new(opts, {
    prompt_title = 'Doeff Playlists',
    finder = finders.new_table({
      results = items,
      entry_maker = make_entry_maker(),
    }),
    sorter = conf.generic_sorter(opts),
    previewer = make_previewer(),
    attach_mappings = function(prompt_bufnr, map)
      -- Default action: run in float
      actions.select_default:replace(run_action('float'))

      -- Custom mappings
      map('i', '<C-x>', run_action('horizontal'))
      map('n', '<C-x>', run_action('horizontal'))
      map('i', '<C-v>', run_action('vertical'))
      map('n', '<C-v>', run_action('vertical'))
      map('i', '<C-f>', run_action('float'))
      map('n', '<C-f>', run_action('float'))

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
