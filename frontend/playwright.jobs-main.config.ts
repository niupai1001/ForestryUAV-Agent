import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir:'./e2e',testMatch:'jobs-main-entry.spec.ts',timeout:45000,
  use:{baseURL:'http://127.0.0.1:8797',channel:'msedge',viewport:{width:1440,height:960},screenshot:'only-on-failure'},
  reporter:[['list'],['json',{outputFile:'../workspace/evidence/jobs-main-20261026.json'}]],
  outputDir:'../workspace/evidence/jobs-main-20261026-results',
  webServer:{command:'..\\.venv\\Scripts\\python.exe -m uvicorn research.report_ui_app:make_jobs_app --factory --app-dir .. --host 127.0.0.1 --port 8797',
    url:'http://127.0.0.1:8797/health',reuseExistingServer:false,timeout:30000}
});
