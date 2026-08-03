import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(css|ts|tsx)$/.test(path) ? [path] : [];
  });
}

const files = sourceFiles("src").filter((file) => !file.endsWith("tokens.css"));
const violations = [];
for (const file of files) {
  const source = readFileSync(file, "utf8");
  if (/#[0-9a-f]{3,8}\b/i.test(source)) violations.push(`${file}: raw color`);
  if (/linear-gradient|radial-gradient/i.test(source)) violations.push(`${file}: gradient`);
}
if (violations.length) {
  console.error(violations.join("\n"));
  process.exitCode = 1;
}
