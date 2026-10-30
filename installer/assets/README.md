# Setup icon

`stationdesk.svg` uses the existing full O3 mark in `../../hub/icon.svg`, with
cream strokes, ink tile, orange agent dot and padding. The full mark is required
at 32px and above; the simplified hub favicon is only for 16px.

`stationdesk.png` is its committed 1024px raster. Both setup packages use this
same image; macOS builds its icns with built-in sips/iconutil. No SVG renderer is
needed to build or run either installer. To regenerate after editing the SVG,
use any conforming SVG renderer (CairoSVG was used for this version).

App icons are copied directly from `../../hub/web/assets/app-icons/` at package
build time, without duplicated source images.
