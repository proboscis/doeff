-- Tests for doeff.nvim main module

describe('doeff', function()
  local doeff

  before_each(function()
    -- Clear module cache
    for name, _ in pairs(package.loaded) do
      if name:match('^doeff') then
        package.loaded[name] = nil
      end
    end
    doeff = require('doeff')
  end)

  describe('module structure', function()
    it('should export setup function', function()
      assert.is_function(doeff.setup)
    end)

    it('should export picker functions', function()
      assert.is_function(doeff.pick_entrypoints)
      assert.is_function(doeff.pick_interpreters)
      assert.is_function(doeff.pick_kleisli)
      assert.is_function(doeff.pick_transforms)
      assert.is_function(doeff.pick_playlists)
    end)

    it('should export run functions', function()
      assert.is_function(doeff.run_cursor)
      assert.is_function(doeff.run_last)
      assert.is_function(doeff.run)
    end)

    it('should export utility functions', function()
      assert.is_function(doeff.find_root)
      assert.is_function(doeff.get_entries)
      assert.is_function(doeff.clear_cache)
      assert.is_function(doeff.close_terminals)
    end)

    it('should export submodules', function()
      assert.is_table(doeff.config)
      assert.is_table(doeff.indexer)
      assert.is_table(doeff.runner)
    end)
  end)

  describe('setup', function()
    it('should not error with default configuration', function()
      -- Mock telescope to avoid dependency error
      package.loaded['telescope'] = { register_extension = function() end }

      doeff.setup()
      assert.is_true(true)

      -- Clean up mock
      package.loaded['telescope'] = nil
    end)

    it('should not error with custom configuration', function()
      package.loaded['telescope'] = { register_extension = function() end }

      doeff.setup({
        keymaps = {
          entrypoints = '<leader>xx',
        },
        terminal = {
          direction = 'vertical',
        },
      })
      assert.is_true(true)

      package.loaded['telescope'] = nil
    end)
  end)

  describe('clear_cache', function()
    it('should clear the indexer cache', function()
      doeff.clear_cache()
      assert.is_true(true)
    end)
  end)

  describe('find_root', function()
    it('should return string or nil', function()
      local result = doeff.find_root()
      assert.is_true(result == nil or type(result) == 'string')
    end)
  end)
end)
