function mergeOrphanOrderedListMarkers(markdown: string): string {
  let current = markdown;
  for (let index = 0; index < 8; index += 1) {
    const next = current.replace(
      /(^|\n)(\d{1,3})(?:\.|．)[ \t]*(?:\r?\n[ \t]*)+(?=\S)/g,
      "$1$2. ",
    );
    if (next === current) break;
    current = next;
  }
  return current;
}

export function preprocessMarkdown(markdown: string): string {
  return mergeOrphanOrderedListMarkers(markdown.replace(/\uFF5C/g, "|"));
}
