// Capture the design showcase as images that render directly on GitHub.
// Supply PLAYWRIGHT_PACKAGE and PLAYWRIGHT_CHROMIUM_EXECUTABLE for external installs.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { mkdir } from 'node:fs/promises';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
const root = path.resolve('.');
const output = path.join(root, 'design', 'images');
const appCss = path.join(root, 'src', 'command_deck', 'static', 'app.css');
const fontDir = path.join(root, 'node_modules', '@fontsource', 'ibm-plex-sans', 'files');
await mkdir(output, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
    : {}),
});
try {
  const page = await browser.newPage({
    viewport: { width: 1280, height: 900 },
    deviceScaleFactor: 1.5,
    colorScheme: 'dark',
    locale: 'en-GB',
  });

  // The interactive HTML uses CDN CSS. Use the project's built DaisyUI CSS and
  // bundled fonts so checked-in images are repeatable without network access.
  await page.route('https://cdn.jsdelivr.net/**', route =>
    route.fulfill({ path: appCss, contentType: 'text/css' }));
  await page.route('https://fonts.googleapis.com/**', route =>
    route.fulfill({ status: 204 }));
  await page.route('https://fonts.gstatic.com/**', route => route.abort());
  await page.goto(pathToFileURL(path.join(root, 'design', 'component-showcase.html')).href);
  await page.addStyleTag({ content: [400, 600, 700].map(weight =>
    `@font-face { font-family: "IBM Plex Sans"; font-style: normal; font-weight: ${weight}; ` +
    `src: url("${pathToFileURL(path.join(fontDir, `ibm-plex-sans-latin-${weight}-normal.woff2`)).href}") format("woff2"); }`
  ).join('\n') });
  await page.evaluate(() => document.fonts.ready);

  for (const [theme, file] of [
    ['midnight-ink', 'theme-midnight-ink.png'],
    ['midnight-violet', 'theme-midnight-violet.png'],
  ]) {
    await page.locator('#theme-select').selectOption(theme);
    await page.waitForFunction(selectedTheme => {
      const pageColor = document.querySelector('[data-token="--color-base-100"]');
      return document.documentElement.dataset.theme === selectedTheme
        && pageColor.textContent.trim() === getComputedStyle(document.documentElement)
          .getPropertyValue('--color-base-100').trim();
    }, theme);
    await page.locator('#foundations').screenshot({ path: path.join(output, file) });
  }

  await page.locator('#theme-select').selectOption('midnight-ink');
  await page.locator('#product-patterns').screenshot({ path: path.join(output, 'component-patterns.png') });
  await page.locator('#daisy-gallery').screenshot({ path: path.join(output, 'component-gallery.png') });
  await browser.close();
} catch (error) {
  await browser.close();
  throw error;
}
