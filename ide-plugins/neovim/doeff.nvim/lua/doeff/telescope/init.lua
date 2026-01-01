-- doeff.nvim telescope extension
local M = {}

M.entrypoints = require('doeff.telescope.entrypoints')
M.playlists = require('doeff.telescope.playlists')

---Register telescope extension
function M.register()
  local ok, telescope = pcall(require, 'telescope')
  if not ok then
    vim.notify('doeff.nvim: telescope.nvim is required', vim.log.levels.ERROR)
    return false
  end

  telescope.register_extension({
    exports = {
      doeff = M.entrypoints.picker,
      entrypoints = M.entrypoints.picker,
      playlists = M.playlists.picker,
    },
  })

  return true
end

return M
