# Developer guide

A short orientation for anyone who wants to read or change the Waves code.
Ten minutes here saves an afternoon of reverse-engineering.

## Architecture at a glance

```
┌─────────────────────────────  Waves (GUI)  ─────────────────────────────┐
│                                                                         │
│  qml/Main.qml ── the entire main window (views, cards, queue, player)   │
│  qml/SettingsPage.qml ── schema-driven settings editor                  │
│        │                                    ▲                           │
│        │ calls slots on `waves`             │ signals (queued,          │
│        ▼ (context property)                 │ GUI-thread delivery)      │
│  backend.py ── WavesBridge(QObject): every slot QML can call,           │
│        │       every signal QML listens to (the library half lives      │
│        │       in the bridge_library.py mixin)                          │
│        │                                                                │
│        ├── threadpool (QThreadPool): search, artist pages, metadata     │
│        └── dl_pool   (QThreadPool, 1 thread): the ONE download job in   │
│                                     flight; queued rows wait as specs   │
└────────┼────────────────────────────────────────────────────────────────┘
         ▼ imports, unchanged
   waves engine ──── Settings, Tidal (auth/session), Download
                     (streaming, FLAC extraction, tagging)
```

One process, one window, one bridge object. QML never talks to TIDAL and
Python never builds UI.

## Package layout and the engine/UI seam

Waves began as a fork of Tidaler and now maintains its own engine: the
download engine modules at the top of the `waves` package descend from the
upstream code (with many fixes of our own), and everything UI-specific lives
in `waves/waves_ui/`. The engine/UI split is a hard seam: engine modules stay
close to their inherited shape and UI-owned behavior lands in `waves_ui`
subclasses and helpers, which keeps the engine easy to audit. User-facing
state lives in its own `Waves` folder, independent of the package name
(`~/.config/Waves` on Linux, `~/Library/Application Support/Waves` on macOS,
`%APPDATA%\Waves` on Windows; see `__config_dirname__` in
`waves/__init__.py`).

## Threading model

- The **GUI thread** runs Qt's event loop, all QML, and every signal
  handler. Bridge state (`_objs`, caches) is only mutated here; `_queue`
  rows are also touched by download workers under `_queue_lock`, and QML
  hears about it through the coalesced GUI-thread flush
  (`_flush_queue_changes`).
- **`threadpool`** runs short blocking work: login, search, album tracks,
  artist pages, browse pages.
- **`dl_pool`** runs the one download job in flight (queue items are
  serial; track-level parallelism lives inside the engine's per-collection
  executor, sized by the "concurrent downloads" setting). Rows behind it
  wait as lightweight specs until `_pump_queue` builds their job, so a
  backlog of any size holds no Workers, no Download objects and no relays.

The pattern for anything slow, used by every slot in `backend.py`:

```python
@Slot(str)
def doThing(self, arg: str) -> None:      # called from QML
    def work():
        result = something_blocking(arg)   # worker thread
        self.thingLoaded.emit(result)      # Qt queues this to the GUI thread
    self.threadpool.start(Worker(work))
```

Signals emitted from a worker are delivered on the GUI thread automatically
(queued connection), which is why the bridge never needs locks around
QML-facing state.

## Where state lives

| State                                                     | Owner                                                                    | Why                                                |
| --------------------------------------------------------- | ------------------------------------------------------------------------ | -------------------------------------------------- |
| View routing, filters, scroll positions, preview UI state | `Main.qml` root properties                                               | UI transients; die with the window                 |
| Download queue, live tidalapi objects, page caches        | `WavesBridge` (see its class docstring)                                  | Must survive view switches and feed multiple views |
| User preferences                                          | engine `Settings` (`settings.json`) plus `waves.json` for GUI-only prefs | Persisted across runs                              |
| Login token                                               | engine `Tidal` (`token.json`)                                            | Owned by the engine                                |

## Worked example: adding a feature end to end

Say you want a "share link" action on album cards:

1. **Bridge slot** (`backend.py`): add `@Slot(str)` `def shareAlbum(self,
album_id)`, look the album up in `self._objs["album"]`, do the work on
   `self.threadpool` via `Worker`, emit a new signal with the result.
2. **Signal**: declare it near the other signals with a comment saying what
   it carries and when it fires (see `BRIDGE.md` in `waves/waves_ui/`).
3. **QML**: add a `function onShareAlbum(...)` handler inside Main.qml's
   `Connections { target: waves }` block, and call `waves.shareAlbum(id)`
   from the card's control line.
4. **Conventions**: reuse the shared components (ArtistLinks, DotMatrix,
   button spec constants on the root item) so the new surface matches the
   rest of the app, and keep any dynamic `Text` as `Text.PlainText` (a test
   enforces this).

## Testing and verification

```bash
poetry run pytest                     # unit tests, incl. the QML guards
poetry run python -m waves.waves_ui   # run the app from source
make gui-waves                        # Nuitka build -> dist/waves.app
```

Updating a checkout across the package rename (`tidaler/` to `waves/`)? Run
`pip uninstall tidaler` in the old venv, then re-run `poetry install` (or
`pip install -e ".[gui]"`). A stale editable install keeps `import tidaler`
resolving against dead code, and without the `waves` distribution installed
the app treats the run as a dev environment and opens against the separate
`Waves-dev` config folder, which looks like being signed out.

The QML plain-text guard test fails if any dynamic `Text` in Main.qml can
render rich text (remote strings must never inject markup).

## More detail

- `waves/waves_ui/README.md`: layout, key concepts, architecture notes.
- `waves/waves_ui/BRIDGE.md`: reference for every bridge signal and slot
  pattern.
- `WavesBridge`'s class docstring in `backend.py`: the state model.
