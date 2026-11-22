# ruff: noqa: F403,F405
"""Public Toolbox API composed from focused implementation mixins."""

from .common import *  # noqa: F403
from .creative import CreativeToolsMixin
from .filesystem import FilesystemToolsMixin
from .memory import MemoryToolsMixin
from .pc import PcToolsMixin
from .weather import WeatherToolsMixin
from .web import WebToolsMixin


class Toolbox(FilesystemToolsMixin, PcToolsMixin, WebToolsMixin, MemoryToolsMixin,
               CreativeToolsMixin, WeatherToolsMixin):
    def __init__(self, store: Store, *, minecraft=None, vision=None) -> None:
        self.store = store
        self.minecraft = minecraft
        self.vision = vision
        self.tools: dict[str, dict[str, Any]] = {}
        self._weather_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        from .registry import register_tool_methods
        register_tool_methods(self)

    def add(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        func: Callable[..., Any],
        *,
        label: str,
        risk: str = "read",
        confirm: bool = False,
        capabilities: tuple[str, ...] = ("legacy",),
        network: bool = True,
    ) -> None:
        self.tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "func": func,
            "label": label,
            "risk": risk,
            "confirm": confirm,
            "capabilities": tuple(capabilities),
            "network": bool(network),
        }

    def schemas(self, names: list[str] | set[str] | None = None) -> list[dict[str, Any]]:
        result = []
        selected_names = None if names is None else set(names)
        selected = (tool for name, tool in self.tools.items() if selected_names is None or name in selected_names)
        for tool in selected:
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            })
        return result

    def label(self, name: str) -> str:
        return self.tools.get(name, {}).get("label", name)

    def risk(self, name: str) -> str:
        return self.tools.get(name, {}).get("risk", "high")

    def allowed(self, mode: str, name: str) -> bool:
        if name not in self.tools:
            return False
        if mode == "safe":
            return self.risk(name) in ("read", "low")
        return True

    def needs_confirm(self, mode: str, name: str) -> bool:
        if name not in self.tools:
            return False
        if mode == "safe":
            return False
        # Auto mode may skip confirmation for read-only and routine low-risk
        # helpers, but it must never silently run commands or other high-risk
        # actions. The access mode is not a trust boundary for model output.
        if self.risk(name) == "high":
            return True
        if mode == "auto":
            return False
        return bool(self.tools[name].get("confirm"))

    def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if not tool:
            return {"ok": False, "text": f"Неизвестный инструмент: {name}"}
        args = args if isinstance(args, dict) else {}
        func = tool["func"]
        try:
            signature = inspect.signature(func)
            accepted = {k: v for k, v in args.items() if k in signature.parameters}
            result = func(**accepted)
        except TypeError as exc:
            return {"ok": False, "text": f"Неверные аргументы инструмента {name}: {exc}"}
        except Exception as exc:  # noqa: BLE001 - tools must never crash the agent
            return {"ok": False, "text": f"Ошибка инструмента {name}: {exc}"}

        if isinstance(result, dict):
            result.setdefault("ok", True)
            if "text" not in result:
                result["text"] = json.dumps(result, ensure_ascii=False)[:4000]
            result["text"] = _truncate(str(result["text"]))
            return result
        return {"ok": True, "text": _truncate(str(result))}

    def _resolve(self, path: str | None) -> Path:
        raw = str(path or "").strip().strip('"')
        configured_root = self.store.settings.get("work_dir") or str(Path.home())
        configured_root = os.path.expandvars(os.path.expanduser(str(configured_root)))
        root = Path(configured_root).resolve()
        if not raw:
            raw = str(root)
        raw = os.path.expandvars(os.path.expanduser(raw))
        p = Path(raw)
        if not p.is_absolute():
            p = root / p
        resolved = p.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Путь за пределами рабочей папки ({root}) запрещён. Измени её в настройках, если нужен другой каталог.") from exc
        return resolved

    def sensitive_file_path(self, path: str | None) -> Path | None:
        """Return the resolved path when it could contain credentials or private app data."""
        target = self._resolve(path)
        parts = [part.casefold() for part in target.parts]
        name = target.name.casefold()

        try:
            target.relative_to(Path(self.store.settings_path.parent).resolve())
            return target
        except ValueError:
            pass

        if name in {
            ".envrc", ".npmrc", ".pypirc", ".git-credentials", ".netrc",
            "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "known_hosts", "ntuser.dat",
        }:
            return target
        if name.endswith((".pem", ".pfx", ".kdbx")):
            return target
        if len(parts) >= 2 and parts[-2:] == [".docker", "config.json"]:
            return target
        if name == ".env" or name.startswith(".env."):
            return target
        if any(part in {".ssh", ".aws", ".azure", ".gnupg", ".kube", "credentials", "credential"}
               for part in parts):
            return target
        if name in {
            "login data", "cookies", "cookies.sqlite", "web data", "local state",
            "logins.json", "key4.db", "key3.db", "cert9.db", "formhistory.sqlite",
            "signons.sqlite", "passwords.json", "credentials.json", "vault.db",
        }:
            return target
        browser_names = {"chrome", "chromium", "edge", "brave", "firefox", "mozilla"}
        if any(part in browser_names for part in parts) and any(part in {"user data", "profiles"} for part in parts):
            return target
        return None
