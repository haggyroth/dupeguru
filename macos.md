## How to build dupeGuru for macos
These instructions are for the Qt version of the UI on macOS.

*Note: The Cocoa UI of dupeGuru is hosted in a separate repo: https://github.com/arsenetar/dupeguru-cocoa and is no longer "supported".*
### Prerequisites

- [Python 3.10+][python]
- [Xcode 12.3][xcode] or just Xcode command line tools (older versions can be used if not interested in arm macs)
- [Homebrew][homebrew]

#### Prerequisite setup
1. Install Xcode if desired
2. Install [Homebrew][homebrew], if not on the path after install (arm based Macs) create `~/.zshrc`
with `export PATH="/opt/homebrew/bin:$PATH"`. Will need to reload terminal or source the file to take
effect.
3. If you are using a version of macos without system python 3.10+ you will need to install
one via `brew` or with `pyenv`.

    NOTE: Qt itself does **not** need to be installed. PyQt6 is the default binding and ships
    manylinux/macOS wheels for both arm64 and x86_64, so `pip install -r requirements.txt` is
    enough. A `brew install qt5` step used to be required here because PyQt5 had no arm64 wheel
    and had to be built from source; that is only relevant if you deliberately switch to the
    PyQt5 fallback.

4. May need to launch a new terminal to have everything working.

### With build.py
macOS ships a python3, but it is usually too old: **3.10 or newer is required**, because
`core/hash_cache.py` uses PEP 604 unions in signatures evaluated at import time, so 3.8 and 3.9
cannot import the package at all. Install a newer python via `brew` or `pyenv` if the system one
is older.

The first line below is only needed if you are building the PyQt5 fallback from source; with the
default PyQt6 it can be omitted. (Path shown is for an arm mac.)

    $ export PATH="/opt/homebrew/opt/qt/bin:$PATH"
    $ cd <dupeGuru directory>
    $ python3 -m venv ./env
    $ source ./env/bin/activate
    $ pip install -r requirements.txt
    $ pip install setuptools
    $ python build.py
    $ python run.py

`setuptools` is installed explicitly because `build.py` shells out to `setup.py build_ext`, and
virtual environments stopped seeding setuptools in python 3.12. Skipping it fails the build with
`ModuleNotFoundError: No module named 'setuptools'`.

### Generate OSX Packages
The extra requirements need to be installed to run packaging: `pip install -r requirements-extra.txt`.
Run the following in the respective virtual environment.

    $ python package.py

This will produce a dupeGuru.app in the dist folder.

This produces `dist/dupeguru.app`, with `CFBundleShortVersionString` and `CFBundleVersion`
stamped from `core.__version__`. To wrap it in a disk image:

    $ python -c "from hscommon.build import build_dmg; build_dmg('dist/dupeguru.app', 'dist-dmg')"

CI does both automatically -- see the `applications` job in `.github/workflows/packaging.yml`.

### Running tests
The complete test suite can be run with tox just like on linux. The test tooling lives in
`requirements-extra.txt`:

    $ pip install -r requirements-extra.txt
    $ pytest core hscommon qt tests

[python]: http://www.python.org/
[homebrew]: https://brew.sh/
[xcode]: https://developer.apple.com/xcode/
