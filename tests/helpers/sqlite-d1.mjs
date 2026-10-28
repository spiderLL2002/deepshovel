// Real SQLite behind the small D1 surface the Web tier uses, with the
// repository's own migration chain (triggers, CHECKs and foreign keys included).
import fs from "node:fs";
import { DatabaseSync } from "node:sqlite";

class Statement {
  args = [];
  constructor(owner, sql) { this.owner = owner; this.sql = sql; }
  bind(...args) { this.args = args; return this; }
  async first() { return this.owner.database.prepare(this.sql).get(...this.args) ?? null; }
  async all() { return { results: this.owner.database.prepare(this.sql).all(...this.args) }; }
  async run() {
    const result = this.owner.database.prepare(this.sql).run(...this.args);
    return { success: true, meta: { changes: Number(result.changes) } };
  }
}

export class SqliteD1 {
  queries = [];
  constructor(database) { this.database = database; }
  prepare(sql) { this.queries.push(sql); return new Statement(this, sql); }
  async batch(statements) {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const results = [];
      for (const statement of statements) results.push(await statement.run());
      this.database.exec("COMMIT");
      return results;
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
  }
}

export function migratedDatabase() {
  const database = new DatabaseSync(":memory:");
  database.exec("PRAGMA foreign_keys=ON");
  const names = fs.readdirSync(new URL("../../drizzle/", import.meta.url))
    .filter((name) => /^\d{4}_.+\.sql$/u.test(name))
    .sort();
  for (const name of names) {
    const sql = fs.readFileSync(new URL(`../../drizzle/${name}`, import.meta.url), "utf8");
    for (const statement of sql.split("--> statement-breakpoint")) {
      if (statement.trim()) database.exec(statement);
    }
  }
  return database;
}
