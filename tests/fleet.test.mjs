import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import net from 'node:net';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import { handleRequest, MAX_LINE_BYTES } from '../scripts/fleet-mcp.mjs';
import { HOST, SERVICES, PROBE_CONCURRENCY, PROBE_TIMEOUT_MS, serviceStatus, machineInfo, probeTcp } from '../scripts/fleet-status.mjs';

const request = (method, params, id = 1) => ({ jsonrpc: '2.0', id, method, ...(params === undefined ? {} : { params }) });

test('service status only probes fixed loopback ports with bounded concurrency', async () => {
  let active = 0;
  let peak = 0;
  const calls = [];
  const status = await serviceStatus(async (target) => {
    calls.push(target);
    peak = Math.max(peak, ++active);
    await new Promise((resolve) => setTimeout(resolve, 5));
    active--;
    if (target.port === 11434) throw new Error('private path must not escape');
    return target.port === 3100;
  });
  assert.equal(peak, PROBE_CONCURRENCY);
  assert.deepEqual(calls, SERVICES.map(({ port }) => ({ host: '127.0.0.1', port, timeoutMs: 500 })));
  assert.equal(HOST, '127.0.0.1');
  assert.equal(PROBE_TIMEOUT_MS, 500);
  assert.match(status.observation, /does not establish application health/);
  assert.deepEqual(status.services.map((service) => service.tcpReachable), [true, false, false, false, false, false]);
  assert.doesNotMatch(JSON.stringify(status), /private path/);
});

test('machine info contains only intended aggregate system fields', () => {
  const info = machineInfo();
  assert.deepEqual(Object.keys(info).sort(), ['architecture', 'cpuCount', 'freeMemoryBytes', 'platform', 'totalMemoryBytes'].sort());
  assert.equal(typeof info.architecture, 'string');
  assert.equal(typeof info.platform, 'string');
  assert.ok(info.cpuCount > 0);
  assert.ok(info.totalMemoryBytes >= info.freeMemoryBytes);
  assert.ok(info.freeMemoryBytes >= 0);
});

test('failed fleet probes log bounded fixed diagnostics only to stderr', async () => {
  let stderr = '';
  const result = await handleRequest(request('tools/call', { name: 'danslab_service_status' }), {
    probe: async () => { throw new Error('credential=do-not-disclose /private/path'); },
    stderr: { write: value => { stderr += value; } },
  });
  assert.equal(stderr.trim().split('\n').length, SERVICES.length);
  assert.match(stderr, /fleet probe failed/);
  assert.doesNotMatch(stderr + JSON.stringify(result), /credential|do-not-disclose|private\/path/);
  assert.equal(JSON.parse(result.result.content[0].text).services.every(s => !s.tcpReachable), true);
});

test('real TCP probe reports listener reachability and closed port accurately', async (t) => {
  const server = net.createServer((socket) => socket.end());
  t.after(() => server.close());
  server.listen(0, HOST);
  await once(server, 'listening');
  const port = server.address().port;
  assert.equal(await probeTcp({ host: HOST, port, timeoutMs: 500 }), true);
  await new Promise((resolve) => server.close(resolve));
  assert.equal(await probeTcp({ host: HOST, port, timeoutMs: 500 }), false);
});

test('MCP tool schemas and calls reject network overrides, malformed arguments, unknown tools', async () => {
  const listing = await handleRequest(request('tools/list'));
  assert.equal(listing.result.tools.length, 2);
  for (const tool of listing.result.tools) assert.equal(tool.inputSchema.additionalProperties, false);
  for (const args of [{ host: 'example.org' }, { port: 80 }, { command: 'id' }, [], null, 'bad']) {
    const result = await handleRequest(request('tools/call', { name: 'danslab_service_status', arguments: args }));
    assert.equal(result.error.code, -32602);
  }
  assert.equal((await handleRequest(request('tools/call', { name: 'unknown' }))).error.code, -32602);
  const result = await handleRequest(request('tools/call', { name: 'danslab_machine_info', arguments: {} }, 'machine'));
  assert.equal(result.id, 'machine');
  assert.ok(JSON.parse(result.result.content[0].text).cpuCount > 0);
});

test('JSON-RPC validates request shape and preserves request IDs', async () => {
  assert.equal(await handleRequest({ jsonrpc: '2.0', method: 'notifications/initialized' }), undefined);
  assert.equal((await handleRequest(request('unknown', undefined, 'x'))).error.code, -32601);
  for (const invalid of [null, [], {}, { jsonrpc: '1.0', id: 1, method: 'ping' }, request('ping', {}, {})]) {
    assert.equal((await handleRequest(invalid)).error.code, -32600);
  }
  assert.equal((await handleRequest(request('initialize', {}))).error.code, -32602);
  assert.deepEqual(await handleRequest(request('ping', undefined, null)), { jsonrpc: '2.0', id: null, result: {} });
});

test('stdio subprocess completes handshake and survives parse and size errors', { timeout: 10000 }, async (t) => {
  const child = spawn(process.execPath, [fileURLToPath(new URL('../scripts/fleet-mcp.mjs', import.meta.url))], { stdio: ['pipe', 'pipe', 'pipe'] });
  t.after(() => child.kill());
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (data) => { stdout += data; });
  child.stderr.on('data', (data) => { stderr += data; });
  const send = (value) => child.stdin.write(`${JSON.stringify(value)}\n`);
  send(request('initialize', { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'test', version: '1' } }, 10));
  send({ jsonrpc: '2.0', method: 'notifications/initialized' });
  send(request('tools/list', undefined, 11));
  send(request('tools/call', { name: 'danslab_machine_info' }, 12));
  send(request('tools/call', { name: 'unknown' }, 13));
  send(request('tools/call', { name: 'danslab_service_status', arguments: { host: '8.8.8.8' } }, 14));
  child.stdin.write('broken JSON\n');
  // Split across chunks to exercise the bounded incremental framing path.
  child.stdin.write('x'.repeat(MAX_LINE_BYTES));
  child.stdin.write('overflow\n');
  send(request('ping', undefined, 15));
  child.stdin.end();
  const [code] = await once(child, 'close');
  assert.equal(code, 0);
  assert.equal(stderr, '');
  const messages = stdout.trim().split('\n').map((line) => JSON.parse(line));
  assert.equal(messages.length, 8);
  assert.equal(messages[0].result.protocolVersion, '2024-11-05');
  assert.equal(messages[0].id, 10);
  assert.equal(messages[1].result.tools.length, 2);
  assert.ok(JSON.parse(messages[2].result.content[0].text).totalMemoryBytes > 0);
  assert.equal(messages[3].error.code, -32602);
  assert.equal(messages[4].error.code, -32602);
  assert.equal(messages[5].error.code, -32700);
  assert.equal(messages[6].error.code, -32600);
  assert.equal(messages[7].id, 15);
  assert.deepEqual(messages[7].result, {});
});
