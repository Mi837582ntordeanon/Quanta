"""Windows application inventory, window controls and Yandex Music shortcuts."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import socket
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any

from . import automation, cdp

CREATE_NO_WINDOW = 0x08000000
MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1}


def _start_apps() -> list[dict[str, str]]:
    powershell = automation._powershell()
    script = (
        "$ErrorActionPreference='Stop'; "
        "$json = @(Get-StartApps | Select-Object Name,AppID) | ConvertTo-Json -Compress; "
        "[Console]::Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))"
    )
    try:
        result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, encoding="ascii", errors="replace",
                                timeout=10, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise automation.AutomationError(f"Не удалось получить список приложений: {exc}") from exc
    encoded = result.stdout.strip()
    if result.returncode != 0 or not encoded:
        detail = result.stderr.strip() or "команда Get-StartApps недоступна"
        raise automation.AutomationError(f"Не удалось получить меню приложений: {detail[:300]}")
    try:
        data = json.loads(base64.b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise automation.AutomationError("Windows вернула некорректный список приложений") from exc
    if isinstance(data, dict):
        data = [data]
    return [
        {"name": str(item.get("Name") or "").strip(), "id": str(item.get("AppID") or "").strip()}
        for item in (data or []) if item.get("Name") and item.get("AppID")
    ]


def _normal(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def list_applications(query: str = "", limit: int = 120) -> list[dict[str, str]]:
    items = _start_apps()
    needle = _normal(query)
    if needle:
        items = [item for item in items if needle in _normal(item["name"]) or needle in _normal(item["id"])]
    items.sort(key=lambda item: item["name"].casefold())
    seen: set[str] = set()
    result = []
    for item in items:
        key = _normal(item["name"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= max(1, min(int(limit), 200)):
            break
    return result


def _match_application(query: str) -> dict[str, str]:
    needle = _normal(query)
    if not needle:
        raise ValueError("Укажи название приложения")
    items = _start_apps()
    exact = [item for item in items if _normal(item["name"]) == needle]
    matches = exact or [item for item in items if needle in _normal(item["name"])]
    unique = {item["id"]: item for item in matches}
    matches = list(unique.values())
    if not matches:
        raise FileNotFoundError(f"Приложение «{query}» не найдено в меню Пуск")
    if len(matches) > 1:
        names = ", ".join(item["name"] for item in matches[:8])
        raise ValueError(f"Нашла несколько приложений: {names}. Уточни название.")
    return matches[0]


def launch_application(query: str) -> dict[str, Any]:
    item = _match_application(query)
    app_id = item["id"]
    try:
        if app_id.lower().endswith(".lnk") and Path(app_id).is_file():
            os.startfile(app_id)  # type: ignore[attr-defined]
        elif "!" in app_id or app_id.lower().startswith("shell:"):
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"], close_fds=True)
        elif Path(app_id).is_file():
            os.startfile(app_id)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"], close_fds=True)
    except OSError as exc:
        return {"ok": False, "text": f"Не удалось открыть «{item['name']}»: {exc}"}
    return {"ok": True, "text": f"Открываю приложение «{item['name']}»."}


def list_windows(query: str = "", limit: int = 80) -> list[dict[str, Any]]:
    needle = _normal(query)
    windows = automation.list_windows(limit=limit)
    result: list[dict[str, Any]] = []
    for window in windows:
        process = _process_name(int(window.get("process_id") or 0))
        item = {**window, "process": process}
        if needle and needle not in _normal(item.get("title", "")) and needle not in _normal(process):
            continue
        result.append(item)
    return result[: max(1, min(int(limit), 120))]


def _process_name(pid: int) -> str:
    if os.name != "nt" or pid <= 0:
        return ""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        capacity = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(capacity)):
            return Path(buffer.value).name
        return ""
    finally:
        kernel32.CloseHandle(handle)


def focus_window(hwnd: int) -> dict[str, Any]:
    return automation.call({"op": "focus_window", "hwnd": int(hwnd)})


def close_window(hwnd: int) -> dict[str, Any]:
    if os.name != "nt":
        raise automation.AutomationError("Управление окнами доступно только в Windows")
    user32 = ctypes.windll.user32
    user32.IsWindow.argtypes = [ctypes.c_void_p]
    user32.IsWindow.restype = ctypes.c_int
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.PostMessageW.restype = ctypes.c_int
    if not user32.IsWindow(int(hwnd)):
        raise FileNotFoundError("Окно уже закрыто")
    # WM_CLOSE asks the application to close normally; it never force-kills the process.
    if not user32.PostMessageW(int(hwnd), 0x0010, 0, 0):
        raise OSError("Windows не приняла запрос на закрытие окна")
    return {"ok": True, "text": "Передала приложению запрос на обычное закрытие."}


def find_window(query: str) -> dict[str, Any]:
    needle = _normal(query)
    if not needle:
        raise ValueError("Укажи заголовок окна или имя приложения")
    matches = [item for item in list_windows(limit=120)
               if needle in _normal(item.get("title", "")) or needle in _normal(item.get("process", ""))]
    if not matches:
        raise FileNotFoundError(f"Окно «{query}» не найдено")
    if len(matches) > 1:
        titles = ", ".join(f"{item.get('title') or item.get('process')} (hwnd {item['hwnd']})" for item in matches[:6])
        raise ValueError(f"Подходит несколько окон: {titles}. Уточни заголовок.")
    return matches[0]


def focus_application(query: str) -> dict[str, Any]:
    window = find_window(query)
    result = focus_window(int(window["hwnd"]))
    return {**result, "ok": True, "text": f"Переключилась на окно «{window.get('title') or window.get('process')}».", "window": window}


def close_application(query: str) -> dict[str, Any]:
    window = find_window(query)
    result = close_window(int(window["hwnd"]))
    return {**result, "ok": True, "text": f"Отправила окну «{window.get('title') or window.get('process')}» запрос на закрытие.", "window": window}


def _music_norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", _normal(value))
    return "".join(ch for ch in value if not unicodedata.combining(ch) and ch.isalnum())


def _music_window(launch: bool = False) -> dict[str, Any]:
    matches = list_windows("Яндекс Музыка", limit=30)
    title_matches = [item for item in matches if "яндекс музыка" in _normal(item.get("title", ""))]
    matches = title_matches or [item for item in matches
                                if "яндекс музыка.exe" in _normal(item.get("process", ""))]
    if len(matches) > 1:
        main = [item for item in matches if _normal(item.get("title", "")) == _normal("Яндекс Музыка — собираем музыку для вас")]
        matches = main if len(main) == 1 else matches
    if len(matches) == 1:
        return matches[0]
    if matches:
        titles = ", ".join(str(item.get("title") or item.get("hwnd")) for item in matches[:5])
        raise ValueError(f"У Яндекс Музыки несколько окон: {titles}")
    if not launch:
        raise FileNotFoundError("Приложение Яндекс Музыка не запущено")
    result = launch_application("Яндекс Музыка")
    if not result.get("ok"):
        raise RuntimeError(result.get("text") or "Не удалось открыть Яндекс Музыку")
    for _ in range(20):
        time.sleep(0.4)
        candidates = list_windows("Яндекс Музыка", limit=30)
        matches = [item for item in candidates if "яндекс музыка" in _normal(item.get("title", ""))]
        matches = matches or [item for item in candidates
                              if "яндекс музыка.exe" in _normal(item.get("process", ""))]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError("У Яндекс Музыки открылось несколько окон; не стала выбирать наугад")
    raise TimeoutError("Яндекс Музыка не показала окно после запуска")


def _music_cdp_client() -> cdp.Client | None:
    for port in range(9222, 9242):
        client = cdp.Client(port, timeout=1.25)
        try:
            client.connect()
            url = str(client.evaluate("location.href") or "")
            if url.startswith("music-application://desktop"):
                return client
        except Exception:
            pass
        client.close()
    return None


def _start_music_with_cdp() -> cdp.Client:
    # Yandex Music is Electron. If it was opened without a debugging port,
    # ask its own window to close normally before restarting it with a loopback
    # CDP port. Never force-kill the user's music app or its process tree.
    try:
        window = _music_window()
    except FileNotFoundError:
        window = None
    if window:
        pid = int(window.get("process_id") or 0)
        close_window(int(window["hwnd"]))
        if pid > 0:
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel32.WaitForSingleObject.restype = ctypes.c_ulong
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            process = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if process:
                try:
                    for _ in range(40):
                        if kernel32.WaitForSingleObject(process, 0) == 0:
                            break
                        time.sleep(0.2)
                    else:
                        raise TimeoutError(
                            "Яндекс Музыка не закрылась штатно; оставила её работать и ничего не завершала принудительно"
                        )
                finally:
                    kernel32.CloseHandle(process)
        time.sleep(0.3)
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "YandexMusic" / "Яндекс Музыка.exe",
        Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "YandexMusic" / "Яндекс Музыка.exe",
    ]
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        raise FileNotFoundError("Не нашла исполняемый файл Яндекс Музыки для локального управления")
    port = None
    for candidate in range(9222, 9242):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.15)
            if probe.connect_ex(("127.0.0.1", candidate)) != 0:
                port = candidate
                break
    if port is None:
        raise RuntimeError("Локальные порты управления 9222–9241 заняты")
    subprocess.Popen(
        [str(executable), f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1"],
        cwd=str(executable.parent), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
    )
    for _ in range(60):
        time.sleep(0.25)
        client = _music_cdp_client()
        if client is not None:
            return client
    raise TimeoutError("Яндекс Музыка открылась, но локальный интерфейс управления не запустился")


def _ensure_music_cdp() -> cdp.Client:
    client = _music_cdp_client()
    return client if client is not None else _start_music_with_cdp()


def _music_search_and_play(query: str) -> dict[str, Any]:
    query = " ".join(str(query or "").split())[:200]
    if not query:
        return {"ok": False, "text": "Укажи исполнителя или название трека для поиска."}
    parts = re.split(r"\s+(?:[—–-]|by|от)\s+", query, maxsplit=1, flags=re.IGNORECASE)
    title_query = parts[0].strip()
    artist_query = parts[1].strip() if len(parts) > 1 else ""
    client = _ensure_music_cdp()
    try:
        opened_search = False
        for _ in range(40):
            opened_search = bool(client.evaluate(r'''(() => {
              if (document.querySelector('input[type="search"]')) return true;
              const search = [...document.querySelectorAll('a[href], [role="link"]')].find(a =>
                /\/search(?:[?#]|$)/i.test(a.getAttribute('href') || '') ||
                String(a.innerText || '').trim().toLocaleLowerCase() === 'поиск');
              if (!search) return false;
              search.click();
              return true;
            })()'''))
            if opened_search:
                break
            time.sleep(0.25)
        if not opened_search:
            return {"ok": False, "text": "Открыла приложение, но раздел поиска ещё не появился."}
        for _ in range(40):
            if client.evaluate("Boolean(document.querySelector('input[type=search]'))"):
                break
            time.sleep(0.25)
        else:
            return {"ok": False, "text": "Открыла Яндекс Музыку, но поле поиска не появилось."}
        client.evaluate(
            "(function(q){const e=document.querySelector('input[type=search]');"
            "if(!e)throw new Error('Поле поиска исчезло');e.focus();"
            "const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
            "s.call(e,q);e.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:q}));"
            "e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));"
            "e.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));"
            "return e.value})(" + json.dumps(query, ensure_ascii=False) + ")"
        )
        selector = r'''(function(title,artist){
          const norm=s=>String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu,'');
          const titleNorm=norm(title), artistNorm=norm(artist);
          const links=[...document.querySelectorAll('a[href*="/album/track"]')];
          const matches=links.map(a=>({a,row:a.closest('div[class*="HorizontalCardContainer_root"]')}))
            .filter(x=>x.row && norm(x.a.textContent.trim())===titleNorm);
          if(!matches.length) return {ok:false,reason:'not-found'};
          const chosen=artistNorm ? (matches.find(x=>{
            const names=[...x.row.querySelectorAll('a[href*="artist"]')].map(a=>a.textContent.trim());
            return norm(names.join(' ')).includes(artistNorm);
          }) || null) : matches[0];
          if(!chosen) return {ok:false,reason:'artist-not-found'};
          const {a,row}=chosen;
          const button=[...row.querySelectorAll('button[aria-label]')]
            .find(b=>/^(Воспроизведение|Пауза)/i.test(b.getAttribute('aria-label')||''));
          if(!button) return {ok:false,reason:'no-play-button'};
          const artists=[...row.querySelectorAll('a[href*="artist"]')].map(a=>a.textContent.trim()).filter(Boolean);
          const current=[...document.querySelectorAll('a[href*="/album/track"]')].at(-1);
          const playerButtons=[...document.querySelectorAll('button[aria-label]')]
            .filter(b=>/^(Пауза|Воспроизведение)$/i.test(b.getAttribute('aria-label')||''));
          const playerButton=playerButtons.at(-1);
          const isAlreadyPlaying=current?.href===a.href && /^Пауза$/i.test(playerButton?.getAttribute('aria-label')||'');
          const state=button.getAttribute('aria-label')||'';
          if(!isAlreadyPlaying && !/^Пауза/i.test(state)) button.click();
          return {ok:true,title:a.textContent.trim(),artists,href:a.href,clicked:!isAlreadyPlaying && !/^Пауза/i.test(state)};
        })(''' + json.dumps(title_query, ensure_ascii=False) + "," + json.dumps(artist_query, ensure_ascii=False) + ")"
        selection = None
        for _ in range(36):
            result = client.evaluate(selector)
            if result and result.get("ok"):
                selection = result
                break
            time.sleep(0.25)
        if selection is None:
            return {"ok": False, "text": f"Яндекс Музыка не нашла точный трек «{title_query}». Похожий не запускала."}
        expected_href = str(selection.get("href") or "")
        verify = r'''(() => {
          const a=[...document.querySelectorAll('a[href*="/album/track"]')].at(-1);
          const buttons=[...document.querySelectorAll('button[aria-label]')]
            .filter(b=>/^(Пауза|Воспроизведение)$/i.test(b.getAttribute('aria-label')||''));
          return {href:a?.href||'',title:(a?.textContent||'').trim(),play:buttons.at(-1)?.getAttribute('aria-label')||''};
        })()'''
        player = None
        for _ in range(24):
            time.sleep(0.25)
            player = client.evaluate(verify)
            if player and player.get("href") == expected_href and player.get("play") == "Пауза":
                artists = ", ".join(str(name) for name in (selection.get("artists") or []))
                label = f" — {artists}" if artists else ""
                return {"ok": True, "track": selection.get("title"), "artists": artists,
                        "text": f"Включила «{selection.get('title')}»{label} в Яндекс Музыке."}
        current_title = str((player or {}).get("title") or "")
        return {"ok": False, "text": f"Нашла «{selection.get('title') or title_query}», но плеер не подтвердил запуск. Сейчас в плеере: «{current_title or 'неизвестно'}»."}
    finally:
        client.close()


def _music_start_wave() -> dict[str, Any]:
    client = _ensure_music_cdp()
    try:
        result = None
        for _ in range(40):
            result = client.evaluate(r'''(() => {
              const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase();
              const link = [...document.querySelectorAll('[role="link"], a[href]')]
                .find(e => norm(e.innerText || e.getAttribute('aria-label')) === 'моя волна');
              if (!link) return {ok:false, reason:'wave-link-not-found'};
              link.click();
              return {ok:true};
            })()''')
            if result and result.get("ok"):
                break
            time.sleep(0.25)
        if not result or not result.get("ok"):
            return {"ok": False, "text": "В открытом интерфейсе Яндекс Музыки не нашла пункт «Моя волна». Ничего не запускала."}

        player = None
        for _ in range(40):
            time.sleep(0.25)
            player = client.evaluate(r'''(() => {
              const button = document.querySelector('button[class*="VibePlayerControls_playButton"]') ||
                [...document.querySelectorAll('button[aria-label]')].filter(b => {
                  const r = b.getBoundingClientRect();
                  return r.height > 0 && r.y > innerHeight - 180 && /^(Пауза|Воспроизведение)$/i.test(b.getAttribute('aria-label') || '');
                }).at(-1);
              const bar = document.querySelector('section[class*="VibePlayerBar"]');
              const lines = (bar?.innerText || '').trim().split('\n').map(line => line.trim()).filter(Boolean);
              const isTime = line => /^\d{1,2}:\d{2}\s*\/\s*\d{1,2}:\d{2}$/.test(line);
              return {play:button?.getAttribute('aria-label') || '', track:lines.find(line => !isTime(line)) || ''};
            })()''')
            if player and player.get("play") and player.get("track"):
                break
        if not player or not player.get("play"):
            return {"ok": False, "text": "Открыла раздел «Моя волна», но не смогла прочитать состояние плеера."}

        if str(player["play"]).casefold() != "пауза":
            clicked = client.evaluate(r'''(() => {
              const button = document.querySelector('button[class*="VibePlayerControls_playButton"]') ||
                [...document.querySelectorAll('button[aria-label]')].filter(b => {
                  const r = b.getBoundingClientRect();
                  return r.height > 0 && r.y > innerHeight - 180 && /^(Пауза|Воспроизведение)$/i.test(b.getAttribute('aria-label') || '');
                }).at(-1);
              if (!button || !/воспроизведение/i.test(button.getAttribute('aria-label') || '')) return false;
              button.click();
              return true;
            })()''')
            if not clicked:
                return {"ok": False, "text": "Раздел «Моя волна» открылся, но кнопка запуска плеера недоступна."}
            for _ in range(24):
                time.sleep(0.25)
                state = client.evaluate(r'''(() => {
                  const button = document.querySelector('button[class*="VibePlayerControls_playButton"]');
                  return button?.getAttribute('aria-label') || '';
                })()''')
                if str(state).casefold() == "пауза":
                    player["play"] = state
                    break
            else:
                return {"ok": False, "text": "Нажала запуск «Моей волны», но плеер не подтвердил воспроизведение."}

        track = str(player.get("track") or "").strip()
        text = "Включила «Мою волну» в Яндекс Музыке."
        if track:
            text += f" Плеер подтверждает трек «{track[:120]}»."
        return {"ok": True, "track": track, "text": text}
    finally:
        client.close()


def music_control(action: str, query: str = "") -> dict[str, Any]:
    action = str(action or "").strip().lower()
    if action == "open":
        try:
            client = _ensure_music_cdp()
            client.close()
            window = _music_window(launch=True)
            focus_window(int(window.get("hwnd") or 0))
            return {"ok": True, "text": "Открыла приложение Яндекс Музыка."}
        except Exception as exc:  # noqa: BLE001 - report an actionable desktop error
            return {"ok": False, "text": f"Не удалось открыть Яндекс Музыку: {exc}"}
    if action == "search":
        try:
            return _music_search_and_play(query)
        except Exception as exc:  # noqa: BLE001 - report desktop automation failures
            return {"ok": False, "text": f"Не удалось найти и запустить трек в Яндекс Музыке: {exc}"}
    if action == "wave":
        try:
            return _music_start_wave()
        except Exception as exc:  # noqa: BLE001 - report desktop automation failures
            return {"ok": False, "text": f"Не удалось включить «Мою волну» в Яндекс Музыке: {exc}"}
    vk = MEDIA_KEYS.get(action)
    if vk is None:
        return {"ok": False, "text": "Поддерживаются действия: open, search, play_pause, next, previous."}
    if os.name != "nt":
        return {"ok": False, "text": "Медиаклавиши доступны только в Windows."}
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 0x0002, 0)
    labels = {"play_pause": "пауза или продолжение", "next": "следующий трек", "previous": "предыдущий трек"}
    return {"ok": True, "text": f"Отправила системную медиаклавишу: {labels[action]}."
            " Она действует на активную музыкальную сессию Windows."}
