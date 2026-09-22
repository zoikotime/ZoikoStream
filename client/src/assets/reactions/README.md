# Reaction artwork

Microsoft **Fluent Emoji** (3D style), **MIT licensed** —
https://github.com/microsoft/fluentui-emoji

Vendored here rather than pulled at runtime so the bundle owns them: Vite fingerprints each
file for cache-busting, and no reaction depends on a third-party CDN being reachable.

| File | Reaction key | Character | Source codepoint |
|------|--------------|-----------|------------------|
| `like.webp`  | `like`  | 👍 | `1f44d`      |
| `heart.webp` | `heart` | ❤️ | `2764-fe0f`  |
| `party.webp` | `party` | 🎉 | `1f389`      |
| `fire.webp`  | `fire`  | 🔥 | `1f525`      |
| `clap.webp`  | `clap`  | 👏 | `1f44f`      |

256×256 WebP with an alpha channel, 26.6 KB for the set. Obtained from the
`@lobehub/fluent-emoji-3d` npm package (MIT), which redistributes the Microsoft artwork;
the five files were extracted and committed here, so the package itself is **not** a
dependency of this project.

Filenames are the reaction's wire key, which is what `src/data/reactions.js` imports them
by. Those keys are protocol — see that file before renaming anything.
