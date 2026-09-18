import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'


// ── DROP CHUNKS NOTHING CAN REACH (mobile build only) ────────────────────────────────────
// The mobile build removes the admin and organization consoles from the route table
// (HAS_CONSOLES in src/App.jsx), and that part works — no console path survives in the
// entry bundle. What does survive is their CODE: `const X = lazy(() => import(...))` is a
// top-level call, so the bundler emits the chunk behind it whether or not anything ever
// imports it. The result was ~40 orphan chunks, ~380 kB, unreachable from the entry and
// shipped inside the APK regardless.
//
// A /* @__PURE__ */ annotation on each lazy() is the textbook answer and is present in
// App.jsx, but it only frees the bundler to drop the declaration — it does not make it fold
// `HAS_CONSOLES` (a const in another module, derived from a replaced import.meta.env) to a
// literal false, which is what would have to happen first. Rather than contort the source
// into a shape one bundler's constant propagation happens to see through, this deletes the
// orphans after the fact, from the one place that can answer the question exactly: the
// finished bundle, where reachability is a graph walk and not a guess.
//
// Deliberately conservative. It starts from every entry chunk and follows BOTH static and
// dynamic imports, so anything genuinely lazy-loaded at runtime is kept; only chunks with no
// path from an entry at all are removed. If the console routes are ever restored to the
// mobile build, every chunk becomes reachable again and this plugin removes nothing.
const pruneUnreachableChunks = () => ({
  name: 'zoiko:prune-unreachable-chunks',
  generateBundle(_options, bundle) {
    // ── REACHABILITY IS READ FROM THE EMITTED CODE, NOT FROM THE CHUNK GRAPH ────────────
    // The obvious implementation — walk `chunk.imports` / `chunk.dynamicImports` from each
    // entry — finds nothing here, and it is worth saying why so nobody "fixes" this back
    // into it. Those arrays are module-graph metadata, recorded before dead-code
    // elimination: the entry chunk still reports 29 dynamic imports after the eliminated
    // HAS_CONSOLES branch has taken all 29 `import()` calls out of its code. The graph says
    // reachable; the code says otherwise, and the code is what ships.
    //
    // So the walk matches on the one thing that is definitely true of a real import: the
    // target chunk's content-hashed filename appears verbatim in the importing chunk's
    // source. Hashed names are unique and unguessable, which makes a false positive
    // (keeping a chunk that is in fact dead) the only realistic failure — the safe
    // direction.
    const chunks = Object.entries(bundle).filter(([, c]) => c.type === 'chunk')
    const byName = new Map(chunks.map(([name, c]) => [name, c]))
    // Match on the basename: import specifiers in the emitted code are relative
    // ("./Commerce-hYCKXVNX.js"), not the "assets/..." key they are filed under here.
    const base = (name) => name.slice(name.lastIndexOf('/') + 1)

    const reachable = new Set()
    const queue = chunks.filter(([, c]) => c.isEntry).map(([name]) => name)
    for (const name of queue) reachable.add(name)

    while (queue.length) {
      const code = byName.get(queue.pop())?.code || ''
      for (const [name] of chunks) {
        if (reachable.has(name)) continue
        if (code.includes(base(name))) {
          reachable.add(name)
          queue.push(name)
        }
      }
    }

    let removed = 0
    let bytes = 0
    for (const [name, chunk] of chunks) {
      if (reachable.has(name)) continue
      bytes += chunk.code?.length || 0
      removed += 1
      delete bundle[name]
    }
    if (removed) {
      this.warn(`pruned ${removed} unreachable chunk(s), ${(bytes / 1024).toFixed(0)} kB — ` +
                'console pages the mobile route table cannot reach')
    }
  },
})

export default defineConfig(({ mode }) => ({
  // Load env from the repo root so backend + frontend share one .env file.
  envDir: '..',
  plugins: [
    react(),
    tailwindcss(),
    // Mobile only. On the web build every chunk IS reachable, so the plugin would have
    // nothing to do — but running a bundle-mutating step against the artefact that serves
    // production, to achieve nothing, is not a trade worth making.
    ...(mode === 'mobile' ? [pruneUnreachableChunks()] : []),
  ],
  // The app's .jsx uses the automatic JSX runtime (no `import React`). Vitest transforms
  // through esbuild rather than the react plugin's babel step, so it needs telling — without
  // this, every component fails with "React is not defined" under test only.
  esbuild: { jsx: 'automatic', jsxImportSource: 'react' },
  // Vitest — dev-only. jsdom because the payment flow reads window.location/history.
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    // Only our own tests; never crawl node_modules or the build output.
    include: ['src/**/*.test.{js,jsx}'],
    css: false,
  },
}))
