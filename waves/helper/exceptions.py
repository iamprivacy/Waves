class LoginError(Exception):
    pass


class MediaUnknown(Exception):
    pass


class UnknownManifestFormat(Exception):
    pass


class MediaMissing(Exception):
    pass


class DownloadIncomplete(RuntimeError):
    """A download finished without delivering everything it was asked for.

    Its message is written for the user and carries nothing but counts and
    plain words, which is what makes it the one download failure the queue row
    may repeat verbatim: any other exception can spell out a path, a URL or a
    host, and the queue is on screen. See the queue row's failure reason.

    A RuntimeError so it stays the exception this has always been to everyone
    who only needs to know that the download failed; the class is there for
    the one caller that needs to know the message is safe to show.
    """
