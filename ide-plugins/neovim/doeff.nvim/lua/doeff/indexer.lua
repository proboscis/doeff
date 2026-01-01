-- doeff.nvim indexer module
-- Integrates with doeff-indexer binary for entrypoint discovery

local M = {}

local config = require('doeff.config')

---@class DoeffEntry
---@field name string Function/method name
---@field qualified_name string Fully qualified name (module.path.name)
---@field file_path string Absolute path to file
---@field line number Line number in file
---@field item_kind string 'function', 'async_function', or 'assignment'
---@field categories string[] Categories like 'program_interpreter', 'kleisli_program'
---@field decorators string[] Decorators on the function
---@field markers string[] Doeff markers (interpreter, transform, kleisli)
---@field docstring string|nil Function docstring
---@field return_annotation string|nil Return type annotation
---@field program_parameters DoeffParameter[] Parameters
---@field type_usages DoeffTypeUsage[] Type usages in signature

---@class DoeffParameter
---@field name string Parameter name
---@field annotation string|nil Type annotation
---@field is_required boolean Whether parameter is required
---@field position number Position index
---@field kind string 'positional', 'keyword', 'var_arg', 'var_keyword'

---@class DoeffTypeUsage
---@field kind string 'program' or 'kleisli'
---@field raw string Raw type string
---@field type_arguments string[] Type arguments

---@class DoeffIndex
---@field version string Indexer version
---@field root string Project root path
---@field generated_at string ISO timestamp
---@field entries DoeffEntry[]
---@field stats table Statistics about the index

-- Cache for index results
local cache = {
  data = nil,
  timestamp = 0,
  root = nil,
}

---Find the project root directory
---@param start_path string|nil Starting path for search
---@return string|nil
function M.find_root(start_path)
  start_path = start_path or vim.fn.getcwd()
  local markers = config.get().root_markers

  local root = vim.fs.find(markers, {
    path = start_path,
    upward = true,
    stop = vim.env.HOME,
  })

  if #root > 0 then
    return vim.fs.dirname(root[1])
  end
  return nil
end

---Execute doeff-indexer command
---@param args string[] Command arguments
---@param root string Project root
---@return table|nil result Parsed JSON result or nil on error
---@return string|nil error Error message if any
function M.exec(args, root)
  local cfg = config.get()
  local cmd = vim.list_extend({ cfg.indexer.binary }, args)
  table.insert(cmd, '--root')
  table.insert(cmd, root)

  local result = vim.system(cmd, { text = true }):wait()

  if result.code ~= 0 then
    return nil, result.stderr or 'Unknown error'
  end

  local ok, parsed = pcall(vim.json.decode, result.stdout)
  if not ok then
    return nil, 'Failed to parse JSON: ' .. tostring(parsed)
  end

  return parsed, nil
end

---Check if cache is valid
---@param root string Project root
---@return boolean
local function is_cache_valid(root)
  if cache.root ~= root then
    return false
  end
  local cfg = config.get()
  local age = vim.uv.now() - cache.timestamp
  return age < cfg.indexer.cache_ttl
end

---Get full index of all doeff entries
---@param root string|nil Project root (auto-detected if nil)
---@param force boolean|nil Force refresh even if cached
---@return DoeffIndex|nil
---@return string|nil error
function M.index(root, force)
  root = root or M.find_root()
  if not root then
    return nil, 'Could not find project root'
  end

  if not force and is_cache_valid(root) and cache.data then
    return cache.data, nil
  end

  local result, err = M.exec({ 'index' }, root)
  if err then
    return nil, err
  end

  cache.data = result
  cache.timestamp = vim.uv.now()
  cache.root = root

  return result, nil
end

---Find interpreters (Program -> T)
---@param root string|nil Project root
---@param opts table|nil Options (type_arg, proximity_file, proximity_line)
---@return DoeffEntry[]|nil
---@return string|nil error
function M.find_interpreters(root, opts)
  root = root or M.find_root()
  if not root then
    return nil, 'Could not find project root'
  end

  opts = opts or {}
  local args = { 'find-interpreters' }

  if opts.type_arg then
    table.insert(args, '--type-arg')
    table.insert(args, opts.type_arg)
  end

  if opts.proximity_file then
    table.insert(args, '--proximity-file')
    table.insert(args, opts.proximity_file)
  end

  if opts.proximity_line then
    table.insert(args, '--proximity-line')
    table.insert(args, tostring(opts.proximity_line))
  end

  local result, err = M.exec(args, root)
  if err then
    return nil, err
  end

  return result.entries, nil
end

---Find transforms (Program -> Program)
---@param root string|nil Project root
---@param opts table|nil Options (type_arg, proximity_file, proximity_line)
---@return DoeffEntry[]|nil
---@return string|nil error
function M.find_transforms(root, opts)
  root = root or M.find_root()
  if not root then
    return nil, 'Could not find project root'
  end

  opts = opts or {}
  local args = { 'find-transforms' }

  if opts.type_arg then
    table.insert(args, '--type-arg')
    table.insert(args, opts.type_arg)
  end

  if opts.proximity_file then
    table.insert(args, '--proximity-file')
    table.insert(args, opts.proximity_file)
  end

  if opts.proximity_line then
    table.insert(args, '--proximity-line')
    table.insert(args, tostring(opts.proximity_line))
  end

  local result, err = M.exec(args, root)
  if err then
    return nil, err
  end

  return result.entries, nil
end

---Find Kleisli functions (() -> Program[T])
---@param root string|nil Project root
---@param opts table|nil Options (type_arg, proximity_file, proximity_line)
---@return DoeffEntry[]|nil
---@return string|nil error
function M.find_kleisli(root, opts)
  root = root or M.find_root()
  if not root then
    return nil, 'Could not find project root'
  end

  opts = opts or {}
  local args = { 'find-kleisli' }

  if opts.type_arg then
    table.insert(args, '--type-arg')
    table.insert(args, opts.type_arg)
  end

  if opts.proximity_file then
    table.insert(args, '--proximity-file')
    table.insert(args, opts.proximity_file)
  end

  if opts.proximity_line then
    table.insert(args, '--proximity-line')
    table.insert(args, tostring(opts.proximity_line))
  end

  local result, err = M.exec(args, root)
  if err then
    return nil, err
  end

  return result.entries, nil
end

---Find interceptors (Effect -> Effect | Program)
---@param root string|nil Project root
---@param opts table|nil Options (type_arg)
---@return DoeffEntry[]|nil
---@return string|nil error
function M.find_interceptors(root, opts)
  root = root or M.find_root()
  if not root then
    return nil, 'Could not find project root'
  end

  opts = opts or {}
  local args = { 'find-interceptors' }

  if opts.type_arg then
    table.insert(args, '--type-arg')
    table.insert(args, opts.type_arg)
  end

  local result, err = M.exec(args, root)
  if err then
    return nil, err
  end

  return result.entries, nil
end

---Find environment chain for a program
---@param program_name string Qualified program name
---@param root string|nil Project root
---@return table|nil Environment chain info
---@return string|nil error
function M.find_env_chain(program_name, root)
  root = root or M.find_root()
  if not root then
    return nil, 'Could not find project root'
  end

  local result, err = M.exec({ 'find-env-chain', '--program', program_name }, root)
  if err then
    return nil, err
  end

  return result, nil
end

---Clear the cache
function M.clear_cache()
  cache.data = nil
  cache.timestamp = 0
  cache.root = nil
end

---Get all entrypoints (interpreters, kleisli, transforms)
---@param root string|nil Project root
---@param force boolean|nil Force refresh
---@return DoeffEntry[]|nil
---@return string|nil error
function M.get_all_entries(root, force)
  local index, err = M.index(root, force)
  if err then
    return nil, err
  end
  return index.entries, nil
end

---Find entry at specific file and line
---@param file string File path
---@param line number Line number
---@param root string|nil Project root
---@return DoeffEntry|nil
function M.find_at_location(file, line, root)
  local entries, err = M.get_all_entries(root)
  if err or not entries then
    return nil
  end

  -- Find closest entry at or before the given line
  local best_match = nil
  local best_distance = math.huge

  for _, entry in ipairs(entries) do
    if entry.file_path == file then
      local distance = line - entry.line
      -- Entry should be at or before the line
      if distance >= 0 and distance < best_distance then
        best_distance = distance
        best_match = entry
      end
    end
  end

  return best_match
end

return M
