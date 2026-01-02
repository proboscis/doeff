.PHONY: test lint deps clean

# Run tests with plenary
test:
	nvim --headless -u tests/minimal_init.lua \
		-c "PlenaryBustedDirectory tests/ {minimal_init = 'tests/minimal_init.lua', sequential = true}"

# Run a single test file
test-file:
	nvim --headless -u tests/minimal_init.lua \
		-c "PlenaryBustedFile $(FILE)"

# Install test dependencies
deps:
	@mkdir -p /tmp
	@if [ ! -d /tmp/plenary.nvim ]; then \
		git clone --depth=1 https://github.com/nvim-lua/plenary.nvim /tmp/plenary.nvim; \
	fi
	@if [ ! -d /tmp/telescope.nvim ]; then \
		git clone --depth=1 https://github.com/nvim-telescope/telescope.nvim /tmp/telescope.nvim; \
	fi

# Lint with luacheck (if available)
lint:
	@command -v luacheck >/dev/null 2>&1 && luacheck lua/ || echo "luacheck not installed"

# Clean test dependencies
clean:
	rm -rf /tmp/plenary.nvim /tmp/telescope.nvim
