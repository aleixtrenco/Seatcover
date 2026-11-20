# Cactus Decal — Seat Cover Builder

Interactive rib seat cover configurator with a live 3D preview on a CRF450-style bike.
It's a single static page (`index.html`) with no build step. Three.js loads from a CDN.

## Run locally

```bash
python3 -m http.server 8000
```

Then open http://localhost:8000.

## Customize

Edit the `CONFIG` block at the top of the `<script type="module">` in `index.html`:

- `currency`, `locale`, `basePrice`, `prices`: shop pricing
- `leadTime`: the yellow shipping line
- `logoText`: text drawn on the seat logos in the preview
- `models`: model and year dropdowns
- `heightCodes`: supplier part numbers for each height option. They're printed in the supplier order.

`COLORS` holds the 16 seat colors and `PRESETS` holds the quick-look combinations.

## Notes

- The design is saved in the URL hash, so a design link reopens the same configuration.
- "Add to cart" checks the required options, then shows a preview image plus a supplier order text to copy.
  Connect it to your shop cart for production.
