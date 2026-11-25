"""
state.py – shared rules for the .ordnig folders and the exit codes.

Exit codes (for cron, systemd and scripts):
  0  all done
  1  errors in single files (read the report) or crash
  2  wrong call or setup: archive/inbox missing or not mounted, read-only, tools missing,
     bad rules.toml, data from a newer or older version
  3  another run is using the same .ordnig folder right now
  4  sort: done, but files wait in the review folder
  (bit rot check: 1 if files changed or are missing)

There is one .ordnig folder for everything that stays between runs: in the inbox
(<archive>/Inbox or --inbox), because the container runs on different machines and in
different versions. It holds a lock and its format version (version.json).
"""
import fcntl, json, os, re, socket, sys, time
from pathlib import Path

OK, ERROR, SETUP, LOCKED, REVIEW = 0, 1, 2, 3, 4

INBOX, STATE = "Inbox", ".ordnig"
# Folders that never belong to the archive: our own data, recycle bins, snapshots and indexes of
# NAS (Synology, QNAP, NetApp/NFS) and operating systems. Snapshots would count everything twice.
SYSTEM_DIRS = frozenset({
    STATE, ".git",
    "@eaDir", "#recycle", "#snapshot",                      # Synology
    "@Recycle", "@Recently-Snapshot", ".@__thumb",          # QNAP
    ".snapshot", ".snapshots",                              # NetApp, NFS, Btrfs/snapper
    "$RECYCLE.BIN", "System Volume Information",            # Windows
    ".Trashes", ".Spotlight-V100", ".fseventsd", ".TemporaryItems",  # macOS
    "lost+found", "OneNote_RecycleBin",
})
FORMAT = 2               # layout of the .ordnig folders; +1 on every incompatible change
MIGRATIONS = {}          # {old version: function(folder, archive)} lifts a folder by one version
LOG_NAME = "run-log.csv"
LOG_PATHS = ("source", "target")
# Files of the German versions (up to 0.9): not migrated before production, so refused.
OLD_NAMES = ("regeln.toml", "protokoll.csv", "texte.json.gz", "migration_log.csv")

_held = []               # open locks until the process ends (the lock belongs to the file descriptor)


def ordnig_version():
    p = Path(__file__).resolve().parent / "versions.txt"
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("ordnig:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "dev"


def inbox_dir(root: Path, inbox=None):
    """The inbox: --inbox, else <archive>/Inbox. Never searched for."""
    return Path(inbox).resolve() if inbox else (root / INBOX).resolve()


def state_dir(root: Path, inbox=None):
    return inbox_dir(root, inbox) / STATE


def need_inbox(inbox: Path):
    """Writers keep their state in the inbox; a missing inbox is not created (wrong mount?)."""
    if not inbox.is_dir():
        stop(f"Inbox missing: {inbox} – create it or pass --inbox PATH.")


def open_state(folder: Path, root: Path = None):
    """Creates the folder, locks it for this process and checks or writes the format version.
    Stops with exit code 3 if another run holds it, with 2 for another format version."""
    folder.mkdir(parents=True, exist_ok=True)
    lock(folder / "lock")
    check_old_names(folder)
    check_format(folder, root)


def check_old_names(folder: Path):
    old = [n for n in OLD_NAMES if (folder / n).exists()]
    if old:
        stop(f"{folder} was written by a German ordnig version ({', '.join(old)}).\n"
             f"Its state is not migrated: move {folder} away; rules go to rules.toml with the English keys.")


def lock(path: Path):
    """POSIX lock (lockf), also works across machines over NFS/SMB. The file stays;
    after a crash the system releases the lock, there is nothing to clean up."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = os.pread(fd, 300, 0).decode("utf-8", "replace").strip() or "unknown"
        os.close(fd)
        print(f"{path.parent} is in use by another ordnig run ({holder}).\n"
              f"Wait until it is done.", file=sys.stderr)
        raise SystemExit(LOCKED)
    warn_local_lock(path)
    os.ftruncate(fd, 0)
    os.pwrite(fd, f"host {socket.gethostname()}, PID {os.getpid()}, "
                  f"since {time.strftime('%Y-%m-%d %H:%M:%S')}\n".encode(), 0)
    _held.append(fd)


def mount_of(path: Path, mounts="/proc/self/mounts"):
    """(file system, options) of the mount that path is on; None if unknown."""
    try:
        lines = Path(mounts).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    path, best = str(Path(path).resolve()), None
    for line in lines:
        f = line.split()
        if len(f) < 4:
            continue
        point = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), f[1])   # \040 = space
        inside = path == point or path.startswith(point.rstrip("/") + "/")
        if inside and (best is None or len(point) >= len(best[0])):
            best = (point, f[2], set(f[3].split(",")))
    return best[1:] if best else None


def warn_local_lock(path: Path, mounts="/proc/self/mounts"):
    """SMB/cifs with nobrl does not pass locks to the server: they only hold on this machine."""
    m = mount_of(path, mounts)
    if m and m[0] in ("cifs", "smb3") and "nobrl" in m[1]:
        print(f"Warning: {path.parent} is on an SMB mount with nobrl. The lock only holds on this "
              f"machine; a run on another machine is not prevented.\n"
              f"Mount without nobrl if ordnig runs on several machines.", file=sys.stderr)
        return True
    return False


def check_format(folder: Path, root: Path = None):
    vfile = folder / "version.json"
    if not vfile.exists():
        write_format(vfile, FORMAT)
        return
    try:
        fmt = int(json.loads(vfile.read_text(encoding="utf-8"))["format"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        stop(f"{vfile} unreadable ({e}) – not guessing, please check.")
    if fmt > FORMAT:
        stop(f"{folder} comes from a newer ordnig version (format {fmt}, this one knows {FORMAT}).\n"
             f"Update the image instead of continuing with the old version.")
    while fmt < FORMAT:
        if fmt not in MIGRATIONS:
            stop(f"{folder}: format {fmt} cannot be lifted to {FORMAT}; move the folder away.")
        MIGRATIONS[fmt](folder, root)
        fmt += 1
        write_format(vfile, fmt)


def to_rel(value: str, root: Path):
    """Path for the log: relative to the archive, so it survives any mount layout.
    Absolute paths outside the archive stay as they are (cannot be mapped)."""
    if not value or not os.path.isabs(value):
        return value
    p = Path(value)
    return str(p.relative_to(root)) if p.is_relative_to(root) else value


def to_abs(value: str, root: Path):
    """Path from the log for this run."""
    return value if not value or os.path.isabs(value) else str(root / value)


def write_format(vfile: Path, fmt: int):
    tmp = vfile.with_suffix(".tmp")
    tmp.write_text(json.dumps({"format": fmt, "ordnig": ordnig_version()}) + "\n", encoding="utf-8")
    tmp.replace(vfile)


def main_cli():
    """For the entry script: `state.py dir <archive> [--inbox PATH]` prints the .ordnig folder."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["dir"])
    ap.add_argument("root", type=Path)
    ap.add_argument("--inbox", type=Path, default=None)
    a = ap.parse_args()
    print(state_dir(a.root.resolve(), a.inbox))


def stop(msg, code=SETUP):
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def run(main):
    """Runs main(); sys.exit("text") (missing archive, bad rules …) becomes exit code 2."""
    try:
        code = main()
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            code = SETUP
        else:
            code = e.code
    sys.exit(code or OK)


if __name__ == "__main__":
    run(main_cli)
