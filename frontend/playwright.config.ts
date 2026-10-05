import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 60000,
  use: { baseURL: 'http://127.0.0.1:8765', channel: 'msedge',
    viewport: { width: 1440, height: 960 }, screenshot: 'only-on-failure' },
  reporter: [['list'], ['json', { outputFile: '../workspace/evidence/ui-tests.json' }]],
  outputDir: '../workspace/evidence/ui-test-results'
});
