import json
import logging
import os
import random
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

ERAS: dict[str, tuple[int, int]] = {
    "1940s_1960s": (1940, 1969),
    "1960s_1980s": (1960, 1989),
    "1980s_2000": (1980, 1999),
    "2000_now": (2000, 2026),
}

ERA_LABELS: dict[str, str] = {
    "1940s_1960s": "the 1940s through the 1960s (releases about 1940–1969)",
    "1960s_1980s": "the 1960s through the 1980s (releases about 1960–1989)",
    "1980s_2000": "the 1980s through the 1990s (releases about 1980–1999)",
    "2000_now": "from 2000 through today",
}


def _song_key(title: str, artist: str) -> str:
    return f"{artist.strip().casefold()}|{title.strip().casefold()}"


@contextmanager
def _exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fp = open(lock_path, "a+b")
    fd = fp.fileno()
    locked = False
    try:
        fp.seek(0)
        fp.write(b"\x00")
        fp.flush()
        fp.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            locked = True
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            locked = True
        yield
    finally:
        if locked:
            fp.seek(0)
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UNLOCK)
            except OSError:
                logger.warning("Could not fully release guess-the-song file lock.")
        fp.close()


class GuessTheSongGame:
    """Guess-the-song rounds: picks from song_bank.json, LLM generates a safe thematic clue."""

    MIN_SONGS_PER_ERA = 15

    def __init__(
        self,
        state_path: Path,
        *,
        bank_path: Path | None = None,
        model: str | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.state_path = state_path
        self.bank_path = (
            Path(bank_path)
            if bank_path is not None
            else Path(__file__).resolve().with_name("song_bank.json")
        )
        self.model = model or os.environ.get("GUESS_SONG_MODEL", "gpt-4.1-mini")
        self._client = client or AsyncOpenAI()
        self._lock_path = self.state_path.with_name(self.state_path.name + ".lock")
        self._songs_by_era = self._load_and_validate_bank(self.bank_path)

    def _load_and_validate_bank(self, path: Path) -> dict[str, list[dict[str, str]]]:
        if not path.exists():
            raise FileNotFoundError(
                f"Song bank missing: '{path}'. Add song_bank.json with at least "
                f"{self.MIN_SONGS_PER_ERA} entries per era ({', '.join(ERAS)}), "
                "each with keys title, artist, decade."
            )

        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(f"Song bank must be a JSON array: '{path}'")

        by_era: dict[str, list[dict[str, str]]] = {k: [] for k in ERAS}
        for idx, entry in enumerate(raw):
            if not isinstance(entry, dict):
                logger.warning("Skipping non-object entry at bank index %s", idx)
                continue
            title = entry.get("title")
            artist = entry.get("artist")
            decade = entry.get("decade")
            if (
                not isinstance(title, str)
                or not isinstance(artist, str)
                or not isinstance(decade, str)
                or not title.strip()
                or not artist.strip()
                or decade not in ERAS
            ):
                logger.warning("Skipping malformed bank entry at index %s", idx)
                continue
            by_era[decade].append(
                {
                    "title": title.strip(),
                    "artist": artist.strip(),
                    "decade": decade,
                }
            )

        for era_key in ERAS:
            n = len(by_era[era_key])
            if n < self.MIN_SONGS_PER_ERA:
                raise ValueError(
                    f"Song bank '{path}' needs at least {self.MIN_SONGS_PER_ERA} valid songs "
                    f"for era {era_key!r}; found {n}."
                )

        return by_era

    def _normalize(self, text: str) -> str:
        lowered = text.casefold()
        stripped = re.sub(r"[^a-z0-9\s]", "", lowered)
        return " ".join(stripped.split())

    def _remove_articles(self, text: str) -> str:
        tokens = [token for token in text.split() if token not in {"a", "an", "the"}]
        return " ".join(tokens)

    def _guess_matches(self, user: str, title: str, artist: str) -> bool:
        nu = self._normalize(user)
        nt = self._normalize(title)
        na = self._normalize(artist)

        variants = (
            nt,
            f"{nt} {na}",
            f"{na} {nt}",
        )

        for expected in variants:
            if self._is_plain_match(nu, expected):
                return True

        # Substring title only when it is unlikely to be a stray short word inside a sentence.
        if nt and nt in nu:
            if " " in nt:
                return True
            if len(nt) >= 10:
                return True

        combined = self._normalize(f"{title} by {artist}")
        if nu == combined:
            return True

        # "Artist — Title" style
        for sep in (" - ", " – ", ": "):
            if sep in user:
                parts = [self._normalize(p.strip()) for p in user.split(sep) if p.strip()]
                if len(parts) >= 2 and nt in parts and na in parts:
                    return True
        return False

    def _is_plain_match(self, candidate: str, expected: str) -> bool:
        if candidate == expected:
            return True
        c2 = self._remove_articles(candidate)
        e2 = self._remove_articles(expected)
        return bool(c2) and bool(e2) and c2 == e2

    def _blank_state(self) -> dict[str, Any]:
        return {
            "used_indices_by_era": {k: [] for k in ERAS},
            "current_round": None,
        }

    def _parse_used_indices(self, raw_state: dict[str, Any]) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {k: [] for k in ERAS}
        block = raw_state.get("used_indices_by_era")
        if not isinstance(block, dict):
            return out
        for era_key in ERAS:
            items = block.get(era_key, [])
            if not isinstance(items, list):
                continue
            ints: list[int] = []
            for x in items:
                if isinstance(x, bool):
                    continue
                if isinstance(x, int) and x >= 0:
                    ints.append(x)
                elif isinstance(x, str) and x.isdigit():
                    ints.append(int(x))
            out[era_key] = ints
        return out

    def _load_state_unlocked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._blank_state()

        try:
            raw_state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Unreadable guess-the-song state; using defaults until next save: %s", exc)
            return self._blank_state()

        if not isinstance(raw_state, dict):
            return self._blank_state()

        current = raw_state.get("current_round")
        if current is not None and not isinstance(current, dict):
            current = None

        return {
            "used_indices_by_era": self._parse_used_indices(raw_state),
            "current_round": current,
        }

    def _save_state_unlocked(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _fallback_clue(self, decade_key: str) -> str:
        span = ERA_LABELS[decade_key].split("(")[0].strip().rstrip(".")
        if span.lower().startswith("from"):
            return f"This is a well-known song {span}."
        return f"This is a well-known song from {span}."

    async def _generate_clue(self, title: str, artist: str, decade_key: str) -> str:
        era_label_for_prompt = ERA_LABELS[decade_key]

        user_content = (
            f"Describe the song '{title}' by {artist} ({era_label_for_prompt}) "
            "in 2-3 sentences.\n"
            "Do not quote any lyrics. Do not mention the title or artist name.\n"
            "Describe: the theme or story, the mood it creates, and one cultural detail "
            "(for example a film it featured in, a dance it inspired, or how it fit its era).\n"
            "Return only the clue text—no preamble or heading."
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You write short thematic clues for a voice-based 'guess the song' game. "
                    "Never quote lyrics, song titles, or artist names—even partial lines. "
                    "Never restate wording that would reveal the hook or chorus uniquely. "
                    "Focus on theme, mood, and one factual cultural anchor. Plain prose only."
                ),
            },
            {"role": "user", "content": user_content},
        ]

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                store=False,
            )
            raw = response.choices[0].message.content
            clue = raw.strip() if isinstance(raw, str) else ""
            if clue:
                return clue
            logger.warning("Clue generation returned empty content; using fallback.")
        except Exception as exc:
            logger.warning("Clue generation failed, using fallback: %s", exc)
        return self._fallback_clue(decade_key)

    async def next_challenge(self, era_key: str) -> str:
        normalized_era = era_key if era_key in ERAS else "2000_now"
        label = ERA_LABELS[normalized_era]
        songs_for_era = self._songs_by_era[normalized_era]
        exhaustion_prefix = ""

        with _exclusive_file_lock(self._lock_path):
            state = self._load_state_unlocked()
            used_list = [
                idx
                for idx in state["used_indices_by_era"].get(normalized_era, [])
                if 0 <= idx < len(songs_for_era)
            ]
            used_set = set(used_list)

            remaining = [i for i in range(len(songs_for_era)) if i not in used_set]
            if not remaining:
                state["used_indices_by_era"][normalized_era] = []
                used_set.clear()
                remaining = list(range(len(songs_for_era)))
                exhaustion_prefix = (
                    "You've already heard each song from this era, so we've cleared the slate "
                    "and shuffled—you're getting a fresh run.\n\n"
                )

            pick = random.choice(remaining)
            used_updated = sorted(used_set | {pick})

            song = songs_for_era[pick]
            state["used_indices_by_era"][normalized_era] = used_updated
            state["current_round"] = {
                "title": song["title"],
                "artist": song["artist"],
                "decade": song["decade"],
            }
            self._save_state_unlocked(state)

        clue_text = await self._generate_clue(song["title"], song["artist"], normalized_era)

        return (
            f"{exhaustion_prefix}"
            f"Guess the song from {label}.\n\n"
            f"Speak the following thematic description aloud warmly and naturally, but do "
            f"NOT name the song title or artist until they guess or give up:\n\n"
            f"{clue_text}"
        )

    def check_answer(self, user_guess: str) -> str:
        with _exclusive_file_lock(self._lock_path):
            state = self._load_state_unlocked()
            current = state.get("current_round")

        if not isinstance(current, dict):
            raise ValueError("No active guess-the-song round. Start a round first.")

        title = current.get("title")
        artist = current.get("artist")
        if (
            not isinstance(title, str)
            or not isinstance(artist, str)
            or not title.strip()
            or not artist.strip()
        ):
            raise ValueError("No active guess-the-song round. Start a round first.")

        ok = self._guess_matches(user_guess, title, artist)
        if ok:
            return f'Correct—it was "{title}" by {artist}.'
        return f'Nice try—it was "{title}" by {artist}.'
