# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The digest dupeGuru deletes on must not be one you can forge (issue #189).

dupeGuru decides two files are identical when their digests match, and then offers to delete one
of them. That makes the hash a safety boundary rather than a performance detail.

On the xxhash path a collision is a probability argument -- roughly 10^-27 for a corpus of
678,000 files, which is not worth engineering against. The fallback used when xxhash cannot be
imported was md5, and there the argument does not apply at all: md5 collisions are
*constructible*, and colliding pairs have been published since 2004. One of them is used below,
and it is not a curiosity -- both halves are 128 bytes, so they share a size, land in the same
bucket, and get compared.

The fallback should not normally run. xxhash is pinned in requirements.txt and setup.cfg and
ships in the frozen builds; this path exists for a source checkout whose install did not
complete, which is exactly the situation where nobody reads the console.
"""

import hashlib
import subprocess
import sys
import textwrap

import pytest

from core import fs, hash_cache

#: Wang et al., 2004. Two 128-byte blocks with the same md5 digest, differing in six bytes.
#: Equal length matters: dupeGuru buckets by size before it compares anything, so a collision
#: between different-sized inputs could never reach the comparison in the first place.
MD5_COLLISION_A = bytes.fromhex(
    "d131dd02c5e6eec4693d9a0698aff95c2fcab58712467eab4004583eb8fb7f89"
    "55ad340609f4b30283e488832571415a085125e8f7cdc99fd91dbdf280373c5b"
    "d8823e3156348f5bae6dacd436c919c6dd53e2b487da03fd02396306d248cda0"
    "e99f33420f577ee8ce54b67080a80d1ec69821bcb6a8839396f9652b6ff72a70"
)
MD5_COLLISION_B = bytes.fromhex(
    "d131dd02c5e6eec4693d9a0698aff95c2fcab50712467eab4004583eb8fb7f89"
    "55ad340609f4b30283e4888325f1415a085125e8f7cdc99fd91dbd7280373c5b"
    "d8823e3156348f5bae6dacd436c919c6dd53e23487da03fd02396306d248cda0"
    "e99f33420f577ee8ce54b67080280d1ec69821bcb6a8839396f965ab6ff72a70"
)


class TestTheCollisionIsReal:
    """Establishes the premise, so the rest is not an argument from authority."""

    def test_the_two_blocks_are_different_files(self):
        assert MD5_COLLISION_A != MD5_COLLISION_B

    def test_they_are_the_same_size(self):
        # Otherwise dupeGuru would never compare them: it buckets by size first.
        assert len(MD5_COLLISION_A) == len(MD5_COLLISION_B) == 128

    def test_md5_cannot_tell_them_apart(self):
        assert hashlib.md5(MD5_COLLISION_A).digest() == hashlib.md5(MD5_COLLISION_B).digest()


class TestTheHashInUseIsNotFooled:
    def test_the_active_hasher_distinguishes_them(self):
        assert fs.hasher(MD5_COLLISION_A).digest() != fs.hasher(MD5_COLLISION_B).digest()

    def test_the_cache_hasher_distinguishes_them(self):
        first, second = hash_cache._make_hasher(), hash_cache._make_hasher()
        first.update(MD5_COLLISION_A)
        second.update(MD5_COLLISION_B)
        assert first.digest() != second.digest()

    def test_a_scan_does_not_call_them_duplicates(self, tmp_path):
        # The end of the chain, and the only assertion that speaks in the application's terms.
        from core.scanner import Scanner, ScanType

        (tmp_path / "a.bin").write_bytes(MD5_COLLISION_A)
        (tmp_path / "b.bin").write_bytes(MD5_COLLISION_B)
        scanner = Scanner()
        scanner.scan_type = ScanType.CONTENTS
        assert scanner.get_dupe_groups(list(fs.get_files(tmp_path))) == []


class TestTheFallback:
    """Checked in a subprocess with xxhash hidden, because it is chosen at import time."""

    def _in_fallback(self, body):
        script = (
            textwrap.dedent(
                """
            import builtins, sys
            real = builtins.__import__
            def no_xxhash(name, *a, **k):
                if name == "xxhash":
                    raise ImportError("simulated")
                return real(name, *a, **k)
            builtins.__import__ = no_xxhash
            from core import fs, hash_cache
            """
            )
            + textwrap.dedent(body)
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=".")
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def test_the_fallback_is_not_md5(self):
        assert self._in_fallback("print(fs.HASH_ALGORITHM)") != "md5"

    def test_both_modules_choose_the_same_fallback(self):
        # They maintain separate caches. Disagreeing would mean two digests of the same file
        # under two algorithms, which is the confusion HASH_ALGORITHM exists to prevent.
        out = self._in_fallback("print(fs.HASH_ALGORITHM, hash_cache.HASH_ALGORITHM)")
        first, second = out.split()
        assert first == second

    def test_the_fallback_still_emits_sixteen_bytes(self):
        # Everything downstream stores and compares these; a different width would silently
        # change the cache schema's meaning.
        assert self._in_fallback("print(len(fs.hasher(b'x').digest()))") == "16"

    def test_the_fallback_resists_the_collision(self):
        out = self._in_fallback(
            "a = bytes.fromhex('%s')\n"
            "b = bytes.fromhex('%s')\n"
            "print(fs.hasher(a).digest() != fs.hasher(b).digest())" % (MD5_COLLISION_A.hex(), MD5_COLLISION_B.hex())
        )
        assert out == "True"

    def test_the_fallback_works_incrementally(self):
        # _calc_digest feeds a file in 1 MB chunks, so the object has to accept update() as well
        # as being constructed with the whole input.
        out = self._in_fallback(
            "h = fs.hasher()\n" "h.update(b'a')\n" "h.update(b'b')\n" "print(h.digest() == fs.hasher(b'ab').digest())"
        )
        assert out == "True"


class TestCachesNoticeTheChange:
    def test_the_recorded_algorithm_matches_the_one_in_use(self):
        # The two are written to the cache as a pair; a mismatch would leave digests from one
        # algorithm being served to another.
        assert fs.HASH_ALGORITHM == hash_cache.HASH_ALGORITHM

    def test_a_cache_from_another_algorithm_is_discarded(self, tmp_path):
        # The guarantee that makes changing the fallback safe: old md5 digests are dropped
        # rather than compared against new ones.
        db = hash_cache.HashCache()
        db.connect(str(tmp_path / "h.db"))
        db.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('hash_algorithm', 'md5')")
        db.conn.commit()
        db.close()

        again = hash_cache.HashCache()
        again.connect(str(tmp_path / "h.db"))
        row = again.conn.execute("SELECT value FROM meta WHERE key='hash_algorithm'").fetchone()
        again.close()
        assert row[0] == hash_cache.HASH_ALGORITHM, "the cache kept its md5 marking"


@pytest.mark.parametrize("name", ["md5", "sha1"])
def test_no_broken_hash_is_referenced_in_the_hashing_code(name):
    """A guard against the fallback drifting back.

    Not style policing: both of these have practical collision attacks, and the whole point of
    this module is that dupeGuru deletes on digest equality.
    """
    for path in ("core/fs.py", "core/hash_cache.py"):
        with open(path, encoding="utf-8") as fp:
            code = [line for line in fp if not line.lstrip().startswith("#")]
        assert f"hashlib.{name}" not in "".join(code), f"{path} reaches for {name}"
