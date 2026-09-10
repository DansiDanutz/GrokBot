import { setTimeout as delay } from 'node:timers/promises';

// The PID comes only from the child we spawned with detached:true. Never accept
// a configured PID or discover processes by name/port when shutting down.
export function superviseChild(child, { graceMs = 8000, kill = process.kill.bind(process), signals = process } = {}) {
  let failed = false;
  let stopping = false;
  let finished = false;
  let timer;
  let resolveDone;
  const done = new Promise((resolve) => { resolveDone = resolve; });
  const signalGroup = (signal) => {
    if (!Number.isInteger(child.pid) || child.pid <= 0) return;
    try { kill(-child.pid, signal); } catch (error) {
      if (error.code !== 'ESRCH') failed = true;
    }
  };
  const stop = () => {
    if (finished || stopping) return;
    stopping = true;
    signalGroup('SIGTERM');
    timer = setTimeout(() => signalGroup('SIGKILL'), graceMs);
  };
  const onSignal = () => stop();
  const finish = (code, signal) => {
    if (finished) return;
    finished = true;
    clearTimeout(timer);
    signals.removeListener('SIGINT', onSignal);
    signals.removeListener('SIGTERM', onSignal);
    const status = failed ? 1 : (signal ? (stopping ? 0 : 1) : (code ?? 1));
    resolveDone(status);
  };
  signals.on('SIGINT', onSignal);
  signals.on('SIGTERM', onSignal);
  child.once('error', () => { failed = true; finish(1); });
  child.once('exit', finish);
  return {
    done,
    stop,
    fail() { failed = true; },
    get stopping() { return stopping; },
    get finished() { return finished; },
  };
}

export async function waitForReady(lifecycle, check, { timeoutMs = 45000, pollMs = 500 } = {}) {
  const controller = new AbortController();
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error(`GrokBot did not become ready within ${timeoutMs / 1000} seconds. See startup output.`));
    }, timeoutMs);
  });
  const exited = lifecycle.done.then(() => { throw new Error('GrokBot exited before becoming ready. See startup output.'); });
  try {
    while (!lifecycle.stopping) {
      const ready = await Promise.race([
        Promise.resolve().then(() => check(controller.signal)).catch(() => false),
        timeout,
        exited,
      ]);
      if (ready && !lifecycle.stopping) return true;
      await Promise.race([delay(pollMs, undefined, { signal: controller.signal }), timeout, exited]);
    }
    return false;
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
}
