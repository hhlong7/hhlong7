# Profile cards

- `render_cards.py` renders every card into `cards/` (animated SVG, fonts subset and embedded).
- `STATIC=1 python scripts/render_cards.py` writes final-frame cards plus `preview.html` to `cards-static/` for screenshots.
- `fetch_contributions.py` writes `data/contributions.json` (needs `GH_TOKEN`). The Action runs both scripts daily and commits `data/` and `cards/`.
- Colours follow the browser light/dark setting (THEME=light|dark forces one). Edit the about text, projects, palettes and stagger timings at the top of `render_cards.py`.

Needs `pip install fonttools`.
