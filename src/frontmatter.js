import YAML from "yaml";

export class FrontmatterError extends Error {
  constructor(message, filePath) {
    super(filePath ? `${message}: ${filePath}` : message);
    this.name = "FrontmatterError";
    this.filePath = filePath;
  }
}

export function parseMarkdown(raw, filePath) {
  if (!raw.startsWith("---\n")) {
    throw new FrontmatterError("Missing YAML frontmatter", filePath);
  }

  const closeIndex = raw.indexOf("\n---", 4);
  if (closeIndex === -1) {
    throw new FrontmatterError("Unclosed YAML frontmatter", filePath);
  }

  const yamlText = raw.slice(4, closeIndex).trim();
  const afterClose = raw.slice(closeIndex + 4);
  const metadata = yamlText.length > 0 ? YAML.parse(yamlText) : {};

  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    throw new FrontmatterError("YAML frontmatter must be a mapping", filePath);
  }

  return {
    metadata,
    body: afterClose.replace(/^\r?\n/, ""),
  };
}

export function formatMarkdown(metadata, body) {
  const yamlText = YAML.stringify(metadata).trimEnd();
  return `---\n${yamlText}\n---\n\n${body.trimStart()}`;
}

