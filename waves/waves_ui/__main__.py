import sys

if "--library-worker" in sys.argv[1:]:
    # The library scanner process (see waves.library_worker), decided before
    # the app's own imports: the scanner must never load Qt.
    from waves.library_worker import main as worker_main

    raise SystemExit(worker_main(sys.argv[1:]))

from .app import waves_activate

raise SystemExit(waves_activate())
