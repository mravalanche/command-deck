// Browser smoke test for the opt-in layout probe. Requires Playwright and a Chromium browser.
// Run mock-screenshot-pages.py first, then: node scripts/test-layout-check.cjs
const { chromium } = require('playwright');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');

const root = path.join(os.tmpdir(), 'command-deck-screenshot-fixtures');
const mime = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml' };
const server = http.createServer(async (request, response) => {
  const pathname = new URL(request.url, 'http://localhost').pathname;
  const file = path.resolve(root, `.${pathname}`);
  if (!file.startsWith(`${root}/`)) { response.writeHead(403).end(); return; }
  try {
    const bytes = await fs.readFile(file);
    response.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' }).end(bytes);
  } catch { response.writeHead(404).end(); }
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [label, width, height] of [['desktop', 1440, 900], ['deck', 800, 480], ['iphone', 393, 852]]) {
      const page = await browser.newPage({ viewport: { width, height }, hasTouch: label === 'iphone', isMobile: label === 'iphone' });
      await page.goto(`${origin}/overview-healthy.html`);
      if (await page.locator('.layout-check-panel').count()) throw new Error(`${label}: probe shown by default`);
      await page.goto(`${origin}/layout-check.html`);
      await page.locator('.layout-check-report').waitFor();
      await page.waitForFunction(() => document.querySelector('.layout-check-report').value.includes('Fresh server CSS: ios-layout-probe-2'));
      await page.evaluate(() => {
        const box = document.createElement('section');
        box.style.cssText = 'height:1400px;flex:none';
        document.querySelector('#dashboard-content').append(box);
        // A late layout change moves the menu top while its bottom stays anchored.
        // This must not make the scroll check fail.
        document.querySelector('.view-nav').style.paddingTop = '20px';
      });
      await page.locator('[data-layout-action="end"]').click();
      await page.waitForFunction(() => document.querySelector('.layout-check-report').value.includes('menu steady PASS'));
      const result = await page.evaluate(() => ({
        report: document.querySelector('.layout-check-report').value,
        markerBottom: document.querySelector('.layout-check-css-edge').getBoundingClientRect().bottom,
        navBottom: document.querySelector('.view-nav').getBoundingClientRect().bottom,
        bandHeight: document.querySelector('.layout-check-safe-band').getBoundingClientRect().height,
      }));
      if (!result.report.includes('assets: PASS') || !result.report.includes('fixed at CSS bottom PASS')) throw new Error(`${label}: failed probe checks\n${result.report}`);
      if (Math.abs(result.markerBottom - height) > 1 || Math.abs(result.navBottom - height) > 1 || result.bandHeight < 8) throw new Error(`${label}: incorrect marker/nav geometry`);
      console.log(`PASS ${label}: probe, versions, fixed marker, scroll end`);
      await page.close();
    }

    const services = await browser.newPage({ viewport: { width: 393, height: 852 }, hasTouch: true, isMobile: true });
    await services.goto(`${origin}/services-layout-check.html`);
    await services.evaluate(() => {
      const list = document.querySelector('.service-list');
      const row = list.querySelector('.service-row');
      for (let index = 0; index < 20; index++) list.append(row.cloneNode(true));
    });
    await services.locator('[data-layout-action="end"]').click();
    await services.waitForFunction(() => document.querySelector('.layout-check-report')?.value.includes('menu steady PASS'));
    const serviceScroll = await services.locator('.service-list').evaluate(list => ({ top: list.scrollTop, max: list.scrollHeight - list.clientHeight }));
    if (serviceScroll.max < 100 || Math.abs(serviceScroll.top - serviceScroll.max) > 1) throw new Error(`Services did not scroll to end: ${JSON.stringify(serviceScroll)}`);
    console.log('PASS iphone services: nested content scroll and menu anchor');
    await services.evaluate(() => {
      const list = document.querySelector('.service-list');
      list.scrollTop = 0;
      const displace = () => {
        if (list.scrollTop <= 0) return;
        document.querySelector('.view-nav').style.bottom = '10px';
        list.removeEventListener('scroll', displace);
      };
      list.addEventListener('scroll', displace);
    });
    await services.locator('[data-layout-action="end"]').click();
    await services.waitForFunction(() => document.querySelector('.layout-check-report')?.value.includes('menu steady CHECK'));
    console.log('PASS iphone services: displaced menu detected during scroll');
    await services.close();

    const stale = await browser.newPage({ viewport: { width: 393, height: 852 } });
    await stale.route('**/static/app.css', async route => {
      if (new URL(route.request().url()).searchParams.has('layout-check')) { await route.continue(); return; }
      const css = await fs.readFile(path.join(root, 'static/app.css'), 'utf8');
      await route.fulfill({ contentType: 'text/css', body: css.replace('--deck-layout-revision:ios-layout-probe-2;', '') });
    });
    await stale.goto(`${origin}/layout-check.html`);
    await stale.waitForFunction(() => document.querySelector('.layout-check-report')?.value.includes('Fresh server CSS: ios-layout-probe-2'));
    const staleReport = await stale.locator('.layout-check-report').inputValue();
    if (!staleReport.includes('loaded CSS: missing') || !staleReport.includes('assets: CHECK')) throw new Error(`Stale CSS was not detected:\n${staleReport}`);
    console.log('PASS stale loaded CSS detected against fresh server CSS');
    await stale.close();
  } finally {
    await browser.close();
    server.close();
  }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
