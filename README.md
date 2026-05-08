# tidaler! (download from tidal)

fork of tidal_dl_ng by exislow (the original repo and account disappeared). this fork exists to maintain functionality, and add some targeted improvements.

my main focus is the cli. gui support is still included, but i don’t use it, so gui fixes are lower priority. patches are welcome.

[![Release](https://img.shields.io/github/v/release/maya-doshi/tidaler)](https://img.shields.io/github/v/release/maya-doshi/tidaler)
[![Build status](https://img.shields.io/github/actions/workflow/status/maya-doshi/tidaler/release-or-test-build.yml)](https://github.com/maya-doshi/tidaler/actions/workflows/release-or-test-build.yml)
[![Commit activity](https://img.shields.io/github/commit-activity/m/maya-doshi/tidaler)](https://img.shields.io/github/commit-activity/m/maya-doshi/tidaler)
[![License](https://img.shields.io/github/license/maya-doshi/tidaler)](https://img.shields.io/github/license/maya-doshi/tidaler)

This tool allows you to download songs and videos from TIDAL. Multithreaded and multi-chunked downloads are supported.

**Windows** Defender / **Anti Virus** software / web browser alerts, while you try to download the app binary: This is a **false positive**. Please read [this issue](https://github.com/maya-doshi/tidaler/issues/231), [PyInstaller (used by this project) statement](https://github.com/pyinstaller/pyinstaller/blob/develop/.github/ISSUE_TEMPLATE/antivirus.md) and [the alternative installation solution](https://github.com/maya-doshi/tidaler/?tab=readme-ov-file#-installation--upgrade).

**A paid TIDAL plan is required!** Audio quality varies up to HiRes Lossless / TIDAL MAX 24-bit, 192 kHz depending on the song available. Dolby Atmos is supported. You can use the command line or GUI version of this tool.

![App Image](assets/app.png)

```bash
$ tidaler --help

 Usage: tidaler [OPTIONS] COMMAND [ARGS]...

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --version  -v                                                                │
│ --help     -h        Show this message and exit.                             │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ cfg    Print or set an option. If no arguments are given, all options will   │
│        be listed. If only one argument is given, the value will be printed   │
│        for this option. To set a value for an option simply pass the value   │
│        as the second argument                                                │
│ dl                                                                           │
│ dl_fav Download from a favorites collection.                                 │
│ gui                                                                          │
│ login                                                                        │
│ logout                                                                       │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Installation / Upgrade

**Requirements**: Python version 3.12 / 3.13 (other versions might work but are not tested!)

```bash
pip install --upgrade tidaler
# If you like to have the GUI as well use this command instead
pip install --upgrade "tidaler[gui]"
```

## Usage

You can use the command line (CLI) version to download media by URL:

```bash
tidaler dl https://tidal.com/browse/track/46755209
# OR
tdn dl https://tidal.com/browse/track/46755209
```

Or by your favorites collections:

```bash
tidaler dl_fav tracks
tidaler dl_fav artists
tidaler dl_fav albums
tidaler dl_fav videos
```

You can also use the GUI:

```bash
tidaler-gui
# OR
tdng
# OR
tidaler gui
```

If you would like to use the GUI version as a binary, have a look at the
[release page](https://github.com/maya-doshi/tidaler/releases) and download the correct version for your OS.

## Features

- Download tracks, videos, albums, playlists, your favorites etc.
- Multithreaded and multi-chunked downloads
- Metadata for songs
- Adjustable audio and video download quality.
- FLAC extraction from MP4 containers
- Lyrics and album art / cover download
- Creates playlist files
- Can symlink tracks instead of having several copies, if added to different playlists

## Getting started with development

### Install dependencies

Clone this repository and install the dependencies:

```bash
# First, install Poetry. On some operating systems you need to use `pip` instead of `pipx`
pipx install --upgrade poetry
poetry install --all-extras --with dev,docs
```

The main entry points are:

```bash
tidaler/cli.py
tidaler/gui.py
```

### GUI Builder

The GUI is built with `PySide6` using the [Qt Designer](https://doc.qt.io/qt-6/qtdesigner-manual.html):

```bash
PYSIDE_DESIGNER_PLUGINS=tidaler/ui pyside6-designer
```

After all changes are saved, you need to translate the Qt Designer `*.ui` file into Python code, for instance:

```
pyside6-uic tidaler/ui/main.ui -o tidaler/ui/main.py
```

This needs to be done for each created / modified `*.ui` file accordingly.

### Build the project

To build the project use this command:

```bash
# Install virtual environment and dependencies if not already done
make install
# Build macOS GUI
make gui-macos-dmg
# OR Build Windows GUI
make gui-windows
# OR Build Linux GUI
make gui-linux
# Check build output
ls dist/
```

See the `Makefile` for all available build commands.

The CI/CD pipeline will be triggered when you open a pull request, merge to main, or when you create a new release.

To finalize the set-up for publishing to PyPi or Artifactory, see [here](https://fpgmaas.github.io/cookiecutter-poetry/features/publishing/#set-up-for-pypi).
For activating the automatic documentation with MkDocs, see [here](https://fpgmaas.github.io/cookiecutter-poetry/features/mkdocs/#enabling-the-documentation-on-github).
To enable the code coverage reports, see [here](https://fpgmaas.github.io/cookiecutter-poetry/features/codecov/).

## FAQ

### macOS Error Message: File/App is damaged and cannot be opened. You should move it to Trash

If you download an (unsigned) app from any source other than those that Apple deems trusted, the application gets an extended attribute "com.apple.Quarantine". This triggers the message: "<application> is damaged and can't be opened. You should move it to the Bin."

Remove the attribute and you can launch the application. [Source 1](https://discussions.apple.com/thread/253714860?sortBy=rank) [Source 2](https://www.reddit.com/r/macsysadmin/comments/13vu7f3/app_is_damaged_and_cant_be_opened_error_on_ventura/)

```
sudo xattr -dr com.apple.quarantine /Applications/tidaler.app/
```

Why is this app unsigned? Only developers enrolled in the paid Apple Developer Program are allowed to sign (legal) apps. Without this subscription, app signing is not possible.

Does Gatekeeper really annoy you, and you'd like to disable it completely? Follow this [link](https://iboysoft.com/tips/how-to-disable-gatekeeper-macos-sequoia.html)

### My (Windows) antivirus app XYZ says the GUI version of this app is harmful

Short answer: It is a lie. Get rid of your antivirus app.

Long answer: See [here](https://web.archive.org/web/20251213202238/https://github.com/exislow/tidal-dl-ng/issues/231)

### I get an error when `extract_flac` is enabled

Your `path_binary_ffmpeg` is probably wrong. Please read over and over again the help of this particular option until you get it right what path to put for `path_binary_ffmpeg`.

### My Linux (e.g. Ubuntu) complains that `libxcb-cursor0` is not installed

Simply install this dependency using your OS specific package manager.

Ubuntu / Debian

```bash
sudo apt install libxcb-cursor0
```

### A terminal is flashing when I run this app on Windows

Please see this issue [#103](https://web.archive.org/web/20251207002107/https://github.com/exislow/tidal-dl-ng/issues/103).

This is due to the Python `ffmpeg` library which is used and only happens on windows if `extract_flac` is activated.

### How can I download Dolby Atmos files?

You need to activate `download_dolby_atmos` in the settings. Then, if an item is available in Dolby Atmos, it will be downloaded as a Dolby Atmos file instead of a stereo audio file. Dolby Atmos is only available as 320kbps at TIDAL (you cannot adjust the quality for Dolby Atmos downloads). If an item is available in Dolby Atmos, the "Quality" column in the GUI will indicate this with `Dolby Atmos`.

## Disclaimer

- For educational purposes only. I am not liable and responsible for any damage that happens.
- You should not use this method to distribute or pirate music.
- It may be illegal to use this app in your country.

## Contributors

mainly exislow

Thanks to all, who have contributed to this project!

<a href="https://github.com/maya-doshi/tidaler/graphs/contributors"><img src="https://contributors-img.web.app/image?repo=maya-doshi/tidaler" /></a>

This project is based on:

- [cookiecutter-poetry](https://fpgmaas.github.io/cookiecutter-poetry/)
