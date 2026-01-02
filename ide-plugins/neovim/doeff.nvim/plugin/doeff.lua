-- doeff.nvim plugin loader
-- This file is automatically sourced by Neovim

if vim.g.loaded_doeff then
  return
end
vim.g.loaded_doeff = true

-- Create user commands
vim.api.nvim_create_user_command('DoeffEntrypoints', function(opts)
  require('doeff').pick_entrypoints()
end, { desc = 'Open doeff entrypoints picker' })

vim.api.nvim_create_user_command('DoeffInterpreters', function(opts)
  require('doeff').pick_interpreters()
end, { desc = 'Open doeff interpreters picker' })

vim.api.nvim_create_user_command('DoeffKleisli', function(opts)
  require('doeff').pick_kleisli()
end, { desc = 'Open doeff kleisli functions picker' })

vim.api.nvim_create_user_command('DoeffTransforms', function(opts)
  require('doeff').pick_transforms()
end, { desc = 'Open doeff transforms picker' })

vim.api.nvim_create_user_command('DoeffInterceptors', function(opts)
  require('doeff').pick_interceptors()
end, { desc = 'Open doeff interceptors picker' })

vim.api.nvim_create_user_command('DoeffAll', function(opts)
  require('doeff').pick_all()
end, { desc = 'Open doeff all entries picker' })

vim.api.nvim_create_user_command('DoeffPlaylists', function(opts)
  require('doeff').pick_playlists()
end, { desc = 'Open doeff playlists picker' })

vim.api.nvim_create_user_command('DoeffRunCursor', function(opts)
  require('doeff').run_cursor()
end, { desc = 'Run doeff entrypoint under cursor' })

vim.api.nvim_create_user_command('DoeffRunLast', function(opts)
  require('doeff').run_last()
end, { desc = 'Re-run last doeff entrypoint' })

vim.api.nvim_create_user_command('DoeffClearCache', function(opts)
  require('doeff').clear_cache()
  vim.notify('doeff: Cache cleared', vim.log.levels.INFO)
end, { desc = 'Clear doeff indexer cache' })

vim.api.nvim_create_user_command('DoeffCloseTerminals', function(opts)
  require('doeff').close_terminals()
end, { desc = 'Close all doeff terminal windows' })

-- Workflow commands
vim.api.nvim_create_user_command('DoeffWorkflows', function(opts)
  require('doeff').pick_workflows()
end, { desc = 'Open doeff workflows picker' })

vim.api.nvim_create_user_command('DoeffWorkflowAttach', function(opts)
  require('doeff').workflow_attach()
end, { desc = 'Attach to workflow agent' })

vim.api.nvim_create_user_command('DoeffWorkflowStop', function(opts)
  local workflow_id = opts.args
  if workflow_id == '' then
    vim.notify('doeff: Workflow ID required', vim.log.levels.ERROR)
    return
  end
  local ok, err = require('doeff').stop_workflow(workflow_id)
  if ok then
    vim.notify('doeff: Workflow stopped', vim.log.levels.INFO)
  else
    vim.notify('doeff: ' .. (err or 'Failed to stop workflow'), vim.log.levels.ERROR)
  end
end, { nargs = 1, desc = 'Stop a doeff workflow' })

-- Telescope integration is handled by lua/telescope/_extensions/doeff.lua
-- Use :Telescope doeff <subcommand> after loading the extension
