# SkillSinc brand

<!-- markdownlint-disable MD033 -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="wordmark-dark.svg">
  <img src="wordmark-light.svg" alt="SkillSinc" width="320">
</picture>
<!-- markdownlint-enable MD033 -->

The continuous S route represents a single library kept in sync across
harnesses. A mint route ends in a periwinkle terminal on an ink tile. The
silhouette remains readable without color. This is a CLI identity; no terminal
output or command behavior changes.

## Assets

| File | Use |
| --- | --- |
| `icon.svg` | Editable master; square app/avatar icon |
| `wordmark-light.svg` | Ink lettering on light surfaces |
| `wordmark-dark.svg` | Pale lettering on dark surfaces |
| `mark-light.svg`, `mark-dark.svg` | Single color mark without a tile |
| `icon-16.png` through `icon-512.png` | Transparent pixel exports |
| `wordmark-light.png`, `wordmark-dark.png` | Transparent 2x fallback exports |

## Design rules

- Ink: `#172544`; mint: `#78E2CB`; periwinkle: `#A9B8FF`.
- Dark wordmark ink: `#EDF5FA`.
- Keep at least 8 units of clear space around the 64-unit icon.
- Use the icon alone below a 160-pixel wordmark width; minimum icon size: 16px.
- Keep the tile in full color on either theme. Use the single color mark
  where a single ink is required; choose the variant for the background.
- Scale proportionally. Do not stretch, rotate, add shadows, or recolor parts.
- Wordmark lettering is Segoe UI Semibold converted to editable vector paths.
  No font files are distributed; rendering requires no installed fonts.

SVG is authoritative. Edit paths in a vector editor and export transparent PNGs
at the named dimensions. Wordmark PNGs are twice the SVG's intrinsic size.
Check the 16px export at actual size and both wordmarks on their matching
surfaces after every edit. Keep the README picture sources in step with assets.
