-- Tests for doeff.nvim runner module

describe('doeff.runner', function()
  local runner

  before_each(function()
    package.loaded['doeff.runner'] = nil
    package.loaded['doeff.indexer'] = nil
    package.loaded['doeff.config'] = nil
    runner = require('doeff.runner')
  end)

  describe('build_command', function()
    it('should build basic command', function()
      local cmd = runner.build_command({
        program = 'src.pipelines.main',
      })

      assert.is_true(vim.tbl_contains(cmd, 'python'))
      assert.is_true(vim.tbl_contains(cmd, '-m'))
      assert.is_true(vim.tbl_contains(cmd, 'doeff'))
      assert.is_true(vim.tbl_contains(cmd, 'run'))
      assert.is_true(vim.tbl_contains(cmd, '--program'))
      assert.is_true(vim.tbl_contains(cmd, 'src.pipelines.main'))
    end)

    it('should include interpreter when specified', function()
      local cmd = runner.build_command({
        program = 'src.pipelines.main',
        interpreter = 'src.interpreters.default',
      })

      assert.is_true(vim.tbl_contains(cmd, '--interpreter'))
      assert.is_true(vim.tbl_contains(cmd, 'src.interpreters.default'))
    end)

    it('should include transform when specified', function()
      local cmd = runner.build_command({
        program = 'src.pipelines.main',
        transform = 'src.transforms.optimize',
      })

      assert.is_true(vim.tbl_contains(cmd, '--transform'))
      assert.is_true(vim.tbl_contains(cmd, 'src.transforms.optimize'))
    end)

    it('should include all options', function()
      local cmd = runner.build_command({
        program = 'src.pipelines.main',
        interpreter = 'src.interpreters.default',
        transform = 'src.transforms.optimize',
      })

      assert.is_true(vim.tbl_contains(cmd, '--program'))
      assert.is_true(vim.tbl_contains(cmd, '--interpreter'))
      assert.is_true(vim.tbl_contains(cmd, '--transform'))
    end)
  end)

  describe('get_last_run', function()
    it('should return nil when no previous run', function()
      local last = runner.get_last_run()
      assert.is_nil(last)
    end)
  end)

  describe('run_last', function()
    it('should notify when no previous run', function()
      -- This should not error, just notify
      runner.run_last()
      assert.is_true(true)
    end)
  end)

  describe('close_all', function()
    it('should not error when no terminals open', function()
      runner.close_all()
      assert.is_true(true)
    end)
  end)
end)
