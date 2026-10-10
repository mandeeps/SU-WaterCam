"""Power-cut-safe JSON config files (runtime_config.json, lora_config.json).

A unit can lose power at any moment, and brownouts cluster around boot, when these
files are written. Writing in place (truncate, then dump) can leave an empty or
half-written file, which used to make the unit fall back to defaults and then save
those defaults over its real config.

- write_json(): write a temp file, fsync it, keep the current good file as
  <name>.bak (a hard link, so no extra data is written), rename the temp file over the
  original, fsync the directory. Readers see the old file or the new one, never a part.
- read_json(): read the file; if it is unreadable, set it aside as <name>.corrupt-<time>,
  restore <name>.bak and return that. Raises if neither is usable, so callers never
  mistake a damaged file for an empty one.
- locked(): an exclusive flock on <name>.lock for read-modify-write. The lock is a
  separate file because the config file itself is replaced on every write.
"""
import contextlib
import fcntl
import json
import os
import time


class ConfigUnreadable(ValueError):
    """Neither the config file nor its backup could be parsed."""


def _parse(path):
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return data


def _fsync_dir(dirpath):
    try:
        fd = os.open(dirpath or ".", os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@contextlib.contextmanager
def locked(path):
    """Hold an exclusive lock for a read-modify-write of path."""
    fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR, 0o664)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)                    # closing releases the lock


def read_json(path):
    """Return the config at path as a dict, recovering from <path>.bak if it is damaged.

    Raises FileNotFoundError if neither exists and ConfigUnreadable if neither parses.
    """
    try:
        return _parse(path)
    except FileNotFoundError:
        if not os.path.exists(path + ".bak"):
            raise
        problem = "missing"
    except (ValueError, UnicodeDecodeError) as e:
        problem = f"unreadable ({e})"
    try:
        data = _parse(path + ".bak")
    except (OSError, ValueError, UnicodeDecodeError):
        raise ConfigUnreadable(f"{path} is {problem} and has no usable backup") from None
    print(f"⚠️ {path} is {problem}; restoring it from {path}.bak")
    if os.path.exists(path):
        try:
            os.replace(path, f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
        except OSError:
            pass
    try:
        write_json(path, data)
    except OSError as e:
        print(f"⚠️ could not restore {path}: {e}")
    return data


def write_json(path, data, mode=0o600):
    """Atomically replace path with data, keeping the previous good file as <path>.bak."""
    dirpath = os.path.dirname(os.path.abspath(path))
    tmp = f"{path}.tmp.{os.getpid()}"
    st = None
    try:
        st = os.stat(path)
        mode = st.st_mode & 0o777
    except OSError:
        pass
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if st is not None and os.geteuid() == 0 and (st.st_uid, st.st_gid) != (0, 0):
            os.chown(tmp, st.st_uid, st.st_gid)     # a root writer must not take pi's file
        # Keep the current file as the backup, but only if it is good: a damaged file
        # must never replace a good backup.
        try:
            _parse(path)
            os.link(path, tmp + ".bak")
            os.replace(tmp + ".bak", path + ".bak")
        except (OSError, ValueError, UnicodeDecodeError):
            pass
        os.replace(tmp, path)
    except BaseException:
        for leftover in (tmp, tmp + ".bak"):
            try:
                os.unlink(leftover)
            except OSError:
                pass
        raise
    _fsync_dir(dirpath)
