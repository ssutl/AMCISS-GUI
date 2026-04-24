# AMCISS GUI — TODO

## Heatmap
- [ ] LDC selection — ability to select a subset of LDCs to display on heatmap (e.g. only show LDCs 0-31)
- [ ] Select All / Deselect All button for LDC selection
- [ ] Highlight specific LDC column on hover
- [x] Fix heatmap colour bar — auto-scale to actual data range (was fixed at 0-15 µH, invisible with dummy data at ~20 µH baseline)

## General
- [x] Unified "Window (s)" control — single setting controls plot time range, heatmap time range, and recording duration
- [x] Sliding time window — plots/heatmap only show last N seconds of data
- [x] Timed recording — auto-stops after window duration with countdown display
- [x] Relative time axis on single LDC plot (0 = now, negative = past)
