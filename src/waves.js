import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import YAML from "yaml";
import {
  listTickets,
  readEpic,
  readWavePlan,
  relativeToRoot,
  writeTicket,
  writeWavePlan,
} from "./local-store.js";
import { formatMarkdown } from "./frontmatter.js";
import { validateEpic } from "./validation.js";

function intersects(left, right) {
  return left.some((value) => right.has(value));
}

function collectDomains(tickets) {
  return [...new Set(tickets.flatMap((ticket) => ticket.metadata.conflict_domains || []))].sort();
}

async function updateTicket(root, ticket, patch) {
  const metadata = {
    ...ticket.metadata,
    ...patch,
    updated_at: new Date().toISOString(),
  };
  await writeTicket(root, ticket.metadata.id, metadata, ticket.body);
}

export async function planWaves(root, epicId) {
  const validation = await validateEpic(root, epicId);
  if (!validation.ok) {
    const error = new Error(`Cannot plan waves; validation failed for ${epicId}`);
    error.validation = validation;
    throw error;
  }

  const epic = await readEpic(root, epicId);
  const tickets = await listTickets(root, epicId);
  const byId = new Map(tickets.map((ticket) => [ticket.metadata.id, ticket]));
  const remaining = new Set(tickets.map((ticket) => ticket.metadata.id));
  const completed = new Set();
  const deferredReasons = new Map();
  const waves = [];

  while (remaining.size > 0) {
    const candidates = [...remaining]
      .map((id) => byId.get(id))
      .filter((ticket) => (ticket.metadata.depends_on || []).every((dependency) => completed.has(dependency)))
      .sort((a, b) => a.metadata.id.localeCompare(b.metadata.id));

    if (candidates.length === 0) {
      throw new Error(`Cannot plan waves; dependency cycle detected in ${epicId}`);
    }

    const selected = [];
    const usedDomains = new Set();
    for (const ticket of candidates) {
      const domains = ticket.metadata.conflict_domains || [];
      if (selected.length > 0 && intersects(domains, usedDomains)) {
        const conflicting = selected.find((candidate) => intersects(domains, new Set(candidate.metadata.conflict_domains || [])));
        deferredReasons.set(
          ticket.metadata.id,
          `Delayed to avoid conflict with ${conflicting.metadata.id} on ${domains.filter((domain) => (conflicting.metadata.conflict_domains || []).includes(domain)).join(", ")}`,
        );
        continue;
      }
      selected.push(ticket);
      for (const domain of domains) {
        usedDomains.add(domain);
      }
    }

    const waveNumber = waves.length + 1;
    const selectedReasons = selected.map((ticket) => deferredReasons.get(ticket.metadata.id)).filter(Boolean);
    const wave = {
      wave: waveNumber,
      status: "pending",
      tickets: selected.map((ticket) => ticket.metadata.id),
      conflict_domains: collectDomains(selected),
      reason: selectedReasons.length > 0 ? selectedReasons.join("; ") : "Dependencies satisfied without overlapping conflict domains.",
    };
    waves.push(wave);

    for (const ticket of selected) {
      await updateTicket(root, ticket, { wave: waveNumber });
      remaining.delete(ticket.metadata.id);
      completed.add(ticket.metadata.id);
    }
  }

  const plan = {
    epic: epicId,
    status: "planned",
    generated_at: new Date().toISOString(),
    epic_path: relativeToRoot(root, epic.path),
    waves,
  };
  await writeWavePlan(root, epicId, plan);
  return plan;
}

function briefBody(ticket, waveNumber) {
  const verification = (ticket.metadata.verification || []).map((cmd) => `- \`${cmd}\``).join("\n");
  return [
    `# ${ticket.metadata.id}: ${ticket.metadata.title}`,
    "",
    "## Implementation Brief",
    "",
    `Wave: ${waveNumber}`,
    `Branch: \`${ticket.metadata.branch}\``,
    "",
    "## Deliverable",
    "",
    extractSection(ticket.body, "## End Goal / Deliverable"),
    "",
    "## Required Verification",
    "",
    verification || "- Add ticket verification before implementation.",
    "",
    "## Source Ticket",
    "",
    ticket.body.trim(),
    "",
  ].join("\n");
}

function extractSection(body, heading) {
  const start = body.indexOf(heading);
  if (start === -1) {
    return "See source ticket.";
  }
  const afterHeading = body.slice(start + heading.length);
  const next = afterHeading.search(/\n## /);
  return (next === -1 ? afterHeading : afterHeading.slice(0, next)).trim() || "See source ticket.";
}

export async function startWave(root, epicId) {
  let plan;
  try {
    plan = (await readWavePlan(root, epicId)).data;
  } catch (error) {
    if (error.code !== "ENOENT") {
      throw error;
    }
    plan = await planWaves(root, epicId);
  }

  const wave = plan.waves.find((candidate) => candidate.status !== "done");
  if (!wave) {
    return { epic: epicId, wave: null, tickets: [], status: "complete" };
  }

  wave.status = "in_progress";
  wave.started_at = wave.started_at || new Date().toISOString();
  plan.status = "in_progress";
  await writeWavePlan(root, epicId, plan);

  const epic = await readEpic(root, epicId);
  const tickets = await listTickets(root, epicId);
  const selected = tickets.filter((ticket) => wave.tickets.includes(ticket.metadata.id));
  const briefsDir = join(epic.folder, "briefs");
  await mkdir(briefsDir, { recursive: true });

  const controllerTickets = [];
  for (const ticket of selected) {
    await updateTicket(root, ticket, { status: "in_progress", wave: wave.wave });
    const briefPath = join(briefsDir, `${ticket.metadata.id}-${ticket.metadata.slug}.md`);
    const briefMetadata = {
      id: `${ticket.metadata.id}-BRIEF`,
      kind: "implementation_brief",
      epic: epicId,
      ticket: ticket.metadata.id,
      wave: wave.wave,
      current_gate: "no_pr",
      branch: ticket.metadata.branch,
    };
    await writeFile(briefPath, formatMarkdown(briefMetadata, briefBody(ticket, wave.wave)), "utf8");
    controllerTickets.push({
      id: ticket.metadata.id,
      title: ticket.metadata.title,
      branch: ticket.metadata.branch,
      brief_path: relativeToRoot(root, briefPath),
      current_gate: "no_pr",
      verification: ticket.metadata.verification || [],
    });
  }

  const state = {
    epic: epicId,
    current_wave: {
      wave: wave.wave,
      status: "in_progress",
      started_at: wave.started_at,
    },
    controller_rule: "The wave controller manages state and subagents; it does not implement code.",
    tickets: controllerTickets,
  };
  const controllerStatePath = join(epic.folder, "controller-state.yaml");
  await writeFile(controllerStatePath, YAML.stringify(state), "utf8");

  return {
    epic: epicId,
    wave: wave.wave,
    tickets: controllerTickets.map((ticket) => ticket.id),
    controllerStatePath,
    briefs: controllerTickets.map((ticket) => ticket.brief_path),
    status: "in_progress",
  };
}

export async function reconcileWave(root, epicId, options = {}) {
  const { data: plan } = await readWavePlan(root, epicId);
  const waveNumber = Number(options.wave);
  const wave = plan.waves.find((candidate) => candidate.wave === waveNumber);
  if (!wave) {
    throw new Error(`Wave not found: ${waveNumber}`);
  }
  const status = options.status || "done";
  wave.status = status;
  wave.reconciled_at = new Date().toISOString();
  if (status === "done" && plan.waves.every((candidate) => candidate.status === "done")) {
    plan.status = "done";
  }
  await writeWavePlan(root, epicId, plan);

  if (status === "done") {
    const tickets = await listTickets(root, epicId);
    for (const ticket of tickets.filter((candidate) => wave.tickets.includes(candidate.metadata.id))) {
      await updateTicket(root, ticket, { status: "done" });
    }
  }

  return { epic: epicId, wave: waveNumber, status };
}

