-- doeff.nvim workflows module
-- Integrates with doeff-agentic CLI for workflow monitoring and interaction

local M = {}

local config = require('doeff.config')

---@class DoeffAgent
---@field name string Agent name
---@field status string Agent status (running, blocked, done, failed)
---@field session_name string Tmux session name
---@field pane_id string|nil Tmux pane ID
---@field started_at string ISO timestamp
---@field last_output_hash string|nil Hash of last output

---@class DoeffWorkflow
---@field id string Workflow ID (short hash)
---@field name string Workflow name
---@field status string Workflow status (running, blocked, completed, failed, stopped)
---@field started_at string ISO timestamp
---@field updated_at string ISO timestamp
---@field current_agent string|nil Currently active agent name
---@field agents DoeffAgent[] List of agents in workflow
---@field last_slog table|nil Last structured log entry
---@field error string|nil Error message if failed

---Check if doeff-agentic CLI is available
---@return boolean
function M.is_available()
  local cfg = config.get()
  local binary = cfg.workflows and cfg.workflows.binary or 'doeff-agentic'
  return vim.fn.executable(binary) == 1
end

---Execute doeff-agentic command
---@param args string[] Command arguments
---@return table|nil result Parsed JSON result or nil on error
---@return string|nil error Error message if any
function M.exec(args)
  local cfg = config.get()
  local binary = cfg.workflows and cfg.workflows.binary or 'doeff-agentic'
  local cmd = vim.list_extend({ binary }, args)

  local result = vim.system(cmd, { text = true }):wait()

  if result.code ~= 0 then
    local err = result.stderr or 'Unknown error'
    -- Clean up error message
    err = err:gsub('^%s+', ''):gsub('%s+$', '')
    if err == '' then
      err = 'Command failed with exit code ' .. result.code
    end
    return nil, err
  end

  local stdout = result.stdout or ''
  if stdout:match('^%s*$') then
    -- Empty output is valid for some commands
    return {}, nil
  end

  local ok, parsed = pcall(vim.json.decode, stdout)
  if not ok then
    return nil, 'Failed to parse JSON: ' .. tostring(parsed)
  end

  return parsed, nil
end

---List all workflows
---@param opts table|nil Options (status filter, agent_status filter)
---@return DoeffWorkflow[]|nil
---@return string|nil error
function M.list(opts)
  opts = opts or {}
  local args = { 'ps', '--json' }

  if opts.status then
    for _, s in ipairs(opts.status) do
      table.insert(args, '--status')
      table.insert(args, s)
    end
  end

  if opts.agent_status then
    for _, s in ipairs(opts.agent_status) do
      table.insert(args, '--agent-status')
      table.insert(args, s)
    end
  end

  local result, err = M.exec(args)
  if err then
    return nil, err
  end

  return result or {}, nil
end

---Get workflow details
---@param workflow_id string Workflow ID or prefix
---@return DoeffWorkflow|nil
---@return string|nil error
function M.get(workflow_id)
  local result, err = M.exec({ 'show', workflow_id, '--json' })
  if err then
    return nil, err
  end

  return result, nil
end

---Attach to workflow's agent tmux session
---@param workflow_id string Workflow ID or prefix
---@param agent string|nil Specific agent name
function M.attach(workflow_id, agent)
  local cfg = config.get()
  local binary = cfg.workflows and cfg.workflows.binary or 'doeff-agentic'

  local cmd = { binary, 'attach', workflow_id }
  if agent then
    table.insert(cmd, '--agent')
    table.insert(cmd, agent)
  end

  -- Open terminal with attach command
  local in_tmux = vim.env.TMUX ~= nil
  if in_tmux then
    -- Just run the attach command, it will switch tmux session
    vim.system(cmd, { text = true }, function(result)
      if result.code ~= 0 then
        vim.schedule(function()
          vim.notify('doeff: Failed to attach: ' .. (result.stderr or 'unknown error'), vim.log.levels.ERROR)
        end)
      end
    end)
  else
    -- Not in tmux, open in terminal
    vim.cmd('terminal ' .. table.concat(cmd, ' '))
  end
end

---Watch workflow updates in terminal
---@param workflow_id string Workflow ID or prefix
function M.watch(workflow_id)
  local cfg = config.get()
  local binary = cfg.workflows and cfg.workflows.binary or 'doeff-agentic'

  vim.cmd('terminal ' .. binary .. ' watch ' .. vim.fn.shellescape(workflow_id))
end

---Send message to workflow's agent
---@param workflow_id string Workflow ID or prefix
---@param message string Message to send
---@param agent string|nil Specific agent name
---@return boolean success
---@return string|nil error
function M.send(workflow_id, message, agent)
  local args = { 'send', workflow_id, message, '--json' }
  if agent then
    table.insert(args, '--agent')
    table.insert(args, agent)
  end

  local result, err = M.exec(args)
  if err then
    return false, err
  end

  return result and result.ok, nil
end

---Stop workflow
---@param workflow_id string Workflow ID or prefix
---@return boolean success
---@return string|nil error
function M.stop(workflow_id)
  local result, err = M.exec({ 'stop', workflow_id, '--json' })
  if err then
    return false, err
  end

  return result and result.ok, nil
end

---Format workflow status for display
---@param status string Workflow status
---@return string formatted
---@return string highlight_group
function M.format_status(status)
  local formats = {
    running = { '[running]', 'DiagnosticInfo' },
    blocked = { '[blocked]', 'DiagnosticWarn' },
    completed = { '[done]', 'DiagnosticOk' },
    done = { '[done]', 'DiagnosticOk' },
    failed = { '[failed]', 'DiagnosticError' },
    stopped = { '[stopped]', 'DiagnosticHint' },
  }
  local fmt = formats[status] or { '[' .. status .. ']', 'Comment' }
  return fmt[1], fmt[2]
end

---Format relative time from ISO timestamp
---@param iso_str string ISO timestamp string
---@return string formatted
function M.format_time(iso_str)
  -- Parse ISO 8601 timestamp
  local pattern = '(%d+)-(%d+)-(%d+)T(%d+):(%d+):(%d+)'
  local year, month, day, hour, min, sec = iso_str:match(pattern)
  if not year then
    return iso_str
  end

  local ts = os.time({
    year = tonumber(year),
    month = tonumber(month),
    day = tonumber(day),
    hour = tonumber(hour),
    min = tonumber(min),
    sec = tonumber(sec),
  })

  local now = os.time()
  local diff = now - ts

  if diff < 0 then
    return 'in the future'
  elseif diff < 60 then
    return diff .. 's ago'
  elseif diff < 3600 then
    return math.floor(diff / 60) .. 'm ago'
  elseif diff < 86400 then
    return math.floor(diff / 3600) .. 'h ago'
  else
    return math.floor(diff / 86400) .. 'd ago'
  end
end

return M
