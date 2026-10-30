// A plain LCS diff over lines or words; no library, no options.

export type DiffPart = { kind: "same" | "added" | "removed"; text: string };

function sequenceDiff(a: string[], b: string[], joiner: string): DiffPart[] {
  const rows = a.length + 1;
  const columns = b.length + 1;
  const table = Array.from({ length: rows }, () => new Array<number>(columns).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i][j] = a[i] === b[j]
        ? table[i + 1][j + 1] + 1
        : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const parts: DiffPart[] = [];
  function push(kind: DiffPart["kind"], text: string) {
    const last = parts[parts.length - 1];
    if (last && last.kind === kind) last.text = `${last.text}${last.text ? joiner : ""}${text}`;
    else parts.push({ kind, text });
  }
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      push("same", a[i]);
      i++;
      j++;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      push("removed", a[i]);
      i++;
    } else {
      push("added", b[j]);
      j++;
    }
  }
  while (i < a.length) push("removed", a[i++]);
  while (j < b.length) push("added", b[j++]);
  return parts;
}

export function diffLines(before: string, after: string): DiffPart[] {
  if (before === after) return before ? [{ kind: "same", text: before }] : [];
  return sequenceDiff(before.split("\n"), after.split("\n"), "\n");
}

function words(text: string): string[] {
  return text.match(/\S+|\s+/g) ?? [];
}

export function diffWords(before: string, after: string): DiffPart[] {
  if (before === after) return before ? [{ kind: "same", text: before }] : [];
  return sequenceDiff(words(before), words(after), "");
}
