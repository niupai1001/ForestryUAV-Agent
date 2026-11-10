import { defineConfig } from '@playwright/test';
const evidence=process.env.FORESTRY_REPORT_UI_EVIDENCE||'20261026';
export default defineConfig({
  testDir:'./e2e',testMatch:'report-main-entry.spec.ts',timeout:45000,
  use:{baseURL:'http://127.0.0.1:8796',channel:'msedge',viewport:{width:1440,height:960},screenshot:'only-on-failure'},
  reporter:[['list'],['json',{outputFile:`../workspace/evidence/report-main-${evidence}.json`}]],
  outputDir:`../workspace/evidence/report-main-${evidence}-results`,
  webServer:{command:'..\\.venv\\Scripts\\python.exe -m uvicorn research.report_ui_app:make_app --factory --app-dir .. --host 127.0.0.1 --port 8796',
    url:'http://127.0.0.1:8796/health',reuseExistingServer:false,timeout:30000}
});
