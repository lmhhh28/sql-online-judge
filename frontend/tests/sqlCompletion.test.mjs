import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import ts from "typescript";

const sourcePath = fileURLToPath(
  new URL("../src/lib/sqlCompletion.ts", import.meta.url)
);
const source = readFileSync(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020
  },
  fileName: sourcePath
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const {
  SQL_COMPLETION_LIMITS,
  createCompletionProvider,
  parseSchema
} = await import(moduleUrl);

test("parseSchema preserves DDL and legacy completion behavior with linear deduplication", () => {
  const ddl = parseSchema(`
    CREATE TABLE users (
      id BIGINT PRIMARY KEY,
      email TEXT,
      balance NUMERIC(12, 2),
      CONSTRAINT users_email_unique UNIQUE (email),
      id INTEGER
    );
    CREATE TABLE audit (
      id BIGINT,
      user_id BIGINT,
      PRIMARY KEY (id)
    );
  `);

  assert.deepEqual(ddl.tables, {
    users: ["id", "email", "balance"],
    audit: ["id", "user_id"]
  });
  assert.deepEqual(ddl.columns, ["id", "email", "balance", "user_id"]);

  const legacy = parseSchema("users(id, name)\nposts(id, user_id)");
  assert.deepEqual(legacy.tables, {
    users: ["id", "name"],
    posts: ["id", "user_id"]
  });
  assert.deepEqual(legacy.columns, ["id", "name", "user_id"]);
});

test("parseSchema safely supports table names that overlap Object prototype properties", () => {
  const schema = parseSchema(`
    CREATE TABLE __proto__ (id INTEGER);
    CREATE TABLE constructor (value TEXT);
  `);

  assert.deepEqual(Object.keys(schema.tables), ["__proto__", "constructor"]);
  assert.deepEqual(schema.tables.__proto__, ["id"]);
  assert.deepEqual(schema.tables.constructor, ["value"]);
});

test("parseSchema stops at the table limit", () => {
  const ddl = Array.from(
    { length: SQL_COMPLETION_LIMITS.schemaTables + 20 },
    (_, index) => `CREATE TABLE t${index} (id INTEGER);`
  ).join("\n");

  const schema = parseSchema(ddl);
  assert.equal(Object.keys(schema.tables).length, SQL_COMPLETION_LIMITS.schemaTables);
  assert.deepEqual(schema.columns, ["id"]);
});

test("parseSchema enforces global unique-column and aggregate entry limits", () => {
  const uniqueDdl = Array.from({ length: 5 }, (_, tableIndex) => {
    const definitions = Array.from(
      { length: SQL_COMPLETION_LIMITS.schemaColumnsPerTable },
      (_, columnIndex) => `u${tableIndex}_${columnIndex} INTEGER`
    ).join(",");
    return `CREATE TABLE unique_${tableIndex} (${definitions});`;
  }).join("\n");
  const uniqueSchema = parseSchema(uniqueDdl);
  assert.equal(
    uniqueSchema.columns.length,
    SQL_COMPLETION_LIMITS.schemaUniqueColumns
  );
  assert.equal(Object.keys(uniqueSchema.tables).length, 4);

  const sharedDefinitions = Array.from(
    { length: SQL_COMPLETION_LIMITS.schemaColumnsPerTable },
    (_, index) => `shared_${index} INTEGER`
  ).join(",");
  const aggregateDdl = Array.from(
    { length: 20 },
    (_, index) => `CREATE TABLE shared_${index} (${sharedDefinitions});`
  ).join("\n");
  const aggregateSchema = parseSchema(aggregateDdl);
  assert.equal(
    Object.values(aggregateSchema.tables).reduce(
      (total, tableColumns) => total + tableColumns.length,
      0
    ),
    SQL_COMPLETION_LIMITS.schemaColumnEntries
  );
  assert.equal(Object.keys(aggregateSchema.tables).length, 16);
});

test("malformed nested CREATE TABLE input does not trigger repeated rescans", {
  timeout: 5_000
}, () => {
  const malformed = Array.from(
    { length: 20_000 },
    (_, index) => `CREATE TABLE broken_${index} (`
  ).join(" ");
  const startedAt = performance.now();
  const schema = parseSchema(malformed);
  const elapsedMs = performance.now() - startedAt;

  assert.deepEqual(schema, { tables: {}, columns: [] });
  assert.ok(
    elapsedMs < 2_000,
    `malformed input parsing took ${elapsedMs.toFixed(1)} ms`
  );
});

test("a 50,000-column input is bounded and avoids quadratic suggestion construction", {
  timeout: 5_000
}, () => {
  const definitions = Array.from(
    { length: 50_000 },
    (_, index) => `column_${index} INTEGER`
  ).join(",");
  const startedAt = performance.now();
  const schema = parseSchema(
    `CREATE TABLE huge (${definitions}); CREATE TABLE ignored (id INTEGER);`
  );
  const elapsedMs = performance.now() - startedAt;

  assert.equal(
    schema.tables.huge.length,
    SQL_COMPLETION_LIMITS.schemaColumnsPerTable
  );
  assert.equal(schema.columns.length, SQL_COMPLETION_LIMITS.schemaColumnsPerTable);
  assert.equal(schema.tables.ignored, undefined);
  assert.ok(
    elapsedMs < 2_000,
    `bounded parsing took ${elapsedMs.toFixed(1)} ms`
  );
});

function fakeMonaco() {
  return {
    languages: {
      CompletionItemKind: {
        Class: 1,
        Field: 2,
        Keyword: 3
      }
    }
  };
}

function fakeModel(line) {
  return {
    getWordUntilPosition() {
      return { startColumn: 1, endColumn: 1, word: "" };
    },
    getValueInRange() {
      return line;
    }
  };
}

const position = { lineNumber: 1, column: 1 };

test("completion results enforce table, column, dotted-column, and total limits", () => {
  const manyColumns = Array.from({ length: 5_000 }, (_, index) => `c${index}`);
  const tables = Object.fromEntries(
    Array.from({ length: 1_000 }, (_, index) => [`t${index}`, manyColumns])
  );
  const provider = createCompletionProvider(fakeMonaco(), {
    tables,
    columns: [...manyColumns, ...manyColumns]
  });

  const general = provider.provideCompletionItems(fakeModel("SELECT "), position);
  assert.ok(general.suggestions.length <= SQL_COMPLETION_LIMITS.totalSuggestions);
  assert.ok(
    general.suggestions.filter((item) => item.detail === "table").length <=
      SQL_COMPLETION_LIMITS.tableSuggestions
  );
  assert.ok(
    general.suggestions.filter((item) => item.detail === "column").length <=
      SQL_COMPLETION_LIMITS.columnSuggestions
  );

  const dotted = provider.provideCompletionItems(fakeModel("t0."), position);
  assert.equal(dotted.suggestions.length, SQL_COMPLETION_LIMITS.dotColumnSuggestions);
  assert.equal(new Set(dotted.suggestions.map((item) => item.label)).size,
    dotted.suggestions.length);

  const prototypeLookup = provider.provideCompletionItems(
    fakeModel("toString."),
    position
  );
  assert.deepEqual(prototypeLookup.suggestions, []);
});
