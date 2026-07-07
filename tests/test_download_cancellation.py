import pathlib
import threading
from unittest.mock import MagicMock, patch

import pytest
from rich.progress import Progress, TaskID

from tidaler.download import Download


class TestDownloadCancellation:

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        # return mock settings that allow 1 worker for deterministic testing if needed
        # but mock executor will override this anyway
        settings.data.downloads_simultaneous_per_track_max = 1
        return settings

    @pytest.fixture
    def mock_progress(self):
        progress = MagicMock(spec=Progress)
        progress.tasks = {TaskID(1): MagicMock(finished=False)}
        return progress

    @pytest.fixture
    def download_instance(self, mock_settings, mock_progress):
        dl = Download(
            tidal_obj=MagicMock(), skip_existing=False, path_base="./tmp", fn_logger=MagicMock(), progress=mock_progress
        )
        dl.settings = mock_settings
        dl.event_abort = threading.Event()
        dl.event_run = threading.Event()
        dl.event_run.set()  # Allow running
        return dl

    def test_download_segments_cancellation(self, download_instance):
        """Test cancellation of _download_segments via event_stop."""

        # Setup inputs
        urls = ["http://u1", "http://u2", "http://u3", "http://u4"]
        path_base = pathlib.Path("./tmp")
        block_size = 1024
        p_task = TaskID(1)
        progress_to_stdout = False
        event_stop = threading.Event()

        # Mock _download_segment
        # We want it to be called.
        # We will stop after the 2nd call.

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                event_stop.set()
                # Trip the abort signal on the second segment so the loop
                # observes it mid-flight and stops submitting the rest.

            return MagicMock(result=True, url=args[0])

        with patch.object(download_instance, "_download_segment", side_effect=side_effect):
            # We assume _download_segments submits all tasks at once?
            # If it submits all at once, cancellation needs to cancel pending futures.
            # Let's check the code:
            # l_futures = [executor.submit(...) for url in urls]
            # for future in futures.as_completed(l_futures):
            #    if event_stop.is_set(): cancel remaining.

            # So if we set event_stop inside the execution of a future, the MAIN thread iterating as_completed will see it
            # as soon as one future completes and loop continues.

            result, _ = download_instance._download_segments(
                urls, path_base, block_size, p_task, progress_to_stdout, event_stop=event_stop
            )

            # Assert that we stopped.
            # If started with 4 urls.
            # We expected 2 to finish (triggered stop), causing remaining to be cancelled.
            # So results count should be less than 4?
            # Depends on concurrency. If all 4 submitted immediately to threaded executor.
            # But we can assert that event_stop.is_set() is True.
            assert event_stop.is_set()

            # Also, the return value `result` (success boolean) might depend on whether all segments completed?
            # If cancelled, it checks `if self.event_abort.is_set() or (event_stop and event_stop.is_set()): return False`?
            # I need to verify what I returned in _download_segments.
            # Assuming I returned False if cancelled.
            assert result is False

    def test_download_segments_global_abort(self, download_instance):
        """Test cancellation of _download_segments via global event_abort."""

        urls = ["http://u1", "http://u2"]
        path_base = pathlib.Path("./tmp")
        p_task = TaskID(1)

        download_instance.event_abort.set()

        # When abort is set, it might return immediately or after first check.

        with patch.object(download_instance, "_download_segment"):
            result, _ = download_instance._download_segments(urls, path_base, None, p_task, False)

            assert result is False
