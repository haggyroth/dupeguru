## How to build dupeGuru for macos
These instructions are for the Qt version of the UI on macOS.

*Note: The Cocoa UI of dupeGuru is hosted in a separate repo: https://github.com/arsenetar/dupeguru-cocoa and is no longer "supported".*
### Prerequisites

- [Python 3.10+][python]
- [Xcode 12.3][xcode] or just Xcode command line tools (older versions can be used if not interested in arm macs)
- [Homebrew][homebrew]
- [qt5](https://www.qt.io/)

#### Prerequisite setup
1. Install Xcode if desired
2. Install [Homebrew][homebrew], if not on the path after install (arm based Macs) create `~/.zshrc`
with `export PATH="/opt/homebrew/bin:$PATH"`. Will need to reload terminal or source the file to take
effect.
3. Install qt5 with `brew`. If you are using a version of macos without system python 3.10+ then you will
also need to install that via brew or with pyenv.

        $ brew install qt5

    NOTE: Using `brew` to install qt5 is to allow pyqt5 to build without a native wheel
    available.  If you are using an intel based mac you can probably skip this step.

4. May need to launch a new terminal to have everything working.

### With build.py
macOS ships a python3, but it is usually too old: **3.10 or newer is required**, because
`core/hash_cache.py` uses PEP 604 unions in signatures evaluated at import time, so 3.8 and 3.9
cannot import the package at all. Install a newer python via `brew` or `pyenv` if the system one
is older. If needing to build pyqt5 from source then the first line below is needed, else it may
be omitted. (Path shown is for an arm mac.)

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

NOTE: `requirements-extra.txt` pins `pyinstaller>=5.6,<6.0`, which has no build for python 3.14.
On 3.14 that install fails outright, so packaging currently needs an older interpreter. See
`HANDOFF.md` for why the pin has not simply been widened.

### Running tests
The complete test suite can be run with tox just like on linux. The test tooling lives in
`requirements-extra.txt`, but on python 3.14 that file cannot be installed (see the note above);
install just the test dependencies instead:

    $ pip install pytest'>=7,<8' pytest-cov flake8 black

[python]: http://www.python.org/
[homebrew]: https://brew.sh/
[xcode]: https://developer.apple.com/xcode/
