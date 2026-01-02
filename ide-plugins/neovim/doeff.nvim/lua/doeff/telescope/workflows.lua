-- doeff.nvim workflows telescope picker
-- Provides UI for listing and interacting with doeff-agentic workflows

local M = {}

local workflows = require('doeff.workflows')

local pickers = require('telescope.pickers')
local finders = require('telescope.finders')
local conf = require('telescope.config').values
local actions = require('telescope.actions')
local action_state = require('telescope.actions.state')
local previewers = require('telescope.previewers')
local entry_display = require('telescope.pickers.entry_display')

---Create entry maker for workflow picker
---@return function
local function make_entry_maker()
  local displayer = entry_display.create({
    separator = ' ',
    items = {
      { width = 8 },      -- ID (short)
      { width = 22 },     -- Name
      { width = 10 },     -- Status badge
      { width = 14 },     -- Current agent
      { remaining = true }, -- Updated time
    },
  })

  return function(workflow)
    local status_text, status_hl = workflows.format_status(workflow.status)
    local agent = workflow.current_agent or '-'
    local updated = workflows.format_time(workflow.updated_at)

    -- Shorten ID for display (7 chars like git)
    local short_id = workflow.id:sub(1, 7)

    return {
      value = workflow,
      display = function()
        return displayer({
          { short_id, 'TelescopeResultsConstant' },
          { workflow.name, 'TelescopeResultsIdentifier' },
          { status_text, status_hl },
          { agent, 'TelescopeResultsFunction' },
          { updated, 'TelescopeResultsComment' },
        })
      end,
      ordinal = workflow.id .. ' ' .. workflow.name .. ' ' .. workflow.status .. ' ' .. (workflow.current_agent or ''),
    }
  end
end

---Build preview lines for a workflow
---@param workflow table The workflow
---@return string[] lines
local function build_preview_lines(workflow)
  local lines = {}

  -- Header
  table.insert(lines, '# ' .. workflow.name)
  table.insert(lines, '')

  -- Basic info
  table.insert(lines, 'ID: ' .. workflow.id)
  table.insert(lines, 'Status: ' .. workflow.status)
  table.insert(lines, 'Started: ' .. workflow.started_at)
  table.insert(lines, 'Updated: ' .. workflow.updated_at)
  table.insert(lines, '')

  -- Current agent
  if workflow.current_agent then
    table.insert(lines, 'Current Agent: ' .. workflow.current_agent)
  end

  -- Agents
  if workflow.agents and #workflow.agents > 0 then
    table.insert(lines, '')
    table.insert(lines, '## Agents')
    for _, agent in ipairs(workflow.agents) do
      local marker = agent.name == workflow.current_agent and '*' or ' '
      local line = string.format('  %s %s: %s (%s)', marker, agent.name, agent.status, agent.session_name)
      table.insert(lines, line)
    end
  end

  -- Last slog
  if workflow.last_slog then
    table.insert(lines, '')
    table.insert(lines, '## Last Status')
    -- Format each key-value pair on its own line for readability
    if type(workflow.last_slog) == 'table' then
      for key, value in pairs(workflow.last_slog) do
        local value_str = type(value) == 'table' and vim.json.encode(value) or tostring(value)
        table.insert(lines, string.format('  %s: %s', key, value_str))
      end
    else
      local ok, json_str = pcall(vim.json.encode, workflow.last_slog)
      if ok then
        table.insert(lines, '  ' .. json_str)
      end
    end
  end

  -- Error
  if workflow.error then
    table.insert(lines, '')
    table.insert(lines, '## Error')
    table.insert(lines, workflow.error)
  end

  return lines
end

---Create previewer for workflows
---@return table
local function make_previewer()
  return previewers.new_buffer_previewer({
    title = 'Workflow Details',
    define_preview = function(self, entry, _status)
      local lines = build_preview_lines(entry.value)
      vim.api.nvim_buf_set_lines(self.state.bufnr, 0, -1, false, lines)
      vim.api.nvim_set_option_value('filetype', 'markdown', { buf = self.state.bufnr })
    end,
  })
end

---Action: Attach to workflow's agent
---@return function
local function attach_action()
  return function(prompt_bufnr)
    local selection = action_state.get_selected_entry()
    actions.close(prompt_bufnr)
    if selection and selection.value then
      workflows.attach(selection.value.id)
    end
  end
end

---Action: Watch workflow updates
---@return function
local function watch_action()
  return function(prompt_bufnr)
    local selection = action_state.get_selected_entry()
    actions.close(prompt_bufnr)
    if selection and selection.value then
      workflows.watch(selection.value.id)
    end
  end
end

---Action: Stop workflow
---@return function
local function stop_action()
  return function(prompt_bufnr)
    local selection = action_state.get_selected_entry()
    if not selection or not selection.value then
      return
    end

    local wf = selection.value
    local confirm = vim.fn.confirm(
      string.format('Stop workflow "%s" (%s)?', wf.name, wf.id:sub(1, 7)),
      '&Yes\n&No',
      2
    )

    if confirm == 1 then
      local ok, err = workflows.stop(wf.id)
      if ok then
        vim.notify('doeff: Workflow stopped', vim.log.levels.INFO)
        -- Refresh picker
        actions.close(prompt_bufnr)
        vim.schedule(function()
          M.picker()
        end)
      else
        vim.notify('doeff: Failed to stop: ' .. (err or 'unknown error'), vim.log.levels.ERROR)
      end
    end
  end
end

---Action: Send message to workflow
---@return function
local function send_action()
  return function(prompt_bufnr)
    local selection = action_state.get_selected_entry()
    if not selection or not selection.value then
      return
    end

    local wf = selection.value
    vim.ui.input({ prompt = 'Message to send: ' }, function(message)
      if message and message ~= '' then
        local ok, err = workflows.send(wf.id, message)
        if ok then
          vim.notify('doeff: Message sent', vim.log.levels.INFO)
        else
          vim.notify('doeff: Failed to send: ' .. (err or 'unknown error'), vim.log.levels.ERROR)
        end
      end
    end)
  end
end

---Main workflows picker
---@param opts table|nil Telescope picker options
function M.picker(opts)
  opts = opts or {}

  -- Check if CLI is available
  if not workflows.is_available() then
    vim.notify('doeff: doeff-agentic CLI not found. Install it or check your PATH.', vim.log.levels.ERROR)
    return
  end

  -- List workflows
  local wf_list, err = workflows.list()
  if err then
    vim.notify('doeff: ' .. err, vim.log.levels.ERROR)
    return
  end

  if not wf_list or #wf_list == 0 then
    vim.notify('doeff: No workflows found', vim.log.levels.INFO)
    return
  end

  pickers.new(opts, {
    prompt_title = 'Doeff Workflows [CR:attach C-w:watch C-k:kill C-s:send]',
    finder = finders.new_table({
      results = wf_list,
      entry_maker = make_entry_maker(),
    }),
    sorter = conf.generic_sorter(opts),
    previewer = make_previewer(),
    attach_mappings = function(prompt_bufnr, map)
      -- Default action: attach to workflow's agent
      actions.select_default:replace(attach_action())

      -- Watch workflow
      map('i', '<C-w>', watch_action(), { desc = 'Watch workflow' })
      map('n', '<C-w>', watch_action(), { desc = 'Watch workflow' })

      -- Kill/stop workflow
      map('i', '<C-k>', stop_action(), { desc = 'Stop workflow' })
      map('n', '<C-k>', stop_action(), { desc = 'Stop workflow' })

      -- Send message
      map('i', '<C-s>', send_action(), { desc = 'Send message' })
      map('n', '<C-s>', send_action(), { desc = 'Send message' })

      return true
    end,
  }):find()
end

return M
