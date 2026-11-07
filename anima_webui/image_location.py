"""Resolve a recorded ComfyUI image and select it in the local Windows shell."""
from __future__ import annotations

import ctypes
import ntpath
from pathlib import Path, PureWindowsPath
import re
import socket
import sys
from typing import Any
from urllib.parse import urlsplit


IS_WINDOWS = sys.platform == "win32"
_DIRECTORY_FLAGS = {"--base-directory", "--output-directory", "--input-directory", "--temp-directory"}
_FILE_TYPES = {"output", "input", "temp"}


class ImageLocationError(Exception):
    def __init__(self, message: str, *, code: str = "image_location_unavailable", status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def _absolute_windows_path(value: Any) -> PureWindowsPath:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ImageLocationError("ComfyUI 文件目录无效，无法定位原图")
    path = PureWindowsPath(value)
    # UNC/device paths and drive-relative paths cannot identify this desktop's files.
    if not path.is_absolute() or not re.fullmatch(r"[a-zA-Z]:", path.drive):
        raise ImageLocationError("无法确认 ComfyUI 文件目录，请使用本机绝对路径启动 ComfyUI")
    if any(part == ".." or part.endswith((".", " ")) or any(char in part for char in ':<>"|?*')
           for part in path.parts[1:]):
        raise ImageLocationError("ComfyUI 文件目录无效，无法定位原图")
    return path


def _system_argv(stats: dict[str, Any]) -> list[str]:
    system = stats.get("system") if isinstance(stats, dict) else None
    if not isinstance(system, dict) or system.get("os") != "win32":
        raise ImageLocationError("只能定位本机 Windows ComfyUI 生成的原图")
    argv = system.get("argv")
    if not isinstance(argv, list) or not argv or len(argv) > 512 or any(
        not isinstance(value, str) or "\0" in value for value in argv
    ):
        raise ImageLocationError("ComfyUI 未提供可确认的启动路径，无法定位原图")
    main = _absolute_windows_path(argv[0])
    if main.name.lower() != "main.py":
        raise ImageLocationError("无法确认 ComfyUI 启动脚本，无法定位原图")
    return argv


def comfy_image_directory(stats: dict[str, Any], file_type: str) -> PureWindowsPath:
    """Mirror ComfyUI's directory flags without guessing its process cwd."""
    if file_type not in _FILE_TYPES:
        raise ImageLocationError("历史记录的图片文件类型不支持定位", code="invalid_image_location", status=400)
    argv = _system_argv(stats)
    overrides: dict[str, PureWindowsPath] = {}
    index = 1
    while index < len(argv):
        flag, separator, value = argv[index].partition("=")
        if flag not in _DIRECTORY_FLAGS and flag.startswith("--") and any(known.startswith(flag) for known in _DIRECTORY_FLAGS):
            raise ImageLocationError("ComfyUI 使用了缩写目录参数，请改用完整参数名后定位原图")
        if flag in _DIRECTORY_FLAGS:
            if not separator:
                index += 1
                value = argv[index] if index < len(argv) else ""
            overrides[flag] = _absolute_windows_path(value)
        index += 1
    base = overrides.get("--base-directory", _absolute_windows_path(argv[0]).parent)
    override = overrides.get(f"--{file_type}-directory")
    # ComfyUI appends 'temp' to --temp-directory, unlike input/output overrides.
    return (override / "temp" if file_type == "temp" else override) if override is not None else base / file_type


def _record_parts(record: dict[str, Any]) -> tuple[str, ...]:
    filename, subfolder = record.get("filename"), record.get("subfolder") or ""
    if not isinstance(filename, str) or not filename or not isinstance(subfolder, str):
        raise ImageLocationError("历史记录缺少有效的图片文件名", code="invalid_image_location", status=400)
    if "/" in filename or "\\" in filename or PureWindowsPath(subfolder).anchor:
        raise ImageLocationError("历史记录的图片路径无效", code="invalid_image_location", status=400)
    parts = tuple(subfolder.replace("\\", "/").split("/")) if subfolder else ()
    parts += (filename,)
    if any(not part or part in {".", ".."} or part.endswith((".", " "))
           or PureWindowsPath(part).is_reserved()
           or any(ord(char) < 32 or char in ':<>"|?*' for char in part) for part in parts):
        raise ImageLocationError("历史记录的图片路径无效", code="invalid_image_location", status=400)
    return parts


def recorded_image_path(directory: Path, record: dict[str, Any]) -> Path:
    parts = _record_parts(record)
    try:
        root = directory.resolve(strict=True)
        target = root.joinpath(*parts).resolve(strict=True)
        if not root.is_dir() or not target.is_relative_to(root):
            raise ImageLocationError("图片路径超出 ComfyUI 文件目录，无法定位", code="invalid_image_location", status=400)
        if not target.is_file():
            raise FileNotFoundError()
        return target
    except (FileNotFoundError, NotADirectoryError):
        raise ImageLocationError("原图文件不存在，可能已被移动或删除", code="image_file_missing", status=404) from None
    except (OSError, RuntimeError, ValueError):
        raise ImageLocationError("无法访问原图文件，请检查目录与文件权限") from None


def _listening_process_ids(port: int) -> set[int]:
    """Use IP Helper directly: the PowerShell networking CIM provider is slow."""
    class TcpRow(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint32) for name in ("state", "local_address", "local_port", "remote_address", "remote_port", "pid")]

    class Tcp6Row(ctypes.Structure):
        _fields_ = [("local_address", ctypes.c_ubyte * 16), ("local_scope", ctypes.c_uint32), ("local_port", ctypes.c_uint32),
                    ("remote_address", ctypes.c_ubyte * 16), ("remote_scope", ctypes.c_uint32), ("remote_port", ctypes.c_uint32),
                    ("state", ctypes.c_uint32), ("pid", ctypes.c_uint32)]

    api = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_bool, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
    api.restype = ctypes.c_uint32
    pids: set[int] = set()
    for family, row_type in ((2, TcpRow), (23, Tcp6Row)):
        size = ctypes.c_uint32()
        result = api(None, ctypes.byref(size), False, family, 3, 0)  # TCP_TABLE_OWNER_PID_LISTENER
        if result not in (0, 122):  # ERROR_INSUFFICIENT_BUFFER
            raise ImageLocationError("无法核实本机 ComfyUI 监听进程，未打开文件夹")
        for _ in range(3):
            buffer = ctypes.create_string_buffer(size.value)
            result = api(buffer, ctypes.byref(size), False, family, 3, 0)
            if result != 122:
                break
        if result != 0:
            raise ImageLocationError("无法核实本机 ComfyUI 监听进程，未打开文件夹")
        count = ctypes.c_uint32.from_buffer(buffer).value
        if 4 + count * ctypes.sizeof(row_type) > len(buffer):
            raise ImageLocationError("本机 ComfyUI 监听信息无效，未打开文件夹")
        for index in range(count):
            row = row_type.from_buffer(buffer, 4 + index * ctypes.sizeof(row_type))
            if socket.ntohs(row.local_port & 0xFFFF) != port:
                continue
            address = row.local_address.to_bytes(4, "little") if family == 2 else bytes(row.local_address)
            allowed = {b"\0" * 4, b"\x7f\0\0\x01"} if family == 2 else {b"\0" * 16, b"\0" * 15 + b"\x01"}
            if address in allowed:
                pids.add(row.pid)
    return pids


def _process_details(process_id: int) -> dict[str, Any]:
    """Read the actual listener process without launching PowerShell/WMI."""
    class UnicodeString(ctypes.Structure):
        _fields_ = [("length", ctypes.c_uint16), ("maximum_length", ctypes.c_uint16), ("buffer", ctypes.c_void_p)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    ntdll.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    handle = kernel32.OpenProcess(0x1000, False, process_id)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        raise ImageLocationError("无法读取本机 ComfyUI 进程信息，未打开文件夹")
    try:
        image = ctypes.create_unicode_buffer(32768)
        image_size = ctypes.c_uint32(len(image))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(image_size)):
            raise ImageLocationError("无法读取本机 ComfyUI 进程路径，未打开文件夹")
        size = ctypes.c_uint32()
        # ProcessCommandLineInformation is available on supported Windows 10/11.
        ntdll.NtQueryInformationProcess(handle, 60, None, 0, ctypes.byref(size))
        if not ctypes.sizeof(UnicodeString) <= size.value <= 1024 * 1024:
            raise ImageLocationError("无法核实本机 ComfyUI 启动参数，未打开文件夹")
        buffer = ctypes.create_string_buffer(size.value)
        if ntdll.NtQueryInformationProcess(handle, 60, buffer, len(buffer), ctypes.byref(size)) < 0:
            raise ImageLocationError("无法核实本机 ComfyUI 启动参数，未打开文件夹")
        command = UnicodeString.from_buffer(buffer)
        start = ctypes.addressof(buffer)
        if not command.buffer or command.length % 2 or not start <= command.buffer <= start + len(buffer) - command.length:
            raise ImageLocationError("本机 ComfyUI 启动参数无效，未打开文件夹")
        return {"ProcessId": process_id, "ExecutablePath": image.value,
                "CommandLine": ctypes.wstring_at(command.buffer, command.length // 2)}
    finally:
        kernel32.CloseHandle(handle)


def _listening_processes(port: int) -> list[dict[str, Any]]:
    pids = _listening_process_ids(port)
    if len(pids) != 1:
        return []
    process = _process_details(next(iter(pids)))
    if _listening_process_ids(port) != pids:
        raise ImageLocationError("ComfyUI 监听进程已变化，请稍后重试")
    return [process]


def _command_line_args(command_line: str) -> list[str]:
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    pointer = shell32.CommandLineToArgvW(command_line, ctypes.byref(count))
    if not pointer:
        raise ImageLocationError("无法读取本机 ComfyUI 启动参数，未打开文件夹")
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        kernel32.LocalFree(pointer)


def _same_executable(command_path: str, actual_path: str) -> bool:
    if ntpath.normcase(command_path) == ntpath.normcase(actual_path):
        return True
    try:
        # QueryFullProcessImageNameW resolves directory junctions that may still
        # appear verbatim in the original process command line.
        return Path(command_path).samefile(actual_path)
    except OSError:
        return False


def verify_local_comfy(comfy_url: str, stats: dict[str, Any]) -> None:
    try:
        parsed = urlsplit(comfy_url)
        port = parsed.port if parsed.port is not None else 80
        valid = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        valid = valid and 1 <= port <= 65535
        valid = valid and not (parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.strip("/"))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ImageLocationError("只能定位直接运行在本机的 ComfyUI 原图")
    argv = _system_argv(stats)
    rows = _listening_processes(port)
    if len(rows) != 1:
        raise ImageLocationError("无法唯一确认本机 ComfyUI 进程，未打开文件夹")
    process = rows[0]
    executable, command_line = process.get("ExecutablePath"), process.get("CommandLine")
    if not isinstance(executable, str) or not re.fullmatch(r"python(?:w|[0-9]+(?:\.[0-9]+)*)?\.exe", ntpath.basename(executable), re.I):
        raise ImageLocationError("当前 ComfyUI 可能通过远程转发连接，无法定位本机原图")
    if not isinstance(command_line, str) or not command_line:
        raise ImageLocationError("无法核实本机 ComfyUI 启动参数，未打开文件夹")
    args = _command_line_args(command_line)
    main_index = next((index for index, value in enumerate(args[1:], 1)
                       if ntpath.normcase(value) == ntpath.normcase(argv[0])), None)
    # The script and every application argument must match the process owning
    # the actual listener. An SSH/proxy listener must never pass this check.
    interpreter_flags = {"-u", "-B", "-E", "-I", "-s", "-S", "-O", "-OO"}
    if (not args or not _same_executable(args[0], executable) or main_index is None
            or any(value not in interpreter_flags for value in args[1:main_index]) or args[main_index:] != argv):
        raise ImageLocationError("ComfyUI 启动信息与本机进程不一致，未打开文件夹")
    main = Path(argv[0])
    if not main.is_file() or not (main.parent / "folder_paths.py").is_file():
        raise ImageLocationError("无法确认本机 ComfyUI 安装目录，未打开文件夹")


def _select_file(path: Path) -> None:
    """Select a file without invoking a command shell or opening its contents."""
    ole32 = ctypes.WinDLL("ole32")
    shell32 = ctypes.WinDLL("shell32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    shell32.SHParseDisplayName.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint, ctypes.c_void_p]
    shell32.SHParseDisplayName.restype = ctypes.c_long
    shell32.ILFindLastID.argtypes = [ctypes.c_void_p]
    shell32.ILFindLastID.restype = ctypes.c_void_p
    shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint]
    shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    initialized = False
    folder = ctypes.c_void_p()
    item = ctypes.c_void_p()
    try:
        if ole32.CoInitializeEx(None, 2) < 0:  # An executor thread gets its own apartment.
            raise OSError("Shell apartment initialization failed")
        initialized = True
        parsed_folder = shell32.SHParseDisplayName(str(path.parent), None, ctypes.byref(folder), 0, None)
        if parsed_folder < 0 or not folder.value:
            raise OSError("Shell did not resolve the image folder")
        parsed = shell32.SHParseDisplayName(str(path), None, ctypes.byref(item), 0, None)
        if parsed < 0 or not item.value:
            raise OSError("Shell did not resolve the image")
        # Supply the folder and the exact relative child explicitly, keeping
        # both parent allocations alive until the selection request returns.
        child = shell32.ILFindLastID(item)
        if not child:
            raise OSError("Shell did not resolve the image child item")
        children = (ctypes.c_void_p * 1)(child)
        if shell32.SHOpenFolderAndSelectItems(folder, 1, children, 0) < 0:
            raise OSError("Shell selection failed")
    except OSError:
        raise ImageLocationError("资源管理器未能定位原图，请稍后重试", code="image_reveal_failed", status=503) from None
    finally:
        if item.value:
            ole32.CoTaskMemFree(item)
        if folder.value:
            ole32.CoTaskMemFree(folder)
        if initialized:
            ole32.CoUninitialize()


def reveal_image(record: dict[str, Any], stats: dict[str, Any], comfy_url: str) -> None:
    if not IS_WINDOWS:
        raise ImageLocationError("打开所在文件夹仅支持 Windows 本机使用", code="image_reveal_unsupported")
    directory = comfy_image_directory(stats, record.get("file_type") or "output")
    verify_local_comfy(comfy_url, stats)
    path = recorded_image_path(Path(directory), record)
    _select_file(path)
