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

export function parseSchema(md: string): SchemaInfo {
  const tables: Record<string, string[]> = {};
  const columns: string[] = [];
  const re = /([a-zA-Z_]\w*)\s*\(([^)]*)\)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(md)) !== null) {
    const table = m[1];
    const cols = m[2]
      .split(",")
      .map((s) => s.trim())
      .filter((s) => /^[a-zA-Z_]\w*$/.test(s));
    if (cols.length === 0) continue;
    if (!tables[table]) tables[table] = [];
    for (const c of cols) {
      if (!tables[table].includes(c)) tables[table].push(c);
      if (!columns.includes(c)) columns.push(c);
    }
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
