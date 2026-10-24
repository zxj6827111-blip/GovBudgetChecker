const { test } = require('node:test');
const assert = require('node:assert/strict');
const { ensureDevServices, localServiceTargets } = require('./dev-services.cjs');

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
