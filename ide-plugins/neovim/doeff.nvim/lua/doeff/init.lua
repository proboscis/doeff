-- doeff.nvim - Neovim plugin for running doeff entrypoints
-- Main entry point

local M = {}

local config = require('doeff.config')
local indexer = require('doeff.indexer')
local runner = require('doeff.runner')
local workflows = require('doeff.workflows')

---Setup the plugin with user configuration
---@param opts DoeffConfig|nil
function M.setup(opts)
  config.setup(opts)

  -- Check for telescope
  local has_telescope = pcall(require, 'telescope')
  if not has_telescope then
    vim.notify('doeff.nvim: telescope.nvim is required for picker functionality', vim.log.levels.WARN)
  end

  -- Setup keymaps
  M.setup_keymaps()

  -- Setup auto-refresh on file save (if enabled)
  if config.get().indexer.auto_refresh then
    M.setup_auto_refresh()
  end
end

---Setup keymaps based on configuration
function M.setup_keymaps()
  local keymaps = config.get().keymaps

  if keymaps.entrypoints then
    vim.keymap.set('n', keymaps.entrypoints, function()
      M.pick_entrypoints()
    end, { desc = 'Doeff: Search entrypoints [P]' })
  end

  if keymaps.run_cursor then
    vim.keymap.set('n', keymaps.run_cursor, function()
      M.run_cursor()
    end, { desc = 'Doeff: Run entrypoint under cursor' })
  end

  if keymaps.playlists then
    vim.keymap.set('n', keymaps.playlists, function()
      M.pick_playlists()
    end, { desc = 'Doeff: Search playlists' })
  end

  if keymaps.run_last then
    vim.keymap.set('n', keymaps.run_last, function()
      M.run_last()
    end, { desc = 'Doeff: Re-run last entrypoint' })
  end

  if keymaps.transforms then
    vim.keymap.set('n', keymaps.transforms, function()
      M.pick_transforms()
    end, { desc = 'Doeff: Search transforms [T]' })
  end

  if keymaps.interpreters then
    vim.keymap.set('n', keymaps.interpreters, function()
      M.pick_interpreters()
    end, { desc = 'Doeff: Search interpreters [I]' })
  end

  if keymaps.kleisli then
    vim.keymap.set('n', keymaps.kleisli, function()
      M.pick_kleisli()
    end, { desc = 'Doeff: Search kleisli [K]' })
  end

  if keymaps.interceptors then
    vim.keymap.set('n', keymaps.interceptors, function()
      M.pick_interceptors()
    end, { desc = 'Doeff: Search interceptors [IC]' })
  end

  if keymaps.all then
    vim.keymap.set('n', keymaps.all, function()
      M.pick_all()
    end, { desc = 'Doeff: Search all entries' })
  end

  if keymaps.workflows then
    vim.keymap.set('n', keymaps.workflows, function()
      M.pick_workflows()
    end, { desc = 'Doeff: List workflows' })
  end

  if keymaps.workflow_attach then
    vim.keymap.set('n', keymaps.workflow_attach, function()
      M.workflow_attach()
    end, { desc = 'Doeff: Attach to workflow agent' })
  end
end

---Setup auto-refresh on file save
function M.setup_auto_refresh()
  local group = vim.api.nvim_create_augroup('DoeffAutoRefresh', { clear = true })

  vim.api.nvim_create_autocmd('BufWritePost', {
    group = group,
    pattern = '*.py',
    callback = function()
      -- Clear cache to force refresh on next query
      indexer.clear_cache()
    end,
    desc = 'Clear doeff index cache on Python file save',
  })
end

---Open entrypoints picker
---@param opts table|nil Telescope picker options
function M.pick_entrypoints(opts)
  local ok, telescope = pcall(require, 'telescope')
  if not ok then
    vim.notify('doeff.nvim: telescope.nvim is required', vim.log.levels.ERROR)
    return
  end

  local entrypoints = require('doeff.telescope.entrypoints')
  entrypoints.picker(opts)
end

---Open interpreters picker
---@param opts table|nil
function M.pick_interpreters(opts)
  local ok = pcall(require, 'telescope')
  if not ok then
    vim.notify('doeff.nvim: telescope.nvim is required', vim.log.levels.ERROR)
    return
  end

  local entrypoints = require('doeff.telescope.entrypoints')
  entrypoints.interpreters(opts)
end

---Open kleisli functions picker
---@param opts table|nil
function M.pick_kleisli(opts)
  local ok = pcall(require, 'telescope')
  if not ok then
    vim.notify('doeff.nvim: telescope.nvim is required', vim.log.levels.ERROR)
    return
  end

  local entrypoints = require('doeff.telescope.entrypoints')
  entrypoints.kleisli(opts)
end

---Open transforms picker
---@param opts table|nil
function M.pick_transforms(opts)
  local ok = pcall(require, 'telescope')
  if not ok then
    vim.notify('doeff.nvim: telescope.nvim is required', vim.log.levels.ERROR)
    return
  end

  local entrypoints = require('doeff.telescope.entrypoints')
  entrypoints.transforms(opts)
end

---Open interceptors picker
---@param opts table|nil
function M.pick_interceptors(opts)
  local ok = pcall(require, 'telescope')
  if not ok then
    vim.notify('doeff.nvim: telescope.nvim is required', vim.log.levels.ERROR)
    return
  end

  local entrypoints = require('doeff.telescope.entrypoints')
  entrypoints.interceptors(opts)
end

---Open all entries picker
---@param opts table|nil
function M.pick_all(opts)
  local ok = pcall(require, 'telescope')
  if not ok then
    vim.notify('doeff.nvim: telescope.nvim is required', vim.log.levels.ERROR)
    return
  end

  local entrypoints = require('doeff.telescope.entrypoints')
  entrypoints.all(opts)
end

---Open playlists picker
---@param opts table|nil
function M.pick_playlists(opts)
  local ok = pcall(require, 'telescope')
  if not ok then
    vim.notify('doeff.nvim: telescope.nvim is required', vim.log.levels.ERROR)
    return
  end

  local playlists = require('doeff.telescope.playlists')
  playlists.picker(opts)
end

---Open workflows picker
---@param opts table|nil
function M.pick_workflows(opts)
  local ok = pcall(require, 'telescope')
  if not ok then
    vim.notify('doeff.nvim: telescope.nvim is required', vim.log.levels.ERROR)
    return
  end

  local wf_picker = require('doeff.telescope.workflows')
  wf_picker.picker(opts)
end

---Attach to current/most recent workflow's agent
---Opens the workflow picker if multiple workflows are running
function M.workflow_attach()
  if not workflows.is_available() then
    vim.notify('doeff: doeff-agentic CLI not found', vim.log.levels.ERROR)
    return
  end

  local wf_list, err = workflows.list({ status = { 'running', 'blocked' } })
  if err then
    vim.notify('doeff: ' .. err, vim.log.levels.ERROR)
    return
  end

  if not wf_list or #wf_list == 0 then
    vim.notify('doeff: No active workflows', vim.log.levels.INFO)
    return
  end

  if #wf_list == 1 then
    -- Single workflow, attach directly
    workflows.attach(wf_list[1].id)
  else
    -- Multiple workflows, open picker
    M.pick_workflows()
  end
end

---List all workflows
---@param opts table|nil Options (status, agent_status filters)
---@return table|nil workflows
---@return string|nil error
function M.list_workflows(opts)
  return workflows.list(opts)
end

---Get workflow details
---@param workflow_id string
---@return table|nil workflow
---@return string|nil error
function M.get_workflow(workflow_id)
  return workflows.get(workflow_id)
end

---Stop a workflow
---@param workflow_id string
---@return boolean success
---@return string|nil error
function M.stop_workflow(workflow_id)
  return workflows.stop(workflow_id)
end

---Run entrypoint under cursor
---@param direction string|nil Terminal direction
function M.run_cursor(direction)
  runner.run_cursor(direction)
end

---Re-run last entrypoint
---@param direction string|nil Terminal direction override
function M.run_last(direction)
  runner.run_last(direction)
end

---Run a specific program
---@param opts DoeffRunOpts
---@param direction string|nil Terminal direction
function M.run(opts, direction)
  runner.run(opts, direction)
end

---Find project root
---@param start_path string|nil
---@return string|nil
function M.find_root(start_path)
  return indexer.find_root(start_path)
end

---Get all entrypoints
---@param root string|nil
---@param force boolean|nil
---@return DoeffEntry[]|nil
---@return string|nil error
function M.get_entries(root, force)
  return indexer.get_all_entries(root, force)
end

---Clear the indexer cache
function M.clear_cache()
  indexer.clear_cache()
end

---Close all doeff terminal windows
function M.close_terminals()
  runner.close_all()
end

-- Export submodules for advanced usage
M.config = config
M.indexer = indexer
M.runner = runner
M.workflows = workflows

return M
