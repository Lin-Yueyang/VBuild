"""Materials Project key in the macOS login Keychain, outside project files/logs."""

import ctypes as c

SERVICE, ACCOUNT = b'org.vbuild.materialsproject', b'MP_API_KEY'


def library():
    security = c.CDLL('/System/Library/Frameworks/Security.framework/Security')
    security.SecKeychainFindGenericPassword.argtypes = [c.c_void_p, c.c_uint32, c.c_char_p,
        c.c_uint32, c.c_char_p, c.POINTER(c.c_uint32), c.POINTER(c.c_void_p), c.POINTER(c.c_void_p)]
    security.SecKeychainFindGenericPassword.restype = c.c_int32
    security.SecKeychainFreeContent.argtypes = [c.c_void_p, c.c_void_p]
    security.SecKeychainAddGenericPassword.argtypes = [c.c_void_p, c.c_uint32, c.c_char_p,
        c.c_uint32, c.c_char_p, c.c_uint32, c.c_char_p, c.c_void_p]
    security.SecKeychainAddGenericPassword.restype = c.c_int32
    security.SecKeychainItemModifyAttributesAndData.argtypes = [c.c_void_p, c.c_void_p, c.c_uint32, c.c_char_p]
    security.SecKeychainItemModifyAttributesAndData.restype = c.c_int32
    return security


def check(status):
    if status:
        raise OSError(status, '无法访问 Mac 钥匙串；请检查钥匙串授权。')


def read():
    security = library()
    length, data = c.c_uint32(), c.c_void_p()
    status = security.SecKeychainFindGenericPassword(None, len(SERVICE), SERVICE, len(ACCOUNT), ACCOUNT,
                                                   c.byref(length), c.byref(data), None)
    if status == -25300:  # errSecItemNotFound; this user has not saved a key.
        return None
    check(status)
    value = c.string_at(data, length.value)
    security.SecKeychainFreeContent(None, data)
    return value.decode('ascii')


def save(key):
    security = library()
    data = key.encode('ascii')
    status = security.SecKeychainAddGenericPassword(None, len(SERVICE), SERVICE, len(ACCOUNT), ACCOUNT,
                                                   len(data), data, None)
    if status == -25299:  # errSecDuplicateItem; update the existing item.
        item = c.c_void_p()
        check(security.SecKeychainFindGenericPassword(None, len(SERVICE), SERVICE, len(ACCOUNT), ACCOUNT,
                                                     None, None, c.byref(item)))
        status = security.SecKeychainItemModifyAttributesAndData(item, None, len(data), data)
        foundation = c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        foundation.CFRelease.argtypes = [c.c_void_p]
        foundation.CFRelease.restype = None
        foundation.CFRelease(item)
    check(status)
