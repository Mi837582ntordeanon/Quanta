# ruff: noqa: F403,F405
"""Tool implementations grouped by web domain."""

from .common import *  # noqa: F403
from .decorators import tool


class WebToolsMixin:
    @tool(
        name='web_search', label='Поиск в интернете',
        risk='read', confirm=False,
        capabilities=('сеть_наружу',),
        description='Search the web via DuckDuckGo and return titles, URLs and snippets. Requires internet.',
        parameters=schema(query=prop('string', 'Search query.', required=True), limit=prop('integer', 'Number of results (1-10).', default=5)),
    )
    def _web_search(self, query: str, limit: int = 5) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"ok": False, "text": "Пустой поисковый запрос"}
        limit = max(1, min(10, int(limit or 5)))
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        try:
            page_raw, _content_type, _final_url = _safe_get(
                url, timeout=20,
                headers={"Accept": "text/html,application/xhtml+xml",
                         "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
            )
            page = page_raw.decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            return {"ok": False, "text": f"Поиск недоступен: {exc}"}

        links = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
        if not links:
            lite_url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote_plus(query)
            try:
                lite_raw, _content_type, _final_url = _safe_get(
                    lite_url, timeout=20, headers={"Accept-Language": "ru-RU,ru;q=0.9"}
                )
                lite_page = lite_raw.decode("utf-8", "replace")
                links = re.findall(
                    r'<a[^>]+href="([^"]+)"[^>]+class=["\']result-link["\'][^>]*>(.*?)</a>',
                    lite_page, re.S,
                )
                snippets = re.findall(r'<td[^>]+class=["\']result-snippet["\'][^>]*>(.*?)</td>', lite_page, re.S)
            except Exception:
                pass
        results = []
        for index, (href, title) in enumerate(links):
            href = html.unescape(href)
            if href.startswith("//"):
                href = "https:" + href
            if "uddg=" in href:
                try:
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    href = parsed.get("uddg", [href])[0]
                except Exception:
                    pass
            snippet = _strip_tags(snippets[index]) if index < len(snippets) else ""
            results.append((_strip_tags(title), href, snippet))
            if len(results) >= limit:
                break
        if not results:
            return {
                "ok": False,
                "text": "DuckDuckGo не вернул результаты (возможно, временно блокирует запросы). "
                        "Попробуй открыть страницу напрямую через fetch_url.",
            }
        lines = [f"Результаты поиска: {query}", ""]
        for index, (title, href, snippet) in enumerate(results, 1):
            lines.append(f"{index}. {title}\n   {href}")
            if snippet:
                lines.append(f"   {snippet[:300]}")
        return {"ok": True, "text": "\n".join(lines)}

    @tool(
        name='fetch_url', label='Загрузка страницы',
        risk='read', confirm=False,
        capabilities=('сеть_наружу',),
        description='Download a web page and return its readable text. Requires internet.',
        parameters=schema(url=prop('string', 'HTTP(S) URL.', required=True), max_chars=prop('integer', 'Maximum characters to return.', default=12000)),
    )
    def _fetch_url(self, url: str, max_chars: int = 12000) -> dict[str, Any]:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "text": "URL должен начинаться с http:// или https://"}
        try:
            raw, content_type, _final_url = _safe_get(
                url, timeout=25, headers={"Accept": "text/html,text/plain,*/*"}, max_bytes=2_000_000
            )
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            return {"ok": False, "text": f"Не удалось загрузить страницу: {exc}"}
        if b"\0" in raw[:4096]:
            return {"ok": False, "text": "Это не текстовая страница."}
        if content_type and not any(token in content_type for token in ("text", "html", "json", "xml")):
            return {"ok": False, "text": f"Это не текстовая страница ({content_type})."}
        text = ""
        for encoding in ("utf-8", "cp1251"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            text = raw.decode("utf-8", "replace")
        if "html" in content_type or text.lstrip().startswith("<"):
            parser = _TextExtractor()
            try:
                parser.feed(text)
                title = parser.title.strip()
                text = parser.text()
            except Exception:
                text = _strip_tags(text)
            header = f"Страница: {url}"
            if title:
                header += f"\nЗаголовок: {title}"
            text = header + "\n\n" + text
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n… [обрезано, всего {len(text)} символов]"
        return {"ok": True, "text": text}
