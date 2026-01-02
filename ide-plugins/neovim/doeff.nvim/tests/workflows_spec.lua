-- Tests for doeff.nvim workflows module

describe('doeff.workflows', function()
  local workflows
  local config

  before_each(function()
    package.loaded['doeff.workflows'] = nil
    package.loaded['doeff.config'] = nil
    config = require('doeff.config')
    workflows = require('doeff.workflows')
  end)

  describe('is_available', function()
    it('should check if doeff-agentic is available', function()
      local result = workflows.is_available()
      -- Result depends on whether binary is installed
      assert.is_boolean(result)
    end)
  end)

  describe('format_status', function()
    it('should format running status', function()
      local text, hl = workflows.format_status('running')
      assert.equals('[running]', text)
      assert.equals('DiagnosticInfo', hl)
    end)

    it('should format blocked status', function()
      local text, hl = workflows.format_status('blocked')
      assert.equals('[blocked]', text)
      assert.equals('DiagnosticWarn', hl)
    end)

    it('should format completed status', function()
      local text, hl = workflows.format_status('completed')
      assert.equals('[done]', text)
      assert.equals('DiagnosticOk', hl)
    end)

    it('should format failed status', function()
      local text, hl = workflows.format_status('failed')
      assert.equals('[failed]', text)
      assert.equals('DiagnosticError', hl)
    end)

    it('should format unknown status', function()
      local text, hl = workflows.format_status('unknown')
      assert.equals('[unknown]', text)
      assert.equals('Comment', hl)
    end)
  end)

  describe('format_time', function()
    it('should format recent time', function()
      local now = os.date('!%Y-%m-%dT%H:%M:%S')
      local result = workflows.format_time(now)
      assert.is_string(result)
      assert.is_true(result:match('%d+s ago') ~= nil or result == '0s ago')
    end)

    it('should handle invalid timestamp', function()
      local result = workflows.format_time('invalid')
      assert.equals('invalid', result)
    end)
  end)

  describe('list', function()
    it('should return empty table when CLI not available', function()
      -- Configure with non-existent binary
      config.setup({
        workflows = {
          binary = 'nonexistent-binary-xyz-12345',
        },
      })
      -- Reload workflows to pick up new config
      package.loaded['doeff.workflows'] = nil
      workflows = require('doeff.workflows')

      local result, err = workflows.list()
      -- Should return nil with error when binary not found
      assert.is_nil(result)
      assert.is_string(err)
    end)
  end)
end)
