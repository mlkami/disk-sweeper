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
