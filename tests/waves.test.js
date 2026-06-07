import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import {
  createEpic,
  createTicket,
  initProject,
  planWaves,
  validateEpic,
} from "../src/wdd.js";

let root;

beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), "wdd-waves-"));
});

afterEach(async () => {
  await rm(root, { recursive: true, force: true });
});

async function setupEpic() {
  await initProject(root, { agent: "codex" });
  return createEpic(root, {
    type: "feature",
    slug: "parallel-auth",
    title: "Parallel Auth",
  });
}

describe("ticket validation and wave planning", () => {
  test("validates generated ticket sections and metadata", async () => {
    const epic = await setupEpic();
    await createTicket(root, epic.id, {
      slug: "token-contract",
      title: "Token Contract",
      verification: ["npm test -- auth"],
    });

    const validation = await validateEpic(root, epic.id);

    expect(validation.ok).toBe(true);
    expect(validation.errors).toEqual([]);
  });

  test("plans dependency waves while separating overlapping conflict domains", async () => {
    const epic = await setupEpic();
    await createTicket(root, epic.id, {
      slug: "token-contract",
      title: "Token Contract",
      conflictDomains: ["src/auth/**"],
      verification: ["npm test -- auth"],
    });
    await createTicket(root, epic.id, {
      slug: "api-refresh-route",
      title: "API Refresh Route",
      dependsOn: ["WDD-0001-T001"],
      conflictDomains: ["src/api/**"],
      verification: ["npm test -- api"],
    });
    await createTicket(root, epic.id, {
      slug: "ui-session-banner",
      title: "UI Session Banner",
      dependsOn: ["WDD-0001-T001"],
      conflictDomains: ["src/ui/**"],
      verification: ["npm test -- ui"],
    });
    await createTicket(root, epic.id, {
      slug: "api-refresh-tests",
      title: "API Refresh Tests",
      dependsOn: ["WDD-0001-T001"],
      conflictDomains: ["src/api/**"],
      verification: ["npm test -- api"],
    });

    const plan = await planWaves(root, epic.id);

    expect(plan.waves.map((wave) => wave.tickets)).toEqual([
      ["WDD-0001-T001"],
      ["WDD-0001-T002", "WDD-0001-T003"],
      ["WDD-0001-T004"],
    ]);
    expect(plan.waves[1].conflict_domains).toEqual(["src/api/**", "src/ui/**"]);
    expect(plan.waves[2].reason).toContain("conflict");
  });
});

