import { expect, test } from "vitest";

import { diffLines, diffWords } from "@/lib/diff";

test("identical input is one same part", () => {
  expect(diffLines("a\nb", "a\nb")).toEqual([{ kind: "same", text: "a\nb" }]);
  expect(diffWords("a b", "a b")).toEqual([{ kind: "same", text: "a b" }]);
  expect(diffLines("", "")).toEqual([]);
});

test("line diff marks changed lines", () => {
  expect(diffLines("one\ntwo\nthree", "one\nTWO\nthree")).toEqual([
    { kind: "same", text: "one" },
    { kind: "removed", text: "two" },
    { kind: "added", text: "TWO" },
    { kind: "same", text: "three" },
  ]);
});

test("line diff marks added and removed runs", () => {
  expect(diffLines("a\nc", "a\nb1\nb2\nc")).toEqual([
    { kind: "same", text: "a" },
    { kind: "added", text: "b1\nb2" },
    { kind: "same", text: "c" },
  ]);
  expect(diffLines("a\nx\ny\nc", "a\nc")).toEqual([
    { kind: "same", text: "a" },
    { kind: "removed", text: "x\ny" },
    { kind: "same", text: "c" },
  ]);
});

test("word diff keeps whitespace and marks changed words", () => {
  // \S+ runs keep punctuation with its word, so "tools." and "well." differ.
  const parts = diffWords("I built fast tools.", "I built slow tools well.");
  expect(parts).toEqual([
    { kind: "same", text: "I built " },
    { kind: "removed", text: "fast" },
    { kind: "added", text: "slow" },
    { kind: "same", text: " " },
    { kind: "removed", text: "tools." },
    { kind: "added", text: "tools well." },
  ]);
});
