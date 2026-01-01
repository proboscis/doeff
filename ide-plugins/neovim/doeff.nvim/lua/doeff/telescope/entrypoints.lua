-- doeff.nvim entrypoints telescope picker
local M = {}

local indexer = require('doeff.indexer')
local runner = require('doeff.runner')

local pickers = require('telescope.pickers')
local finders = require('telescope.finders')
local conf = require('telescope.config').values
local actions = require('telescope.actions')
local action_state = require('telescope.actions.state')
local previewers = require('telescope.previewers')
local entry_display = require('telescope.pickers.entry_display')

---Format category badges for display
---@param categories string[]
---@return string
local function format_categories(categories)
  local badges = {}
  for _, cat in ipairs(categories) do
    if cat == 'program_interpreter' then
      table.insert(badges, '[I]')
    elseif cat == 'program_transformer' then
      table.insert(badges, '[T]')
    elseif cat == 'kleisli_program' then
      table.insert(badges, '[K]')
    elseif cat == 'do_function' then
      table.insert(badges, '[@do]')
    elseif cat == 'interceptor' then
      table.insert(badges, '[IC]')
    end
  end
  return table.concat(badges, ' ')
end

---Get relative path from root
---@param file_path string
---@param root string
---@return string
local function get_relative_path(file_path, root)
  if file_path:sub(1, #root) == root then
    return file_path:sub(#root + 2)
  end
  return file_path
end

---Create entry maker for telescope
---@param root string Project root
---@return function
local function make_entry_maker(root)
  local displayer = entry_display.create({
    separator = ' ',
    items = {
      { width = 8 },     -- Category badge
      { width = 30 },    -- Name
      { remaining = true }, -- File path
    },
  })

  return function(entry)
    local relative_path = get_relative_path(entry.file_path, root)
    local display_path = relative_path .. ':' .. entry.line
    local badges = format_categories(entry.categories or {})

    return {
      value = entry,
      display = function()
        return displayer({
          { badges, 'TelescopeResultsComment' },
          { entry.name, 'TelescopeResultsIdentifier' },
          { display_path, 'TelescopeResultsComment' },
        })
      end,
      ordinal = entry.name .. ' ' .. entry.qualified_name .. ' ' .. relative_path,
      filename = entry.file_path,
      lnum = entry.line,
    }
  end
end

---Create previewer for entrypoints
---@return table
local function make_previewer()
  return previewers.new_buffer_previewer({
    title = 'Entrypoint Preview',
    define_preview = function(self, entry, status)
      local value = entry.value
      local lines = {}

      -- Header with name and type
      table.insert(lines, '# ' .. value.name)
      table.insert(lines, '')

      -- Categories
      if value.categories and #value.categories > 0 then
        table.insert(lines, 'Categories: ' .. table.concat(value.categories, ', '))
      end

      -- Markers
      if value.markers and #value.markers > 0 then
        table.insert(lines, 'Markers: ' .. table.concat(value.markers, ', '))
      end

      -- Decorators
      if value.decorators and #value.decorators > 0 then
        table.insert(lines, 'Decorators: ' .. table.concat(value.decorators, ', '))
      end

      -- Docstring
      if value.docstring and value.docstring ~= '' then
        table.insert(lines, '')
        table.insert(lines, '## Description')
        table.insert(lines, value.docstring)
      end

      -- Parameters
      if value.program_parameters and #value.program_parameters > 0 then
        table.insert(lines, '')
        table.insert(lines, '## Parameters')
        for _, param in ipairs(value.program_parameters) do
          local param_line = '  - ' .. param.name
          if param.annotation then
            param_line = param_line .. ': ' .. param.annotation
          end
          if not param.is_required then
            param_line = param_line .. ' (optional)'
          end
          table.insert(lines, param_line)
        end
      end

      -- Return annotation
      if value.return_annotation then
        table.insert(lines, '')
        table.insert(lines, 'Returns: ' .. value.return_annotation)
      end

      -- File location
      table.insert(lines, '')
      table.insert(lines, '---')
      table.insert(lines, 'File: ' .. value.file_path .. ':' .. value.line)
      table.insert(lines, 'Qualified: ' .. value.qualified_name)

      vim.api.nvim_buf_set_lines(self.state.bufnr, 0, -1, false, lines)
      vim.api.nvim_set_option_value('filetype', 'markdown', { buf = self.state.bufnr })
    end,
  })
end

---Create action to run entrypoint
---@param direction string Terminal direction
---@return function
local function run_action(direction)
  return function(prompt_bufnr)
    local selection = action_state.get_selected_entry()
    actions.close(prompt_bufnr)
    if selection then
      runner.run_entry(selection.value, direction)
    end
  end
end

---Create action to edit/jump to entrypoint
---@return function
local function edit_action()
  return function(prompt_bufnr)
    local selection = action_state.get_selected_entry()
    actions.close(prompt_bufnr)
    if selection then
      vim.cmd('edit ' .. vim.fn.fnameescape(selection.value.file_path))
      vim.api.nvim_win_set_cursor(0, { selection.value.line, 0 })
    end
  end
end

---Main entrypoints picker
---@param opts table|nil Telescope picker options
function M.picker(opts)
  opts = opts or {}

  local root = indexer.find_root()
  if not root then
    vim.notify('doeff: Could not find project root', vim.log.levels.ERROR)
    return
  end

  local entries, err = indexer.get_all_entries(root)
  if err then
    vim.notify('doeff: ' .. err, vim.log.levels.ERROR)
    return
  end

  if not entries or #entries == 0 then
    vim.notify('doeff: No entrypoints found', vim.log.levels.INFO)
    return
  end

  pickers.new(opts, {
    prompt_title = 'Doeff Entrypoints',
    finder = finders.new_table({
      results = entries,
      entry_maker = make_entry_maker(root),
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
      map('i', '<C-e>', edit_action())
      map('n', '<C-e>', edit_action())

      return true
    end,
  }):find()
end

---Picker for interpreters only
---@param opts table|nil
function M.interpreters(opts)
  opts = opts or {}

  local root = indexer.find_root()
  if not root then
    vim.notify('doeff: Could not find project root', vim.log.levels.ERROR)
    return
  end

  local entries, err = indexer.find_interpreters(root)
  if err then
    vim.notify('doeff: ' .. err, vim.log.levels.ERROR)
    return
  end

  if not entries or #entries == 0 then
    vim.notify('doeff: No interpreters found', vim.log.levels.INFO)
    return
  end

  pickers.new(opts, {
    prompt_title = 'Doeff Interpreters',
    finder = finders.new_table({
      results = entries,
      entry_maker = make_entry_maker(root),
    }),
    sorter = conf.generic_sorter(opts),
    previewer = make_previewer(),
    attach_mappings = function(prompt_bufnr, map)
      actions.select_default:replace(edit_action())
      map('i', '<C-e>', edit_action())
      map('n', '<C-e>', edit_action())
      return true
    end,
  }):find()
end

---Picker for kleisli functions only
---@param opts table|nil
function M.kleisli(opts)
  opts = opts or {}

  local root = indexer.find_root()
  if not root then
    vim.notify('doeff: Could not find project root', vim.log.levels.ERROR)
    return
  end

  local entries, err = indexer.find_kleisli(root)
  if err then
    vim.notify('doeff: ' .. err, vim.log.levels.ERROR)
    return
  end

  if not entries or #entries == 0 then
    vim.notify('doeff: No kleisli functions found', vim.log.levels.INFO)
    return
  end

  pickers.new(opts, {
    prompt_title = 'Doeff Kleisli Functions',
    finder = finders.new_table({
      results = entries,
      entry_maker = make_entry_maker(root),
    }),
    sorter = conf.generic_sorter(opts),
    previewer = make_previewer(),
    attach_mappings = function(prompt_bufnr, map)
      actions.select_default:replace(run_action('float'))
      map('i', '<C-x>', run_action('horizontal'))
      map('n', '<C-x>', run_action('horizontal'))
      map('i', '<C-v>', run_action('vertical'))
      map('n', '<C-v>', run_action('vertical'))
      map('i', '<C-f>', run_action('float'))
      map('n', '<C-f>', run_action('float'))
      map('i', '<C-e>', edit_action())
      map('n', '<C-e>', edit_action())
      return true
    end,
  }):find()
end

---Picker for transforms only
---@param opts table|nil
function M.transforms(opts)
  opts = opts or {}

  local root = indexer.find_root()
  if not root then
    vim.notify('doeff: Could not find project root', vim.log.levels.ERROR)
    return
  end

  local entries, err = indexer.find_transforms(root)
  if err then
    vim.notify('doeff: ' .. err, vim.log.levels.ERROR)
    return
  end

  if not entries or #entries == 0 then
    vim.notify('doeff: No transforms found', vim.log.levels.INFO)
    return
  end

  pickers.new(opts, {
    prompt_title = 'Doeff Transforms',
    finder = finders.new_table({
      results = entries,
      entry_maker = make_entry_maker(root),
    }),
    sorter = conf.generic_sorter(opts),
    previewer = make_previewer(),
    attach_mappings = function(prompt_bufnr, map)
      actions.select_default:replace(edit_action())
      map('i', '<C-e>', edit_action())
      map('n', '<C-e>', edit_action())
      return true
    end,
  }):find()
end

return M
