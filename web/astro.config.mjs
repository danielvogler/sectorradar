// @ts-check
import { defineConfig } from 'astro/config';

export default defineConfig({
  // Asset URLs are made relative after the build, by scripts/relativise.mjs.
  //
  // Not by `base`. Every value of it was tried: `'./'` emits `/./assets/…`,
  // `'.'` emits `/.././assets/…`, and `''` emits `/assets/…`. All three are
  // absolute, and all three work when served over HTTP — which is exactly what
  // makes the bug so easy to ship. Opened as a file, the page resolves them
  // against the filesystem root and loads with no styles and no script.
  //
  // A folder that opens from the filesystem is the property that makes this
  // something you can hand to somebody, so it is worth a post-build pass and a
  // test rather than a config option that nearly works.
  build: { assets: 'assets', format: 'file' },
  vite: {
    build: {
      assetsInlineLimit: 0,
      rollupOptions: {
        output: {
          // Astro's defaults name a chunk after the module graph that produced
          // it: `index.astro_astro_type_script_index_0_lang.D3Oss7K6.js`. Fine
          // in a build directory nobody reads, less so in a bucket somebody
          // was sent a link to. The hash stays — it is what lets these cache
          // forever while the page they belong to cannot.
          entryFileNames: 'assets/app.[hash].js',
          assetFileNames: 'assets/[name].[hash][extname]',
        },
      },
    },
  },
});
