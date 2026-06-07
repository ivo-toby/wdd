import { readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";

const requiredSkills = [
  "subagent-pr-orchestration",
  "wave-driven-development",
  "wdd-constitution",
  "wdd-init-project",
  "wdd-plan-waves",
  "wdd-reconcile-wave",
  "wdd-start-epic",
  "wdd-start-wave",
  "wdd-status",
  "wdd-validate-tickets",
  "wdd-write-tickets",
];

const requiredTemplates = [
  "constitution.md",
  "epic.md",
  "ticket.md",
  "wave-plan.md",
  "controller-state.md",
  "implementation-brief.md",
  "validation-checklist.md",
];

function fail(message) {
  console.error(message);
  process.exitCode = 1;
}

function frontmatter(text, file) {
  if (!text.startsWith("---\n")) {
    fail(`${file}: missing YAML frontmatter`);
    return {};
  }
  const end = text.indexOf("\n---", 4);
  if (end === -1) {
    fail(`${file}: unclosed YAML frontmatter`);
    return {};
  }
  const raw = text.slice(4, end);
  const parsed = {};
  for (const line of raw.split(/\r?\n/)) {
    const match = line.match(/^([a-zA-Z_][a-zA-Z0-9_-]*):\s*(.*)$/);
    if (match) {
      parsed[match[1]] = match[2].trim().replace(/^"|"$/g, "");
    }
  }
  return parsed;
}

async function exists(path) {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
}

for (const skill of requiredSkills) {
  const file = join("skills", skill, "SKILL.md");
  if (!(await exists(file))) {
    fail(`${file}: missing required skill`);
    continue;
  }
  const text = await readFile(file, "utf8");
  const meta = frontmatter(text, file);
  if (meta.name !== skill) {
    fail(`${file}: frontmatter name must be ${skill}`);
  }
  if (!meta.description || meta.description.length < 40) {
    fail(`${file}: description is too short for reliable discovery`);
  }
  for (const section of ["## User Input", "## Preconditions", "## Workflow", "## Done When"]) {
    if (!text.includes(section)) {
      fail(`${file}: missing section ${section}`);
    }
  }
  if (/(^|[`'"])wdd\s/.test(text)) {
    fail(`${file}: must not rely on a wdd CLI command`);
  }
}

for (const template of requiredTemplates) {
  const file = join("templates", template);
  if (!(await exists(file))) {
    fail(`${file}: missing required template`);
    continue;
  }
  const text = await readFile(file, "utf8");
  if (!text.startsWith("---\n")) {
    fail(`${file}: template must use YAML frontmatter`);
  }
}

const skillDirs = (await readdir("skills", { withFileTypes: true }))
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .sort();
for (const dir of skillDirs) {
  if (!requiredSkills.includes(dir)) {
    fail(`skills/${dir}: unexpected skill directory not listed in validator`);
  }
}

if (process.exitCode) {
  process.exit(process.exitCode);
}

console.log(`Validated ${requiredSkills.length} skills and ${requiredTemplates.length} templates.`);
