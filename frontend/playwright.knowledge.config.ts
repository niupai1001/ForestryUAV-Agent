import { defineConfig } from '@playwright/test';
import base from './playwright.config';
const tag=process.env.FORESTRY_EVIDENCE_TAG||'v1';

export default defineConfig({...base,
  reporter:[['list'],['json',{outputFile:`../workspace/evidence/knowledge-ui-20261010-${tag}.json`}]],
  outputDir:`../workspace/evidence/knowledge-ui-results-20261010-${tag}`,
});
