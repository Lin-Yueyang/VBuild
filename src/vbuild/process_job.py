"""An owned Windows job stops the installer and every child it starts."""

import ctypes
from ctypes import wintypes


class ProcessJob:
    def __init__(self, pid):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, arguments in (('CreateJobObjectW', [ctypes.c_void_p, wintypes.LPCWSTR]),
                                ('OpenProcess', [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])):
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, wintypes.HANDLE
        for name, arguments in (('AssignProcessToJobObject', [wintypes.HANDLE, wintypes.HANDLE]),
                                ('TerminateJobObject', [wintypes.HANDLE, wintypes.UINT]),
                                ('CloseHandle', [wintypes.HANDLE])):
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        process = self.api.OpenProcess(0x0101, False, pid)
        if not process:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)
        assigned = self.api.AssignProcessToJobObject(self.handle, process)
        error = ctypes.get_last_error()
        self.api.CloseHandle(process)
        if not assigned:
            self.close()
            raise ctypes.WinError(error)

    def stop(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        self.api.CloseHandle(self.handle)
