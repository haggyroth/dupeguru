"""Enable `python -m dupeguru` as an alias for the CLI."""

import os.path as op
import sys

# Run as `python -m dupeguru` from the parent directory, this package's own directory is
# not on sys.path, so a bare `import cli` raised ModuleNotFoundError and the documented
# invocation never worked. Put it first so the checkout's own modules win.
sys.path.insert(0, op.dirname(op.abspath(__file__)))

from cli import main  # noqa: E402  (must follow the sys.path fix above)

if __name__ == "__main__":
    # The guard matters beyond style: content scans use a ProcessPoolExecutor, and on
    # spawn platforms each worker re-imports the main module. Calling main() at import
    # time, as this file used to, would make every worker re-run the whole CLI.
    sys.exit(main())
