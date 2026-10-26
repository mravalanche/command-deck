# Command Deck design

Command Deck is a glanceable homelab dashboard for server capacity, backups, and service health. The current application uses **Midnight Ink**. **Midnight Violet** remains an alternate in the interactive design showcase. All values in the design previews and screenshots are illustrative.

## Dashboard direction

![Illustrative Command Deck overview at 800 × 480](docs/screenshots/overview-healthy.png)

The target displays are 800 × 480 and 1024 × 600. Overview surfaces the current signal and a few concise summaries; Servers, Services, and Events carry more detail. A telemetry label describes source freshness, while colour and text on a card describe the monitored condition. Missing or stale data must never look healthy.

The [Overview preview](design/dashboard-preview-v2.html) and [Servers preview](design/servers-preview.html) are interactive design explorations. The [earlier Overview preview](design/dashboard-preview.html) preserves the initial typography and layout exploration. These previews document design decisions rather than the current feature set. The [application screenshots](docs/screenshots/) show rendered dashboard states.

## Theme and typography

![Midnight Ink theme foundations, type, and semantic colours](design/images/theme-midnight-ink.png)

Near-black surfaces, quiet blue separation, and restrained semantic colours keep the display readable. IBM Plex Sans carries interface text; CaskaydiaCove Nerd Font is reserved for short technical labels. The [theme reference](design/THEME.md) contains both theme captures, colour values, type guidance, and the source CSS. The [interactive showcase](design/component-showcase.html) lets you switch themes.

## Components and states

![Command Deck server, attention, backup, availability, and event patterns](design/images/component-patterns.png)

The [component reference](design/COMPONENTS.md) documents the chosen dashboard patterns, status and telemetry treatment, navigation, and the optional DaisyUI gallery. It includes captures of the product patterns and gallery; the [interactive component showcase](design/component-showcase.html) remains the comprehensive browser view.

## Current implementation

The application implements Overview, Servers, Services, and current-signal Events. It reads Prometheus host metrics and optional Healthchecks, Uptime Kuma, TrueNAS, and Proxmox VE data. The [Proxmox integration](docs/proxmox.md) supplies physical node CPU, RAM, logical CPU count, and root filesystem capacity; its history charts are unavailable. Prometheus hosts have five-minute CPU and memory charts. Historical failure and recovery events are not connected yet. The design showcase demonstrates additional states and controls; its sample values are not live data.

To refresh the captured design images after changing the showcase, build the application CSS and run `node scripts/capture-design-reference.mjs` with Playwright available. The script uses the local CSS and fonts so the images do not depend on CDN access.
