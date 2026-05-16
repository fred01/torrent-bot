#!/usr/bin/env python3
"""
Experiment harness: RuTracker search + LLM intent parsing & relevance filtering.

Not imported by the bot. Standalone, for trying models/prompts on real output.

Usage:
  RUTRACKER_USERNAME=... RUTRACKER_PASSWORD=... \
  AI_LLM_API_BASE_URL=... AI_LLM_API_KEY=... \
  venv/bin/python experiments/llm_search_lab.py "игра Warcraft III" --models qwen3:14b gemma3:12b
"""

import os
import re
import sys
import json
import time
import argparse
from dataclasses import dataclass
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

COOKIE_CACHE = os.path.join(os.path.dirname(__file__), ".rt_cookies.json")


# ---------------------------------------------------------------------------
# RuTracker (standalone copy of the bot's logic)
# ---------------------------------------------------------------------------

@dataclass
class RTItem:
    topic_id: str
    title: str
    forum: str
    size_bytes: int
    seeds: int
    leeches: int


class RuTracker:
    BASE = "https://rutracker.org/forum/"

    def __init__(self, user: str, pw: str):
        self.user = user
        self.pw = pw
        self.s: Optional[requests.Session] = None
        self._authed = False

    def _new_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) "
                          "Gecko/20100101 Firefox/109.0",
        })
        return s

    @staticmethod
    def _is_authed(html: str) -> bool:
        # bb_session is set for guests too; the only reliable signal is the
        # logged-in page content (logout link / non-guest JS flag).
        low = html.lower()
        return ("выход" in low) or ("is_guest: !!'1'" not in low
                                    and "logged-in-username" in low)

    def login(self) -> bool:
        if self.s and self._authed:
            return True
        # Reuse cached cookies to avoid hammering login.php between runs.
        if os.path.exists(COOKIE_CACHE):
            try:
                with open(COOKIE_CACHE) as f:
                    jar = json.load(f)
                self.s = self._new_session()
                self.s.cookies.update(jar)
                probe = self.s.get(self.BASE + "index.php", timeout=30)
                if self._is_authed(probe.text):
                    self._authed = True
                    return True
            except Exception:
                pass
        self.s = self._new_session()
        r = self.s.post(self.BASE + "login.php", data={
            "login_username": self.user,
            "login_password": self.pw,
            "login": "Вход",
        }, allow_redirects=True, timeout=60)
        if not self._is_authed(r.text):
            self.s = None
            return False
        self._authed = True
        with open(COOKIE_CACHE, "w") as f:
            json.dump(requests.utils.dict_from_cookiejar(self.s.cookies), f)
        return True

    def search(self, query: str) -> List[RTItem]:
        if not self.login():
            raise RuntimeError("RuTracker login failed")
        resp = self.s.get(self.BASE + "tracker.php", params={"nm": query})
        resp.encoding = "windows-1251"
        soup = BeautifulSoup(resp.text, "lxml")
        out: List[RTItem] = []
        for row in soup.select("tr.tCenter.hl-tr"):
            topic_id = row.get("data-topic_id", "")
            forum_el = row.select_one("td.f-name-col a")
            forum = forum_el.get_text(strip=True) if forum_el else "Неизвестно"
            title_el = row.select_one("a.tLink")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not topic_id:
                topic_id = title_el.get("data-topic_id", "")
            size_td = row.select_one("td.tor-size")
            size_bytes = 0
            if size_td:
                try:
                    size_bytes = int(size_td.get("data-ts_text", "0"))
                except ValueError:
                    pass
            seeds = 0
            sel = row.select_one("b.seedmed")
            if sel:
                try:
                    seeds = int(sel.get_text(strip=True))
                except ValueError:
                    pass
            leeches = 0
            le = row.select_one("td.leechmed")
            if le:
                try:
                    leeches = int(le.get_text(strip=True))
                except ValueError:
                    pass
            out.append(RTItem(topic_id, title, forum, size_bytes, seeds, leeches))
        return out


# ---------------------------------------------------------------------------
# LLM client (OpenAI-compatible / OpenWebUI)
# ---------------------------------------------------------------------------

class LLM:
    def __init__(self, base_url: str, api_key: str):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key = api_key

    def chat(self, model: str, system: str, user: str,
             connect_timeout: int = 10, idle_timeout: int = 300,
             keep_alive: str = "30m") -> tuple[str, float]:
        """Streaming chat. No total timeout — instead we tolerate a long gap
        before the FIRST token (cold model load), then require steady chunks.
        keep_alive asks Ollama to keep the model resident so the next call
        is warm (~seconds) instead of a cold load (~minutes)."""
        t0 = time.time()
        r = requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": True,
                "temperature": 0,
                "keep_alive": keep_alive,
            },
            stream=True,
            # (connect, read) — read timeout is the max gap *between* chunks;
            # set high enough to cover a cold model load before token #1.
            timeout=(connect_timeout, idle_timeout),
        )
        r.raise_for_status()
        parts: List[str] = []
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
                delta = obj["choices"][0]["delta"].get("content")
                if delta:
                    parts.append(delta)
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
        return "".join(parts), time.time() - t0


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json(text: str):
    """Pull the first JSON object/array out of a (possibly noisy) LLM reply."""
    text = _THINK_RE.sub("", text).strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    # find first { or [ and matching close by brace counting
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return json.loads(text)  # last resort, will raise


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

INTENT_SYSTEM = """\
Ты — парсер поисковых запросов для торрент-трекера RuTracker.
Пользователь пишет что хочет найти на естественном языке (русский/английский).
Твоя задача — извлечь чистую поисковую строку и тип контента.

Верни СТРОГО JSON без пояснений:
{"query": "<чистая строка для поиска>", "category": "<одна из: game, movie, tv, music, book, audiobook, software, other, any>"}

Правила:
- query: убери слова-указатели типа "игра", "фильм", "скачать", "сериал", оставь только название/предмет поиска. Сохраняй важные уточнения (год, часть, платформу).
- category: что именно пользователь хочет. Если тип не указан явно — "any".
- Никакого текста кроме JSON."""

INTENT_FEWSHOT = [
    ("игра Warcraft III", '{"query": "Warcraft III", "category": "game"}'),
    ("Фильм ошибка резидента", '{"query": "Обитель зла", "category": "movie"}'),
    ("Warcraft", '{"query": "Warcraft", "category": "any"}'),
    ("сериал Чернобыль 2019", '{"query": "Чернобыль 2019", "category": "tv"}'),
]

FILTER_SYSTEM = """\
Ты фильтруешь результаты поиска торрент-трекера RuTracker.
Дан желаемый тип контента и список найденных раздач (номер, раздел форума, название).
Определи, какие раздачи действительно соответствуют тому, что ищет пользователь.

Тип "game" = только сама игра/репак/портативная версия для запуска.
НЕ включай: саундтреки, музыку из игры, артбуки, книги, гайды, фанфики, фильмы,
обои, моды-наборы без игры, видеопрохождения.
Аналогично для movie/tv/music/book/audiobook/software — только сам предмет нужного типа.
Если тип "any" — оставь всё.

Верни СТРОГО JSON без пояснений:
{"relevant": [<номера подходящих раздач>]}

Только JSON, без текста."""


def build_filter_user(category: str, query: str, items: List[RTItem]) -> str:
    lines = [f"Пользователь искал: {query}",
             f"Желаемый тип: {category}", "", "Раздачи:"]
    for i, it in enumerate(items):
        lines.append(f"{i}. [{it.forum}] {it.title}")
    return "\n".join(lines)


# Realistic noisy fixtures (RuTracker-style forum names) for offline runs.
FIXTURES = {
    "warcraft": [
        RTItem("1", "Warcraft III: Reign of Chaos + The Frozen Throne [GOG]",
               "Стратегии (RTS)", 8 * 10**9, 412, 15),
        RTItem("2", "Warcraft III: Reforged [Battle.net] (2020)",
               "Стратегии (RTS)", 30 * 10**9, 188, 9),
        RTItem("3", "Warcraft II: Battle.net Edition",
               "Старые игры", 600 * 10**6, 95, 3),
        RTItem("4", "Warcraft: Orcs & Humans (1994)",
               "Старые игры", 30 * 10**6, 41, 1),
        RTItem("5", "World of Warcraft: Dragonflight [сервер]",
               "MMO игры", 90 * 10**9, 73, 22),
        RTItem("6", "Warcraft / Варкрафт (2016) BDRip 1080p",
               "Зарубежное кино", 12 * 10**9, 540, 30),
        RTItem("7", "Warcraft III: Reign of Chaos - Soundtrack (OST)",
               "Саундтреки (lossless)", 400 * 10**6, 60, 2),
        RTItem("8", "World of Warcraft: The Burning Crusade - OST",
               "Музыка (lossless)", 700 * 10**6, 33, 1),
        RTItem("9", "Кристи Голден - Warcraft: Артас (аудиокнига)",
               "Аудиокниги", 800 * 10**6, 120, 4),
        RTItem("10", "World of Warcraft: Chronicle. Том 1 (артбук) PDF",
               "Цифровые книги", 300 * 10**6, 88, 2),
        RTItem("11", "Варкрафт. Дюротан / Кристи Голден [FB2]",
               "Художественная литература", 5 * 10**6, 47, 0),
        RTItem("12", "Warcraft III TFT - DotA Allstars + сборник карт",
               "Игры под Windows: Дополнения", 2 * 10**9, 210, 8),
        RTItem("13", "World of Warcraft - обои / wallpapers 4K",
               "Обои и Картинки", 1 * 10**9, 12, 0),
        RTItem("14", "Hearthstone: Heroes of Warcraft",
               "Игры для Android", 3 * 10**9, 64, 5),
        RTItem("15", "Warcraft III: Reforged - русификатор звука",
               "Игры под Windows: Дополнения", 500 * 10**6, 28, 1),
    ],
    # Captured from a real rutracker.org search for "Warcraft" (50-result
    # page, trimmed to 33 representative rows with real forum names/seeds).
    "warcraft_real": [
        RTItem("a", "(Score / Arrangement) [WEB] Dwelling of Duels 2021 Comp",
               "Аранжировки музыки из игр", 1, 6, 0),
        RTItem("b", "Cortney Alameda / Кортни Аламеда - War of the Scaleborn",
               "Самиздат и книги, изданные...", 1, 8, 0),
        RTItem("c", "[Other] Сервер [World of Warcraft: The Burning Crusade]",
               "Моды, дополнения, утилиты", 1, 5, 0),
        RTItem("d", "[Other] Сервер [World of Warcraft: Classic, 1.12.1]",
               "Моды, дополнения, утилиты", 1, 5, 0),
        RTItem("e", "[DL] Warcraft I: Remastered + Warcraft II: Remastered [P]",
               "Стратегии в реальном времени (RTS)", 1, 66, 2),
        RTItem("f", "[CD] Warcraft II (2): Battle.net Edition (Tides of Darkness)",
               "Старые игры (Стратегии)", 1, 37, 1),
        RTItem("g", "[CD/DL] [Антология] Total DOS Collection 23 (Blood...)",
               "Антологии и сборники игр", 1, 48, 1),
        RTItem("h", "Courtney Alameda - World of Warcraft: War of the Scaleborn",
               "Самиздат и книги, изданные...", 1, 9, 0),
        RTItem("i", "Matt Forbeck / Мэтт Форбек - World of Warcraft: ...",
               "Зарубежная фантастика / фэнтези", 1, 9, 0),
        RTItem("j", "Варкрафт / Warcraft (Дункан Джонс) [2016, UHD BDRemux]",
               "Зарубежное кино (UHD Video)", 1, 8, 1),
        RTItem("k", "Варкрафт / Warcraft (Дункан Джонс) [2016, 2160p]",
               "Зарубежное кино (UHD Video)", 1, 13, 2),
        RTItem("l", "Варкрафт / Warcraft (Дункан Джонс) [2016, HDR10]",
               "Зарубежное кино (UHD Video)", 1, 21, 3),
        RTItem("m", "[CD] Warcraft III (3): Reign of Chaos + The Frozen Throne",
               "Старые игры (Стратегии)", 1, 56, 2),
        RTItem("n", "Christie Golden / Кристи Голден - Sylvanas (warcraft)",
               "Самиздат и книги, изданные...", 1, 7, 0),
        RTItem("o", "Sandra Rosner - World of Warcraft: The Sundering",
               "Самиздат и книги, изданные...", 1, 8, 0),
        RTItem("p", "[PS4 PSX Classics] WarCraft II: The Dark Saga (Мифы Тьмы)",
               "PS4", 1, 5, 1),
        RTItem("q", "[CD] [Сборник] Антология Warcraft (Orcs and Humans, II)",
               "Старые игры (Стратегии)", 1, 25, 1),
        RTItem("r", "[CD] World of Warcraft: Wrath of the Lich King [P] [RUS]",
               "Старые игры (Ролевые игры)", 1, 48, 3),
        RTItem("s", "[CD] Warcraft III (3): Reign of Chaos + The Frozen Throne [P]",
               "Старые игры (Стратегии)", 1, 371, 12),
        RTItem("t", "(Score) [CD] WarCraft III (3): The Frozen Throne Soundtrack",
               "Саундтреки к играм (lossless)", 1, 5, 0),
        RTItem("u", "(Score) [CD] WarCraft III (3): Reign of Chaos Soundtrack",
               "Саундтреки к играм (lossless)", 1, 3, 0),
        RTItem("v", "[TR24][OF][GM] Blizzard - World of Warcraft (Hi-Res)",
               "Саундтреки (Hi-Res stereo)", 1, 3, 0),
        RTItem("w", "(Score, Complete Recording, Unofficial) [CD] Варкрафт",
               "Неофициальные сборники саундтреков", 1, 5, 0),
        RTItem("x", "World of Warcraft - Burning Crusade, Wrath (game video)",
               "Игровое видео", 1, 7, 0),
        RTItem("y", "[DL] Warcraft II (2): Battle.net Edition (Tides of Darkness)",
               "Старые игры (Стратегии)", 1, 33, 1),
        RTItem("z", "[DL] [Антология] Warcraft I + Warcraft II: Remastered [P]",
               "Стратегии в реальном времени (RTS)", 1, 163, 5),
        RTItem("A", "[DL] Warcraft II (2): Remastered [P] [RUS + ENG]",
               "Стратегии в реальном времени (RTS)", 1, 162, 4),
        RTItem("B", "[DL] Warcraft I: Remastered [P] [RUS + ENG] (2024)",
               "Стратегии в реальном времени (RTS)", 1, 90, 3),
        RTItem("C", "[WildStorm / АСТ] Walter Simonson - Warcraft (комикс)",
               "Комиксы на русском языке", 1, 7, 0),
        RTItem("D", "(Score) [CD] World of Warcraft: Wrath of the Lich King OST",
               "Саундтреки к играм (lossless)", 1, 5, 0),
        RTItem("E", "(Score) [CD] World of Warcraft The Burning Crusade OST",
               "Саундтреки к играм (lossless)", 1, 5, 0),
        RTItem("F", "[Maps] Warcraft 3: карты и кампании [Reign of Chaos]",
               "Моды, дополнения, утилиты", 1, 26, 1),
        RTItem("G", "Warcraft III (Reign Of Chaos + The Frozen Throne) [amd64]",
               "Игры для Linux с Wine", 1, 23, 1),
    ],
}


def build_intent_user(raw: str) -> str:
    ex = "\n".join(f"Запрос: {q}\nОтвет: {a}" for q, a in INTENT_FEWSHOT)
    return f"{ex}\n\nЗапрос: {raw}\nОтвет:"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(llm: LLM, model: str, raw_query: str, items: List[RTItem]):
    print(f"\n{'=' * 70}\nMODEL: {model}\n{'=' * 70}")

    # Call 1: intent
    try:
        raw, dt1 = llm.chat(model, INTENT_SYSTEM, build_intent_user(raw_query))
        intent = extract_json(raw)
        clean_q = intent.get("query", raw_query)
        category = intent.get("category", "any")
        print(f"[intent {dt1:.1f}s] query={clean_q!r}  category={category!r}")
    except Exception as e:
        print(f"[intent FAILED] {type(e).__name__}: {e}")
        return

    # Call 2: filter on the REAL search output for the cleaned query
    fu = build_filter_user(category, clean_q, items)
    try:
        raw2, dt2 = llm.chat(model, FILTER_SYSTEM, fu)
        res = extract_json(raw2)
        rel = res.get("relevant", []) if isinstance(res, dict) else res
        rel = [i for i in rel if isinstance(i, int) and 0 <= i < len(items)]
    except Exception as e:
        print(f"[filter FAILED] {type(e).__name__}: {e}\n  raw: {raw2[:300]!r}")
        return

    kept = sorted((items[i] for i in rel), key=lambda x: x.seeds, reverse=True)
    print(f"[filter {dt2:.1f}s] kept {len(kept)}/{len(items)}")
    print("-" * 70)
    for it in kept[:15]:
        print(f"  🌱{it.seeds:>5}  [{it.forum[:22]:22}] {it.title[:60]}")
    dropped = [items[i] for i in range(len(items)) if i not in set(rel)]
    print(f"  --- dropped {len(dropped)} (sample) ---")
    for it in dropped[:8]:
        print(f"  ✗        [{it.forum[:22]:22}] {it.title[:60]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="natural-language request, fed to intent call")
    ap.add_argument("--search", default=None,
                    help="actual RuTracker query for the fixed result set "
                         "(default: same as query)")
    ap.add_argument("--models", nargs="+", default=["qwen3:14b"])
    ap.add_argument("--fixture", default=None,
                    help="use a built-in offline result set instead of "
                         "querying RuTracker (e.g. 'warcraft')")
    args = ap.parse_args()

    llm = LLM(os.environ["AI_LLM_API_BASE_URL"], os.environ["AI_LLM_API_KEY"])

    if args.fixture:
        items = FIXTURES[args.fixture]
        print(f"FIXTURE {args.fixture!r}: {len(items)} results")
    else:
        rt = RuTracker(os.environ["RUTRACKER_USERNAME"],
                       os.environ["RUTRACKER_PASSWORD"])
        # Search ONCE with a clean query so every model filters the same
        # real, noisy set. Intent is judged separately on the raw query.
        search_q = args.search or args.query
        print(f"Searching RuTracker for: {search_q!r} ...")
        items = rt.search(search_q)
    print(f"Got {len(items)} raw results "
          f"across {len(set(i.forum for i in items))} forums")
    for it in items:
        print(f"  [{it.forum[:24]:24}] 🌱{it.seeds:>4} {it.title[:55]}")

    for m in args.models:
        run(llm, m, args.query, items)


if __name__ == "__main__":
    main()
