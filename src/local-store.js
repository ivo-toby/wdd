import { mkdir, readdir, readFile, stat, writeFile } from "node:fs/promises";
import { join, relative } from "node:path";
import YAML from "yaml";
import { formatMarkdown, parseMarkdown } from "./frontmatter.js";

const ID_PREFIX = "WDD";

export function wddDir(root) {
  return join(root, ".wdd");
}

export function epicsDir(root) {
  return join(wddDir(root), "epics");
}

export function slugify(value) {
  const slug = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (!slug) {
    throw new Error("Slug cannot be empty");
  }
  return slug;
}

export function titleFromSlug(slug) {
  return slug
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export async function initProject(root, options = {}) {
  const agent = options.agent || "codex";
  const base = wddDir(root);
  await mkdir(epicsDir(root), { recursive: true });

  const configPath = join(base, "config.yaml");
  const constitutionPath = join(base, "constitution.md");
  const config = {
    version: 1,
    agent,
    storage: "local",
    id_prefix: ID_PREFIX,
    adapters: {},
  };

  await writeFileIfMissing(configPath, YAML.stringify(config), { force: options.force });
  await writeFileIfMissing(
    constitutionPath,
    [
      "# Project Constitution",
      "",
      "## Boundaries",
      "",
      "- Define what the project owns and what it must not change.",
      "",
      "## Prerequisites",
      "",
      "- Define setup, verification, deployment, and safety requirements.",
      "",
      "## Development Rules",
      "",
      "- WDD planning owns epics, tickets, waves, and controller state.",
      "- Implementation agents work one bounded ticket at a time.",
      "- Local markdown artifacts are the default source of truth.",
      "",
    ].join("\n"),
    { force: options.force },
  );

  return { wddDir: base, configPath, constitutionPath };
}

async function listDirectories(path) {
  try {
    const entries = await readdir(path, { withFileTypes: true });
    return entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort();
  } catch (error) {
    if (error.code === "ENOENT") {
      return [];
    }
    throw error;
  }
}

async function writeFileIfMissing(path, content, options = {}) {
  if (!options.force) {
    try {
      await stat(path);
      return false;
    } catch (error) {
      if (error.code !== "ENOENT") {
        throw error;
      }
    }
  }
  await writeFile(path, content, "utf8");
  return true;
}

async function listFiles(path) {
  try {
    const entries = await readdir(path, { withFileTypes: true });
    return entries.filter((entry) => entry.isFile()).map((entry) => entry.name).sort();
  } catch (error) {
    if (error.code === "ENOENT") {
      return [];
    }
    throw error;
  }
}

async function assertInitialized(root) {
  try {
    await stat(wddDir(root));
  } catch (error) {
    if (error.code === "ENOENT") {
      throw new Error("WDD is not initialized. Run `wdd init` first.");
    }
    throw error;
  }
}

async function nextEpicId(root) {
  const folders = await listDirectories(epicsDir(root));
  const max = folders.reduce((current, folder) => {
    const match = folder.match(/^WDD-(\d{4})-/);
    return match ? Math.max(current, Number(match[1])) : current;
  }, 0);
  return `${ID_PREFIX}-${String(max + 1).padStart(4, "0")}`;
}

async function nextTicketId(epicFolder, epicId) {
  const files = await listFiles(join(epicFolder, "tickets"));
  const escapedEpic = epicId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`^${escapedEpic}-T(\\d{3})-`);
  const max = files.reduce((current, file) => {
    const match = file.match(pattern);
    return match ? Math.max(current, Number(match[1])) : current;
  }, 0);
  return `${epicId}-T${String(max + 1).padStart(3, "0")}`;
}

export async function resolveEpicFolder(root, epicId) {
  const folders = await listDirectories(epicsDir(root));
  const folderName = folders.find((folder) => folder === epicId || folder.startsWith(`${epicId}-`));
  if (!folderName) {
    throw new Error(`Epic not found: ${epicId}`);
  }
  return join(epicsDir(root), folderName);
}

export async function createEpic(root, options) {
  await assertInitialized(root);
  const type = options.type || "feature";
  if (!["feature", "spike"].includes(type)) {
    throw new Error("Epic type must be `feature` or `spike`");
  }

  const slug = slugify(options.slug);
  const title = options.title || titleFromSlug(slug);
  const id = await nextEpicId(root);
  const folder = join(epicsDir(root), `${id}-${slug}`);
  const now = new Date().toISOString();
  await mkdir(join(folder, "tickets"), { recursive: true });
  await mkdir(join(folder, "briefs"), { recursive: true });
  await mkdir(join(folder, "decisions"), { recursive: true });
  await mkdir(join(folder, "archive"), { recursive: true });

  const metadata = {
    id,
    kind: "epic",
    type,
    slug,
    title,
    status: "draft",
    created_at: now,
    updated_at: now,
    constitution_version: 1,
    adapter_links: {
      github_issue: null,
      github_project: null,
    },
  };
  const body = [
    `# ${title}`,
    "",
    "## Product Brief / PRD",
    "",
    "Describe the user need, business context, constraints, and non-goals.",
    "",
    "## Design Direction",
    "",
    "Describe the recommended technical shape and meaningful tradeoffs.",
    "",
    "## Ticket Strategy",
    "",
    "Tickets must be self-contained, dependency-explicit, and ready for one implementation agent.",
    "",
    "## Wave Strategy",
    "",
    "Run `wdd waves plan` after tickets are written and validated.",
    "",
  ].join("\n");

  await writeFile(join(folder, "epic.md"), formatMarkdown(metadata, body), "utf8");
  await writeFile(join(folder, "prd.md"), "# PRD\n\nExpand the product brief here.\n", "utf8");
  await writeFile(join(folder, "design.md"), "# Design\n\nExpand the implementation design here.\n", "utf8");

  return { id, folder, path: join(folder, "epic.md"), metadata };
}

export async function readEpic(root, epicId) {
  const folder = await resolveEpicFolder(root, epicId);
  const path = join(folder, "epic.md");
  const parsed = parseMarkdown(await readFile(path, "utf8"), path);
  return { ...parsed, path, folder };
}

export async function writeEpic(root, epicId, metadata, body) {
  const folder = await resolveEpicFolder(root, epicId);
  const path = join(folder, "epic.md");
  await writeFile(path, formatMarkdown(metadata, body), "utf8");
  return { path, folder };
}

export async function listEpics(root) {
  const folders = await listDirectories(epicsDir(root));
  const epics = [];
  for (const folderName of folders) {
    const path = join(epicsDir(root), folderName, "epic.md");
    try {
      const parsed = parseMarkdown(await readFile(path, "utf8"), path);
      epics.push({ ...parsed, path, folder: join(epicsDir(root), folderName) });
    } catch (error) {
      if (error.code !== "ENOENT") {
        throw error;
      }
    }
  }
  return epics;
}

function ticketBody(title, verification) {
  const verificationLines = verification.length > 0 ? verification.map((cmd) => `- \`${cmd}\``) : ["- Add at least one concrete verification command."];
  return [
    `# ${title}`,
    "",
    "## Context",
    "",
    "Explain the relevant local files, contracts, and constraints for this ticket.",
    "",
    "## End Goal / Deliverable",
    "",
    `Deliver ${title} as a bounded, reviewable change.`,
    "",
    "## Scope",
    "",
    "- Implement only the behavior required by this ticket.",
    "",
    "## RED/GREEN TDD",
    "",
    "- First add or run a failing check that demonstrates the missing behavior.",
    "- Implement the smallest change that makes the check pass.",
    "",
    "## Acceptance Criteria",
    "",
    "- The deliverable is observable in code or tests.",
    "- Dependencies listed in frontmatter are respected.",
    "",
    "## Verification",
    "",
    ...verificationLines,
    "",
    "## Review Handoff",
    "",
    "Ask the reviewer to check spec compliance, dependency boundaries, test evidence, and maintainability.",
    "",
    "## Out of Scope",
    "",
    "- Do not modify unrelated domains or start dependent tickets.",
    "",
  ].join("\n");
}

export async function createTicket(root, epicId, options) {
  const epic = await readEpic(root, epicId);
  const slug = slugify(options.slug);
  const title = options.title || titleFromSlug(slug);
  const id = await nextTicketId(epic.folder, epicId);
  const path = join(epic.folder, "tickets", `${id}-${slug}.md`);
  const dependsOn = options.dependsOn || options.depends_on || [];
  const conflictDomains = options.conflictDomains || options.conflict_domains || [];
  const verification = options.verification || [];
  const branch = options.branch || `codex/${id.toLowerCase()}-${slug}`;
  const now = new Date().toISOString();
  const metadata = {
    id,
    kind: "ticket",
    epic: epicId,
    slug,
    title,
    status: "todo",
    wave: null,
    depends_on: dependsOn,
    conflict_domains: conflictDomains,
    branch,
    verification,
    created_at: now,
    updated_at: now,
    adapter_links: {
      github_issue: null,
      pull_request: null,
    },
  };

  await writeFile(path, formatMarkdown(metadata, ticketBody(title, verification)), "utf8");
  return { id, path, metadata };
}

export async function listTickets(root, epicId) {
  const epic = await readEpic(root, epicId);
  const files = await listFiles(join(epic.folder, "tickets"));
  const tickets = [];
  for (const file of files) {
    if (!file.endsWith(".md")) {
      continue;
    }
    const path = join(epic.folder, "tickets", file);
    const parsed = parseMarkdown(await readFile(path, "utf8"), path);
    tickets.push({ ...parsed, path, folder: epic.folder });
  }
  return tickets.sort((a, b) => String(a.metadata.id).localeCompare(String(b.metadata.id)));
}

export async function readTicket(root, ticketId) {
  const epics = await listEpics(root);
  for (const epic of epics) {
    const tickets = await listTickets(root, epic.metadata.id);
    const ticket = tickets.find((candidate) => candidate.metadata.id === ticketId);
    if (ticket) {
      return ticket;
    }
  }
  throw new Error(`Ticket not found: ${ticketId}`);
}

export async function writeTicket(root, ticketId, metadata, body) {
  const ticket = await readTicket(root, ticketId);
  await writeFile(ticket.path, formatMarkdown(metadata, body), "utf8");
  return ticket.path;
}

export async function readWavePlan(root, epicId) {
  const epic = await readEpic(root, epicId);
  const path = join(epic.folder, "wave-plan.yaml");
  const content = await readFile(path, "utf8");
  return { path, folder: epic.folder, data: YAML.parse(content) };
}

export async function writeWavePlan(root, epicId, data) {
  const epic = await readEpic(root, epicId);
  const path = join(epic.folder, "wave-plan.yaml");
  await writeFile(path, YAML.stringify(data), "utf8");
  return path;
}

export function relativeToRoot(root, path) {
  return relative(root, path);
}
