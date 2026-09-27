/**
 * Bundle the editor front end into llex/static/editor.bundle.js.
 *
 * The bundle is a build artifact and is deliberately not tracked: committing an
 * 800 kB generated file lets the shipped editor silently drift away from its
 * source, which is exactly what happened before. `npm run build` regenerates
 * it, and the release workflow bundles it into the published wheel.
 *
 * Usage:
 *   node build.mjs              readable output, for development
 *   node build.mjs --production  minified, with a source map
 *   node build.mjs --watch       rebuild on change
 */

import * as esbuild from 'esbuild';
import { mkdir, rm, stat } from 'node:fs/promises';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(fileURLToPath(import.meta.url));
const ENTRY = resolve(ROOT, 'llex/static/js/main.js');
const OUTFILE = resolve(ROOT, 'llex/static/editor.bundle.js');
const SOURCEMAP = `${OUTFILE}.map`;

const argv = new Set(process.argv.slice(2));
const production = argv.has('--production');
const watch = argv.has('--watch');

/** @type {import('esbuild').BuildOptions} */
const options = {
  entryPoints: [ENTRY],
  outfile: OUTFILE,
  bundle: true,
  // An IIFE keeps the bundle a plain classic script, so the editor page needs
  // no module loader and works identically in every webview backend.
  format: 'iife',
  platform: 'browser',
  target: ['chrome100', 'firefox100', 'safari15', 'edge100'],
  minify: production,
  sourcemap: production ? 'linked' : 'inline',
  sourcesContent: false,
  // Fail loudly rather than shipping a bundle that throws on load.
  logLevel: 'info',
  legalComments: 'none',
  define: {
    __LLEX_DEV__: JSON.stringify(!production),
  },
  metafile: true,
};

async function report(result) {
  const bytes = Object.values(result.metafile.outputs)[0]?.bytes ?? 0;
  const kb = (bytes / 1024).toFixed(1);
  const mode = production ? 'production' : 'development';
  process.stdout.write(`llex: bundled editor (${mode}) -> ${relative(ROOT, OUTFILE)} (${kb} kB)\n`);
}

async function assertEntryExists() {
  try {
    await stat(ENTRY);
  } catch {
    process.stderr.write(`llex: entry point not found: ${relative(ROOT, ENTRY)}\n`);
    process.exit(1);
  }
}

async function main() {
  await assertEntryExists();
  await mkdir(dirname(OUTFILE), { recursive: true });

  if (watch) {
    const context = await esbuild.context(options);
    await context.watch();
    process.stdout.write('llex: watching for changes (Ctrl+C to stop)\n');
    return;
  }

  // Remove a stale external source map so a development build cannot leave a
  // dangling reference to a production one.
  await rm(SOURCEMAP, { force: true });

  const result = await esbuild.build({ ...options, metafile: true });
  await report(result);
}

main().catch((error) => {
  process.stderr.write(`llex: build failed: ${error?.message ?? error}\n`);
  process.exit(1);
});
