import net from 'node:net';
import os from 'node:os';
import { pathToFileURL } from 'node:url';

export const HOST = '127.0.0.1';
export const PROBE_TIMEOUT_MS = 500;
export const PROBE_CONCURRENCY = 3;
export const SERVICES = Object.freeze([
  Object.freeze({ name: 'Paperclip API', port: 3100 }),
  Object.freeze({ name: 'Paperclip UI', port: 3210 }),
  Object.freeze({ name: 'OpenClaw', port: 18789 }),
  Object.freeze({ name: 'Routing oracle', port: 18900 }),
  Object.freeze({ name: 'Codex proxy', port: 8996 }),
  Object.freeze({ name: 'Ollama', port: 11434 }),
]);

// Internal dependency injection is for tests only; MCP callers cannot set targets.
export function probeTcp({ host, port, timeoutMs }) {
  return new Promise((resolve) => {
    let settled = false;
    const socket = net.createConnection({ host, port });
    const finish = (reachable) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.destroy();
      resolve(reachable);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    socket.once('connect', () => finish(true));
    socket.once('error', () => finish(false));
  });
}

export async function serviceStatus(probe = probeTcp) {
  const services = new Array(SERVICES.length);
  let next = 0;
  await Promise.all(Array.from({ length: PROBE_CONCURRENCY }, async () => {
    while (next < SERVICES.length) {
      const index = next++;
      const service = SERVICES[index];
      let reachable = false;
      try {
        reachable = (await probe({ host: HOST, port: service.port, timeoutMs: PROBE_TIMEOUT_MS })) === true;
      } catch {
        // Probe failures expose neither operating-system errors nor local paths.
      }
      services[index] = { ...service, host: HOST, tcpReachable: reachable };
    }
  }));
  return {
    observation: 'TCP reachability only; this does not establish application health or service identity.',
    timeoutMs: PROBE_TIMEOUT_MS,
    services,
  };
}

export function machineInfo() {
  return {
    platform: os.platform(),
    architecture: os.arch(),
    cpuCount: os.cpus().length,
    totalMemoryBytes: os.totalmem(),
    freeMemoryBytes: os.freemem(),
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.stdout.write(`${JSON.stringify(await serviceStatus(), null, 2)}\n`);
}
