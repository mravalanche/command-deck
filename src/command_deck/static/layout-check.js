/* On-device viewport probe. This leaves the dashboard and its navigation in place. */
(() => {
  const revision = "ios-layout-probe-5";
  const nav = document.querySelector(".view-nav");
  const main = document.querySelector(".page");
  if (!nav || !main) return;
  const header = main.querySelector(".masthead");

  const panel = document.createElement("aside");
  panel.className = "layout-check-panel";
  panel.setAttribute("aria-label", "Layout check measurements");
  panel.innerHTML = `
    <h2>Layout check</h2>
    <p class="layout-check-legend">Cyan = menu box · amber = its bottom padding · pink = CSS viewport bottom. In screenshots, compare the pink line with the Home indicator.</p>
    <div class="layout-check-actions">
      <button type="button" data-layout-action="end">Scroll to end</button>
      <button type="button" data-layout-action="copy">Copy report</button>
      <a data-layout-action="close" href="#">Close</a>
    </div>
    <textarea class="layout-check-report" aria-label="Layout measurements" readonly></textarea>
    <p class="layout-check-status" role="status"></p>`;
  document.body.append(panel);

  const edge = document.createElement("div");
  edge.className = "layout-check-css-edge";
  edge.setAttribute("aria-hidden", "true");
  document.body.append(edge);

  const band = document.createElement("div");
  band.className = "layout-check-safe-band";
  band.setAttribute("aria-hidden", "true");
  nav.append(band);

  const probe = document.createElement("div");
  probe.style.cssText = "position:absolute;visibility:hidden;pointer-events:none;width:0;padding-bottom:env(safe-area-inset-bottom);";
  document.body.append(probe);
  const effectiveProbe = document.createElement("div");
  effectiveProbe.style.cssText = "position:absolute;visibility:hidden;pointer-events:none;width:0;padding-bottom:var(--deck-bottom-inset);";
  document.body.append(effectiveProbe);
  const heightProbes = {};
  for (const unit of ["vh", "dvh", "svh"]) {
    const element = document.createElement("div");
    element.style.cssText = `position:absolute;visibility:hidden;pointer-events:none;width:0;height:100${unit}`;
    document.body.append(element);
    heightProbes[unit] = element;
  }

  const report = panel.querySelector(".layout-check-report");
  const status = panel.querySelector(".layout-check-status");
  panel.querySelector('[data-layout-action="close"]').href = location.pathname;
  status.textContent = "The pink line marks the CSS viewport edge; compare it with the physical screen edge in a screenshot.";
  let freshCssRevision = "checking";
  let scrollCheck = null;

  const px = (value) => Math.round(value * 10) / 10;
  const number = (value) => Number.parseFloat(value) || 0;
  const yes = (value) => value ? "PASS" : "CHECK";

  function scrollContainer() {
    const candidates = [main, main.querySelector(".dashboard-content"), main.querySelector(".service-list"), main.querySelector(".event-list"), document.scrollingElement].filter(Boolean);
    return candidates.reduce((best, candidate) =>
      candidate.scrollHeight - candidate.clientHeight > best.scrollHeight - best.clientHeight ? candidate : best
    );
  }

  function update() {
    const navRect = nav.getBoundingClientRect();
    const links = [...nav.querySelectorAll("a, span")];
    const linkBottom = Math.max(...links.map((link) => link.getBoundingClientRect().bottom));
    const navStyle = getComputedStyle(nav);
    const padding = number(navStyle.paddingBottom);
    const inset = number(getComputedStyle(probe).paddingBottom);
    const effectiveInset = number(getComputedStyle(effectiveProbe).paddingBottom);
    const scroller = scrollContainer();
    const overview = document.querySelector("#overview-live");
    const content = main.querySelector(".dashboard-content");
    const headerRect = header.getBoundingClientRect();
    const overviewLast = overview?.querySelector(".summaries > :last-child");
    const viewport = window.visualViewport;
    const cssRevision = navStyle.getPropertyValue("--deck-layout-revision").trim() || "missing";
    band.style.height = `${padding}px`;

    const fixed = navStyle.position === "fixed" && Math.abs(navRect.bottom - window.innerHeight) <= 1;
    const paddingMatches = Math.abs(padding - (8 + effectiveInset)) <= 1;
    const linkGapMatches = Math.abs(navRect.bottom - linkBottom - padding) <= 1;
    if (scrollCheck && scrollCheck.scroller === scroller) {
      scrollCheck.maxAnchorDrift = Math.max(
        scrollCheck.maxAnchorDrift,
        Math.abs(navRect.bottom - innerHeight - scrollCheck.anchorOffset)
      );
      scrollCheck.maxHeaderDrift = Math.max(
        scrollCheck.maxHeaderDrift,
        Math.abs(headerRect.top - scrollCheck.headerTop),
        Math.abs(headerRect.bottom - scrollCheck.headerBottom)
      );
      scrollCheck.maxViewportShift = Math.max(
        scrollCheck.maxViewportShift,
        Math.abs(innerHeight - scrollCheck.viewportHeight)
      );
    }
    const scrollTested = scrollCheck?.scroller === scroller && Math.abs(scroller.scrollTop - scrollCheck.startTop) > 1;
    const navHeld = fixed && scrollCheck?.maxAnchorDrift <= 1;
    const headerHeld = scrollCheck?.maxHeaderDrift <= 1;
    const versionsMatch = [document.body.dataset.layoutRevision, cssRevision, freshCssRevision].every((value) => value === revision);
    const lines = [
      `Probe: ${revision}`,
      `HTML: ${document.body.dataset.layoutRevision || "missing"} · loaded CSS: ${cssRevision}`,
      `Fresh server CSS: ${freshCssRevision} · assets: ${yes(versionsMatch)}`,
      `Mode: ${matchMedia("(display-mode: standalone)").matches || navigator.standalone ? "Home Screen" : "browser"} · CSS standalone ${matchMedia("(display-mode: standalone)").matches}`,
      `Screen: ${screen.width}×${screen.height} CSS px · DPR ${devicePixelRatio}`,
      `innerHeight ${px(innerHeight)} · root clientHeight ${px(document.documentElement.clientHeight)}`,
      `visualViewport h ${viewport ? px(viewport.height) : "n/a"} · top ${viewport ? px(viewport.offsetTop) : "n/a"} · scale ${viewport ? px(viewport.scale) : "n/a"}`,
      `100vh ${px(heightProbes.vh.getBoundingClientRect().height)} · 100dvh ${px(heightProbes.dvh.getBoundingClientRect().height)} · 100svh ${px(heightProbes.svh.getBoundingClientRect().height)}`,
      `safe-area bottom ${px(inset)} · viewport unit gap ${px(heightProbes.vh.getBoundingClientRect().height - heightProbes.dvh.getBoundingClientRect().height)}`,
      `effective bottom inset ${px(effectiveInset)} · menu padding bottom ${px(padding)}`,
      `menu top ${px(navRect.top)} · bottom ${px(navRect.bottom)} · height ${px(navRect.height)}`,
      `link bottom ${px(linkBottom)} · gap to CSS bottom ${px(innerHeight - linkBottom)}`,
      `menu CSS position ${navStyle.position} · bottom ${navStyle.bottom}`,
      `fixed at CSS bottom ${yes(fixed)} · padding formula ${yes(paddingMatches)} · link gap ${yes(linkGapMatches)}`,
      `scroll ${px(scroller.scrollTop)} / ${px(scroller.scrollHeight - scroller.clientHeight)} · menu steady ${scrollTested ? yes(navHeld) : "not tested"}`,
      `header steady ${scrollTested ? yes(headerHeld) : "not tested"} · header drift ${scrollCheck ? px(scrollCheck.maxHeaderDrift) : "not tested"}`,
      `scroll anchor drift ${scrollCheck ? px(scrollCheck.maxAnchorDrift) : "not tested"} · viewport shift ${scrollCheck ? px(scrollCheck.maxViewportShift) : "not tested"}`,
      ...(overviewLast ? [
        `overview last bottom ${px(overviewLast.getBoundingClientRect().bottom)} · menu top ${px(navRect.top)}`,
        `overview content scroll ${px(content.scrollTop)} / ${px(content.scrollHeight - content.clientHeight)} · inner padding ${getComputedStyle(overview).paddingBottom} · page padding ${getComputedStyle(main).paddingBottom}`,
        `header top ${px(headerRect.top)} · bottom ${px(headerRect.bottom)}`,
      ] : []),
      `Path: ${location.pathname} · ${new Date().toISOString()}`,
      `User agent: ${navigator.userAgent}`,
    ];
    report.value = lines.join("\n");
  }

  async function readFreshCssRevision() {
    try {
      const response = await fetch(`/static/app.css?layout-check=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const text = await response.text();
      freshCssRevision = text.match(/--deck-layout-revision\s*:\s*([\w-]+)/)?.[1] || "missing";
    } catch (error) {
      freshCssRevision = `unavailable (${error.message})`;
    }
    update();
  }

  panel.querySelector('[data-layout-action="end"]').addEventListener("click", () => {
    const scroller = scrollContainer();
    scrollCheck = {
      scroller,
      startTop: scroller.scrollTop,
      anchorOffset: nav.getBoundingClientRect().bottom - innerHeight,
      viewportHeight: innerHeight,
      headerTop: header.getBoundingClientRect().top,
      headerBottom: header.getBoundingClientRect().bottom,
      maxAnchorDrift: 0,
      maxHeaderDrift: 0,
      maxViewportShift: 0,
    };
    scroller.scrollTop = scroller.scrollHeight;
    requestAnimationFrame(update);
  });
  panel.querySelector('[data-layout-action="copy"]').addEventListener("click", async () => {
    update();
    try {
      await navigator.clipboard.writeText(report.value);
      status.textContent = "Report copied. Paste it into the chat with a screenshot.";
    } catch {
      report.focus();
      report.select();
      status.textContent = "Copy unavailable. Long press the selected report to copy it.";
    }
  });

  window.addEventListener("resize", update);
  window.addEventListener("orientationchange", update);
  window.addEventListener("scroll", update, true);
  window.visualViewport?.addEventListener("resize", update);
  window.visualViewport?.addEventListener("scroll", update);
  update();
  readFreshCssRevision();
  setInterval(update, 1000);
})();
