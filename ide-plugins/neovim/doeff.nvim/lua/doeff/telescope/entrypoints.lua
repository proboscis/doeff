-- doeff.nvim entrypoints telescope picker
-- Version: 2026-01-02-v2 (vim.NIL fix)
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

---Shorten a type annotation for display
---@param annotation string|nil Full type annotation
---@param max_len number Maximum length
---@return string shortened type
local function shorten_type(annotation, max_len)
  -- Check type FIRST before any other operations
  if type(annotation) ~= 'string' then
    return ''
  end
  if annotation == '' then
    return ''
  end

  -- Extract just the class name (last part after dots)
  local short = annotation:match('[^%.]+$') or annotation

  -- Remove generic parameters for display (e.g., "Program[int]" -> "Program")
  short = short:match('^([^%[]+)') or short

  -- Truncate if still too long
  if #short > max_len then
    short = short:sub(1, max_len - 2) .. '..'
  end

  return short
end

---Get the first parameter's type annotation
---@param entry table The entry
---@return string|nil annotation
local function get_first_param_type(entry)
  local params = entry.all_parameters or entry.program_parameters
  if params and #params > 0 then
    return params[1].annotation
  end
  return nil
end

---Format category badges for display
---@param entry table The full entry (to check item_kind)
---@return string badges
---@return string|nil first_param_type (shortened)
local function format_categories(entry)
  local categories = entry.categories or {}
  local badges = {}
  local first_param_type = nil

  -- Check if this is a Program entrypoint (assignment with no function categories)
  local is_program_var = entry.item_kind == 'assignment'
  local has_func_category = false

  for _, cat in ipairs(categories) do
    if cat == 'program_interpreter' then
      table.insert(badges, '[I]')
      has_func_category = true
      first_param_type = get_first_param_type(entry)
    elseif cat == 'program_transformer' then
      table.insert(badges, '[T]')
      has_func_category = true
      first_param_type = get_first_param_type(entry)
    elseif cat == 'kleisli_program' then
      table.insert(badges, '[K]')
      has_func_category = true
      first_param_type = get_first_param_type(entry)
    elseif cat == 'interceptor' then
      table.insert(badges, '[IC]')
      has_func_category = true
    end
  end

  -- If it's an assignment (global variable) without function categories, it's a Program entrypoint
  if is_program_var and not has_func_category then
    table.insert(badges, 1, '[P]')  -- Program entrypoint - insert at front
  end

  return table.concat(badges, ' '), first_param_type
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
      { width = 6 },      -- Category badge [K]/[T]/etc
      { width = 12 },     -- First param type (shortened)
      { width = 28 },     -- Name
      { remaining = true }, -- File path
    },
  })

  return function(entry)
    local relative_path = get_relative_path(entry.file_path, root)
    local display_path = relative_path .. ':' .. entry.line
    local badges, first_param_type = format_categories(entry)

    -- Shorten the type for display (max 10 chars)
    local type_display = ''
    if first_param_type then
      type_display = shorten_type(first_param_type, 10)
    end

    return {
      value = entry,
      display = function()
        return displayer({
          { badges, 'TelescopeResultsComment' },
          { type_display, 'TelescopeResultsNumber' },
          { entry.name, 'TelescopeResultsIdentifier' },
          { display_path, 'TelescopeResultsComment' },
        })
      end,
      ordinal = entry.name .. ' ' .. entry.qualified_name .. ' ' .. relative_path .. ' ' .. type_display,
      filename = entry.file_path,
      lnum = entry.line,
    }
  end
end

---Build preview lines for an entry
---@param value table The entry value
---@return string[] lines
local function build_preview_lines(value)
  local lines = {}

  -- Header with name and type
  table.insert(lines, '# ' .. (value.name or 'Unknown'))
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

  -- Docstring - split by newlines to handle multiline
  if value.docstring and value.docstring ~= '' then
    table.insert(lines, '')
    table.insert(lines, '## Description')
    for line in value.docstring:gmatch('[^\r\n]+') do
      table.insert(lines, line)
    end
  end

  -- All parameters (use all_parameters if available, fallback to program_parameters)
  local params = value.all_parameters or value.program_parameters
  if params and #params > 0 then
    table.insert(lines, '')
    table.insert(lines, '## Parameters')
    for _, param in ipairs(params) do
      local param_line = '  - ' .. (param.name or '?')
      -- Check for valid string annotation (vim.NIL from JSON null is truthy but not a string)
      if type(param.annotation) == 'string' and param.annotation ~= '' then
        param_line = param_line .. ': ' .. param.annotation
      end
      if param.is_required == false then
        param_line = param_line .. ' (optional)'
      end
      table.insert(lines, param_line)
    end
  end

  -- Return annotation
  if type(value.return_annotation) == 'string' and value.return_annotation ~= '' then
    table.insert(lines, '')
    table.insert(lines, 'Returns: ' .. value.return_annotation)
  end

  -- File location
  table.insert(lines, '')
  table.insert(lines, '---')
  table.insert(lines, 'File: ' .. (value.file_path or '?') .. ':' .. (value.line or '?'))
  table.insert(lines, 'Qualified: ' .. (value.qualified_name or '?'))

  return lines
end

---Create async previewer using termopen (bat/cat)
---@return table
local function make_previewer()
  local from_entry = require('telescope.from_entry')
  local utils = require('telescope.utils')

  return previewers.new_termopen_previewer({
    title = 'Preview',
    get_command = function(entry, status)
      local p = from_entry.path(entry, true)
      if p == nil or p == '' then
        return nil
      end

      local lnum = entry.lnum or 1
      local context = 10  -- lines of context around the target line

      -- Try bat first (with syntax highlighting), fallback to cat
      if vim.fn.executable('bat') == 1 then
        return {
          'bat',
          '--style=numbers,changes',
          '--color=always',
          '--pager=never',
          '--highlight-line=' .. lnum,
          '--line-range=' .. math.max(1, lnum - context) .. ':' .. (lnum + context),
          p,
        }
      else
        -- Fallback to sed for line range
        local start_line = math.max(1, lnum - context)
        local end_line = lnum + context
        return { 'sed', '-n', start_line .. ',' .. end_line .. 'p', p }
      end
    end,
  })
end

---Create action to edit/jump to entrypoint (DEFAULT action)
---@return function
local function edit_action()
  return function(prompt_bufnr)
    local selection = action_state.get_selected_entry()
    actions.close(prompt_bufnr)
    if selection and selection.value then
      vim.cmd('edit ' .. vim.fn.fnameescape(selection.value.file_path))
      vim.api.nvim_win_set_cursor(0, { selection.value.line, 0 })
      vim.cmd('normal! zz')  -- Center the view
    end
  end
end

---Create action to run entrypoint (only for Program entrypoints)
---@param direction string Terminal direction
---@return function
local function run_action(direction)
  return function(prompt_bufnr)
    local selection = action_state.get_selected_entry()
    actions.close(prompt_bufnr)
    if selection and selection.value then
      -- Only run if it's a Program entrypoint (assignment)
      if selection.value.item_kind == 'assignment' then
        runner.run_entry(selection.value, direction)
      else
        vim.notify('doeff: Can only run Program entrypoints [P], not functions', vim.log.levels.WARN)
        -- Fall back to edit
        vim.cmd('edit ' .. vim.fn.fnameescape(selection.value.file_path))
        vim.api.nvim_win_set_cursor(0, { selection.value.line, 0 })
      end
    end
  end
end

---Filter entries to only Program entrypoints (global variables)
---@param entries table[] All entries
---@return table[] Filtered entries
local function filter_program_entrypoints(entries)
  local result = {}
  for _, entry in ipairs(entries) do
    -- Program entrypoints are assignments (global variables), not functions
    if entry.item_kind == 'assignment' then
      table.insert(result, entry)
    end
  end
  return result
end

---Main entrypoints picker - shows only Program[T] global variables
---@param opts table|nil Telescope picker options
function M.picker(opts)
  opts = opts or {}

  local root = indexer.find_root()
  if not root then
    vim.notify('doeff: Could not find project root', vim.log.levels.ERROR)
    return
  end

  local all_entries, err = indexer.get_all_entries(root)
  if err then
    vim.notify('doeff: ' .. err, vim.log.levels.ERROR)
    return
  end

  -- Filter to only show Program entrypoints (global variables)
  local entries = filter_program_entrypoints(all_entries or {})

  if #entries == 0 then
    vim.notify('doeff: No Program entrypoints found', vim.log.levels.INFO)
    return
  end

  pickers.new(opts, {
    prompt_title = 'Doeff Entrypoints [Enter:goto C-r:run]',
    finder = finders.new_table({
      results = entries,
      entry_maker = make_entry_maker(root),
    }),
    sorter = conf.generic_sorter(opts),
    previewer = make_previewer(),
    attach_mappings = function(prompt_bufnr, map)
      -- Default action: jump to definition
      actions.select_default:replace(edit_action())

      -- Run mappings (only for Program entrypoints)
      map('i', '<C-r>', run_action('float'), { desc = 'Run in tmux window' })
      map('n', '<C-r>', run_action('float'), { desc = 'Run in tmux window' })
      map('i', '<C-x>', run_action('horizontal'), { desc = 'Run in horizontal pane' })
      map('n', '<C-x>', run_action('horizontal'), { desc = 'Run in horizontal pane' })
      map('i', '<C-v>', run_action('vertical'), { desc = 'Run in vertical pane' })
      map('n', '<C-v>', run_action('vertical'), { desc = 'Run in vertical pane' })

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
    prompt_title = 'Doeff Interpreters [Enter:goto]',
    finder = finders.new_table({
      results = entries,
      entry_maker = make_entry_maker(root),
    }),
    sorter = conf.generic_sorter(opts),
    previewer = make_previewer(),
    attach_mappings = function(prompt_bufnr, map)
      -- Default: jump to definition
      actions.select_default:replace(edit_action())
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
    prompt_title = 'Doeff Kleisli [Enter:goto]',
    finder = finders.new_table({
      results = entries,
      entry_maker = make_entry_maker(root),
    }),
    sorter = conf.generic_sorter(opts),
    previewer = make_previewer(),
    attach_mappings = function(prompt_bufnr, map)
      -- Default: jump to definition (kleisli functions can't be run directly)
      actions.select_default:replace(edit_action())
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
    prompt_title = 'Doeff Transforms [Enter:goto]',
    finder = finders.new_table({
      results = entries,
      entry_maker = make_entry_maker(root),
    }),
    sorter = conf.generic_sorter(opts),
    previewer = make_previewer(),
    attach_mappings = function(prompt_bufnr, map)
      -- Default: jump to definition
      actions.select_default:replace(edit_action())
      return true
    end,
  }):find()
end

---Picker for interceptors only
---@param opts table|nil
function M.interceptors(opts)
  opts = opts or {}

  local root = indexer.find_root()
  if not root then
    vim.notify('doeff: Could not find project root', vim.log.levels.ERROR)
    return
  end

  local entries, err = indexer.find_interceptors(root)
  if err then
    vim.notify('doeff: ' .. err, vim.log.levels.ERROR)
    return
  end

  if not entries or #entries == 0 then
    vim.notify('doeff: No interceptors found', vim.log.levels.INFO)
    return
  end

  pickers.new(opts, {
    prompt_title = 'Doeff Interceptors [Enter:goto]',
    finder = finders.new_table({
      results = entries,
      entry_maker = make_entry_maker(root),
    }),
    sorter = conf.generic_sorter(opts),
    previewer = make_previewer(),
    attach_mappings = function(prompt_bufnr, map)
      -- Default: jump to definition
      actions.select_default:replace(edit_action())
      return true
    end,
  }):find()
end

---Picker for ALL entries (entrypoints, kleisli, transforms, interpreters, interceptors)
---@param opts table|nil
function M.all(opts)
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
    vim.notify('doeff: No entries found', vim.log.levels.INFO)
    return
  end

  pickers.new(opts, {
    prompt_title = 'Doeff All [Enter:goto C-r:run C-e:edit ?:help]',
    finder = finders.new_table({
      results = entries,
      entry_maker = make_entry_maker(root),
    }),
    sorter = conf.generic_sorter(opts),
    previewer = make_previewer(),
    attach_mappings = function(prompt_bufnr, map)
      -- Default: jump to definition
      actions.select_default:replace(edit_action())

      -- Run mappings (only works for Program entrypoints [P])
      map('i', '<C-r>', run_action('float'), { desc = 'Run in tmux window' })
      map('n', '<C-r>', run_action('float'), { desc = 'Run in tmux window' })
      map('i', '<C-x>', run_action('horizontal'), { desc = 'Run in horizontal pane' })
      map('n', '<C-x>', run_action('horizontal'), { desc = 'Run in horizontal pane' })
      map('i', '<C-v>', run_action('vertical'), { desc = 'Run in vertical pane' })
      map('n', '<C-v>', run_action('vertical'), { desc = 'Run in vertical pane' })
      map('i', '<C-e>', edit_action(), { desc = 'Edit source file' })
      map('n', '<C-e>', edit_action(), { desc = 'Edit source file' })
      return true
    end,
  }):find()
end

return M
