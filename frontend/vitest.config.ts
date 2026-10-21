import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/setupTests.ts',
    // Playwright e2e specs run separately via the Playwright runner, not vitest.
    exclude: ['**/node_modules/**', '**/dist/**', '**/playwright.**.spec.js', '**/e2e/**'],
  },
})
