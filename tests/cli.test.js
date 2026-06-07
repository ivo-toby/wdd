import { mkdtemp, readFile, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

const execFileAsync = promisify(execFile);
const bin = resolve("bin/wdd.js");
let root;

beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), "wdd-cli-"));
});

afterEach(async () => {
  await rm(root, { recursive: true, force: true });
});

async function wdd(args) {
  return execFileAsync(process.execPath, [bin, ...args], { cwd: root });
}

describe("wdd CLI", () => {
  test("creates a local epic, ticket, wave plan, and controller state", async () => {
    await wdd(["init", "--agent", "codex"]);
    const { stdout: newOutput } = await wdd(["new", "feature", "auth-refresh", "--title", "Auth Refresh", "--json"]);
    const epic = JSON.parse(newOutput);

    expect(epic.id).toBe("WDD-0001");

    await wdd([
      "ticket",
      "create",
      "WDD-0001",
      "token-contract",
      "--title",
      "Token Contract",
      "--verify",
      "npm test -- auth",
    ]);
    const { stdout: validateOutput } = await wdd(["validate", "WDD-0001", "--json"]);
    expect(JSON.parse(validateOutput).ok).toBe(true);

    await wdd(["waves", "plan", "WDD-0001"]);
    const { stdout: startOutput } = await wdd(["start-wave", "WDD-0001", "--json"]);
    const state = JSON.parse(startOutput);

    expect(state.wave).toBe(1);
    expect(state.tickets).toEqual(["WDD-0001-T001"]);

    const folder = join(root, ".wdd", "epics", "WDD-0001-auth-refresh");
    await expect(readFile(join(folder, "wave-plan.yaml"), "utf8")).resolves.toContain("tickets:");
    await expect(readFile(join(folder, "controller-state.yaml"), "utf8")).resolves.toContain("current_gate: no_pr");
    await expect(readFile(join(folder, "briefs", "WDD-0001-T001-token-contract.md"), "utf8")).resolves.toContain("## Implementation Brief");
  });

  test("installs packaged skills into an explicit agent skill target", async () => {
    const target = join(root, "agent-skills");
    const { stdout } = await wdd(["install-skills", "--target", target, "--json"]);
    const result = JSON.parse(stdout);

    expect(result.installed).toEqual(["wave-driven-development", "subagent-pr-orchestration"]);
    await expect(readFile(join(target, "wave-driven-development", "SKILL.md"), "utf8")).resolves.toContain("local-first");
    await expect(readFile(join(target, "subagent-pr-orchestration", "SKILL.md"), "utf8")).resolves.toContain("controller");
  });
});
