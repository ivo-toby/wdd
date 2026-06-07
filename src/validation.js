import { listTickets, readEpic } from "./local-store.js";

const REQUIRED_TICKET_FIELDS = [
  "id",
  "kind",
  "epic",
  "slug",
  "title",
  "status",
  "depends_on",
  "conflict_domains",
  "verification",
];

const REQUIRED_SECTIONS = [
  "## Context",
  "## End Goal / Deliverable",
  "## Scope",
  "## RED/GREEN TDD",
  "## Acceptance Criteria",
  "## Verification",
  "## Review Handoff",
  "## Out of Scope",
];

function hasValue(value) {
  if (Array.isArray(value)) {
    return true;
  }
  return value !== undefined && value !== null && value !== "";
}

export async function validateEpic(root, epicId) {
  const errors = [];
  const epic = await readEpic(root, epicId);
  if (epic.metadata.kind !== "epic") {
    errors.push(`${epic.metadata.id || epicId}: kind must be epic`);
  }
  if (!epic.metadata.id || !epic.metadata.slug || !epic.metadata.status) {
    errors.push(`${epicId}: epic frontmatter must include id, slug, and status`);
  }

  const tickets = await listTickets(root, epicId);
  const ticketIds = new Set(tickets.map((ticket) => ticket.metadata.id));
  for (const ticket of tickets) {
    const id = ticket.metadata.id || ticket.path;
    for (const field of REQUIRED_TICKET_FIELDS) {
      if (!hasValue(ticket.metadata[field])) {
        errors.push(`${id}: missing frontmatter field ${field}`);
      }
    }
    if (ticket.metadata.kind !== "ticket") {
      errors.push(`${id}: kind must be ticket`);
    }
    if (ticket.metadata.epic !== epicId) {
      errors.push(`${id}: epic must be ${epicId}`);
    }
    for (const dependency of ticket.metadata.depends_on || []) {
      if (!ticketIds.has(dependency)) {
        errors.push(`${id}: missing dependency ${dependency}`);
      }
    }
    if (!Array.isArray(ticket.metadata.verification) || ticket.metadata.verification.length === 0) {
      errors.push(`${id}: verification must include at least one command`);
    }
    for (const section of REQUIRED_SECTIONS) {
      if (!ticket.body.includes(section)) {
        errors.push(`${id}: missing section ${section}`);
      }
    }
  }

  return {
    ok: errors.length === 0,
    errors,
    epic: epic.metadata.id,
    tickets: tickets.map((ticket) => ticket.metadata.id),
  };
}

