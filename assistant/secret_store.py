"""Windows DPAPI storage for provider credentials."""
from __future__ import annotations

import base64
import binascii
import ctypes
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from ctypes import wintypes

SECRET_MASK = "********"
_SECRET_ENTROPY = b"Q.W.A.N.T.A. API secrets v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecretStorageError(RuntimeError):
    """Raised when credentials cannot be read or protected safely."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _input_blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buffer


def _dpapi_transform(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise SecretStorageError("Защищённое хранилище ключей доступно только в Windows")
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as exc:
        raise SecretStorageError("Не удалось загрузить Windows DPAPI") from exc

    input_data, input_buffer = _input_blob(data)
    entropy, entropy_buffer = _input_blob(_SECRET_ENTROPY)
    output = _DataBlob()
    description = ctypes.c_void_p()
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    try:
        if decrypt:
            transform = crypt32.CryptUnprotectData
            transform.argtypes = [
                ctypes.POINTER(_DataBlob), ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                wintypes.DWORD, ctypes.POINTER(_DataBlob),
            ]
            transform.restype = wintypes.BOOL
            succeeded = transform(
                ctypes.byref(input_data), ctypes.byref(description), ctypes.byref(entropy),
                None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
            )
        else:
            transform = crypt32.CryptProtectData
            transform.argtypes = [
                ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
                ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                ctypes.POINTER(_DataBlob),
            ]
            transform.restype = wintypes.BOOL
            succeeded = transform(
                ctypes.byref(input_data), "Q.W.A.N.T.A. provider credentials",
                ctypes.byref(entropy), None, None, _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output),
            )
        if not succeeded:
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(output.pbData, output.cbData)
    except OSError as exc:
        action = "расшифровать" if decrypt else "зашифровать"
        raise SecretStorageError(f"Windows DPAPI не смог {action} ключи") from exc
    finally:
        if output.pbData:
            local_free(ctypes.cast(output.pbData, ctypes.c_void_p))
        if description:
            local_free(description)
        # Keep input buffers alive until the DPAPI call and release complete.
        _ = input_buffer, entropy_buffer


def _dpapi_protect(data: bytes) -> bytes:
    return _dpapi_transform(data, decrypt=False)


def _dpapi_unprotect(data: bytes) -> bytes:
    return _dpapi_transform(data, decrypt=True)


class DPAPISecretStore:
    """Persist a JSON credential map as a DPAPI-protected opaque payload."""

    def __init__(
        self,
        path: Path,
        *,
        protect: Callable[[bytes], bytes] | None = None,
        unprotect: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self.path = Path(path)
        self._protect = protect or _dpapi_protect
        self._unprotect = unprotect or _dpapi_unprotect
        self._lock = threading.RLock()

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                envelope = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(envelope, dict) or envelope.get("version") != 1:
                    raise ValueError("unsupported version")
                protected = base64.b64decode(envelope.get("protected", ""), validate=True)
                payload = self._unprotect(protected)
                value = json.loads(payload.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("invalid payload")
                return value
            except SecretStorageError:
                raise
            except (OSError, ValueError, TypeError, UnicodeError, binascii.Error) as exc:
                raise SecretStorageError("Не удалось прочитать защищённое хранилище API-ключей") from exc

    def save(self, values: dict[str, Any]) -> None:
        if not isinstance(values, dict):
            raise ValueError("Хранилище API-ключей ожидает объект")
        payload = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        protected = self._protect(payload)
        envelope = json.dumps({
            "version": 1,
            "protected": base64.b64encode(protected).decode("ascii"),
        }, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="", dir=self.path.parent,
                    prefix=self.path.name + ".", suffix=".tmp", delete=False,
                ) as handle:
                    temp_path = Path(handle.name)
                    handle.write(envelope)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self.path)
            except OSError as exc:
                raise SecretStorageError("Не удалось сохранить защищённое хранилище API-ключей") from exc
            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError:
                        pass
