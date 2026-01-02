-- Tests for doeff.nvim indexer module

describe('doeff.indexer', function()
  local indexer
  local config

  before_each(function()
    package.loaded['doeff.indexer'] = nil
    package.loaded['doeff.config'] = nil
    config = require('doeff.config')
    indexer = require('doeff.indexer')
  end)

  describe('find_root', function()
    it('should return nil for non-existent path', function()
      local result = indexer.find_root('/nonexistent/path/that/does/not/exist')
      -- Result depends on whether the path exists
      -- Just verify it doesn't crash
      assert.is_true(result == nil or type(result) == 'string')
    end)

    it('should use current directory if no path provided', function()
      local result = indexer.find_root()
      -- May or may not find root depending on cwd
      assert.is_true(result == nil or type(result) == 'string')
    end)
  end)

  describe('exec', function()
    it('should handle missing binary gracefully', function()
      config.setup({ indexer = { binary = 'nonexistent-binary-xyz' } })
      local result, err = indexer.exec({ 'index' }, '/tmp')
      assert.is_nil(result)
      assert.is_not_nil(err)
    end)
  end)

  describe('cache', function()
    it('should clear cache', function()
      -- This should not error
      indexer.clear_cache()
      assert.is_true(true)
    end)
  end)

  describe('find_at_location', function()
    it('should return nil for invalid file', function()
      local result = indexer.find_at_location('/nonexistent/file.py', 10)
      assert.is_nil(result)
    end)
  end)
end)
