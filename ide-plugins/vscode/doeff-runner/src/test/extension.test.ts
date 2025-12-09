import * as assert from 'assert';

// Test the PROGRAM_REGEX and parsing logic directly
const PROGRAM_REGEX =
  /^\s*([A-Za-z_]\w*)\s*:\s*(?:["']?Program(?:\s*\[\s*([^\]]+)\s*\])?["']?)/;

interface ProgramDeclaration {
  name: string;
  typeArg: string;
}

function parseProgramDeclaration(line: string): ProgramDeclaration | undefined {
  const code = line.split('#')[0];
  const match = PROGRAM_REGEX.exec(code);
  if (!match) {
    return;
  }

  // Skip if this looks like a function parameter
  // 1. Check if line ends with ',' or ')' after the annotation (typical for function args)
  const afterAnnotation = code.slice(match.index + match[0].length).trim();
  if (afterAnnotation.endsWith(',') || afterAnnotation.endsWith(')')) {
    return;
  }

  // 2. Check if there are unmatched opening parens before the variable name
  //    This indicates we're inside a function signature like: def foo(arg: Program[T])
  const beforeMatch = code.slice(0, match.index);
  const openParens = (beforeMatch.match(/\(/g) || []).length;
  const closeParens = (beforeMatch.match(/\)/g) || []).length;
  if (openParens > closeParens) {
    return; // Inside parentheses, likely a function parameter
  }

  const name = match[1];
  const typeArg = match[2]?.trim() || '';
  return { name, typeArg };
}

suite('Extension Test Suite', () => {
  suite('Program Declaration Parsing', () => {
    test('should detect module-level Program assignment', () => {
      const result = parseProgramDeclaration('my_program: Program[int] = something');
      assert.ok(result);
      assert.strictEqual(result?.name, 'my_program');
      assert.strictEqual(result?.typeArg, 'int');
    });

    test('should detect module-level Program annotation without assignment', () => {
      const result = parseProgramDeclaration('my_program: Program[str]');
      assert.ok(result);
      assert.strictEqual(result?.name, 'my_program');
      assert.strictEqual(result?.typeArg, 'str');
    });

    test('should detect untyped Program', () => {
      const result = parseProgramDeclaration('my_program: Program');
      assert.ok(result);
      assert.strictEqual(result?.name, 'my_program');
      assert.strictEqual(result?.typeArg, '');
    });

    test('should NOT detect function parameter on same line as def', () => {
      const result = parseProgramDeclaration('def my_func(program: Program[int]) -> str:');
      assert.strictEqual(result, undefined);
    });

    test('should NOT detect function parameter on continuation line with comma', () => {
      const result = parseProgramDeclaration('    program: Program[int],');
      assert.strictEqual(result, undefined);
    });

    test('should NOT detect function parameter on continuation line with close paren', () => {
      const result = parseProgramDeclaration('    program: Program[int])');
      assert.strictEqual(result, undefined);
    });

    test('should NOT detect function parameter with default value and comma', () => {
      const result = parseProgramDeclaration('    program: Program[int] = None,');
      assert.strictEqual(result, undefined);
    });

    test('should NOT detect function parameter with close paren and colon', () => {
      const result = parseProgramDeclaration('    program: Program[int]) -> str:');
      assert.strictEqual(result, undefined);
    });

    test('should detect indented assignment (class attribute)', () => {
      const result = parseProgramDeclaration('    my_program: Program[int] = something');
      assert.ok(result);
      assert.strictEqual(result?.name, 'my_program');
    });

    test('should handle quoted type annotation', () => {
      const result = parseProgramDeclaration('my_program: "Program[MyType]" = value');
      assert.ok(result);
      assert.strictEqual(result?.name, 'my_program');
      assert.strictEqual(result?.typeArg, 'MyType');
    });

    test('should ignore lines with comments after code', () => {
      const result = parseProgramDeclaration('my_program: Program[int]  # some comment');
      assert.ok(result);
      assert.strictEqual(result?.name, 'my_program');
    });
  });
});
