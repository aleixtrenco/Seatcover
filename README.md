# Cactus Decal — Seat Cover Builder

Interactive rib seat cover configurator with a live 3D preview for Honda, KTM, Husqvarna, Yamaha and Stark (models in `models/`).
It's a static page (`index.html`) with no build step. Three.js loads from a CDN.

Each bike's seat comes from its own 3D model. It's found by material name, or by a bounding box when the seat is merged into another mesh (Husqvarna). The configurator colors the top as Center and the flanks as Side.
It projects the ribs and logos onto the seat surface, and reshapes the seat for the height, bump and step options.

## Run locally

```bash
python3 -m http.server 8000
```

Then open http://localhost:8000. The model can't load if you open the file directly (file://).

## Customize

Edit the `CONFIG` block at the top of the `<script type="module">` in `index.html`:

- `bikes`: one entry per make, with:
  - its 3D model file
  - how to find the seat
  - where the Cactus Decal logo goes
  - the model and year lists customers can pick (rib covers are universal, so KTM and Husqvarna list every model)
- `currency`, `locale`, `basePrice`, `prices`: shop pricing (currently AED)
- `leadTime`: the yellow shipping line
- `seatLogo`: logo shown on the seat cover (the GUTS logo, as printed by GUTS Racing)
- `bikeLogo`: logo shown on the bike's side number plates (Cactus Decal Lab)
- `models`: model and year dropdowns
- `heightCodes`: supplier part numbers for each height option. They're printed in the supplier order.

`COLORS` holds the 16 seat colors and `PRESETS` holds the quick-look combinations.

## Notes

- The design is saved in the URL hash, so a design link reopens the same configuration.
- "Add to cart" checks the required options, then shows a preview image plus a supplier order text to copy.
  Connect it to your shop cart for production.

## Adding a new bike model

The bikes use the same axes: meters, front of the bike toward +z, up = +y, tires on y = 0.

- **USDZ** (for example from Apple / Reality Composer): unzip it, convert the scene to text with macOS's `usdcat`, then:

  ```bash
  python3 tools/usda2glb.py scene.usda models/new-bike.glb
  ```

- **glTF / GLB that is too heavy or oriented differently:**

  ```bash
  python3 tools/gltf_optimize.py scene.gltf models/new-bike.glb --rotate-y 180 --scale 0.53 --keep Seat
  ```

  Run it with `--info` first to see the size and orientation.

Then add an entry under `CONFIG.bikes` with:
- the seat material name
- where the logo goes
- the model list
- a `credit` line if the model's licence requires one

## 3D model credits

- Stark Varg: "Stark Varg SM" by Hari Yojuta (https://sketchfab.com/hariroosi30), CC BY 4.0,
  https://sketchfab.com/3d-models/stark-varg-sm-bab4fe6d066342eeb96611f37c616311.
  The model was simplified and recolored for this configurator. The credit is shown on the page whenever the Stark bike is selected.
