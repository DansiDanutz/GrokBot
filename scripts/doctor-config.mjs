import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { STABLE_NODE } from './runtime.mjs';

// Explicit operator action from the repository root; doctor never executes it.
const FIX = `node --input-type=module -e 'import fs from "node:fs"; const p=".runtime/data/config.json"; const c=JSON.parse(fs.readFileSync(p,"utf8")); c.mcpServers.danslab_status.command="${STABLE_NODE}"; fs.writeFileSync(p,JSON.stringify(c,null,2)+"\\n");'`;

export function inspectMcpCommand(p, commandExists = existsSync) {
  let command;
  try {
    command = JSON.parse(readFileSync(join(p.data, 'config.json'), 'utf8'))
      .mcpServers?.danslab_status?.command;
  } catch {
    return { commandExists: false, stableCommand: false, warning: 'MCP configuration missing or unreadable; inspect configuration manually.', fix: null };
  }
  if (typeof command !== 'string' || !command.trim()) {
    return { commandExists: false, stableCommand: false, warning: 'MCP server command missing or invalid; inspect the complete server configuration manually.', fix: null };
  }
  const exists = typeof command === 'string' && commandExists(command);
  const stable = command === STABLE_NODE;
  const warning = !stable ? 'MCP command is not the stable Homebrew Node path; repair explicitly from the repository root.'
    : (!exists ? 'Stable MCP Node command is missing; restore the Homebrew Node installation.' : null);
  return { commandExists: exists, stableCommand: stable, warning, fix: !stable ? FIX : null };
}
