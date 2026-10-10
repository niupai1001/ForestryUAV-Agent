import { defineConfig } from '@playwright/test';
import base from './playwright.config';

export default defineConfig({...base,
  reporter: [['list'], ['json', {outputFile:'../workspace/evidence/memory-ui-20261010-v1.json'}]],
  outputDir:'../workspace/evidence/memory-ui-results-20261010-v1',
});
