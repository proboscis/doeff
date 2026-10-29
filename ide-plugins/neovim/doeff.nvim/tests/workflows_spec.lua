-- Tests for doeff.nvim workflows module

-- The `ps --json` the doeff-agentic CLI writes, shared with VS Code's doeff-runner tests
local PS_FIXTURE = vim.fn.fnamemodify(
  vim.fn.fnamemodify(debug.getinfo(1, 'S').source:sub(2), ':p:h') .. '/../../../test-fixtures/doeff-agentic-ps.json',
  ':p'
)

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
    -- Outside a uv project the configured binary is called (inside one, `uv run doeff-agentic`)
    local cwd
    local outside

    before_each(function()
      cwd = vim.fn.getcwd()
      outside = vim.fn.tempname()
      vim.fn.mkdir(outside, 'p')
      vim.cmd.cd(outside)
    end)

    after_each(function()
      vim.cmd.cd(cwd)
    end)

    it('should return nil with an error when CLI not available', function()
      config.setup({
        workflows = {
          binary = 'nonexistent-binary-xyz-12345',
        },
      })

      local result, err = workflows.list()
      assert.is_nil(result)
      assert.is_string(err)
    end)

    it('should read the workflows the doeff-agentic CLI writes for ps --json', function()
      -- A stand-in CLI that answers only `ps --json`, with what the Python CLI writes for a
      -- recorded state directory (packages/doeff-agentic/tests/test_cli.py pins the file to
      -- the CLI's output under its real handlers)
      local cli = outside .. '/doeff-agentic'
      vim.fn.writefile({
        '#!/bin/sh',
        '[ "$*" = "ps --json" ] || { echo "unexpected: $*" >&2; exit 2; }',
        'cat ' .. vim.fn.shellescape(PS_FIXTURE),
      }, cli)
      vim.fn.setfperm(cli, 'rwxr-xr-x')
      config.setup({ workflows = { binary = cli } })

      local result, err = workflows.list()

      assert.is_nil(err)
      assert.same(
        { { 'b7e1d4f', 'blocked', 'fixer' }, { 'a3f8b2c', 'running', 'reviewer' } },
        vim.tbl_map(function(wf)
          return { wf.id, wf.status, wf.current_agent }
        end, result)
      )
      assert.same(
        { 'reviewer', 'running', 'doeff-wf-reviewer', '%3' },
        { result[2].agents[1].name, result[2].agents[1].status, result[2].agents[1].session_name, result[2].agents[1].pane_id }
      )
    end)
  end)
end)
