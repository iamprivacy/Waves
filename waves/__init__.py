#!/usr/bin/env python
import importlib.metadata
import tomllib
from pathlib import Path
from urllib.parse import urlparse

import requests

from waves.constants import REQUESTS_TIMEOUT_SEC
from waves.model.meta import ProjectInformation, ReleaseLatest

# Sentinel version returned by latest_version_information() when the update
# check could not be completed (e.g. no network). It must never compare equal
# to a real release tag, and update_available() treats it as "no update".
VERSION_CHECK_FAILED: str = "v0.0.0"


def metadata_project() -> ProjectInformation:
    result: ProjectInformation
    file_path: Path = Path(__file__)
    tmp_result: dict = {}

    paths: list[Path] = [
        file_path.parent,
        file_path.parent.parent,
        file_path.parent.parent.parent,
    ]

    for pyproject_toml_dir in paths:
        pyproject_toml_file: Path = pyproject_toml_dir / "pyproject.toml"

        if pyproject_toml_file.is_file():
            with pyproject_toml_file.open("rb") as f:
                tmp_result = tomllib.load(f)

            break

    if tmp_result:
        result = ProjectInformation(
            version=tmp_result["project"]["version"], repository_url=tmp_result["project"]["urls"]["repository"]
        )
    else:
        try:
            meta_info = importlib.metadata.metadata(name_package())
            repo_url = meta_info["Home-page"]

            if not repo_url:
                urls = meta_info.get_all("Project-URL")
                # attempt to parse, else use hardcoded fallback
                repo_url = next(
                    (url.split(", ")[1] for url in urls if url.startswith("Repository")),
                    "https://github.com/iamprivacy/Waves",
                )

            result = ProjectInformation(version=meta_info["Version"], repository_url=repo_url)
        except Exception:
            result = ProjectInformation(version="0.0.0", repository_url="https://anerroroccur.ed/sorry/for/that")

    return result


def version_app() -> str:
    metadata: ProjectInformation = metadata_project()
    version: str = metadata.version

    return version


def repository_url() -> str:
    metadata: ProjectInformation = metadata_project()
    url_repo: str = metadata.repository_url

    return url_repo


def repository_path() -> str:
    url_repo: str = repository_url()
    url_path: str = urlparse(url_repo).path

    return url_path


def latest_version_information() -> ReleaseLatest:
    release_info: ReleaseLatest
    repo_path: str = repository_path()
    url: str = f"https://api.github.com/repos{repo_path}/releases/latest"

    try:
        response = requests.get(url, timeout=REQUESTS_TIMEOUT_SEC)
        response.raise_for_status()

        release_info_json: dict = response.json()

        release_info = ReleaseLatest(
            version=release_info_json["tag_name"],
            url=release_info_json["html_url"],
            release_info=release_info_json["body"],
        )
    except (requests.RequestException, KeyError, ValueError):
        # Report the check as failed. Do NOT surface the raw API URL (the
        # Download button would otherwise open it) and use the sentinel version
        # so update_available() reports "no update available" rather than a
        # bogus one.
        release_info = ReleaseLatest(
            version=VERSION_CHECK_FAILED,
            url="",
            release_info="Could not retrieve update information. Check your internet connection.",
        )

    return release_info


def name_package() -> str:
    package_name: str = __package__ or __name__

    return package_name


# is_dev_env() cache: the verdict cannot change within a process, and the
# importlib.metadata probe behind it costs real import-time work (it used to
# run twice before the first window appeared).
_is_dev_env: bool | None = None


def is_dev_env() -> bool:
    global _is_dev_env

    if _is_dev_env is not None:
        return _is_dev_env

    package_name: str = name_package()
    result: bool = False

    # Check if package is running from source code == dev mode
    # If package is not running in Nuitka environment, try to import it from pip libraries.
    # If this also fails, it is dev mode.
    if "__compiled__" not in globals():
        try:
            importlib.metadata.version(package_name)
        except Exception:
            # If package is not installed
            result = True

    _is_dev_env = result
    return result


def name_app() -> str:
    app_name: str = name_package()
    is_dev: bool = is_dev_env()

    if is_dev:
        app_name += "-dev"

    return app_name


# __name_display__, __version__ and __config_dirname__ are computed on first
# access (PEP 562) instead of at import time: eagerly they cost two
# importlib.metadata probes plus a pyproject.toml parse before the first
# window can appear, and most launches never read __name_display__ or
# __version__ at all (the UI carries its own version literal).
#
# __config_dirname__ is the per-user state directory name (settings, token,
# log, managed ffmpeg, updater staging). A hardcoded literal, deliberately
# independent of the package name: a package rename must never move or strand
# this folder. It also must never collide with ~/.config/tidaler, where an
# installed copy of the upstream Tidaler / tidal-dl-ng app keeps ITS login
# and settings; Waves state stays fully isolated from every other app's.
def __getattr__(name: str):
    if name == "__name_display__":
        value = name_app()
    elif name == "__version__":
        value = version_app()
    elif name == "__config_dirname__":
        value = "Waves-dev" if is_dev_env() else "Waves"
    else:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)

    globals()[name] = value  # cache: later reads skip this hook entirely
    return value


def update_available() -> tuple[bool, ReleaseLatest]:
    latest_info: ReleaseLatest = latest_version_information()
    # version_app(), not the module attribute: a global read from inside the
    # module bypasses the lazy __getattr__ hook and would NameError before
    # anything else touched __version__.
    version_current: str = f"v{version_app()}"

    # A failed check (sentinel version) is NOT an available update; treating it
    # as one would show a bogus "v0.0.0 update available" prompt when offline.
    result = False if latest_info.version == VERSION_CHECK_FAILED else version_current != latest_info.version

    return result, latest_info
