// Capture real browser renderings of the fictitious pages built by mock-screenshot-pages.py.
// Supply PLAYWRIGHT_PACKAGE if Playwright is installed outside this project.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import os from 'node:os';
import { mkdir } from 'node:fs/promises';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
const fixture = path.join(os.tmpdir(), 'command-deck-screenshot-fixtures');
const output = path.resolve('docs/screenshots');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 800, height: 480 },
  deviceScaleFactor: 2,
  colorScheme: 'dark',
  locale: 'en-GB',
});

for (const name of ['overview-healthy', 'overview-alert', 'server', 'nas', 'services']) {
  const page = await context.newPage();
  await page.goto(pathToFileURL(path.join(fixture, `${name}.html`)).href);
  await page.waitForLoadState('load');
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(150);
  const size = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight }));
  console.log(`${name}: layout ${size.width}×${size.height}, viewport 800×480`);
  if (size.width > 800 || size.height > 480) {
    throw new Error(`${name} exceeds the 800×480 display`);
  }
  await page.screenshot({ path: path.join(output, `${name}.png`) });
  if (name === 'overview-healthy') {
    await page.locator('.hosts').screenshot({ path: path.join(output, 'components.png') });
  }
  await page.close();
}

await browser.close();
