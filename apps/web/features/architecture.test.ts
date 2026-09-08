import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..", "..");

function sourceFiles(root: string): string[] {
  return readdirSync(root).flatMap((entry) => {
    const path = join(root, entry);
    return statSync(path).isDirectory()
      ? sourceFiles(path)
      : /\.(ts|tsx)$/.test(entry)
        ? [path]
        : [];
  });
}

for (const file of sourceFiles(join(webRoot, "shared"))) {
  const source = readFileSync(file, "utf8");
  assert.doesNotMatch(
    source,
    /from ["']@\/features\//,
    `shared code must not depend on a feature: ${relative(webRoot, file)}`,
  );
}

for (const file of sourceFiles(join(webRoot, "features"))) {
  const source = readFileSync(file, "utf8");
  assert.doesNotMatch(
    source,
    /from ["']@\/app\//,
    `features must not depend on Next routes: ${relative(webRoot, file)}`,
  );
}

const sdkSource = readFileSync(join(repositoryRoot, "packages", "sdk", "src", "index.ts"), "utf8");
assert.doesNotMatch(sdkSource, /process\.env|from ["']next\//);

console.log("web architecture boundary tests passed");
