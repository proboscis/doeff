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

      assert.equals('uv run doeff run --program src.pipelines.main', cmd)
    end)

    it('should include interpreter when specified', function()
      local cmd = runner.build_command({
        program = 'src.pipelines.main',
        interpreter = 'src.interpreters.default',
      })

      assert.equals('uv run doeff run --program src.pipelines.main --interpreter src.interpreters.default', cmd)
    end)

    it('should include transform when specified', function()
      local cmd = runner.build_command({
        program = 'src.pipelines.main',
        transform = 'src.transforms.optimize',
      })

      assert.equals('uv run doeff run --program src.pipelines.main --transform src.transforms.optimize', cmd)
    end)

    it('should include all options', function()
      local cmd = runner.build_command({
        program = 'src.pipelines.main',
        interpreter = 'src.interpreters.default',
        transform = 'src.transforms.optimize',
      })

      assert.equals(
        'uv run doeff run --program src.pipelines.main'
          .. ' --interpreter src.interpreters.default --transform src.transforms.optimize',
        cmd
      )
    end)

    it('should return an error without a program', function()
      local cmd, err = runner.build_command({})

      assert.is_nil(cmd)
      assert.equals('opts.program is missing or invalid', err)
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
end)
