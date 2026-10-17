# Command Deck layout requirements

Treat the mobile dashboard as a viewport frame: the masthead stays at the top, the view menu stays fixed at the CSS viewport bottom, and only dashboard content scrolls. Overview, Servers, and Events use `.dashboard-content` as their scroll container. Services keeps its `.service-list` scroll container. Do not put the masthead inside a scrolling element.

Every last card or row must scroll completely above the menu, including an Overview that initially fits and then loses viewport height. Keep the menu visible and tappable above content, with the effective bottom safe inset and at least 8px internal padding. Preserve the top safe-area spacing for the masthead. A fully fitting view should not gain a needless scroll range.

Before changing shell layout, run the browser geometry test: build CSS, generate screenshot fixtures, then run `scripts/test-layout-check.cjs` with Playwright. It checks stationary header and menu geometry, scroll range, final-card clearance, the Services list, safe inset variants, and desktop, tablet landscape, Command Deck low resolution, and iPhone sizes. Update the test and the opt-in on-device layout probe when the scroll container or CSS revision changes. Browser emulation cannot verify the iPhone Home indicator; use the on-device probe and an uncropped screenshot for that boundary.
