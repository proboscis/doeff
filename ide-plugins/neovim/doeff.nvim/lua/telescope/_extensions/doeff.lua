-- Telescope extension loader for doeff.nvim
local has_telescope, telescope = pcall(require, 'telescope')
if not has_telescope then
  error('doeff.nvim requires telescope.nvim')
end

local entrypoints = require('doeff.telescope.entrypoints')
local playlists = require('doeff.telescope.playlists')
local workflows = require('doeff.telescope.workflows')

return telescope.register_extension({
  setup = function(ext_config, config)
    -- Extension-specific setup if needed
  end,
  exports = {
    -- Default export (called via :Telescope doeff)
    doeff = entrypoints.picker,
    -- Named exports
    entrypoints = entrypoints.picker,
    interpreters = entrypoints.interpreters,
    kleisli = entrypoints.kleisli,
    transforms = entrypoints.transforms,
    interceptors = entrypoints.interceptors,
    playlists = playlists.picker,
    workflows = workflows.picker,
  },
})
