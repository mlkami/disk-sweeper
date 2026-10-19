#!/usr/bin/env python3
"""disk-sweeper scanner: measure every drive once and write digests for the role agents.

Read-only. Standard library only (Python 3.8+). Never follows symlinks or junctions.

usage:
    python -I scan.py --out DIR [--root PATH ...] [--min-dir-mb 100] [--min-file-mb 150] [--max-rows 200]

Writes to DIR:
    summary.md          drives, elapsed time, totals per role, scan errors
    digest-<role>.md    what each role agent reads (dev, apps, system, data)
    inventory.json      everything recorded, for spot checks
    apps.json           installed applications (for orphan detection)
    processes.json      running processes (for lock checks)
"""
import argparse
import json
import os
import re
import stat
import subprocess
import sys
import time
from datetime import datetime

IS_WINDOWS = os.name == "nt"
ROLES = ("dev", "apps", "system", "data")

# Reparse tags that are links. Other tags (cloud files, dedup, ...) are real data and are walked.
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
IO_REPARSE_TAG_SYMLINK = 0xA000000C

# Directories that are build output or environments. Only the outermost one is recorded.
MARKERS = {
    "node_modules", ".venv", "venv", "target", "__pycache__", ".gradle", ".next", ".nuxt", ".turbo",
    ".parcel-cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "build", "dist",
    "bower_components", ".dart_tool", ".angular", ".svelte-kit",
}
# For ambiguous names, a manifest next to the directory tells whether it is really build output.
MANIFESTS = (
    "Cargo.toml", "package.json", "pyproject.toml", "setup.py", "build.gradle", "build.gradle.kts",
    "pom.xml", "CMakeLists.txt", "Makefile", "tauri.conf.json",
)
AMBIGUOUS_MARKERS = {"target", "build", "dist"}

# First match wins. Paths are normalized: lower case, "/" separators, home directory as "~".
ROLE_RULES = [
    ("system", r"^[a-z]:/(windows|windows\.old|\$recycle\.bin|system volume information|recovery"
               r"|\$windows\.~bt|\$windows\.~ws|\$sysreset|perflogs)(/|$)"),
    ("system", r"^[a-z]:/(hiberfil|pagefile|swapfile)\.sys$"),
    ("system", r"^~/appdata/local/(temp|crashdumps)(/|$)"),
    ("system", r"^[a-z]:/programdata/microsoft/windows/wer(/|$)"),
    ("system", r"^~/\.local/share/trash(/|$)|/\.trash(-\d+)?(/|$)|^~/\.trash(/|$)|/\.trashes(/|$)"),
    ("system", r"^/(var|tmp|boot|swapfile|swap\.img)(/|$)"),
    ("dev", r"^~/(\.npm|\.pnpm-store|\.yarn|\.cargo|\.rustup|\.gradle|\.m2|\.nuget|\.dotnet|go|\.vscode|\.cursor"
            r"|\.ollama|\.lmstudio|\.docker|\.android|\.bun|\.deno|\.local/share/pnpm)(/|$)"),
    ("dev", r"^~/\.cache/(pip|uv|pnpm|yarn|go-build|bazel|puppeteer|ms-playwright|huggingface|torch|node-gyp"
            r"|electron|electron-builder|pre-commit|paru|yay)(/|$)"),
    ("dev", r"^~/appdata/local/(pip|uv|ms-playwright|go-build|npm-cache|pnpm|pnpm-cache|yarn|docker|electron"
            r"|electron-builder|node-gyp|programs/python|programs/microsoft vs code|nvidia/dxcache)(/|$)"),
    ("dev", r"^~/appdata/roaming/(npm|npm-cache|uv|nvm)(/|$)"),
    ("dev", r"^[a-z]:/program files( \(x86\))?/(windows kits|microsoft visual studio|microsoft sdks|dotnet|android"
            r"|nodejs|git|docker|python\d*)(/|$)"),
    ("dev", r"^[a-z]:/programdata/(package cache|docker|chocolatey|microsoft/visualstudio)(/|$)"),
    ("dev", r"^~/(documents/projects|projects|source|src|repos|dev|code|workspace|work)(/|$)"),
    ("data", r"(^|/)(steamapps|steamlibrary|riot games|epic games|ubisoft game launcher|ea games|xboxgames"
             r"|gog galaxy|battle\.net|\.minecraft|modrinthapp|curseforge)(/|$)"),
    ("data", r"^[a-z]:/program files( \(x86\))?/(steam|epic games|riot games|riot vanguard|ubisoft"
             r"|electronic arts|gog galaxy|battle\.net)(/|$)"),
    ("apps", r"^~/appdata/(roaming|local|locallow)(/|$)"),
    ("apps", r"^[a-z]:/(programdata|program files|program files \(x86\))(/|$)"),
    ("apps", r"^~/(\.cache|\.config|\.local/share|library)(/|$)|^/opt(/|$)|^/applications(/|$)"),
    ("data", r""),  # user files, other drives, anything else
]
_COMPILED = [(role, re.compile(rx)) for role, rx in ROLE_RULES]


def normalize(path, home):
    p = path[4:] if path.startswith("\\\\?\\") else path
    p = p.replace("\\", "/")
    if home and (p.lower() == home or p.lower().startswith(home + "/")):
        p = "~" + p[len(home):]
    return p.lower() if IS_WINDOWS or p.startswith("~") else p


def classify(norm):
    low = norm.lower()
    for role, rx in _COMPILED:
        if rx.search(low):
            return role
    return "data"


def display(path):
    for prefix in ("\\\\?\\", "\\??\\"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def as_list(data):
    return [data] if isinstance(data, dict) else list(data or [])


def default_roots():
    if IS_WINDOWS:
        import ctypes
        roots = []
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                root = f"{chr(65 + i)}:\\"
                if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
                    roots.append(root)
        return roots
    if sys.platform == "darwin":
        roots = ["/"]
        if os.path.isdir("/Volumes"):
            for name in sorted(os.listdir("/Volumes")):
                p = os.path.join("/Volumes", name)
                if os.path.ismount(p) and os.path.realpath(p) != "/":
                    roots.append(p)
        return roots
    real_fs = {"ext2", "ext3", "ext4", "btrfs", "xfs", "zfs", "f2fs", "ntfs", "ntfs3", "fuseblk", "vfat",
               "exfat", "jfs", "reiserfs", "bcachefs"}
    roots, seen = [], set()
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                dev, mnt, fstype = line.split()[:3]
                mnt = mnt.replace("\\040", " ")
                if fstype in real_fs and dev.startswith("/dev/") and dev not in seen:
                    seen.add(dev)
                    roots.append(mnt)
    except OSError:
        roots = ["/"]
    return roots or ["/"]


class Scanner:
    def __init__(self, min_dir, min_file, home):
        self.min_dir = min_dir
        self.min_file = min_file
        self.home = home
        self.dirs = []      # (path, size, files, mtime_min, mtime_max)
        self.markers = []   # (path, name, size, files, mtime_max, manifest)
        self.big_files = [] # (path, size, mtime)
        self.links = []     # (path, target)
        self.errors = []    # (path, message)
        self.error_count = 0
        self.seen_inodes = set()
        self.n_dirs = 0
        self.n_files = 0
        self._last_progress = time.time()

    def _is_link(self, entry, st):
        if entry.is_symlink():
            return True
        if IS_WINDOWS:
            if getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                tag = getattr(st, "st_reparse_tag", 0)
                return tag in (IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK) or tag == 0
        return False

    def _error(self, path, exc):
        self.error_count += 1
        if len(self.errors) < 200:
            self.errors.append((display(path), f"{type(exc).__name__}: {exc.strerror or exc}"))

    def _progress(self, path):
        now = time.time()
        if now - self._last_progress >= 15:
            self._last_progress = now
            print(f"[scan] {self.n_dirs:,} dirs, {self.n_files:,} files ... {display(path)[:120]}",
                  file=sys.stderr, flush=True)

    def scan_root(self, root):
        start = root
        if IS_WINDOWS and not root.startswith("\\\\?\\"):
            start = "\\\\?\\" + os.path.abspath(root)
        try:
            root_dev = os.lstat(start).st_dev
        except OSError as e:
            self._error(start, e)
            return 0
        # frame: [path, iterator, size, files, mtime_min, mtime_max, marker_name, inside_marker]
        stack = []

        def push(path, marker_name, inside_marker):
            try:
                it = os.scandir(path)
            except OSError as e:
                self._error(path, e)
                return
            self.n_dirs += 1
            stack.append([path, it, 0, 0, None, None, marker_name, inside_marker])

        push(start, None, False)
        total = 0
        while stack:
            frame = stack[-1]
            try:
                entry = next(frame[1], None)
            except OSError as e:
                self._error(frame[0], e)
                entry = None
            if entry is None:
                frame[1].close()
                stack.pop()
                path, _, size, files, mtmin, mtmax, marker_name, inside = frame
                if size >= self.min_dir:
                    self.dirs.append((display(path), size, files, mtmin, mtmax))
                if marker_name and not inside and size >= 1024 * 1024:
                    parent = os.path.dirname(path.rstrip("\\/"))
                    manifest = ",".join(m for m in MANIFESTS if os.path.exists(os.path.join(parent, m)))
                    # "build", "dist" and "target" are common names; without a manifest they are not build output.
                    if manifest or marker_name not in AMBIGUOUS_MARKERS:
                        self.markers.append((display(path), marker_name, size, files, mtmax, manifest))
                if stack:
                    parent_frame = stack[-1]
                    parent_frame[2] += size
                    parent_frame[3] += files
                    if mtmin is not None:
                        parent_frame[4] = mtmin if parent_frame[4] is None else min(parent_frame[4], mtmin)
                        parent_frame[5] = mtmax if parent_frame[5] is None else max(parent_frame[5], mtmax)
                else:
                    total = size
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as e:
                self._error(entry.path, e)
                continue
            if self._is_link(entry, st):
                try:
                    target = os.readlink(entry.path)
                except (OSError, ValueError):
                    target = "?"
                self.links.append((display(entry.path), display(target)))
                continue
            if stat.S_ISDIR(st.st_mode):
                if not IS_WINDOWS and st.st_dev != root_dev:
                    continue  # another filesystem; scanned as its own root when it is a real drive
                self._progress(entry.path)
                name = entry.name
                inside = frame[7] or bool(frame[6])
                push(entry.path, name if name in MARKERS else None, inside)
                continue
            size = st.st_size
            if not IS_WINDOWS and st.st_nlink > 1:
                key = (st.st_dev, st.st_ino)
                if key in self.seen_inodes:
                    continue
                self.seen_inodes.add(key)
            self.n_files += 1
            frame[2] += size
            frame[3] += 1
            mt = st.st_mtime
            frame[4] = mt if frame[4] is None else min(frame[4], mt)
            frame[5] = mt if frame[5] is None else max(frame[5], mt)
            if size >= self.min_file:
                self.big_files.append((display(entry.path), size, mt))
        return total


def installed_apps():
    apps = []
    if IS_WINDOWS:
        import winreg
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, sub in keys:
            try:
                k = winreg.OpenKey(hive, sub)
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(k)[0]):
                try:
                    sk = winreg.OpenKey(k, winreg.EnumKey(k, i))
                except OSError:
                    continue
                item = {"source": "registry"}
                for field in ("DisplayName", "DisplayVersion", "Publisher", "InstallLocation", "UninstallString"):
                    try:
                        item[field] = str(winreg.QueryValueEx(sk, field)[0])
                    except OSError:
                        pass
                if item.get("DisplayName"):
                    apps.append(item)
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-AppxPackage | Select-Object Name,Version,InstallLocation | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace").stdout
            for p in as_list(json.loads(out or "[]")):
                apps.append({"source": "appx", "DisplayName": p.get("Name"), "DisplayVersion": p.get("Version"),
                             "InstallLocation": p.get("InstallLocation")})
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            apps.append({"source": "error", "DisplayName": f"Get-AppxPackage failed: {e}"})
        return apps
    commands = [
        ("pacman", ["pacman", "-Qq"]), ("dpkg", ["dpkg-query", "-W", "-f", "${Package}\n"]), ("rpm", ["rpm", "-qa"]),
        ("flatpak", ["flatpak", "list", "--app", "--columns=application"]), ("snap", ["snap", "list"]),
        ("brew", ["brew", "list"]),
    ]
    for source, cmd in commands:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        apps += [{"source": source, "DisplayName": line.strip()} for line in out.splitlines() if line.strip()]
    if os.path.isdir("/Applications"):
        apps += [{"source": "Applications", "DisplayName": n} for n in sorted(os.listdir("/Applications"))]
    return apps


def running_processes():
    if IS_WINDOWS:
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process | Where-Object Path | Select-Object Name,Path -Unique | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace").stdout
            return as_list(json.loads(out or "[]"))
        except (OSError, ValueError, subprocess.SubprocessError):
            return []
    procs = []
    if os.path.isdir("/proc"):
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    procs.append({"Name": pid, "Path": os.readlink(f"/proc/{pid}/exe")})
                except OSError:
                    pass
    return procs


def fmt_size(n):
    return f"{n / 1024 ** 3:.2f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def fmt_date(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t else "-"


def write_outputs(out, sc, drives, elapsed, max_rows):
    home = sc.home
    role_of = lambda p: classify(normalize(p, home))
    dirs = [d + (role_of(d[0]),) for d in sc.dirs]
    files = [f + (role_of(f[0]),) for f in sc.big_files]
    links = [link + (role_of(link[0]),) for link in sc.links]
    markers = sorted(sc.markers, key=lambda m: -m[2])

    # A parent whose single child holds nearly all of it adds nothing to a digest: the child's path shows it.
    size_of = {d[0]: d[1] for d in dirs}
    collapsed = set()
    for path, size, *_ in dirs:
        parent = os.path.dirname(path.rstrip("\\/"))
        if parent != path and parent in size_of and size >= 0.95 * size_of[parent]:
            collapsed.add(parent)

    for role in ROLES:
        rows = sorted((d for d in dirs if d[5] == role and d[0] not in collapsed), key=lambda d: -d[1])
        lines = [f"# digest: {role}", "",
                 f"Directories at or above the size threshold, largest first ({len(rows)} total, "
                 f"showing up to {max_rows}). A parent is left out when one child holds 95% or more of it. "
                 "Sizes are logical sizes from one scan.", "",
                 "| path | size | files | oldest | newest |", "|---|---|---|---|---|"]
        for path, size, n, mtmin, mtmax, _ in rows[:max_rows]:
            lines.append(f"| `{path}` | {fmt_size(size)} | {n:,} | {fmt_date(mtmin)} | {fmt_date(mtmax)} |")
        if role == "dev":
            lines += ["", f"## Build output and environments ({len(markers)}, outermost only, any role)", "",
                      "`area` is the role of the path. Inside an installed tool (global npm packages, Programs, "
                      "Program Files) these are part of the tool, not build output.", "",
                      "| path | kind | size | files | newest | manifest next to it | area |",
                      "|---|---|---|---|---|---|---|"]
            for path, name, size, n, mtmax, manifest in markers[:max_rows]:
                lines.append(f"| `{path}` | {name} | {fmt_size(size)} | {n:,} | {fmt_date(mtmax)} "
                             f"| {manifest or '-'} | {role_of(path)} |")
        rfiles = sorted((f for f in files if f[3] == role), key=lambda f: -f[1])
        lines += ["", f"## Large files ({len(rfiles)})", "", "| path | size | modified |", "|---|---|---|"]
        for path, size, mt, _ in rfiles[:max_rows]:
            lines.append(f"| `{path}` | {fmt_size(size)} | {fmt_date(mt)} |")
        rlinks = [link for link in links if link[2] == role]
        if rlinks:
            lines += ["", f"## Links not followed ({len(rlinks)})", "",
                      "A target that no longer exists is worth reporting.", "", "| link | target | target exists |",
                      "|---|---|---|"]
            for path, target, _ in rlinks[:max_rows]:
                exists = "yes" if target != "?" and os.path.exists(target) else "no"
                lines.append(f"| `{path}` | `{target}` | {exists} |")
        if role == "system":
            lines += ["", f"## Unreadable directories ({sc.error_count} total, first {len(sc.errors)})", ""]
            lines += [f"- `{p}`: {msg}" for p, msg in sc.errors]
        with open(os.path.join(out, f"digest-{role}.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    counts = {r: sum(1 for d in dirs if d[5] == r) for r in ROLES}
    lines = ["# disk-sweeper scan summary", "",
             f"- finished: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"- elapsed: {elapsed:.0f} s, {sc.n_dirs:,} directories, {sc.n_files:,} files",
             f"- unreadable directories: {sc.error_count} (see digest-system.md)", "",
             "| drive | total | free | scanned (logical) |", "|---|---|---|---|"]
    for root, total, free, scanned in drives:
        lines.append(f"| `{root}` | {fmt_size(total)} | {fmt_size(free)} | {fmt_size(scanned)} |")
    lines += ["", "| role | directories recorded | digest |", "|---|---|---|"]
    lines += [f"| {r} | {counts[r]} | `digest-{r}.md` |" for r in ROLES]
    lines += ["", "Notes:",
              "- Sizes are logical sizes. Compressed or sparse files take less space on disk.",
              "- On Windows, hard links are counted every time (for example `C:\\Windows\\WinSxS` looks larger"
              " than it is). On Linux and macOS, each hard-linked file is counted once.",
              "- Symlinks and junctions are listed but never followed."]
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    inventory = {
        "drives": [{"root": r, "total": t, "free": fr, "scanned": s} for r, t, fr, s in drives],
        "dirs": [{"path": p, "size": s, "files": n, "oldest": a, "newest": b, "role": r} for p, s, n, a, b, r in dirs],
        "markers": [{"path": p, "kind": k, "size": s, "files": n, "newest": b, "manifest": m}
                    for p, k, s, n, b, m in markers],
        "big_files": [{"path": p, "size": s, "modified": t, "role": r} for p, s, t, r in files],
        "links": [{"path": p, "target": t, "role": r} for p, t, r in links],
        "errors": [{"path": p, "error": e} for p, e in sc.errors],
    }
    with open(os.path.join(out, "inventory.json"), "w", encoding="utf-8") as f:
        json.dump(inventory, f, ensure_ascii=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", action="append", help="scan only these roots (default: every fixed drive)")
    ap.add_argument("--min-dir-mb", type=float, default=100)
    ap.add_argument("--min-file-mb", type=float, default=150)
    ap.add_argument("--max-rows", type=int, default=200)
    ap.add_argument("--home", help=argparse.SUPPRESS)
    ap.add_argument("--no-system-info", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    home = (args.home or os.path.expanduser("~")).replace("\\", "/").rstrip("/")
    home = home.lower() if IS_WINDOWS else home
    sc = Scanner(int(args.min_dir_mb * 1024 ** 2), int(args.min_file_mb * 1024 ** 2), home)
    started = time.time()
    drives = []
    for root in args.root or default_roots():
        t0 = time.time()
        print(f"[scan] {root}", file=sys.stderr, flush=True)
        scanned = sc.scan_root(root)
        try:
            usage = os.statvfs(root) if hasattr(os, "statvfs") else None
            if usage:
                total, free = usage.f_blocks * usage.f_frsize, usage.f_bavail * usage.f_frsize
            else:
                import shutil
                du = shutil.disk_usage(root)
                total, free = du.total, du.free
        except OSError:
            total = free = 0
        drives.append((root, total, free, scanned))
        print(f"[scan] {root} done in {time.time() - t0:.0f} s", file=sys.stderr, flush=True)
    elapsed = time.time() - started
    write_outputs(args.out, sc, drives, elapsed, args.max_rows)
    if not args.no_system_info:
        with open(os.path.join(args.out, "apps.json"), "w", encoding="utf-8") as f:
            json.dump(installed_apps(), f, ensure_ascii=False, indent=1)
        with open(os.path.join(args.out, "processes.json"), "w", encoding="utf-8") as f:
            json.dump(running_processes(), f, ensure_ascii=False, indent=1)
    print(f"[scan] finished in {elapsed:.0f} s -> {args.out}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
