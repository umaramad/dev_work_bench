"""KeychainService — secure secret storage backed by the macOS Keychain.

The native path talks to ``Security.framework`` + ``CoreFoundation`` through
ctypes (no third-party dependency, no subprocess, no argv exposure). When the
framework is unavailable — non-macOS hosts, CI containers, offscreen tests —
secrets fall back to a 0600 file under the application support directory so
the application keeps working in development. Secrets never touch SQLite.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import platform
import threading
from pathlib import Path

logger = logging.getLogger("devworkbench.services.keychain")

_SECURITY_PATH = "/System/Library/Frameworks/Security.framework/Security"
_COREFOUNDATION_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

# SecItem constants (values from the Security framework headers)
_K_SEC_CLASS = b"class"
_K_SEC_CLASS_GENERIC_PASSWORD = b"genp"
_K_SEC_ATTR_SERVICE = b"svce"
_K_SEC_ATTR_ACCOUNT = b"acct"
_K_SEC_VALUE_DATA = b"v_Data"
_K_SEC_RETURN_DATA = b"r_Data"
_K_SEC_MATCH_LIMIT = b"m_Limit"
_K_SEC_MATCH_LIMIT_ONE = b"m_LimitOne"

_ERR_SEC_SUCCESS = 0
_ERR_SEC_ITEM_NOT_FOUND = -25300
_ERR_SEC_DUPLICATE_ITEM = -25299

_K_CF_STRING_UTF8 = 0x08000100
_K_CF_NUMBER_INT = 9  # kCFNumberIntType


class KeychainError(Exception):
    """Raised when a secret cannot be stored or retrieved."""


# ---------------------------------------------------------------------------
# Native macOS Keychain (Security.framework via ctypes)
# ---------------------------------------------------------------------------


class _CFRange(ctypes.Structure):
    _fields_ = [("location", ctypes.c_long), ("length", ctypes.c_long)]


class _NativeKeychain:
    """Thin ctypes wrapper over the macOS Security framework."""

    def __init__(self) -> None:
        self._cf = self._load(_COREFOUNDATION_PATH)
        self._sec = self._load(_SECURITY_PATH)
        self._available = self._cf is not None and self._sec is not None
        if self._available:
            self._bind_functions()

    # -- loading ------------------------------------------------------------

    @staticmethod
    def _load(path: str):
        try:
            return ctypes.CDLL(path)
        except OSError:
            return None

    def _bind_functions(self) -> None:
        cf, sec = self._cf, self._sec

        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFDataCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        cf.CFDataCreate.restype = ctypes.c_void_p
        cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
        cf.CFDictionaryCreate.restype = ctypes.c_void_p
        cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cf.CFNumberCreate.restype = ctypes.c_void_p
        cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
        cf.CFDataGetLength.restype = ctypes.c_long
        cf.CFDataGetBytes.argtypes = [ctypes.c_void_p, _CFRange, ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        sec.SecItemCopyMatching.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        sec.SecItemCopyMatching.restype = ctypes.c_int
        sec.SecItemAdd.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        sec.SecItemAdd.restype = ctypes.c_int
        sec.SecItemUpdate.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        sec.SecItemUpdate.restype = ctypes.c_int
        sec.SecItemDelete.argtypes = [ctypes.c_void_p]
        sec.SecItemDelete.restype = ctypes.c_int

    # -- CF helpers -----------------------------------------------------------

    def _cf_string(self, value: str):
        return self._cf.CFStringCreateWithCString(
            None, value.encode("utf-8"), _K_CF_STRING_UTF8
        )

    def _cf_data(self, payload: bytes):
        buffer = ctypes.create_string_buffer(payload, len(payload))
        return self._cf.CFDataCreate(None, buffer, len(payload))

    def _cf_bool(self, value: bool):
        number = ctypes.c_int(1 if value else 0)
        return self._cf.CFNumberCreate(None, _K_CF_NUMBER_INT, ctypes.byref(number))

    def _make_dict(self, items: list[tuple[bytes, bytes | None, object]]) -> ctypes.c_void_p:
        """Build a CFDictionary via ``CFDictionaryCreate`` with kCFType callbacks.

        Each tuple is (key bytes, str-or-bytes value, prebuilt CF ref value);
        exactly one of the last two is provided. The kCFType callbacks matter:
        ``CFDictionaryCreateMutable`` with NULL callbacks falls back to
        pointer-identity keys, which Security's own constant keys never match
        (that returned ``errSecParam -50`` for every query).
        """
        cf = self._cf
        key_callbacks = ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
        value_callbacks = ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")

        count = len(items)
        keys = (ctypes.c_void_p * count)()
        values = (ctypes.c_void_p * count)()
        owned: list[ctypes.c_void_p] = []
        for index, (key, text_value, ref_value) in enumerate(items):
            key_ref = self._cf_string(key.decode("utf-8"))
            owned.append(key_ref)
            keys[index] = key_ref
            if ref_value is not None:
                values[index] = ref_value
            else:
                value_ref = self._cf_string(text_value.decode("utf-8"))
                owned.append(value_ref)
                values[index] = value_ref
        try:
            # CFDictionaryCreate retains key/value copies, so the owned refs
            # are released here; prebuilt refs are released by their callers.
            return cf.CFDictionaryCreate(
                None, keys, values, count,
                ctypes.byref(key_callbacks), ctypes.byref(value_callbacks),
            )
        finally:
            for ref in owned:
                cf.CFRelease(ref)

    def _data_to_bytes(self, data_ref) -> bytes | None:
        if not data_ref:
            return None
        try:
            length = self._cf.CFDataGetLength(data_ref)
            buffer = ctypes.create_string_buffer(length)
            self._cf.CFDataGetBytes(data_ref, _CFRange(0, length), buffer)
            return buffer.raw[:length]
        finally:
            self._cf.CFRelease(data_ref)

    # -- operations -------------------------------------------------------------

    def get(self, service: str, account: str) -> str | None:
        return_ref = self._cf_bool(True)
        limit_ref = self._cf_string(_K_SEC_MATCH_LIMIT_ONE.decode("utf-8"))
        query = self._make_dict(
            [
                (_K_SEC_CLASS, _K_SEC_CLASS_GENERIC_PASSWORD, None),
                (_K_SEC_ATTR_SERVICE, service.encode(), None),
                (_K_SEC_ATTR_ACCOUNT, account.encode(), None),
                (_K_SEC_RETURN_DATA, None, return_ref),
                (_K_SEC_MATCH_LIMIT, None, limit_ref),
            ]
        )
        result = ctypes.c_void_p()
        try:
            status = self._sec.SecItemCopyMatching(query, ctypes.byref(result))
        finally:
            self._cf.CFRelease(query)
            self._cf.CFRelease(return_ref)
            self._cf.CFRelease(limit_ref)
        if status == _ERR_SEC_ITEM_NOT_FOUND:
            return None
        if status != _ERR_SEC_SUCCESS:
            raise KeychainError(f"SecItemCopyMatching failed with status {status}")
        payload = self._data_to_bytes(result)
        if payload is None:
            return None
        return payload.decode("utf-8", errors="replace")

    def set(self, service: str, account: str, secret: str) -> None:
        service_ref = self._cf_string(service)
        account_ref = self._cf_string(account)
        data_ref = self._cf_data(secret.encode("utf-8"))
        attributes = self._make_dict(
            [
                (_K_SEC_CLASS, _K_SEC_CLASS_GENERIC_PASSWORD, None),
                (_K_SEC_ATTR_SERVICE, None, service_ref),
                (_K_SEC_ATTR_ACCOUNT, None, account_ref),
                (_K_SEC_VALUE_DATA, None, data_ref),
            ]
        )
        try:
            status = self._sec.SecItemAdd(attributes, None)
            if status == _ERR_SEC_SUCCESS:
                return
            if status == _ERR_SEC_DUPLICATE_ITEM:
                query = self._make_dict(
                    [
                        (_K_SEC_CLASS, _K_SEC_CLASS_GENERIC_PASSWORD, None),
                        (_K_SEC_ATTR_SERVICE, None, service_ref),
                        (_K_SEC_ATTR_ACCOUNT, None, account_ref),
                    ]
                )
                update = self._make_dict([(_K_SEC_VALUE_DATA, None, data_ref)])
                try:
                    status = self._sec.SecItemUpdate(query, update)
                finally:
                    self._cf.CFRelease(query)
                    self._cf.CFRelease(update)
                if status != _ERR_SEC_SUCCESS:
                    raise KeychainError(f"SecItemUpdate failed with status {status}")
                return
            raise KeychainError(f"SecItemAdd failed with status {status}")
        finally:
            self._cf.CFRelease(attributes)
            self._cf.CFRelease(service_ref)
            self._cf.CFRelease(account_ref)
            self._cf.CFRelease(data_ref)

    def delete(self, service: str, account: str) -> bool:
        query = self._make_dict(
            [
                (_K_SEC_CLASS, _K_SEC_CLASS_GENERIC_PASSWORD, None),
                (_K_SEC_ATTR_SERVICE, service.encode(), None),
                (_K_SEC_ATTR_ACCOUNT, account.encode(), None),
            ]
        )
        try:
            status = self._sec.SecItemDelete(query)
        finally:
            self._cf.CFRelease(query)
        if status == _ERR_SEC_ITEM_NOT_FOUND:
            return False
        if status != _ERR_SEC_SUCCESS:
            raise KeychainError(f"SecItemDelete failed with status {status}")
        return True


# ---------------------------------------------------------------------------
# Development fallback: 0600 JSON file (never used in the packaged app)
# ---------------------------------------------------------------------------


class _FileSecretStore:
    """Dev-mode secret store: a 0600 JSON file, written atomically."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, str]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict[str, str]) -> None:
        temporary = self._path.with_name(self._path.name + ".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._path)
        try:
            os.chmod(self._path, 0o600)
        except OSError:  # pragma: no cover - permission edge on exotic mounts
            pass

    @staticmethod
    def _key(service: str, account: str) -> str:
        return f"{service}\u241f{account}"

    def get(self, service: str, account: str) -> str | None:
        return self._load().get(self._key(service, account))

    def set(self, service: str, account: str, secret: str) -> None:
        with self._lock:
            data = self._load()
            data[self._key(service, account)] = secret
            self._save(data)

    def delete(self, service: str, account: str) -> bool:
        with self._lock:
            data = self._load()
            key = self._key(service, account)
            existed = key in data
            if existed:
                data.pop(key)
                self._save(data)
            return existed


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------


class KeychainService:
    """Secure secret storage: native Keychain with a dev fallback store.

    ``use_native`` is ``None`` (auto-detect) in production; pass ``False`` in
    tests so they never touch the real user Keychain.
    """

    def __init__(
        self,
        service: str = "DevWorkbench",
        fallback_path: str | Path | None = None,
        use_native: bool | None = None,
    ) -> None:
        self._service = service
        self._fallback = _FileSecretStore(Path(fallback_path)) if fallback_path else None

        if use_native is False or platform.system() != "Darwin":
            self._native = None
        else:
            native = _NativeKeychain()
            self._native = native if native._available else None

        logger.info(
            "KeychainService: native=%s fallback=%s",
            self._native is not None,
            fallback_path is not None,
        )

    # -- state -----------------------------------------------------------------

    def is_native(self) -> bool:
        """True when secrets go to the real macOS Keychain."""
        return self._native is not None

    @property
    def service(self) -> str:
        return self._service

    # -- public surface -----------------------------------------------------------

    def get(self, service: str | None = None, account: str | None = None) -> str | None:
        """Return the stored secret or None."""
        service = service or self._service
        if account is None:
            raise KeychainError("account is required")
        if self._native is not None:
            return self._native.get(service, account)
        if self._fallback is not None:
            return self._fallback.get(service, account)
        raise KeychainError("no keychain backend available")

    def set(self, service: str | None = None, account: str | None = None, secret: str | None = None) -> None:
        """Store or update ``secret`` for ``account`` under ``service``."""
        service = service or self._service
        if account is None or secret is None:
            raise KeychainError("account and secret are required")
        if self._native is not None:
            self._native.set(service, account, secret)
        elif self._fallback is not None:
            self._fallback.set(service, account, secret)
        else:
            raise KeychainError("no keychain backend available")

    def delete(self, service: str | None = None, account: str | None = None) -> bool:
        """Remove a stored secret; returns True if it existed."""
        service = service or self._service
        if account is None:
            raise KeychainError("account is required")
        if self._native is not None:
            return self._native.delete(service, account)
        if self._fallback is not None:
            return self._fallback.delete(service, account)
        raise KeychainError("no keychain backend available")
