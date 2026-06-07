import { cp, readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import {
  createEpic,
  createTicket,
  initProject,
  listEpics,
  listTickets,
} from "./local-store.js";
import { planWaves, reconcileWave, startWave } from "./waves.js";
import { validateEpic } from "./validation.js";

function parseFlags(args) {
  const parsed = { _: [] };
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (!arg.startsWith("--")) {
      parsed._.push(arg);
      continue;
    }
    const name = arg.slice(2);
    if (["json", "done"].includes(name)) {
      parsed[name] = true;
      continue;
    }
    const value = args[index + 1];
    if (value === undefined) {
      throw new Error(`Missing value for --${name}`);
    }
    index += 1;
    if (["verify", "conflict", "conflicts", "depends"].includes(name)) {
      const key = name === "conflicts" ? "conflict" : name;
      parsed[key] = parsed[key] || [];
      parsed[key].push(...String(value).split(",").map((item) => item.trim()).filter(Boolean));
      continue;
    }
    parsed[name] = value;
  }
  return parsed;
}

function print(stdout, value, json) {
  if (json) {
    stdout.write(`${JSON.stringify(value, null, 2)}\n`);
    return;
  }
  stdout.write(`${value}\n`);
}

function help() {
  return [
    "wdd - local-first Wave-Driven Development",
    "",
    "Commands:",
    "  wdd init [--agent codex]",
    "  wdd constitution init",
    "  wdd new feature|spike <slug> [--title <title>]",
    "  wdd ticket create <epic> <slug> --title <title> --verify <cmd>",
    "  wdd validate <epic> [--json]",
    "  wdd waves plan <epic> [--json]",
    "  wdd start-wave <epic> [--json]",
    "  wdd reconcile <epic> --wave <n> [--done]",
    "  wdd status [--json]",
    "  wdd doctor [--json]",
    "  wdd schema [--json]",
    "  wdd install-skills [--target ~/.agents/skills] [--json]",
    "",
  ].join("\n");
}

function schema() {
  return {
    epic_frontmatter: ["id", "kind", "type", "slug", "title", "status", "created_at", "updated_at", "constitution_version", "adapter_links"],
    ticket_frontmatter: ["id", "kind", "epic", "slug", "title", "status", "wave", "depends_on", "conflict_domains", "branch", "verification", "adapter_links"],
    wave_plan: ["epic", "status", "generated_at", "waves[].wave", "waves[].status", "waves[].tickets", "waves[].conflict_domains"],
    controller_state: ["epic", "current_wave", "controller_rule", "tickets[].current_gate", "tickets[].brief_path"],
  };
}

export async function runCli(argv, io = {}) {
  const cwd = io.cwd || process.cwd();
  const stdout = io.stdout || process.stdout;
  const stderr = io.stderr || process.stderr;
  const [command, subcommand, ...rest] = argv;

  try {
    if (!command || ["help", "--help", "-h"].includes(command)) {
      stdout.write(help());
      return 0;
    }

    if (command === "init") {
      const flags = parseFlags([subcommand, ...rest].filter(Boolean));
      const result = await initProject(cwd, { agent: flags.agent || "codex" });
      print(stdout, flags.json ? result : `Initialized WDD at ${result.wddDir}`, flags.json);
      return 0;
    }

    if (command === "constitution" && subcommand === "init") {
      const flags = parseFlags(rest);
      const result = await initProject(cwd, { agent: flags.agent || "codex" });
      print(stdout, flags.json ? result : `Initialized constitution at ${result.constitutionPath}`, flags.json);
      return 0;
    }

    if (command === "new") {
      const flags = parseFlags(rest);
      const [slug] = flags._;
      const result = await createEpic(cwd, { type: subcommand, slug, title: flags.title });
      print(stdout, flags.json ? { id: result.id, path: result.path, folder: result.folder } : `Created ${result.id} at ${result.path}`, flags.json);
      return 0;
    }

    if (command === "ticket" && subcommand === "create") {
      const flags = parseFlags(rest);
      const [epicId, slug] = flags._;
      const result = await createTicket(cwd, epicId, {
        slug,
        title: flags.title,
        dependsOn: flags.depends || [],
        conflictDomains: flags.conflict || [],
        verification: flags.verify || [],
      });
      print(stdout, flags.json ? { id: result.id, path: result.path } : `Created ${result.id} at ${result.path}`, flags.json);
      return 0;
    }

    if (command === "validate") {
      const flags = parseFlags([subcommand, ...rest].filter(Boolean));
      const [epicId] = flags._;
      const result = await validateEpic(cwd, epicId);
      print(stdout, flags.json ? result : result.ok ? `Validation passed for ${epicId}` : `Validation failed:\n${result.errors.join("\n")}`, flags.json);
      return result.ok ? 0 : 1;
    }

    if (command === "waves" && subcommand === "plan") {
      const flags = parseFlags(rest);
      const [epicId] = flags._;
      const result = await planWaves(cwd, epicId);
      print(stdout, flags.json ? result : `Planned ${result.waves.length} wave(s) for ${epicId}`, flags.json);
      return 0;
    }

    if (command === "start-wave") {
      const flags = parseFlags([subcommand, ...rest].filter(Boolean));
      const [epicId] = flags._;
      const result = await startWave(cwd, epicId);
      print(stdout, flags.json ? result : result.wave ? `Started wave ${result.wave} for ${epicId}` : `No remaining waves for ${epicId}`, flags.json);
      return 0;
    }

    if (command === "reconcile") {
      const flags = parseFlags([subcommand, ...rest].filter(Boolean));
      const [epicId] = flags._;
      const result = await reconcileWave(cwd, epicId, { wave: flags.wave, status: flags.done ? "done" : flags.status });
      print(stdout, flags.json ? result : `Reconciled wave ${result.wave} as ${result.status}`, flags.json);
      return 0;
    }

    if (command === "status") {
      const flags = parseFlags([subcommand, ...rest].filter(Boolean));
      const epics = await listEpics(cwd);
      const result = [];
      for (const epic of epics) {
        const tickets = await listTickets(cwd, epic.metadata.id);
        result.push({
          id: epic.metadata.id,
          title: epic.metadata.title,
          status: epic.metadata.status,
          tickets: tickets.length,
        });
      }
      print(stdout, flags.json ? result : result.map((epic) => `${epic.id} ${epic.status} ${epic.title} (${epic.tickets} tickets)`).join("\n") || "No WDD epics found.", flags.json);
      return 0;
    }

    if (command === "doctor") {
      const flags = parseFlags([subcommand, ...rest].filter(Boolean));
      let packageJson = null;
      try {
        packageJson = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
      } catch {
        packageJson = { version: "unknown" };
      }
      const result = {
        ok: true,
        node: process.version,
        wdd: packageJson.version,
      };
      print(stdout, flags.json ? result : `WDD ${result.wdd} on ${result.node}`, flags.json);
      return 0;
    }

    if (command === "schema") {
      const flags = parseFlags([subcommand, ...rest].filter(Boolean));
      const result = schema();
      print(stdout, flags.json ? result : JSON.stringify(result, null, 2), flags.json);
      return 0;
    }

    if (command === "install-skills") {
      const flags = parseFlags([subcommand, ...rest].filter(Boolean));
      const target = flags.target || join(homedir(), ".agents", "skills");
      const skills = ["wave-driven-development", "subagent-pr-orchestration"];
      const sourceRoot = new URL("../skills/", import.meta.url);
      for (const skill of skills) {
        await cp(new URL(`${skill}/`, sourceRoot), join(target, skill), {
          recursive: true,
          force: true,
        });
      }
      const result = { target, installed: skills };
      print(stdout, flags.json ? result : `Installed WDD skills to ${target}`, flags.json);
      return 0;
    }

    throw new Error(`Unknown command: ${argv.join(" ")}`);
  } catch (error) {
    stderr.write(`${error.message}\n`);
    if (error.validation?.errors?.length) {
      stderr.write(`${error.validation.errors.join("\n")}\n`);
    }
    return 1;
  }
}
