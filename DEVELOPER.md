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
│        │       every signal QML listens to                              │
│        │                                                                │
│        ├── threadpool (QThreadPool): search, artist pages, metadata     │
│        └── dl_pool   (QThreadPool): downloads, sized by the             │
│                                     "concurrent downloads" setting      │
└────────┼────────────────────────────────────────────────────────────────┘
         ▼ imports, unchanged
   tidaler engine ── Settings, Tidal (auth/session), Download
                     (streaming, FLAC extraction, tagging)
```

One process, one window, one bridge object. QML never talks to TIDAL and
Python never builds UI.

## Why is the internal package still named `tidaler`?

Waves is a fork of Tidaler that
replaces the GUI and keeps the download engine. The Python package keeps the
upstream name so engine bug fixes merge in cleanly (the merge is one-way,
upstream to Waves). Everything Waves-specific lives in `tidaler/waves_ui/`;
user-facing state is fully separated (config lives under `~/.config/Waves`,
see `__config_dirname__` in `tidaler/__init__.py`).

## Threading model

- The **GUI thread** runs Qt's event loop, all QML, and every signal
  handler. Bridge state (`_queue`, `_objs`, caches) is only mutated here.
- **`threadpool`** runs short blocking work: login, search, album tracks,
  artist pages, browse pages.
- **`dl_pool`** runs downloads so a long album can never starve the UI of
  worker threads.

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

| State                                                     | Owner                                                                     | Why                                                |
| --------------------------------------------------------- | ------------------------------------------------------------------------- | -------------------------------------------------- |
| View routing, filters, scroll positions, preview UI state | `Main.qml` root properties                                                | UI transients; die with the window                 |
| Download queue, live tidalapi objects, page caches        | `WavesBridge` (see its class docstring)                                   | Must survive view switches and feed multiple views |
| User preferences                                          | tidaler `Settings` (`settings.json`) plus `waves.json` for GUI-only prefs | Persisted across runs                              |
| Login token                                               | tidaler `Tidal` (`token.json`)                                            | Owned by the engine                                |

## Worked example: adding a feature end to end

Say you want a "share link" action on album cards:

1. **Bridge slot** (`backend.py`): add `@Slot(str)` `def shareAlbum(self,
album_id)`, look the album up in `self._objs["album"]`, do the work on
   `self.threadpool` via `Worker`, emit a new signal with the result.
2. **Signal**: declare it near the other signals with a comment saying what
   it carries and when it fires (see `BRIDGE.md` in `tidaler/waves_ui/`).
3. **QML**: add a `function onShareAlbum(...)` handler inside Main.qml's
   `Connections { target: waves }` block, and call `waves.shareAlbum(id)`
   from the card's control line.
4. **Conventions**: reuse the shared components (ArtistLinks, DotMatrix,
   button spec constants on the root item) so the new surface matches the
   rest of the app, and keep any dynamic `Text` as `Text.PlainText` (a test
   enforces this).

## Testing and verification

```bash
poetry run pytest                       # unit tests, incl. the QML guards
poetry run python -m tidaler.waves_ui   # run the app from source
make gui-waves                          # Nuitka build -> dist/waves.app
```

The QML plain-text guard test fails if any dynamic `Text` in Main.qml can
render rich text (remote strings must never inject markup).

## More detail

- `tidaler/waves_ui/README.md`: layout, key concepts, architecture notes.
- `tidaler/waves_ui/BRIDGE.md`: reference for every bridge signal and slot
  pattern.
- `WavesBridge`'s class docstring in `backend.py`: the state model.
