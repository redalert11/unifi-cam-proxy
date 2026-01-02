# Web UI Structure

This UI is split into a small shell page and lazy-loaded tab content.

## Layout

- `index.html`
  - Sidebar + tab shell only.
  - Each tab pane has a `data-partial="/static/partials/<tab>.html"` attribute.
- `partials/`
  - One HTML file per tab (`dashboard.html`, `cameras.html`, `status.html`, etc.).
- `js/app.js`
  - Tab router/loader. Loads the partial the first time a tab is opened.
  - Calls each tab module's `start()`/`stop()` for lifecycle control.
- `js/shared.js`
  - Shared helpers (`api`, `toast`, small utilities).
- `js/tabs/`
  - One JS module per tab. Export `init<tab>Tab(root, shared)`.
- `css/app.css`
  - App-specific styles that used to live inline.

## Adding a New Tab

1. Add a new tab pane in `index.html`:
   ```html
   <div class="tab-pane fade" id="new-tab" role="tabpanel" data-partial="/static/partials/new-tab.html"></div>
   ```
2. Create `partials/new-tab.html` with the markup.
3. Create `js/tabs/new-tab.js`:
   ```js
   export function initNewTab(root, shared) {
     return { start() {}, stop() {} };
   }
   ```
4. Register the module in `js/app.js` (`tabModules`).
5. Add the nav link in `index.html`.

## Lifecycle

- `start()` runs when the tab is shown (load data, attach timers, etc.).
- `stop()` runs when switching away (clear timers, stop polling).

Keep heavy polling inside `start()`/`stop()` so only active tabs do work.
