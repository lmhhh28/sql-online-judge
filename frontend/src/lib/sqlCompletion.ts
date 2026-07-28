import type * as monaco from "monaco-editor/editor/editor.api";

const PG_KEYWORDS: readonly string[] = Array.from(new Set([
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
]));

export const SQL_COMPLETION_LIMITS = Object.freeze({
  schemaTables: 128,
  schemaColumnsPerTable: 256,
  schemaUniqueColumns: 1024,
  schemaColumnEntries: 4096,
  tableSuggestions: 128,
  columnSuggestions: 768,
  dotColumnSuggestions: 256,
  totalSuggestions: 1024
});

export interface SchemaInfo {
  tables: Record<string, string[]>;
  columns: string[];
}

function* splitTopLevelCommas(text: string): Generator<string> {
  let depth = 0;
  let partStart = 0;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === "(") {
      depth++;
    } else if (ch === ")") {
      depth--;
    } else if (ch === "," && depth === 0) {
      yield text.slice(partStart, i);
      partStart = i + 1;
    }
  }
  if (partStart < text.length) yield text.slice(partStart);
}

const CONSTRAINT_LEADWORDS = new Set([
  "primary", "foreign", "unique", "check", "constraint", "key", "exclude"
]);

function columnNameOf(def: string): string | null {
  const match = /^\s*([a-zA-Z_]\w*)(?=\s|$)/.exec(def);
  if (!match) return null;
  const name = match[1];
  if (CONSTRAINT_LEADWORDS.has(name.toLowerCase())) return null;
  return name;
}

export function parseSchema(md: string): SchemaInfo {
  interface TableState {
    columns: string[];
    seen: Set<string>;
  }

  const tableStates = new Map<string, TableState>();
  const columns: string[] = [];
  const seenColumns = new Set<string>();
  let columnEntries = 0;

  const finish = (): SchemaInfo => ({
    // Object.fromEntries defines names such as "__proto__" as safe own properties.
    tables: Object.fromEntries(
      Array.from(tableStates, ([table, state]) => [table, state.columns])
    ),
    columns
  });

  // Returns false as soon as accepting another item would exceed a hard limit.
  const add = (table: string, cols: Iterable<string>): boolean => {
    let state = tableStates.get(table);
    for (const c of cols) {
      if (state?.seen.has(c)) continue;
      if (state && state.columns.length >= SQL_COMPLETION_LIMITS.schemaColumnsPerTable) {
        return false;
      }
      if (columnEntries >= SQL_COMPLETION_LIMITS.schemaColumnEntries) return false;

      const isNewColumn = !seenColumns.has(c);
      if (isNewColumn && columns.length >= SQL_COMPLETION_LIMITS.schemaUniqueColumns) {
        return false;
      }
      if (!state) {
        if (tableStates.size >= SQL_COMPLETION_LIMITS.schemaTables) return false;
        state = { columns: [], seen: new Set<string>() };
        tableStates.set(table, state);
      }

      state.seen.add(c);
      state.columns.push(c);
      columnEntries++;
      if (isNewColumn) {
        seenColumns.add(c);
        columns.push(c);
      }
    }
    return true;
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
    // A later match would be nested in the same malformed definition and would
    // rescan the remaining input. Stop here to keep malformed input linear.
    if (end === -1) break;
    const inner = md.slice(start + 1, end);
    function* ddlColumnNames(): Generator<string> {
      for (const definition of splitTopLevelCommas(inner)) {
        const name = columnNameOf(definition);
        if (name !== null) yield name;
      }
    }
    if (!add(table, ddlColumnNames())) return finish();
    tableRe.lastIndex = end + 1;
  }
  if (matchedDdl) return finish();

  // Fallback: legacy "table(col, col)" signature without types.
  const legacyRe = /([a-zA-Z_]\w*)\s*\(([^)]*)\)/g;
  while ((m = legacyRe.exec(md)) !== null) {
    const table = m[1];
    const inner = m[2];
    function* legacyColumnNames(): Generator<string> {
      for (const definition of splitTopLevelCommas(inner)) {
        const name = definition.trim();
        if (/^[a-zA-Z_]\w*$/.test(name)) yield name;
      }
    }
    if (!add(table, legacyColumnNames())) return finish();
  }
  return finish();
}

export function createCompletionProvider(
  monacoInstance: typeof monaco,
  schema: SchemaInfo
): monaco.languages.CompletionItemProvider {
  const tableNames: string[] = [];
  for (const tableName in schema.tables) {
    if (!Object.prototype.hasOwnProperty.call(schema.tables, tableName)) continue;
    tableNames.push(tableName);
    if (tableNames.length >= SQL_COMPLETION_LIMITS.tableSuggestions) break;
  }
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
        const cols = Object.prototype.hasOwnProperty.call(schema.tables, tbl)
          ? schema.tables[tbl]
          : [];
        const suggestions: monaco.languages.CompletionItem[] = [];
        const seen = new Set<string>();
        for (const col of cols) {
          if (
            suggestions.length >= SQL_COMPLETION_LIMITS.dotColumnSuggestions ||
            suggestions.length >= SQL_COMPLETION_LIMITS.totalSuggestions
          ) {
            break;
          }
          if (seen.has(col)) continue;
          seen.add(col);
          suggestions.push({
            label: col,
            kind: kind.Field,
            insertText: col,
            detail: `column · ${tbl}`,
            range
          });
        }
        return {
          suggestions
        };
      }
      const suggestions: monaco.languages.CompletionItem[] = [];
      for (const kw of PG_KEYWORDS) {
        if (suggestions.length >= SQL_COMPLETION_LIMITS.totalSuggestions) break;
        suggestions.push({
          label: kw,
          kind: kind.Keyword,
          insertText: kw,
          range
        });
      }
      for (const t of tableNames) {
        if (suggestions.length >= SQL_COMPLETION_LIMITS.totalSuggestions) break;
        suggestions.push({
          label: t,
          kind: kind.Class,
          insertText: t,
          detail: "table",
          range
        });
      }
      const seenColumns = new Set<string>();
      let columnSuggestionCount = 0;
      for (const c of schema.columns) {
        if (
          columnSuggestionCount >= SQL_COMPLETION_LIMITS.columnSuggestions ||
          suggestions.length >= SQL_COMPLETION_LIMITS.totalSuggestions
        ) {
          break;
        }
        if (seenColumns.has(c)) continue;
        seenColumns.add(c);
        columnSuggestionCount++;
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
