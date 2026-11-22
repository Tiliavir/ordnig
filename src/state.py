"""
state.py – gemeinsame Regeln für die .ordnig-Ordner und die Exit-Codes.

Exit-Codes (für Cron, systemd und Skripte):
  0  alles erledigt
  1  Fehler bei einzelnen Dateien (Bericht lesen) oder Absturz
  2  Aufruf oder Umgebung falsch: Ablage/Eingang fehlt oder ist nicht gemountet,
     schreibgeschützt, Programme fehlen, regeln.toml fehlerhaft, Daten von neuerer Version
  3  ein anderer Lauf arbeitet gerade mit demselben .ordnig-Ordner
  4  sort: erledigt, aber Dateien warten in _Prüfen
  (Bitrot-Prüfung: 1, wenn Dateien geändert sind oder fehlen)

Es gibt einen .ordnig-Ordner für alles, was zwischen Läufen bleibt: im Eingang (00_Eingang),
weil der Container auf verschiedenen Rechnern und in verschiedenen Versionen läuft. Nur eine
Ablage ohne Eingang behält ihn im Wurzelordner. Er hält eine Sperre (lock) und seine
Formatversion (version.json).
"""
import fcntl, json, os, shutil, socket, sys, time
from pathlib import Path

OK, FEHLER, SETUP, GESPERRT, PRUEFEN = 0, 1, 2, 3, 4

INBOX, STATE = "00_Eingang", ".ordnig"
# Ordner, die nie zur Ablage gehören: eigene Daten, Papierkörbe, Snapshots und Indizes von
# NAS (Synology, QNAP, NetApp/NFS) und Betriebssystemen. Snapshots würden sonst alles doppelt zählen.
SYSTEM_DIRS = frozenset({
    STATE, ".git",
    "@eaDir", "#recycle", "#snapshot",                      # Synology
    "@Recycle", "@Recently-Snapshot", ".@__thumb",          # QNAP
    ".snapshot", ".snapshots",                              # NetApp, NFS, Btrfs/snapper
    "$RECYCLE.BIN", "System Volume Information",            # Windows
    ".Trashes", ".Spotlight-V100", ".fseventsd", ".TemporaryItems",  # macOS
    "lost+found", "OneNote_RecycleBin",
})
FORMAT = 1               # Aufbau der .ordnig-Ordner; bei jeder inkompatiblen Änderung +1
MIGRATIONS = {}          # {alte Version: Funktion(ordner)} hebt einen Ordner um eine Version an

_held = []               # offene Sperren bis Prozessende (die Sperre hängt am Dateideskriptor)


def ordnig_version():
    p = Path(__file__).resolve().parent / "versions.txt"
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("ordnig:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "dev"


def find_inbox(root: Path, eingang=None):
    """Der Eingang: --eingang, sonst <ablage>/00_Eingang, sonst genau ein <ablage>/*/00_Eingang
    (mehrere Freigaben unter /ablage gemountet). None, wenn es keinen gibt."""
    if eingang:
        return Path(eingang).resolve()
    if not root.is_dir():
        return None
    if (root / INBOX).is_dir():
        return (root / INBOX).resolve()
    found = sorted(d / INBOX for d in root.iterdir() if d.is_dir() and (d / INBOX).is_dir())
    if len(found) > 1:
        sys.exit(f"Mehrere Eingänge: {', '.join(map(str, found))} – mit --eingang einen wählen")
    return found[0].resolve() if found else None


def state_dir(root: Path, eingang=None):
    inbox = find_inbox(root, eingang)
    return inbox / STATE if inbox else root / STATE


def legacy(root: Path, folder: Path, name: str):
    """Datei am alten Ort (<ablage>/.ordnig/, bis 0.7), falls der Zustand inzwischen woanders liegt."""
    old = root / STATE / name
    return old if folder.resolve() != (root / STATE).resolve() and old.is_file() else None


def adopt(root: Path, folder: Path, name: str):
    """Übernimmt eine Datei vom alten Ort: verschieben, oder Zeilen anhängen, wenn es am neuen Ort
    schon eine gibt. Das Original bleibt als <name>.übernommen liegen (nichts geht verloren)."""
    old = legacy(root, folder, name)
    if not old:
        return
    new = folder / name
    if new.exists():
        rows = old.read_text(encoding="utf-8").splitlines(keepends=True)[1:]
        with open(new, "a", encoding="utf-8") as f:
            f.writelines(rows)
    else:
        shutil.copy2(old, new)
    keep = old.with_name(name + ".übernommen")
    if keep.exists():
        keep = old.with_name(f"{name}.übernommen-{time.strftime('%Y%m%d%H%M%S')}")
    old.rename(keep)
    print(f"{old} übernommen nach {new}", file=sys.stderr)


def open_state(folder: Path):
    """Legt den Ordner an, sperrt ihn für diesen Prozess und prüft bzw. schreibt die Formatversion.
    Bricht mit Exit-Code 3 ab, wenn ein anderer Lauf ihn hält, mit 2 bei neuerer Formatversion."""
    folder.mkdir(parents=True, exist_ok=True)
    lock(folder / "lock")
    check_format(folder)


def lock(path: Path):
    """POSIX-Sperre (lockf), wirkt auch über NFS/SMB zwischen Rechnern. Die Datei bleibt liegen;
    nach einem Absturz gibt das System die Sperre frei, es gibt nichts aufzuräumen."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = os.pread(fd, 300, 0).decode("utf-8", "replace").strip() or "unbekannt"
        os.close(fd)
        print(f"{path.parent} wird gerade von einem anderen ordnig-Lauf benutzt ({holder}).\n"
              f"Warten, bis er fertig ist.", file=sys.stderr)
        raise SystemExit(GESPERRT)
    os.ftruncate(fd, 0)
    os.pwrite(fd, f"Rechner {socket.gethostname()}, PID {os.getpid()}, "
                  f"seit {time.strftime('%Y-%m-%d %H:%M:%S')}\n".encode(), 0)
    _held.append(fd)


def check_format(folder: Path):
    vfile = folder / "version.json"
    if not vfile.exists():
        write_format(vfile, FORMAT)
        return
    try:
        fmt = int(json.loads(vfile.read_text(encoding="utf-8"))["format"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        stop(f"{vfile} unlesbar ({e}) – nicht raten, bitte prüfen.")
    if fmt > FORMAT:
        stop(f"{folder} stammt von einer neueren ordnig-Version (Format {fmt}, diese kann {FORMAT}).\n"
             f"Image aktualisieren statt mit der alten Version weiterzuarbeiten.")
    while fmt < FORMAT:
        if fmt not in MIGRATIONS:
            stop(f"{folder}: Format {fmt} kann nicht auf {FORMAT} angehoben werden.")
        MIGRATIONS[fmt](folder)
        fmt += 1
        write_format(vfile, fmt)


def write_format(vfile: Path, fmt: int):
    tmp = vfile.with_suffix(".tmp")
    tmp.write_text(json.dumps({"format": fmt, "ordnig": ordnig_version()}) + "\n", encoding="utf-8")
    tmp.replace(vfile)


def stop(msg, code=SETUP):
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def run(main):
    """Startet main(); sys.exit("Text") (fehlende Ablage, kaputte Regeln …) wird zu Exit-Code 2."""
    try:
        code = main()
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            code = SETUP
        else:
            code = e.code
    sys.exit(code or OK)
