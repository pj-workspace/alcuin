/** The Canvas already renders the resource title; remove only a matching opening H1. */
export function artifactDocumentBody(content: string, title: string): string {
  const leading = /^(?:\uFEFF)?(?:[\t ]*\r?\n)*/.exec(content)?.[0].length ?? 0;
  const body = content.slice(leading);
  const atx = /^[\t ]{0,3}#[\t ]+([^\r\n]+)(?:\r?\n|$)/.exec(body);
  const setext = atx ? null : /^([^\r\n]+)\r?\n[ ]{0,3}=+[\t ]*(?:\r?\n|$)/.exec(body);
  const heading = atx ?? setext;
  if (!heading) return content;
  const headingTitle = atx ? heading[1]!.replace(/[\t ]+#+[\t ]*$/, "") : heading[1]!;
  const normalize = (value: string) => value.trim().replace(/\s+/g, " ");
  if (normalize(headingTitle) !== normalize(title)) return content;
  return body.slice(heading[0].length).replace(/^\r?\n/, "");
}
