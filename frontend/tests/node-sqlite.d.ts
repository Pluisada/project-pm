// @types/node 20 predates node:sqlite (Node 22.5+); only the surface the Import Bids specs use.
declare module "node:sqlite" {
  export class DatabaseSync {
    constructor(path: string, options?: { readOnly?: boolean });
    prepare(sql: string): { all(...params: (string | number)[]): unknown[]; get(...params: (string | number)[]): unknown };
    close(): void;
  }
}
