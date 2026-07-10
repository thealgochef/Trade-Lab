// Minimal Node declarations for tests that read repo files (the stylesheet
// contract test). The project intentionally ships no @types/node — vitest runs
// in Node, but only this narrow surface is needed.
declare module 'node:fs' {
  export function readFileSync(path: string, encoding: 'utf8'): string;
}

declare module 'node:path' {
  export function resolve(...segments: string[]): string;
}

declare const process: { cwd(): string };
