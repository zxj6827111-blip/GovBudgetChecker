const { test } = require('node:test');
const assert = require('node:assert/strict');
const net = require('node:net');
const { ensureDevServices, localServiceTargets, serviceAvailable } = require('./dev-services.cjs');

const local = { DATABASE_URL: 'postgresql://localhost:5432/test', OCR_ENABLED: 'true',
  OCR_SERVICE_URL: 'http://127.0.0.1:9010' };

test('remote dependencies and disabled OCR are not managed locally', () => {
  assert.deepEqual(localServiceTargets({ DATABASE_URL: 'postgresql://database.example/test',
    OCR_ENABLED: 'true', OCR_SERVICE_URL: 'http://ocr.example:9010' }), []);
  assert.deepEqual(localServiceTargets({ OCR_ENABLED: 'false' }), []);
  assert.deepEqual(localServiceTargets({ DATABASE_URL: 'postgresql://localhost:5440/test',
    OCR_ENABLED: 'true', OCR_SERVICE_URL: 'http://localhost' }), []);
});

test('healthy native services do not require Docker', async () => {
  await ensureDevServices(local, { probe: async () => true,
    start: async () => assert.fail('must not restart a healthy service') });
});

test('stopped local containers are restored before startup succeeds', async () => {
  const recovered = [];
  await ensureDevServices(local, { probe: async () => false, log: () => {},
    start: async (target) => recovered.push(target.container),
    wait: async (target) => recovered.includes(target.container) });
  assert.deepEqual(recovered, ['fiscal_postgres', 'fiscal_ocr']);
});

test('an unready OCR model prevents a successful application startup', async () => {
  await assert.rejects(ensureDevServices({ OCR_ENABLED: 'true' }, {
    probe: async () => false, start: async () => {}, wait: async () => false, log: () => {},
  }), /OCR.*未在 60 秒内就绪/);
});

test('manual dependency management is respected', async () => {
  await ensureDevServices({ ...local, DEV_START_LOCAL_SERVICES: 'false' }, {
    probe: async () => assert.fail('must not probe when disabled'),
  });
});

test('OCR 忙到 /health 超时但端口可达时，按就绪处理并给出提示', async () => {
  const server = net.createServer((socket) => socket.destroy());
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  const target = { kind: 'OCR', host: '127.0.0.1', port, container: 'fiscal_ocr',
    healthUrl: `http://127.0.0.1:${port}/health` };

  const realFetch = globalThis.fetch;
  // 模拟 /health 排不上队（超时）
  globalThis.fetch = async () => { throw new Error('timeout'); };
  try {
    const result = await serviceAvailable(target);
    assert.equal(result.ready, true, '端口可达就不应判成未启动');
    assert.match(result.warning, /\/health 2\.5 秒内无响应/);
  } finally {
    globalThis.fetch = realFetch;
    server.close();
  }
});

test('OCR 明确回报模型未就绪时仍要求重启容器', async () => {
  const target = { kind: 'OCR', host: '127.0.0.1', port: 1, container: 'fiscal_ocr',
    healthUrl: 'http://127.0.0.1:1/health' };
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ models_ready: false }) });
  try {
    assert.equal(await serviceAvailable(target), false);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test('OCR 忙但可用时不会去重启容器，只打印提示', async () => {
  const logs = [];
  await ensureDevServices(local, {
    probe: async (target) => (target.kind === 'OCR'
      ? { ready: true, warning: 'OCR 端口可达但 /health 超时，先按就绪处理。' }
      : true),
    start: async () => assert.fail('不应重启已在运行的容器'),
    log: (message) => logs.push(message),
  });
  assert.ok(logs.some((line) => line.includes('/health 超时')), '应把风险提示打出来');
});
