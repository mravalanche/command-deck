# Command Deck layout requirements

Treat the mobile dashboard as a viewport frame: the masthead stays at the top, the view menu stays fixed at the CSS viewport bottom, and only dashboard content scrolls. Overview, Servers, and Events use `.dashboard-content` as their scroll container. Services keeps its `.service-list` scroll container. Do not put the masthead inside a scrolling element.

Every last card or row must scroll completely above the menu, including an Overview that initially fits and then loses viewport height. Keep the menu visible and tappable above content, with the effective bottom safe inset and at least 8px internal padding. Preserve the top safe-area spacing for the masthead. A fully fitting view should not gain a needless scroll range.

Before changing shell layout, run the browser geometry test: build CSS, generate screenshot fixtures, then run `scripts/test-mobile-layout.cjs` with Playwright. It checks stationary header and menu geometry, scroll range, final-card clearance, the Services list, safe inset variants, and desktop, tablet landscape, Command Deck low resolution, and iPhone sizes. Update the test when the scroll container or safe-area CSS changes. Browser emulation cannot verify the iPhone Home indicator; use an uncropped on-device screenshot for that boundary.

The full-screen clock closes with a subtle, icon-only X. Keep an accessible name and a hit area of at least 44 × 44 CSS pixels. The close button and Escape must close the dialog and return focus to the clock trigger. The browser test covers these interactions.
