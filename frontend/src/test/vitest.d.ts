/* eslint-disable @typescript-eslint/no-empty-object-type, @typescript-eslint/no-unused-vars -- declaration merging needs empty interfaces and vitest's own generics */
import type { TestingLibraryMatchers } from "@testing-library/jest-dom/matchers";

// @testing-library/jest-dom still augments vitest's `Assertion<T>`; vitest 5 declares `Assertion<R, T>`
// (R = the matcher's return type), so the shipped augmentation no longer lines up. This restates it with
// the vitest 5 generics so `toBeInTheDocument()` and friends type-check in the tests.
declare module "vitest" {
  interface Assertion<R extends void | Promise<void> = void, T = unknown> extends TestingLibraryMatchers<unknown, R> {}
  interface AsymmetricMatchersContaining extends TestingLibraryMatchers<unknown, void> {}
}
