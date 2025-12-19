function normalizeSeparators(text: string): string {
  return text.replace(/[_\-./\\:@()[\]{}<>]/g, ' ');
}

export function normalizeForSearch(text: string): string {
  return normalizeSeparators(text)
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();
}

export function tokenizeSearchQuery(query: string): string[] {
  const normalized = normalizeForSearch(query);
  return normalized ? normalized.split(' ') : [];
}

export function fuzzySubsequenceMatch(needle: string, haystack: string): boolean {
  if (!needle) {
    return true;
  }
  if (needle.length > haystack.length) {
    return false;
  }

  let needleIndex = 0;
  for (let i = 0; i < haystack.length && needleIndex < needle.length; i += 1) {
    if (haystack[i] === needle[needleIndex]) {
      needleIndex += 1;
    }
  }
  return needleIndex === needle.length;
}

export function multiTokenFuzzyMatch(query: string, haystack: string): boolean {
  const tokens = tokenizeSearchQuery(query);
  if (tokens.length === 0) {
    return true;
  }

  const normalizedHaystack = normalizeForSearch(haystack);
  if (!normalizedHaystack) {
    return false;
  }

  const collapsedHaystack = normalizedHaystack.replace(/\s+/g, '');
  return tokens.every((token) => {
    if (!token) {
      return true;
    }
    // Fast path: direct substring match in the normalized form.
    if (normalizedHaystack.includes(token)) {
      return true;
    }
    // Fuzzy: subsequence match against a collapsed form (e.g. abc fg -> abc_de_fg).
    const collapsedToken = token.replace(/\s+/g, '');
    return fuzzySubsequenceMatch(collapsedToken, collapsedHaystack);
  });
}

