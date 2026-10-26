// Browser geometry regression tests for the dashboard shell. Requires Playwright
// and a Chromium browser. Generate fixtures with mock-screenshot-pages.py first.
// Supply PLAYWRIGHT_PACKAGE and PLAYWRIGHT_CHROMIUM_EXECUTABLE for external installs.
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
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

function check(condition, label, geometry) {
  if (!condition) throw new Error(`${label}: ${JSON.stringify(geometry)}`);
}

async function open(browser, origin, fixture, width, height) {
  const mobile = width <= 650;
  const page = await browser.newPage({ viewport: { width, height }, hasTouch: mobile, isMobile: mobile });
  await page.goto(`${origin}/${fixture}`);
  await page.locator('.view-nav').waitFor();
  return page;
}

async function scrollGeometry(page, selector, scrollport) {
  return page.evaluate(({ selector, scrollport }) => {
    const header = document.querySelector('.masthead');
    const menu = document.querySelector('.view-nav');
    const last = document.querySelector(selector);
    const port = document.querySelector(scrollport);
    const before = { header: header.getBoundingClientRect(), menu: menu.getBoundingClientRect(), last: last.getBoundingClientRect() };
    port.scrollTop = port.scrollHeight;
    const after = { header: header.getBoundingClientRect(), menu: menu.getBoundingClientRect(), last: last.getBoundingClientRect() };
    return {
      scrollTop: port.scrollTop,
      maxScroll: port.scrollHeight - port.clientHeight,
      frameScroll: document.querySelector('.page').scrollTop,
      headerDrift: Math.max(Math.abs(after.header.top - before.header.top), Math.abs(after.header.bottom - before.header.bottom)),
      menuDrift: Math.max(Math.abs(after.menu.top - before.menu.top), Math.abs(after.menu.bottom - before.menu.bottom)),
      headerTop: after.header.top,
      menuTop: after.menu.top,
      menuBottom: after.menu.bottom,
      menuPadding: parseFloat(getComputedStyle(menu).paddingBottom),
      lastStartBottom: before.last.bottom,
      lastEndBottom: after.last.bottom,
      viewportHeight: window.innerHeight,
    };
  }, { selector, scrollport });
}

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
      : {}),
  });
  try {
    for (const [label, width, height] of [['desktop', 1440, 900], ['tablet landscape', 1024, 768], ['command deck', 800, 480], ['iphone', 393, 852]]) {
      const page = await open(browser, origin, 'overview-healthy.html', width, height);
      const state = await page.evaluate(() => {
        const filler = document.createElement('section');
        filler.style.cssText = 'height:1400px;flex:none';
        document.querySelector('#overview-live').append(filler);
        const nav = document.querySelector('.view-nav');
        return { navPosition: getComputedStyle(nav).position };
      });
      const mobile = width <= 650;
      const geometry = await scrollGeometry(page, '#overview-live > :last-child', mobile ? '.dashboard-content' : 'html');
      check(state.navPosition === 'fixed', `${label} fixed menu`, state);
      check(geometry.maxScroll > 0 && geometry.scrollTop > 0, `${label} scroll range`, geometry);
      check(Math.abs(geometry.menuBottom - height) <= 1 && geometry.menuDrift <= 1, `${label} menu anchor`, geometry);
      check(geometry.menuPadding >= 8 && geometry.lastEndBottom <= geometry.menuTop - 4, `${label} final item clearance`, geometry);
      if (mobile) check(geometry.headerDrift <= 1 && geometry.frameScroll === 0, `${label} fixed header`, geometry);
      console.log(`PASS ${label}: anchored menu, scroll range, final item clearance${mobile ? ', stationary header' : ''}`);
      await page.close();
    }

    // Simulate an Overview that fits on first paint, then loses viewport height.
    // This caught the case where the last summary sat behind the menu with no scroll range.
    const overview = await open(browser, origin, 'overview-healthy.html', 393, 852);
    const insetMeasures = await overview.evaluate(() => {
      const root = document.documentElement;
      root.style.setProperty('--deck-safe-bottom', '34px');
      root.style.setProperty('--deck-bottom-inset', 'max(0px, calc(var(--deck-safe-bottom) - var(--deck-viewport-gap)))');
      const measure = gap => {
        root.style.setProperty('--deck-viewport-gap', `${gap}px`);
        return {
          menu: parseFloat(getComputedStyle(document.querySelector('.view-nav')).paddingBottom),
          content: parseFloat(getComputedStyle(document.querySelector('#overview-live')).paddingBottom),
        };
      };
      return [measure(0), measure(20), measure(41)];
    });
    check(JSON.stringify(insetMeasures) === JSON.stringify([{ menu: 42, content: 106 }, { menu: 22, content: 86 }, { menu: 8, content: 72 }]), 'safe inset variants', insetMeasures);
    const fitted = await overview.evaluate(() => {
      const last = document.querySelector('.summaries').lastElementChild;
      last.style.minHeight = `${last.getBoundingClientRect().height + 50}px`;
      const port = document.querySelector('.dashboard-content');
      return { lastBottom: last.getBoundingClientRect().bottom, menuTop: document.querySelector('.view-nav').getBoundingClientRect().top, maxScroll: port.scrollHeight - port.clientHeight };
    });
    check(fitted.lastBottom < fitted.menuTop && fitted.maxScroll <= 1, 'initially fitting Overview', fitted);
    await overview.setViewportSize({ width: 393, height: 811 });
    const shrunken = await scrollGeometry(overview, '.summaries > :last-child', '.dashboard-content');
    check(shrunken.lastStartBottom > shrunken.menuTop && shrunken.maxScroll > 0 && shrunken.scrollTop > 0, 'Overview gains scroll range after viewport shrink', shrunken);
    check(shrunken.lastEndBottom <= shrunken.menuTop - 4 && shrunken.headerDrift <= 1 && shrunken.menuDrift <= 1 && shrunken.frameScroll === 0, 'Overview last card clears stationary bars', shrunken);
    console.log('PASS iphone near-fit Overview: safe insets, resize, last card clearance');
    await overview.close();

    for (const fixture of ['overview-healthy.html', 'server.html', 'events.html']) {
      const page = await open(browser, origin, fixture, 393, 793);
      await page.evaluate(() => {
        const filler = document.createElement('section');
        filler.style.height = '700px';
        document.querySelector('.dashboard-content').firstElementChild.append(filler);
      });
      const geometry = await scrollGeometry(page, '.dashboard-content > :first-child > :last-child', '.dashboard-content');
      check(geometry.lastStartBottom > geometry.menuTop && geometry.maxScroll > 0 && geometry.scrollTop > 0, `${fixture} scroll range`, geometry);
      check(geometry.lastEndBottom <= geometry.menuTop - 4 && geometry.headerDrift <= 1 && geometry.menuDrift <= 1 && geometry.frameScroll === 0, `${fixture} stationary bars and clearance`, geometry);
      console.log(`PASS iphone ${fixture}: stationary header/menu and scrollable content`);
      await page.close();
    }

    const services = await open(browser, origin, 'services.html', 393, 852);
    await services.evaluate(() => {
      const root = document.documentElement;
      root.style.setProperty('--deck-safe-bottom', '34px');
      root.style.setProperty('--deck-viewport-gap', '41px');
      root.style.setProperty('--deck-bottom-inset', 'max(0px, calc(var(--deck-safe-bottom) - var(--deck-viewport-gap)))');
      const list = document.querySelector('.service-list');
      const row = list.querySelector('.service-row');
      for (let index = 0; index < 20; index++) list.append(row.cloneNode(true));
    });
    const serviceGeometry = await scrollGeometry(services, '.service-list > :last-child', '.service-list');
    check(serviceGeometry.maxScroll > 100 && Math.abs(serviceGeometry.scrollTop - serviceGeometry.maxScroll) <= 1, 'Services nested scroll range', serviceGeometry);
    check(serviceGeometry.lastEndBottom <= serviceGeometry.menuTop - 4 && serviceGeometry.headerDrift <= 1 && serviceGeometry.menuDrift <= 1, 'Services last row clears stationary bars', serviceGeometry);
    console.log('PASS iphone Services: nested scroll and last row clearance');
    await services.close();

    const clock = await open(browser, origin, 'overview-healthy.html', 393, 852);
    await clock.route('**/healthz', route => route.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' }));
    await clock.addScriptTag({ url: `${origin}/static/app.js` });
    const close = clock.locator('#clock-close');
    await clock.locator('#clock-trigger').click();
    await clock.mouse.move(100, 100);
    const hitArea = await close.boundingBox();
    const closeText = (await close.innerText()).trim();
    const closeName = await close.getAttribute('aria-label');
    const closeStyle = await close.evaluate(button => {
      const style = getComputedStyle(button);
      return { background: style.backgroundColor, borderWidth: style.borderTopWidth, opacity: Number(style.opacity) };
    });
    check(closeText === '×' && closeName === 'Close full-screen clock' && hitArea.width >= 44 && hitArea.height >= 44 && closeStyle.background === 'rgba(0, 0, 0, 0)' && closeStyle.borderWidth === '0px' && closeStyle.opacity < 1, 'clock close control', { closeText, closeName, hitArea, closeStyle });
    check(await clock.locator('#clock-dialog').evaluate(dialog => dialog.open), 'clock opens', {});
    await close.click();
    check(!await clock.locator('#clock-dialog').evaluate(dialog => dialog.open) && await clock.evaluate(() => document.activeElement?.id === 'clock-trigger'), 'clock closes by X and restores focus', {});
    await clock.locator('#clock-trigger').click();
    await clock.keyboard.press('Escape');
    check(!await clock.locator('#clock-dialog').evaluate(dialog => dialog.open) && await clock.evaluate(() => document.activeElement?.id === 'clock-trigger'), 'clock closes by Escape and restores focus', {});
    console.log('PASS clock: subtle X, 44px hit area, click/Escape, focus return');
    await clock.close();
  } finally {
    await browser.close();
    server.close();
  }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
