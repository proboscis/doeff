-- Tests for doeff.nvim configuration module

describe('doeff.config', function()
  local config

  before_each(function()
    package.loaded['doeff.config'] = nil
    config = require('doeff.config')
  end)

  describe('defaults', function()
    it('should have default keymaps', function()
      assert.is_not_nil(config.defaults.keymaps)
      assert.equals('<leader>de', config.defaults.keymaps.entrypoints)
      assert.equals('<leader>dr', config.defaults.keymaps.run_cursor)
      assert.equals('<leader>dP', config.defaults.keymaps.playlists)
      assert.equals('<leader>dl', config.defaults.keymaps.run_last)
      assert.equals('<leader>dw', config.defaults.keymaps.workflows)
      assert.equals('<leader>da', config.defaults.keymaps.workflow_attach)
    end)

    it('should have default terminal settings', function()
      assert.is_not_nil(config.defaults.terminal)
      assert.equals('float', config.defaults.terminal.direction)
      assert.equals('rounded', config.defaults.terminal.float_opts.border)
      assert.equals(0.8, config.defaults.terminal.float_opts.width)
      assert.equals(0.8, config.defaults.terminal.float_opts.height)
    end)

    it('should have default indexer settings', function()
      assert.is_not_nil(config.defaults.indexer)
      assert.equals('doeff-indexer', config.defaults.indexer.binary)
      assert.is_true(config.defaults.indexer.auto_refresh)
      assert.equals(5000, config.defaults.indexer.cache_ttl)
    end)

    it('should have default workflows settings', function()
      assert.is_not_nil(config.defaults.workflows)
      assert.equals('doeff-agentic', config.defaults.workflows.binary)
    end)

    it('should have root markers', function()
      assert.is_not_nil(config.defaults.root_markers)
      assert.is_true(#config.defaults.root_markers > 0)
      assert.is_true(vim.tbl_contains(config.defaults.root_markers, 'pyproject.toml'))
      assert.is_true(vim.tbl_contains(config.defaults.root_markers, '.git'))
    end)
  end)

  describe('setup', function()
    it('should apply user configuration', function()
      config.setup({
        keymaps = {
          entrypoints = '<leader>xx',
        },
        terminal = {
          direction = 'horizontal',
        },
      })

      local values = config.get()
      assert.equals('<leader>xx', values.keymaps.entrypoints)
      assert.equals('horizontal', values.terminal.direction)
    end)

    it('should preserve defaults for unspecified options', function()
      config.setup({
        keymaps = {
          entrypoints = '<leader>xx',
        },
      })

      local values = config.get()
      assert.equals('<leader>xx', values.keymaps.entrypoints)
      assert.equals('<leader>dr', values.keymaps.run_cursor) -- default preserved
      assert.equals('float', values.terminal.direction) -- default preserved
    end)

    it('should handle nil configuration', function()
      config.setup(nil)
      local values = config.get()
      assert.equals('<leader>de', values.keymaps.entrypoints)
    end)

    it('should handle empty configuration', function()
      config.setup({})
      local values = config.get()
      assert.equals('<leader>de', values.keymaps.entrypoints)
    end)
  end)

  describe('get', function()
    it('should return current configuration', function()
      local values = config.get()
      assert.is_not_nil(values)
      assert.is_not_nil(values.keymaps)
      assert.is_not_nil(values.terminal)
      assert.is_not_nil(values.indexer)
    end)
  end)
end)
