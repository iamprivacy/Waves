import logging

from PySide6 import QtCore

logger = logging.getLogger(__name__)


# Taken from https://www.pythonguis.com/tutorials/multithreading-pyside6-applications-qthreadpool/
class Worker(QtCore.QRunnable):
    """
    Worker thread

    Inherits from QRunnable to handler worker thread setup, signals and wrap-up.

    :param callback: The function callback to run on this worker thread. Supplied args and
                     kwargs will be passed through to the runner.
    :type callback: function
    :param args: Arguments to pass to the callback function
    :param kwargs: Keywords to pass to the callback function

    """

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        # Store constructor arguments (re-used for processing)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    @QtCore.Slot()  # QtCore.Slot
    def run(self):
        """
        Initialise the runner function with passed args, kwargs.
        """
        # Never let an exception escape into Qt's C++ QThreadPool: depending on
        # the PySide/Qt build that boundary can abort the whole process, and even
        # when it doesn't the failure vanishes into stderr with no context. Log it
        # here so a failed background job (a discography scan, a download, an art
        # fetch) is diagnosable instead of a silent stuck button or a hard crash.
        try:
            self.fn(*self.args, **self.kwargs)
        except Exception:
            logger.exception("Background worker crashed in %r", getattr(self.fn, "__name__", self.fn))

    def thread(self) -> QtCore.QThread:
        return QtCore.QThread.currentThread()
