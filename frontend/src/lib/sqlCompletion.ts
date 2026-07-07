import type * as monaco from "monaco-editor/esm/vs/editor/editor.api";

const PG_KEYWORDS: string[] = [
  "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "UPDATE", "DELETE",
  "SET", "CREATE", "TABLE", "VIEW", "INDEX", "DROP", "ALTER", "ADD", "RENAME",
  "TO", "COLUMN", "PRIMARY", "KEY", "FOREIGN", "REFERENCES", "NOT", "NULL",
  "DEFAULT", "UNIQUE", "CHECK", "CONSTRAINT", "JOIN", "INNER", "LEFT", "RIGHT",
  "FULL", "OUTER", "CROSS", "ON", "USING", "GROUP", "BY", "HAVING", "ORDER",
  "ASC", "DESC", "LIMIT", "OFFSET", "DISTINCT", "AS", "AND", "OR", "IN",
  "BETWEEN", "LIKE", "ILIKE", "IS", "EXISTS", "ANY", "ALL", "UNION",
  "INTERSECT", "EXCEPT", "CASE", "WHEN", "THEN", "ELSE", "END", "WITH",
  "RECURSIVE", "RETURNING", "BEGIN", "COMMIT", "ROLLBACK", "TRANSACTION",
  "GRANT", "REVOKE", "PUBLIC", "TRUNCATE", "CASCADE", "RESTRICT", "AS",
  "CAST", "COALESCE", "NULLIF", "EXTRACT", "EXISTS", "SOME",
  "INTEGER", "INT", "BIGINT", "SMALLINT", "SERIAL", "BIGSERIAL", "TEXT",
  "VARCHAR", "CHAR", "NUMERIC", "DECIMAL", "REAL", "DOUBLE", "PRECISION",
  "BOOLEAN", "BOOL", "DATE", "TIME", "TIMESTAMP", "INTERVAL", "JSON",
  "JSONB", "UUID", "ARRAY", "BYTEA", "MONEY",
  "TRUE", "FALSE", "NOW", "CURRENT_DATE", "CURRENT_TIMESTAMP", "CURRENT_TIME",
  "COUNT", "SUM", "AVG", "MIN", "MAX", "STRING_AGG", "GENERATE_SERIES",
  "ROW_NUMBER", "RANK", "DENSE_RANK", "OVER", "PARTITION", "LEAD", "LAG",
  "GENERATED", "ALWAYS", "IDENTITY", "IF", "EXISTS", "TEMPORARY", "TEMP"
];

export interface SchemaInfo {
  tables: Record<string, string[]>;
  columns: string[];
}

function splitTopLevelCommas(text: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let cur = "";
  for (const ch of text) {
    if (ch === "(") {
      depth++;
      cur += ch;
    } else if (ch === ")") {
      depth--;
      cur += ch;
    } else if (ch === "," && depth === 0) {
      parts.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  if (cur) parts.push(cur);
  return parts;
}

const CONSTRAINT_LEADWORDS = new Set([
  "primary", "foreign", "unique", "check", "constraint", "key", "exclude"
]);

function columnNameOf(def: string): string | null {
  const name = def.trim().split(/\s+/)[0];
  if (!name || !/^[a-zA-Z_]\w*$/.test(name)) return null;
  if (CONSTRAINT_LEADWORDS.has(name.toLowerCase())) return null;
  return name;
}

export function parseSchema(md: string): SchemaInfo {
  const tables: Record<string, string[]> = {};
  const columns: string[] = [];
  const add = (table: string, cols: string[]) => {
    if (!tables[table]) tables[table] = [];
    for (const c of cols) {
      if (!tables[table].includes(c)) tables[table].push(c);
      if (!columns.includes(c)) columns.push(c);
    }
  };

  const tableRe = /create\s+table\s+(?:if\s+not\s+exists\s+)?([a-zA-Z_]\w*)\s*\(/gi;
  let m: RegExpExecArray | null;
  let matchedDdl = false;
  while ((m = tableRe.exec(md)) !== null) {
    matchedDdl = true;
    const table = m[1];
    const start = m.index + m[0].length - 1;
    let depth = 0;
    let end = -1;
    for (let i = start; i < md.length; i++) {
      if (md[i] === "(") depth++;
      else if (md[i] === ")") {
        depth--;
        if (depth === 0) {
          end = i;
          break;
        }
      }
    }
    if (end === -1) continue;
    const inner = md.slice(start + 1, end);
    const cols = splitTopLevelCommas(inner)
      .map((s) => s.trim())
      .filter((s) => s.length > 0)
      .map(columnNameOf)
      .filter((n): n is string => n !== null);
    if (cols.length === 0) continue;
    add(table, cols);
  }
  if (matchedDdl) return { tables, columns };

  // Fallback: legacy "table(col, col)" signature without types.
  const legacyRe = /([a-zA-Z_]\w*)\s*\(([^)]*)\)/g;
  while ((m = legacyRe.exec(md)) !== null) {
    const table = m[1];
    const cols = m[2]
      .split(",")
      .map((s) => s.trim())
      .filter((s) => /^[a-zA-Z_]\w*$/.test(s));
    if (cols.length === 0) continue;
    add(table, cols);
  }
  return { tables, columns };
}

export function createCompletionProvider(
  monacoInstance: typeof monaco,
  schema: SchemaInfo
): monaco.languages.CompletionItemProvider {
  const tableNames = Object.keys(schema.tables);
  const kind = monacoInstance.languages.CompletionItemKind;
  return {
    triggerCharacters: [".", " "],
    provideCompletionItems(model, position) {
      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endLineNumber: position.lineNumber,
        endColumn: word.endColumn
      };
      const lineUntil = model.getValueInRange({
        startLineNumber: position.lineNumber,
        startColumn: 1,
        endLineNumber: position.lineNumber,
        endColumn: position.column
      });
      const dotMatch = lineUntil.match(/([a-zA-Z_]\w*)\.\s*$/);
      if (dotMatch) {
        const tbl = dotMatch[1];
        const cols = schema.tables[tbl] ?? [];
        return {
          suggestions: cols.map((col) => ({
            label: col,
            kind: kind.Field,
            insertText: col,
            detail: `column · ${tbl}`,
            range
          }))
        };
      }
      const suggestions: monaco.languages.CompletionItem[] = [];
      for (const kw of PG_KEYWORDS) {
        suggestions.push({
          label: kw,
          kind: kind.Keyword,
          insertText: kw,
          range
        });
      }
      for (const t of tableNames) {
        suggestions.push({
          label: t,
          kind: kind.Class,
          insertText: t,
          detail: "table",
          range
        });
      }
      for (const c of schema.columns) {
        suggestions.push({
          label: c,
          kind: kind.Field,
          insertText: c,
          detail: "column",
          range
        });
      }
      return { suggestions };
    }
  };
}
