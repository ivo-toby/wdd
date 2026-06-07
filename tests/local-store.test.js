import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import {
  createEpic,
  createTicket,
  initProject,
  readEpic,
  readTicket,
} from "../src/wdd.js";

let root;

beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), "wdd-store-"));
});

afterEach(async () => {
  await rm(root, { recursive: true, force: true });
});

describe("local WDD store", () => {
  test("initializes a project with config and constitution files", async () => {
    const result = await initProject(root, { agent: "codex" });

    expect(result.configPath).toBe(join(root, ".wdd", "config.yaml"));
    await expect(readFile(result.configPath, "utf8")).resolves.toContain("agent: codex");
    await expect(readFile(join(root, ".wdd", "constitution.md"), "utf8")).resolves.toContain("# Project Constitution");
  });

  test("does not overwrite an existing constitution on repeated init", async () => {
    await initProject(root, { agent: "codex" });
    const constitutionPath = join(root, ".wdd", "constitution.md");
    await writeFile(constitutionPath, "# Custom Constitution\n\nKeep this.", "utf8");

    await initProject(root, { agent: "codex" });

    await expect(readFile(constitutionPath, "utf8")).resolves.toBe("# Custom Constitution\n\nKeep this.");
  });

  test("creates epics and tickets with mandatory YAML frontmatter metadata", async () => {
    await initProject(root, { agent: "codex" });
    const epic = await createEpic(root, {
      type: "feature",
      slug: "auth-refresh",
      title: "Auth Refresh",
    });

    expect(epic.id).toBe("WDD-0001");
    expect(epic.folder).toBe(join(root, ".wdd", "epics", "WDD-0001-auth-refresh"));

    const ticket = await createTicket(root, epic.id, {
      slug: "token-contract",
      title: "Token Contract",
      dependsOn: [],
      conflictDomains: ["src/auth/**"],
      verification: ["npm test -- auth"],
    });

    expect(ticket.id).toBe("WDD-0001-T001");
    expect(ticket.path).toBe(join(epic.folder, "tickets", "WDD-0001-T001-token-contract.md"));

    const persistedEpic = await readEpic(root, epic.id);
    const persistedTicket = await readTicket(root, ticket.id);

    expect(persistedEpic.metadata).toMatchObject({
      id: "WDD-0001",
      kind: "epic",
      type: "feature",
      status: "draft",
      slug: "auth-refresh",
    });
    expect(persistedTicket.metadata).toMatchObject({
      id: "WDD-0001-T001",
      kind: "ticket",
      epic: "WDD-0001",
      status: "todo",
      depends_on: [],
      conflict_domains: ["src/auth/**"],
      verification: ["npm test -- auth"],
    });
    expect(persistedTicket.body).toContain("## End Goal / Deliverable");
  });
});
