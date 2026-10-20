"""Minimal Windows Job Object wrapper for bounded, cancellable workers."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


if os.name == "nt":
    _SIZE_T = ctypes.c_size_t
    _ULONG_PTR = ctypes.c_size_t
    _ULONGLONG = ctypes.c_ulonglong
    _LARGE_INTEGER = ctypes.c_longlong

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", _LARGE_INTEGER),
            ("PerJobUserTimeLimit", _LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", _SIZE_T),
            ("MaximumWorkingSetSize", _SIZE_T),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", _ULONG_PTR),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", _ULONGLONG),
            ("WriteOperationCount", _ULONGLONG),
            ("OtherOperationCount", _ULONGLONG),
            ("ReadTransferCount", _ULONGLONG),
            ("WriteTransferCount", _ULONGLONG),
            ("OtherTransferCount", _ULONGLONG),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", _SIZE_T),
            ("JobMemoryLimit", _SIZE_T),
            ("PeakProcessMemoryUsed", _SIZE_T),
            ("PeakJobMemoryUsed", _SIZE_T),
        ]


class WindowsJobObject:
    """Attach one child process to a memory-limited, kill-on-close job."""

    def __init__(self, memory_limit_bytes: int):
        self.memory_limit_bytes = memory_limit_bytes
        self.handle = None
        self._kernel32 = None
        self._memory_limited = False

    def attach(self, pid: int) -> None:
        if os.name != "nt":
            return

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32 = self._kernel32
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
        ]
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = handle

        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00000100 | 0x00002000
        info.ProcessMemoryLimit = self.memory_limit_bytes
        ok = kernel32.SetInformationJobObject(
            handle,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

        process_handle = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process_handle:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not kernel32.AssignProcessToJobObject(handle, process_handle):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise
        finally:
            kernel32.CloseHandle(process_handle)

    @property
    def memory_limited(self) -> bool:
        if os.name != "nt" or not self.handle or not self._kernel32:
            return self._memory_limited
        info = _ExtendedLimitInformation()
        returned = wintypes.DWORD()
        ok = self._kernel32.QueryInformationJobObject(
            self.handle,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
            ctypes.byref(returned),
        )
        if ok and int(info.PeakProcessMemoryUsed) >= self.memory_limit_bytes:
            self._memory_limited = True
        return self._memory_limited

    def close(self) -> None:
        if self.handle and self._kernel32:
            # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE is set, so this also guarantees
            # descendants do not outlive a cancelled worker.
            self._kernel32.CloseHandle(self.handle)
            self.handle = None
