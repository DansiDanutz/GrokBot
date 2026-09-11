import { pathToFileURL } from 'node:url';
import { machineInfo, serviceStatus } from './fleet-status.mjs';

export const MAX_LINE_BYTES = 64 * 1024;
export const PROTOCOL_VERSION = '2024-11-05';
const inputSchema = { type: 'object', properties: {}, additionalProperties: false };
const tools = [
  {
    name: 'danslab_service_status',
    description: 'Read fixed loopback TCP reachability for the six configured Dan’s Lab endpoints. Does not test application health or service identity.',
    inputSchema,
  },
  {
    name: 'danslab_machine_info',
    description: 'Read architecture, operating-system platform, CPU count and total/free RAM. Does not disclose usernames, paths or secrets.',
    inputSchema,
  },
];
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const error = (id, code, message) => ({ jsonrpc: '2.0', id, error: { code, message } });

export async function handleRequest(request, { probe, stderr = process.stderr } = {}) {
  const hasId = object(request) && Object.hasOwn(request, 'id');
  const validId = !hasId || request.id === null || typeof request.id === 'string' || (typeof request.id === 'number' && Number.isFinite(request.id));
  if (!object(request) || request.jsonrpc !== '2.0' || typeof request.method !== 'string' || !validId) {
    return error(hasId && validId ? request.id : null, -32600, 'Invalid Request');
  }
  // All well-formed notifications are deliberately response-free.
  if (!hasId) return undefined;
  const { id, method, params } = request;
  const success = (result) => ({ jsonrpc: '2.0', id, result });
  if (params !== undefined && !object(params)) return error(id, -32602, 'Invalid params');
  if (method === 'initialize') {
    if (!object(params) || typeof params.protocolVersion !== 'string' || !object(params.capabilities) || !object(params.clientInfo) || typeof params.clientInfo.name !== 'string' || typeof params.clientInfo.version !== 'string') {
      return error(id, -32602, 'Invalid initialize params');
    }
    return success({ protocolVersion: PROTOCOL_VERSION, capabilities: { tools: {} }, serverInfo: { name: 'danslab-readonly-fleet', version: '1.0.0' } });
  }
  if (method === 'ping') return success({});
  if (method === 'tools/list') return success({ tools });
  if (method === 'tools/call') {
    if (!object(params) || typeof params.name !== 'string' || Object.keys(params).some((key) => !['name', 'arguments', '_meta'].includes(key))) {
      return error(id, -32602, 'Invalid tool call params');
    }
    const args = params.arguments ?? {};
    if (!object(args) || Object.keys(args).length !== 0 || params.arguments === null) {
      return error(id, -32602, 'Tool arguments must be an empty object');
    }
    if (!tools.some((tool) => tool.name === params.name)) return error(id, -32602, 'Unknown tool');
    try {
      const result = params.name === 'danslab_service_status' ? await serviceStatus(probe, port => stderr.write(`fleet probe failed: loopback port ${port}\n`)) : machineInfo();
      return success({ content: [{ type: 'text', text: JSON.stringify(result) }] });
    } catch {
      stderr.write('fleet information probe failed\n');
      return success({ isError: true, content: [{ type: 'text', text: 'Unable to read fleet information.' }] });
    }
  }
  return error(id, -32601, 'Method not found');
}

export function startServer(input = process.stdin, output = process.stdout) {
  let chunks = [];
  let size = 0;
  let dropping = false;
  let pending = Promise.resolve();
  const write = (response) => {
    if (response !== undefined) output.write(`${JSON.stringify(response)}\n`);
  };
  const dispatch = (line) => {
    pending = pending.then(async () => {
      let request;
      try {
        request = JSON.parse(line);
      } catch {
        write(error(null, -32700, 'Parse error'));
        return;
      }
      write(await handleRequest(request));
    }).catch(() => write(error(null, -32603, 'Internal error')));
  };
  input.on('data', (data) => {
    const buffer = Buffer.isBuffer(data) ? data : Buffer.from(data);
    let start = 0;
    while (start < buffer.length) {
      const newline = buffer.indexOf(10, start);
      const end = newline === -1 ? buffer.length : newline;
      const segment = buffer.subarray(start, end);
      if (!dropping) {
        if (size + segment.length > MAX_LINE_BYTES) {
          chunks = [];
          size = 0;
          dropping = true;
          pending = pending.then(() => write(error(null, -32600, 'Request exceeds 64 KiB limit')));
        } else {
          chunks.push(segment);
          size += segment.length;
        }
      }
      if (newline !== -1) {
        if (!dropping) dispatch(Buffer.concat(chunks, size).toString('utf8'));
        chunks = [];
        size = 0;
        dropping = false;
      }
      start = end + 1;
    }
  });
  input.on('end', () => {
    if (size && !dropping) dispatch(Buffer.concat(chunks, size).toString('utf8'));
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) startServer();
