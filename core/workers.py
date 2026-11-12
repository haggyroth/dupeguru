# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""How many processes a parallel stage gets.

Two stages run in a process pool: hashing file contents (``Scanner._hash_files_parallel``) and
decoding pictures into block signatures (``core.pe.matchblock``). Both were fixed at one fewer
than the cores, with no way to change it. That is the wrong number for most of the storage
people point this at: measured on a pool of spinning disks, reading cold files cost 11.6 ms per
file with one reader, 3.8 with four and 2.0 with sixteen, so the useful count follows the
storage rather than the CPU. One setting, ``workers``, governs both, so the command line and
every preferences window say the same thing.
"""

import os
import sys

#: ProcessPoolExecutor raises ValueError above this on Windows. Left unchecked, that used to land
#: in a catch-all and quietly turn a many-core machine into a sequential one.
MAX_WINDOWS_WORKERS = 61


def worker_count(requested: int = 0) -> int:
    """Processes for a pool.

    *requested* of 0 means automatic: one fewer than the cores, and at least one. An explicit
    value is honoured even above the core count -- the workers mostly wait on the disk -- but
    never above what the platform allows.
    """
    workers = requested if requested > 0 else max(1, (os.cpu_count() or 1) - 1)
    if sys.platform == "win32":
        workers = min(workers, MAX_WINDOWS_WORKERS)
    return workers
