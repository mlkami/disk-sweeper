import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scan  # noqa: E402

MB = 1024 * 1024


def write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)


def make_link(link, target):
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", link, target], check=True, capture_output=True)
    else:
        os.symlink(target, link)


class ScanTreeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="disk-sweeper-test-")
        root = cls.root = os.path.join(cls.tmp, "root")
        write(os.path.join(root, "proj", "package.json"), 10)
        write(os.path.join(root, "proj", "node_modules", "a.bin"), 2 * MB)
        write(os.path.join(root, "proj", "node_modules", "pkg", "node_modules", "b.bin"), 1 * MB)
        write(os.path.join(root, "proj", "build", "x.bin"), MB + MB // 2)
        write(os.path.join(root, "other", "build", "y.bin"), MB + MB // 2)
        write(os.path.join(root, "big.bin"), 3 * MB)
        make_link(os.path.join(root, "linkdir"), os.path.join(root, "proj"))
        make_link(os.path.join(root, "dangling"), os.path.join(cls.tmp, "missing"))
        cls.expected_total = 10 + 2 * MB + 1 * MB + 2 * (MB + MB // 2) + 3 * MB
        cls.out = os.path.join(cls.tmp, "out")
        scan.main(["--out", cls.out, "--root", root, "--min-dir-mb", "1", "--min-file-mb", "2.5",
                   "--no-system-info", "--home", os.path.join(cls.tmp, "home")])
        with open(os.path.join(cls.out, "inventory.json"), encoding="utf-8") as f:
            cls.inv = json.load(f)

    @classmethod
    def tearDownClass(cls):
        for name in ("linkdir", "dangling"):
            p = os.path.join(cls.root, name)
            if os.name == "nt":
                os.rmdir(p)  # removes the junction only
            else:
                os.unlink(p)
        shutil.rmtree(cls.tmp)

    def test_total_counts_each_file_once_and_skips_links(self):
        self.assertEqual(self.inv["drives"][0]["scanned"], self.expected_total)

    def test_links_are_listed_not_followed(self):
        paths = sorted(os.path.basename(link["path"]) for link in self.inv["links"])
        self.assertEqual(paths, ["dangling", "linkdir"])
        self.assertFalse(any("linkdir" in d["path"] for d in self.inv["dirs"]))

    def test_outermost_node_modules_only(self):
        nm = [m for m in self.inv["markers"] if m["kind"] == "node_modules"]
        self.assertEqual(len(nm), 1)
        self.assertEqual(nm[0]["size"], 3 * MB)
        self.assertIn("package.json", nm[0]["manifest"])

    def test_build_dir_needs_manifest(self):
        builds = [m["path"] for m in self.inv["markers"] if m["kind"] == "build"]
        self.assertEqual(len(builds), 1)
        self.assertIn("proj", builds[0])

    def test_big_files(self):
        self.assertEqual([os.path.basename(f["path"]) for f in self.inv["big_files"]], ["big.bin"])

    def test_parent_with_one_dominant_child_is_collapsed(self):
        with open(os.path.join(self.out, "digest-data.md"), encoding="utf-8") as f:
            digest = f.read()
        other = os.path.join(self.root, "other")
        self.assertNotIn(f"`{other}`", digest)
        self.assertIn(f"`{os.path.join(other, 'build')}`", digest)
        self.assertIn(other, [d["path"] for d in self.inv["dirs"]])  # the inventory keeps everything

    def test_digests_written(self):
        for role in scan.ROLES:
            self.assertTrue(os.path.exists(os.path.join(self.out, f"digest-{role}.md")))
        with open(os.path.join(self.out, "digest-data.md"), encoding="utf-8") as f:
            self.assertIn("| no |", f.read())  # the dangling link is flagged


class AppDataTest(unittest.TestCase):
    """Small folders directly under app data locations are listed by name; uninstalled extensions are found."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="disk-sweeper-apps-")
        home = os.path.join(cls.tmp, "home")
        roaming = os.path.join(home, "AppData", "Roaming")
        local = os.path.join(home, "AppData", "Local")
        write(os.path.join(roaming, "tiny-leftover", "settings.json"), 10)
        write(os.path.join(roaming, "big-app", "cache.bin"), 2 * MB)
        write(os.path.join(local, "Programs", "some-app", "app.exe"), 10)
        write(os.path.join(local, "Temp", "x.tmp"), 10)          # system role
        write(os.path.join(local, "npm-cache", "x.tgz"), 10)     # dev role
        write(os.path.join(local, "deep", "nested", "x.bin"), 10)
        ext = os.path.join(home, ".vscode", "extensions")
        write(os.path.join(ext, "old.ext-1.0.0", "package.json"), MB + 10)
        write(os.path.join(ext, "current.ext-2.0.0", "package.json"), 10)
        with open(os.path.join(ext, ".obsolete"), "w", encoding="utf-8") as f:
            json.dump({"old.ext-1.0.0": True, "gone.ext-0.1.0": True}, f)
        cls.out = os.path.join(cls.tmp, "out")
        scan.main(["--out", cls.out, "--root", home, "--min-dir-mb", "1", "--no-system-info", "--home", home])
        with open(os.path.join(cls.out, "inventory.json"), encoding="utf-8") as f:
            cls.inv = json.load(f)
        with open(os.path.join(cls.out, "digest-apps.md"), encoding="utf-8") as f:
            cls.apps_digest = f.read()
        with open(os.path.join(cls.out, "digest-dev.md"), encoding="utf-8") as f:
            cls.dev_digest = f.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_every_app_folder_is_listed_whatever_its_size(self):
        names = sorted((os.path.basename(d["path"]) for d in self.inv["app_dirs"]), key=str.lower)
        self.assertEqual(names, ["big-app", "deep", "Programs", "some-app", "tiny-leftover"])
        self.assertIn("| tiny-leftover | <1 MB |", self.apps_digest)
        self.assertIn("| some-app | <1 MB |", self.apps_digest)  # AppData\Local\Programs is a location too

    def test_other_roles_and_deeper_folders_are_left_out(self):
        for name in ("Temp", "npm-cache", "nested"):
            self.assertNotIn(f"| {name} |", self.apps_digest)

    def test_obsolete_extensions_that_still_exist(self):
        self.assertEqual([os.path.basename(e["path"]) for e in self.inv["obsolete_extensions"]], ["old.ext-1.0.0"])
        self.assertEqual(self.inv["obsolete_extensions"][0]["size"], MB + 10)
        self.assertIn("old.ext-1.0.0", self.dev_digest)
        self.assertNotIn("current.ext-2.0.0", self.dev_digest)


class ListDrivesTest(unittest.TestCase):
    def test_lists_free_space_without_scanning(self):
        tmp = tempfile.mkdtemp(prefix="disk-sweeper-test-")
        try:
            table = scan.list_drives([tmp])
            row = table.splitlines()[2]
            self.assertIn(f"`{tmp}`", row)
            self.assertRegex(row, r"\| \d+%")
            self.assertFalse(os.listdir(tmp))
        finally:
            shutil.rmtree(tmp)

    def test_out_is_required_only_for_a_scan(self):
        with self.assertRaises(SystemExit):
            scan.main(["--root", "."])


class LinkTargetTest(unittest.TestCase):
    def test_relative_target_is_resolved_from_the_link_folder(self):
        tmp = tempfile.mkdtemp(prefix="disk-sweeper-link-")
        try:
            write(os.path.join(tmp, "blobs", "abc"), 1)
            link = os.path.join(tmp, "snapshots", "rev", "model.pt")  # the link itself need not exist here
            self.assertTrue(scan.link_target_exists(link, os.path.join("..", "..", "blobs", "abc")))
            self.assertFalse(scan.link_target_exists(link, os.path.join("..", "..", "blobs", "missing")))
            self.assertFalse(scan.link_target_exists(link, "?"))
        finally:
            shutil.rmtree(tmp)


class ClassifyTest(unittest.TestCase):
    CASES = {
        "c:/windows/winsxs": "system",
        "c:/windows.old": "system",
        "d:/$recycle.bin": "system",
        "c:/pagefile.sys": "system",
        "~/appdata/local/temp/x": "system",
        "/var/cache/pacman/pkg": "system",
        "~/.local/share/trash/files": "system",
        "~/.cache/huggingface/hub": "dev",
        "~/.cache/pip": "dev",
        "~/appdata/local/npm-cache": "dev",
        "~/documents/projects/foo": "dev",
        "c:/program files (x86)/windows kits/10": "dev",
        "c:/programdata/package cache": "dev",
        "d:/steamlibrary/steamapps/common/game": "data",
        "c:/program files (x86)/steam": "data",
        "~/appdata/roaming/modrinthapp/profiles": "data",
        "~/appdata/local/discord": "apps",
        "~/appdata/roaming/comfyui": "apps",
        "c:/program files/rose": "apps",
        "c:/programdata/lghub/depots": "apps",
        "~/.config/foo": "apps",
        "~/pictures/comfyui": "data",
        "~/documents/comfyui/.venv": "data",
        "d:/": "data",
    }

    def test_classify(self):
        for path, role in self.CASES.items():
            with self.subTest(path=path):
                self.assertEqual(scan.classify(path), role)

    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_normalize_windows(self):
        self.assertEqual(scan.normalize("\\\\?\\C:\\Users\\Kamig\\AppData\\Local\\Temp", "c:/users/kamig"),
                         "~/appdata/local/temp")
        self.assertEqual(scan.normalize("D:\\SteamLibrary", "c:/users/kamig"), "d:/steamlibrary")


if __name__ == "__main__":
    unittest.main()
