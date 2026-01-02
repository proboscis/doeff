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

-- Telescope integration
vim.api.nvim_create_user_command('Telescope', function(opts)
  local args = opts.fargs
  if #args > 0 and args[1] == 'doeff' then
    -- Handle :Telescope doeff <subcommand>
    local subcommand = args[2]
    if subcommand == 'entrypoints' or not subcommand then
      require('doeff').pick_entrypoints()
    elseif subcommand == 'interpreters' then
      require('doeff').pick_interpreters()
    elseif subcommand == 'kleisli' then
      require('doeff').pick_kleisli()
    elseif subcommand == 'transforms' then
      require('doeff').pick_transforms()
    elseif subcommand == 'playlists' then
      require('doeff').pick_playlists()
    end
    return
  end

  -- Fall through to default Telescope behavior
  -- This won't interfere with normal Telescope usage since we only handle 'doeff'
end, { nargs = '*', complete = 'customlist,v:lua.require("telescope.command").complete' })
