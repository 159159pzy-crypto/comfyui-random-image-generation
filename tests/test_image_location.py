import os
from pathlib import Path, PureWindowsPath
import subprocess
import tempfile
import unittest
from unittest import mock

from anima_webui import image_location as location


def stats(*args, main=r"C:\Comfy UI\main.py"):
    return {"system": {"os": "win32", "argv": [main, *args]}}


class ImageLocationTests(unittest.TestCase):
    def test_default_and_overridden_directories_follow_comfy_semantics(self):
        for kind in ("output", "input", "temp"):
            with self.subTest(kind=kind):
                self.assertEqual(location.comfy_image_directory(stats(), kind), PureWindowsPath(r"C:\Comfy UI") / kind)
                self.assertEqual(location.comfy_image_directory(stats("--base-directory", r"D:\基础"), kind), PureWindowsPath(r"D:\基础") / kind)
        argv = stats("--base-directory=D:\\base", "--output-directory", r"E:\成品 图片", "--input-directory=E:\\inputs", "--temp-directory", r"E:\scratch")
        self.assertEqual(location.comfy_image_directory(argv, "output"), PureWindowsPath(r"E:\成品 图片"))
        self.assertEqual(location.comfy_image_directory(argv, "input"), PureWindowsPath(r"E:\inputs"))
        self.assertEqual(location.comfy_image_directory(argv, "temp"), PureWindowsPath(r"E:\scratch\temp"))
        self.assertEqual(location.comfy_image_directory(stats("--output-directory", r"D:\old", "--output-directory=D:\\new"), "output"), PureWindowsPath(r"D:\new"))

    def test_ambiguous_relative_remote_or_malformed_runtime_is_rejected(self):
        values = [stats(main="main.py"), stats(main=r"C:main.py"), stats(main=r"\\host\share\main.py"),
                  stats(main=r"C:\Comfy\other.py"), stats("--output-directory"), stats("--base-directory", "relative"),
                  stats("--input-directory=C:\\inputs:stream"), stats("--temp-directory", r"C:\..\temp"),
                  stats("--output-dir", r"D:\pictures"), stats("--base-dir=D:\\base"),
                  {"system": {"os": "linux", "argv": ["/srv/main.py"]}}, {"system": {"os": "win32"}},
                  {"system": {"os": "win32", "argv": [None]}}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(location.ImageLocationError):
                location.comfy_image_directory(value, "output")
        for kind in ("", "private", "../output", None):
            with self.subTest(kind=kind), self.assertRaises(location.ImageLocationError):
                location.comfy_image_directory(stats(), kind)

    def test_recorded_file_resolves_both_separators_and_unicode_without_modification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "单图" / "one" / "成品 图片.png"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"original image bytes")
            for subfolder in ("单图/one", "单图\\one"):
                self.assertEqual(location.recorded_image_path(root, {"filename": target.name, "subfolder": subfolder}), target.resolve())
            self.assertEqual(target.read_bytes(), b"original image bytes")

    def test_untrusted_history_paths_are_rejected_before_filesystem_access(self):
        records = [
            {"filename": name} for name in ("", "../x.png", "x/y.png", "x\\y.png", "x.png:stream", "NUL", "con.png", "name.png.", "name.png ", "x\0.png")
        ] + [{"filename": "x.png", "subfolder": folder} for folder in
             ("..", "a/../b", "/root", r"C:\root", "C:relative", r"\\host\share", "a//b", "a:stream", "a\\.\\b")]
        for record in records:
            with self.subTest(record=record), self.assertRaises(location.ImageLocationError) as caught:
                location.recorded_image_path(Path("missing"), record)
            self.assertEqual(caught.exception.status, 400)

    def test_missing_file_and_directory_are_404(self):
        with tempfile.TemporaryDirectory() as temporary:
            for root, name in ((Path(temporary), "missing.png"), (Path(temporary) / "missing", "file.png")):
                with self.subTest(root=root), self.assertRaises(location.ImageLocationError) as caught:
                    location.recorded_image_path(root, {"filename": name})
                self.assertEqual(caught.exception.code, "image_file_missing")
                self.assertEqual(caught.exception.status, 404)

    def test_symlink_or_junction_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root, outside = parent / "root", parent / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "file.png").write_bytes(b"outside")
            link = root / "escaped"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                # Windows may not grant symlink privileges; mock canonicalization
                # to the same resolved target, covering junctions by the same rule.
                original = Path.resolve
                with mock.patch.object(Path, "resolve", autospec=True, side_effect=lambda path, **kwargs: outside / "file.png" if path.name == "file.png" else original(path, **kwargs)):
                    with self.assertRaises(location.ImageLocationError) as caught:
                        location.recorded_image_path(root, {"filename": "file.png", "subfolder": "escaped"})
            else:
                with self.assertRaises(location.ImageLocationError) as caught:
                    location.recorded_image_path(root, {"filename": "file.png", "subfolder": "escaped"})
            self.assertEqual(caught.exception.status, 400)

    def test_process_probe_rechecks_port_owner_without_starting_any_process(self):
        with mock.patch.object(location, "_listening_process_ids", return_value={3}) as listeners, mock.patch.object(location, "_process_details", return_value={"ProcessId": 3}) as details:
            self.assertEqual(location._listening_processes(8188), [{"ProcessId": 3}])
            self.assertEqual(listeners.call_args_list, [mock.call(8188), mock.call(8188)])
            details.assert_called_once_with(3)
        with mock.patch.object(location, "_listening_process_ids", side_effect=[{3}, {4}]), mock.patch.object(location, "_process_details", return_value={"ProcessId": 3}), self.assertRaises(location.ImageLocationError):
            location._listening_processes(8188)

    @unittest.skipUnless(location.IS_WINDOWS, "Windows process information")
    def test_native_process_query_reads_current_python_without_shell_or_wmi(self):
        details = location._process_details(os.getpid())
        self.assertEqual(details["ProcessId"], os.getpid())
        self.assertIn("python", details["ExecutablePath"].lower())
        self.assertTrue(location._command_line_args(details["CommandLine"]))

    def test_local_process_must_own_port_and_match_the_whole_application_argv(self):
        runtime = stats("--port", "8188")
        executable = r"C:\Python\python.exe"
        process = {"ProcessId": 12, "ExecutablePath": executable, "CommandLine": "encoded by Windows"}
        command = [executable, "-u", *runtime["system"]["argv"]]
        with mock.patch.object(location, "_listening_processes", return_value=[process]) as probe, mock.patch.object(location, "_command_line_args", return_value=command), mock.patch.object(Path, "is_file", return_value=True):
            location.verify_local_comfy("http://127.0.0.1:8188", runtime)
            probe.assert_called_once_with(8188)
        invalid = [([], command), ([process, process], command),
                   ([{**process, "ExecutablePath": r"C:\Windows\ssh.exe"}], command),
                   ([process], [executable, *runtime["system"]["argv"], "--output-directory=C:\\elsewhere"]),
                   ([process], [executable, "-c", "proxy", *runtime["system"]["argv"]]),
                   ([{**process, "CommandLine": None}], command)]
        for rows, arguments in invalid:
            with self.subTest(rows=rows, arguments=arguments), mock.patch.object(location, "_listening_processes", return_value=rows), mock.patch.object(location, "_command_line_args", return_value=arguments), mock.patch.object(Path, "is_file", return_value=True), self.assertRaises(location.ImageLocationError):
                location.verify_local_comfy("http://127.0.0.1:8188", runtime)
        for url in ("http://remote:8188", "http://127.0.0.1:8188/proxy", "https://localhost", "http://user@localhost", "http://localhost:broken"):
            with self.subTest(url=url), mock.patch.object(location, "_listening_processes") as probe, self.assertRaises(location.ImageLocationError):
                location.verify_local_comfy(url, runtime)
            probe.assert_not_called()

    def test_executable_aliases_use_file_identity_and_do_not_trust_similar_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "child").mkdir()
            executable = root / "python.exe"
            executable.write_bytes(b"same file")
            self.assertTrue(location._same_executable(str(root / "child" / ".." / "python.exe"), str(executable)))
            self.assertFalse(location._same_executable(str(root / "other.exe"), str(executable)))

    @unittest.skipUnless(location.IS_WINDOWS, "Windows command-line parser")
    def test_command_line_to_argv_handles_spaces_and_unicode(self):
        values = [r"C:\Python Dir\python.exe", r"C:\Comfy UI\main.py", "--output-directory", r"D:\生成 图片", "--port", "8188"]
        self.assertEqual(location._command_line_args(subprocess.list2cmdline(values)), values)

    def test_reveal_selects_only_an_existing_verified_file_and_never_opens_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "image.png"
            target.write_bytes(b"original")
            record = {"filename": "image.png", "file_type": "output"}
            with mock.patch.object(location, "IS_WINDOWS", True), mock.patch.object(location, "comfy_image_directory", return_value=root), mock.patch.object(location, "verify_local_comfy") as verify, mock.patch.object(location, "_select_file") as select:
                location.reveal_image(record, stats(), "http://localhost:8188")
                verify.assert_called_once()
                select.assert_called_once_with(target.resolve())
                select.reset_mock()
                target.unlink()
                with self.assertRaises(location.ImageLocationError):
                    location.reveal_image(record, stats(), "http://localhost:8188")
                select.assert_not_called()
        with mock.patch.object(location, "IS_WINDOWS", False), mock.patch.object(location, "verify_local_comfy") as verify, mock.patch.object(location, "_select_file") as select, self.assertRaises(location.ImageLocationError) as caught:
            location.reveal_image({}, {}, "http://localhost:8188")
        self.assertEqual(caught.exception.code, "image_reveal_unsupported")
        verify.assert_not_called()
        select.assert_not_called()

    def test_shell_selection_cleans_up_com_and_item_memory_on_success_and_failure(self):
        for phase in ("success", "initialize", "parse_folder", "parse_image", "child", "select"):
            ole32, shell32 = mock.Mock(), mock.Mock()
            ole32.CoInitializeEx.return_value = -1 if phase == "initialize" else 0
            target = Path("saved") / "image_00039_.png"
            def parse(path, _context, item, _attributes, _result):
                folder = path == str(target.parent)
                item._obj.value = 1234 if folder else 5678
                return -1 if phase == ("parse_folder" if folder else "parse_image") else 0
            shell32.SHParseDisplayName.side_effect = parse
            shell32.ILFindLastID.return_value = None if phase == "child" else 5680
            shell32.SHOpenFolderAndSelectItems.return_value = -1 if phase == "select" else 0
            with self.subTest(phase=phase), mock.patch.object(location.ctypes, "WinDLL", side_effect=lambda name: ole32 if name == "ole32" else shell32, create=True):
                if phase == "success":
                    location._select_file(target)
                else:
                    with self.assertRaises(location.ImageLocationError) as caught:
                        location._select_file(target)
                    self.assertEqual(caught.exception.code, "image_reveal_failed")
                self.assertEqual(ole32.CoUninitialize.call_count, phase != "initialize")
                freed = [call.args[0].value for call in ole32.CoTaskMemFree.call_args_list]
                self.assertEqual(freed, [] if phase == "initialize" else [1234] if phase == "parse_folder" else [5678, 1234])
                self.assertEqual(shell32.SHOpenFolderAndSelectItems.call_count, phase in {"success", "select"})
                if phase in {"success", "select"}:
                    folder, count, children, flags = shell32.SHOpenFolderAndSelectItems.call_args.args
                    self.assertEqual(folder.value, 1234)
                    self.assertEqual(count, 1)
                    self.assertEqual(list(children), [5680])
                    self.assertEqual(flags, 0)


if __name__ == "__main__":
    unittest.main()
