import { defineConfig } from '@playwright/test';
const evidence=process.env.FORESTRY_JOBS_EVIDENCE||'20261026';

export default defineConfig({
  testDir:'./e2e',testMatch:'jobs-panel.spec.ts',timeout:30000,
  use:{baseURL:'http://127.0.0.1:8794',channel:'msedge',viewport:{width:900,height:800},screenshot:'only-on-failure'},
  reporter:[['list'],['json',{outputFile:`../workspace/evidence/jobs-panel-${evidence}.json`}]],
  outputDir:`../workspace/evidence/jobs-panel-${evidence}-results`,
  webServer:{command:'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 8794 --strictPort',
    url:'http://127.0.0.1:8794',reuseExistingServer:false,timeout:30000}
});
