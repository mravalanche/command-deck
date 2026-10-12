import { copyFileSync, mkdirSync } from 'node:fs';

const out = 'src/command_deck/static';
mkdirSync(`${out}/fonts`, { recursive: true });
copyFileSync('node_modules/htmx.org/dist/htmx.min.js', `${out}/htmx.min.js`);
copyFileSync('node_modules/htmx.org/LICENSE', `${out}/LICENSE-htmx.txt`);
for (const weight of ['400', '600', '700']) {
  copyFileSync(`node_modules/@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-${weight}-normal.woff2`, `${out}/fonts/ibm-plex-sans-${weight}.woff2`);
}
copyFileSync('node_modules/@fontsource/ibm-plex-sans/LICENSE', `${out}/fonts/LICENSE-IBM-Plex-Sans.txt`);
copyFileSync('design/fonts/CaskaydiaCoveNerdFont-Regular.ttf', `${out}/fonts/CaskaydiaCoveNerdFont-Regular.ttf`);
copyFileSync('design/fonts/LICENSE-CaskaydiaCove.txt', `${out}/fonts/LICENSE-CaskaydiaCove.txt`);
