# Archived pages

Pages removed from the app menu on 2026-11-10 to keep the product focused (Research desk, System signals, Portfolio,
Chart, Learn). Their backend endpoints still exist. To restore one, move its folder back under `src/app/` and add it
to `LINKS` in `src/components/Nav.tsx`.

- `app/board`, `app/radar`, `app/follow`, `app/backtest`, `app/research`, `app/news`, `app/health`,
  `app/methodology`, `app/room`, `app/simple`
- `app/signals-list-page.tsx` was `src/app/signals/page.tsx` (signal detail pages stay: alerts link to them)
- `components/desk/Desk.tsx`, `Cursors.tsx`: the old animated-cursor desk, replaced by the research desk
