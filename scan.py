#!/usr/bin/env python3
"""disk-sweeper scanner: measure every drive once and write digests for the role agents.

Read-only. Standard library only (Python 3.8+). Never follows symlinks or junctions.

usage:
    python -I scan.py --out DIR [--root PATH ...] [--min-dir-mb 100] [--min-file-mb 150] [--max-rows 200]
    python -I scan.py --list-drives     drives and free space only, no scan

Writes to DIR:
    summary.md          drives, elapsed time, totals per role, scan errors
    digest-<role>.md    what each role agent reads (dev, apps, system, data)
    inventory.json      everything recorded, for spot checks
    apps.json           installed applications (for orphan detection)
    processes.json      running processes (for lock checks)
    drivers.json        third-party driver packages and whether a device uses them (Windows)
"""
import argparse
import json
import os
import re
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
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
    ("system", r"^[a-z]:/(amd|nvidia|intel)(/|$)"),  # driver installers unpack here
    ("system", r"^/(usr/)?lib/modules(/|$)"),
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

# Folders where apps keep their data. Every folder directly under one is recorded whatever its size:
# leftovers of uninstalled apps are often small, and the apps role matches them by name.
APP_ANCHORS = re.compile(
    r"[a-z]:/(programdata|program files|program files \(x86\))"
    r"|~/appdata/(roaming|local|locallow|local/programs)"
    r"|~/(\.cache|\.config|\.local/share)|~/library/(application support|caches|containers)|/opt|/applications")

# Editors that keep uninstalled extensions on disk until their next start, listed in extensions/.obsolete.
EDITOR_DIRS = (".vscode", ".vscode-insiders", ".vscode-oss", ".vscode-server", ".cursor", ".windsurf")


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


def link_target_exists(link_path, target):
    """A relative target is relative to the folder that holds the link, not to the current directory."""
    if not target or target == "?":
        return False
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(link_path), target)
    return os.path.exists(target)


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


def disk_usage(root):
    try:
        if hasattr(os, "statvfs"):
            usage = os.statvfs(root)
            return usage.f_blocks * usage.f_frsize, usage.f_bavail * usage.f_frsize
        import shutil
        du = shutil.disk_usage(root)
        return du.total, du.free
    except OSError:
        return 0, 0


def volume_label(root):
    if not IS_WINDOWS:
        return ""
    import ctypes
    buf = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(root, buf, len(buf), None, None, None, None, 0)
    return buf.value if ok else ""


def list_drives(roots):
    """Drives and free space without scanning, for asking the user which drives to sweep."""
    lines = ["| drive | label | total | free | free % |", "|---|---|---|---|---|"]
    for root in roots:
        total, free = disk_usage(root)
        pct = f"{100 * free / total:.0f}%" if total else "-"
        lines.append(f"| `{root}` | {volume_label(root) or '-'} | {fmt_size(total)} | {fmt_size(free)} | {pct} |")
    return "\n".join(lines)


class Scanner:
    def __init__(self, min_dir, min_file, home):
        self.min_dir = min_dir
        self.min_file = min_file
        self.home = home
        self.dirs = []      # (path, size, files, mtime_min, mtime_max)
        self.markers = []   # (path, name, size, files, mtime_max, manifest)
        self.big_files = [] # (path, size, mtime)
        self.links = []     # (path, target)
        self.app_dirs = []  # (path, size or None if unreadable, files, mtime_min, mtime_max, anchor)
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
        # frame: [path, iterator, size, files, mtime_min, mtime_max, marker_name, inside_marker,
        #         anchor (set when this is an app data folder), parent_anchor]
        stack = []

        def push(path, marker_name, inside_marker, parent_anchor):
            try:
                it = os.scandir(path)
            except OSError as e:
                self._error(path, e)
                if parent_anchor:
                    self.app_dirs.append((display(path), None, 0, None, None, parent_anchor))
                return
            self.n_dirs += 1
            anchor = display(path) if APP_ANCHORS.fullmatch(normalize(path, self.home).lower()) else None
            stack.append([path, it, 0, 0, None, None, marker_name, inside_marker, anchor, parent_anchor])

        push(start, None, False, None)
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
                path, _, size, files, mtmin, mtmax, marker_name, inside, _, parent_anchor = frame
                if size >= self.min_dir:
                    self.dirs.append((display(path), size, files, mtmin, mtmax))
                if parent_anchor:
                    self.app_dirs.append((display(path), size, files, mtmin, mtmax, parent_anchor))
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
                push(entry.path, name if name in MARKERS else None, inside, frame[8])
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


def obsolete_extensions(home):
    """Extension folders an editor has marked as uninstalled. The editor deletes them on its next start."""
    found = []
    for editor in EDITOR_DIRS:
        ext_dir = os.path.join(home, editor, "extensions")
        try:
            with open(os.path.join(ext_dir, ".obsolete"), encoding="utf-8") as f:
                names = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(names, dict):
            names = [name for name, flag in names.items() if flag]
        for name in sorted(names):
            path = os.path.join(ext_dir, name)
            if os.path.isdir(path) and not os.path.islink(path):
                found.append((path, Scanner(1 << 62, 1 << 62, "").scan_root(path)))
    return found


# pnputil labels can be localized, so fields are told apart by the shape of their values, not by labels.
_OEM_INF = re.compile(r"\boem\d+\.inf\b", re.IGNORECASE)
_GUID = re.compile(r"^\{[0-9a-f-]{36}\}$", re.IGNORECASE)
_DATE_VERSION = re.compile(r"^(\S+)\s+(\d+(?:\.\d+)+)$")


def parse_enum_drivers(text):
    """Blocks of `pnputil /enum-drivers`: published name, original name, provider, class, then GUID and version."""
    packages = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        values = [m.group(1) for m in re.finditer(r"^\S[^:：\r\n]*[:：][ \t]*(.*?)[ \t\r]*$", block, re.MULTILINE)]
        first = next((i for i, v in enumerate(values) if _OEM_INF.fullmatch(v)), None)
        if first is None or len(values) < first + 4:
            continue
        rest = values[first + 4:]
        guid = next((i for i, v in enumerate(rest) if _GUID.match(v)), -1)
        ver = next(filter(None, (_DATE_VERSION.match(v) for v in rest[guid + 1:])), None)
        packages.append({
            "published": values[first].lower(), "original": values[first + 1], "provider": values[first + 2],
            "class": values[first + 3], "date": ver.group(1) if ver else "", "version": ver.group(2) if ver else "",
        })
    return packages


def parse_enum_drivers_xml(data):
    """`pnputil /enum-drivers /format xml`, which newer Windows builds have. Not localized."""
    packages = []
    for d in ET.fromstring(data).iter("Driver"):
        ver = _DATE_VERSION.match((d.findtext("DriverVersion") or "").strip())
        packages.append({
            "published": (d.get("DriverName") or "").lower(), "original": d.findtext("OriginalName") or "",
            "provider": d.findtext("ProviderName") or "", "class": d.findtext("ClassName") or "",
            "date": ver.group(1) if ver else "", "version": ver.group(2) if ver else "",
        })
    return [p for p in packages if _OEM_INF.fullmatch(p["published"])]


def bound_infs(text):
    """Published names that `pnputil /enum-devices` assigns to a device, connected or not, as driver or extension."""
    return {m.group(0).lower() for m in _OEM_INF.finditer(text)}


def match_driver_folders(packages, inf_dir, repo_dir):
    """Find each package's DriverStore folder: Windows\\INF\\oemN.inf is a byte copy of the original INF there."""
    try:
        folders = os.listdir(repo_dir)
    except OSError:
        folders = []
    for p in packages:
        p["folder"], p["size"] = None, None
        try:
            with open(os.path.join(inf_dir, p["published"]), "rb") as f:
                copy = f.read()
        except OSError:
            continue
        prefix = p["original"].lower() + "_"
        for name in folders:
            if not name.lower().startswith(prefix):
                continue
            folder = os.path.join(repo_dir, name)
            try:
                with open(os.path.join(folder, p["original"]), "rb") as f:
                    if f.read() != copy:
                        continue
            except OSError:
                continue
            p["folder"], p["size"] = folder, Scanner(1 << 62, 1 << 62, "").scan_root(folder)
            break
    return packages


def driver_packages():
    """Third-party driver packages in the Windows driver store, with the folder each one takes."""
    if not IS_WINDOWS:
        return []
    def pnputil(*args):
        r = subprocess.run(["pnputil", *args], capture_output=True, timeout=120)
        if r.returncode != 0:
            raise subprocess.SubprocessError(f"pnputil {' '.join(args)} exited with {r.returncode}")
        return r.stdout
    try:
        packages = parse_enum_drivers_xml(pnputil("/enum-drivers", "/format", "xml"))
    except (OSError, subprocess.SubprocessError, ET.ParseError):  # no /format on this build
        try:
            packages = parse_enum_drivers(pnputil("/enum-drivers").decode("mbcs", errors="replace"))
        except (OSError, subprocess.SubprocessError):
            return []
    try:
        bound = bound_infs(pnputil("/enum-devices").decode("mbcs", errors="replace"))  # Windows 10 2004 and later
    except (OSError, subprocess.SubprocessError):
        bound = None
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    match_driver_folders(packages, os.path.join(windir, "INF"),
                         os.path.join(windir, "System32", "DriverStore", "FileRepository"))
    for p in packages:
        p["in_use"] = "unknown" if bound is None else ("yes" if p["published"] in bound else "no")
    return packages


def fmt_size(n):
    if n is None:
        return "?"
    if n < 1024 ** 2:
        return "<1 MB"
    return f"{n / 1024 ** 3:.2f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def fmt_date(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t else "-"


def version_key(p):
    try:
        return tuple(int(x) for x in p["version"].split("."))
    except ValueError:
        return ()


def write_outputs(out, sc, drives, elapsed, max_rows, obsolete=(), drivers=()):
    home = sc.home
    role_of = lambda p: classify(normalize(p, home))
    dirs = [d + (role_of(d[0]),) for d in sc.dirs]
    # Only the apps role matches app data by name; caches of dev tools, games and Temp belong to other roles.
    app_dirs = [d for d in sc.app_dirs if role_of(d[0]) == "apps"]
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
            lines += ["", f"## Uninstalled editor extensions still on disk ({len(obsolete)}, "
                          f"{fmt_size(sum(s for _, s in obsolete)) if obsolete else '0 MB'})", "",
                      "Listed in the editor's `extensions/.obsolete`: the extension was uninstalled, and the editor "
                      "deletes the folder on its next start anyway.", "", "| path | size |", "|---|---|"]
            lines += [f"| `{path}` | {fmt_size(size)} |" for path, size in obsolete]
        if role == "apps":
            by_anchor = {}
            for d in app_dirs:
                by_anchor.setdefault(d[5], []).append(d)
            lines += ["", f"## Every folder directly under app data locations ({len(app_dirs)})", "",
                      "Listed whatever their size, so leftovers of uninstalled apps can be matched by name against "
                      "`apps.json`. Folders of other roles (dev tool caches, games, Temp) are left out. "
                      "`?` means the folder could not be read."]
            for anchor in sorted(by_anchor, key=str.lower):
                rows_a = sorted(by_anchor[anchor], key=lambda d: d[0].lower())
                lines += ["", f"### `{anchor}` ({len(rows_a)})", "", "| name | size | newest |", "|---|---|---|"]
                for path, size, _, _, mtmax, _ in rows_a:
                    lines.append(f"| {os.path.basename(path)} | {fmt_size(size)} | {fmt_date(mtmax)} |")
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
                exists = "yes" if link_target_exists(path, target) else "no"
                lines.append(f"| `{path}` | `{target}` | {exists} |")
        if role == "system" and drivers:
            rows_d = sorted(drivers, key=lambda p: (p["provider"].lower(), p["class"].lower(), p["original"].lower(),
                                                    tuple(-x for x in version_key(p))))
            lines += ["", f"## Driver packages in the driver store ({len(drivers)}, "
                          f"{fmt_size(sum(p['size'] or 0 for p in drivers))})", "",
                      "Third-party packages from `pnputil /enum-drivers` (Windows' own drivers are not listed). "
                      "`in use` is `yes` when a device, connected or not, uses the package as its driver or "
                      "extension. Printers are used through the print spooler, not as devices, so `no` tells "
                      "nothing for them. `?` means the folder could not be matched.", "",
                      "| published | original | provider | class | version | date | size | in use | folder |",
                      "|---|---|---|---|---|---|---|---|---|"]
            for p in rows_d:
                lines.append(f"| {p['published']} | {p['original']} | {p['provider']} | {p['class']} "
                             f"| {p['version'] or '-'} | {p['date'] or '-'} "
                             f"| {fmt_size(p['size']) if p['size'] is not None else '?'} | {p['in_use']} "
                             f"| {'`' + os.path.basename(p['folder']) + '`' if p['folder'] else '?'} |")
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
        "app_dirs": [{"path": p, "size": s, "files": n, "oldest": a, "newest": b, "anchor": an}
                     for p, s, n, a, b, an in app_dirs],
        "obsolete_extensions": [{"path": p, "size": s} for p, s in obsolete],
        "errors": [{"path": p, "error": e} for p, e in sc.errors],
    }
    with open(os.path.join(out, "inventory.json"), "w", encoding="utf-8") as f:
        json.dump(inventory, f, ensure_ascii=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out")
    ap.add_argument("--root", action="append", help="scan only these roots (default: every fixed drive)")
    ap.add_argument("--list-drives", action="store_true", help="print drives and free space, then exit")
    ap.add_argument("--min-dir-mb", type=float, default=100)
    ap.add_argument("--min-file-mb", type=float, default=150)
    ap.add_argument("--max-rows", type=int, default=200)
    ap.add_argument("--home", help=argparse.SUPPRESS)
    ap.add_argument("--no-system-info", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.list_drives:
        # Volume labels can be non-ASCII; the locale code page (cp932 and so on) garbles them for the agent.
        sys.stdout.buffer.write((list_drives(args.root or default_roots()) + "\n").encode("utf-8"))
        return 0
    if not args.out:
        ap.error("--out is required")

    os.makedirs(args.out, exist_ok=True)
    raw_home = args.home or os.path.expanduser("~")
    home = raw_home.replace("\\", "/").rstrip("/")
    home = home.lower() if IS_WINDOWS else home
    sc = Scanner(int(args.min_dir_mb * 1024 ** 2), int(args.min_file_mb * 1024 ** 2), home)
    started = time.time()
    drives = []
    for root in args.root or default_roots():
        t0 = time.time()
        print(f"[scan] {root}", file=sys.stderr, flush=True)
        scanned = sc.scan_root(root)
        total, free = disk_usage(root)
        drives.append((root, total, free, scanned))
        print(f"[scan] {root} done in {time.time() - t0:.0f} s", file=sys.stderr, flush=True)
    elapsed = time.time() - started
    drivers = [] if args.no_system_info else driver_packages()
    write_outputs(args.out, sc, drives, elapsed, args.max_rows, obsolete_extensions(raw_home), drivers)
    if not args.no_system_info:
        if IS_WINDOWS:
            with open(os.path.join(args.out, "drivers.json"), "w", encoding="utf-8") as f:
                json.dump(drivers, f, ensure_ascii=False, indent=1)
        with open(os.path.join(args.out, "apps.json"), "w", encoding="utf-8") as f:
            json.dump(installed_apps(), f, ensure_ascii=False, indent=1)
        with open(os.path.join(args.out, "processes.json"), "w", encoding="utf-8") as f:
            json.dump(running_processes(), f, ensure_ascii=False, indent=1)
    print(f"[scan] finished in {elapsed:.0f} s -> {args.out}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
