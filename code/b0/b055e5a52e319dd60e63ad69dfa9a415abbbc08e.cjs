const { defineConfig } = require('../../app/node_modules/playwright/test');
const path = require('node:path');

module.exports = defineConfig({
  testDir: path.resolve(__dirname, '../../e2e/tests'),
  testMatch: ['department-directory.spec.ts', 'review-sources-reports.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 30000,
  expect: { timeout: 15000 },
  outputDir: path.resolve(__dirname, 'browser-results'),
  reporter: [['list'], ['json', { outputFile: path.resolve(__dirname, 'browser-results.json') }]],
  use: {
    baseURL: 'http://127.0.0.1:3000',
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: { executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' },
  },
});
