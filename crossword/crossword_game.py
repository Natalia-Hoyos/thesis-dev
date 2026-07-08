import json
import random
import re
from pathlib import Path
from typing import Any

from crossword.display_server import ensure_running, update_state


class CrosswordGame:
    """Crossword v1: numbered blanks, curated puzzle bank, local display window."""

    def __init__(self, puzzles_path: Path, state_path: Path) -> None:
        self.puzzles_path = puzzles_path
        self.state_path = state_path
        self._puzzles = self._load_puzzles()

    def start_crossword(self, difficulty: str = "easy") -> str:
        normalized = difficulty if difficulty in ("easy", "medium") else "easy"
        puzzles = [p for p in self._puzzles if p["difficulty"] == normalized]
        if not puzzles:
            raise ValueError(f"No crossword puzzles configured for difficulty '{normalized}'.")

        state = self._load_state()
        asked_ids = set(state["asked_by_difficulty"].get(normalized, []))
        available = [p for p in puzzles if p["id"] not in asked_ids]

        exhaustion_prefix = ""
        if not available:
            state["asked_by_difficulty"][normalized] = []
            asked_ids = set()
            available = puzzles
            exhaustion_prefix = (
                "You've already played each crossword at this level, so we've shuffled "
                "the deck—you're getting a fresh run.\n\n"
            )

        selected = random.choice(available)
        asked_ids.add(selected["id"])
        state["asked_by_difficulty"][normalized] = sorted(asked_ids)
        state["current_puzzle"] = {
            "id": selected["id"],
            "title": selected["title"],
            "difficulty": normalized,
            "words": selected["words"],
            "current_index": 0,
            "solved_numbers": [],
            "hints_used": 0,
        }
        self._save_state(state)

        ensure_running(open_browser=True)
        self._sync_display(state["current_puzzle"])

        first_clue = self._format_clue(selected["words"][0])
        return (
            f"{exhaustion_prefix}"
            f"Starting {selected['title']} ({len(selected['words'])} words). "
            f"A crossword window should be open on your screen.\n\n"
            f"{self._clue_delivery_instructions()}"
            f"{first_clue}"
        )

    def get_crossword_clue(self) -> str:
        current = self._require_current_puzzle()
        if current["current_index"] >= len(current["words"]):
            return "This crossword is already complete. Start a new one if they'd like another."

        word = current["words"][current["current_index"]]
        return f"{self._clue_delivery_instructions()}{self._format_clue(word)}"

    def get_crossword_hint(self) -> str:
        current = self._require_current_puzzle()
        if current["current_index"] >= len(current["words"]):
            return "This crossword is already complete. Start a new one if they'd like another."

        word = current["words"][current["current_index"]]
        hints_used = int(current.get("hints_used", 0))
        alternate_clue = word.get("alternate_clue")

        if isinstance(alternate_clue, str) and alternate_clue.strip() and hints_used == 0:
            hint = (
                f"Number {word['number']}.\n"
                f"Here is a different clue: {alternate_clue.strip()}.\n"
                f"That is still {len(word['answer'])} letters."
            )
        elif hints_used == 0:
            hint = (
                f"Number {word['number']}.\n"
                f"A little help: the first letter is {word['answer'][0]}.\n"
                f"That is {len(word['answer'])} letters."
            )
        else:
            hint = (
                f"Number {word['number']}.\n"
                f"Another nudge: the answer starts with {word['answer'][:2]}.\n"
                f"That is {len(word['answer'])} letters."
            )

        current["hints_used"] = hints_used + 1
        self._save_current_puzzle(current)

        return (
            "Read this hint slowly and clearly. Do NOT say the full answer:\n\n"
            f"{hint}"
        )

    def reveal_crossword_answer(self) -> str:
        current = self._require_current_puzzle()
        index = current["current_index"]
        words = current["words"]

        if index >= len(words):
            return "This crossword is already complete. Start a new one if they'd like another."

        word = words[index]
        expected = word["answer"]
        number = word["number"]

        solved_numbers = list(current.get("solved_numbers", []))
        if number not in solved_numbers:
            solved_numbers.append(number)
        current["solved_numbers"] = solved_numbers
        current["current_index"] = index + 1
        current["hints_used"] = 0
        self._save_current_puzzle(current)
        self._sync_display(current)

        result = f"The answer was {expected}."
        if current["current_index"] < len(words):
            next_word = words[current["current_index"]]
            return (
                f"{result}\n\n"
                f"{self._clue_delivery_instructions()}"
                f"{self._format_clue(next_word)}"
            )

        return f"{result}\n\nThat was the last word—they've completed the crossword!"

    def check_answer(self, answer: str) -> str:
        current = self._require_current_puzzle()
        index = current["current_index"]
        words = current["words"]

        if index >= len(words):
            return "This crossword is already complete. Start a new one if they'd like another."

        word = words[index]
        expected = word["answer"]
        is_correct = self._is_match(answer, expected)

        if not is_correct:
            return (
                "Not quite. Encourage them warmly, but do NOT reveal the answer. "
                "Invite them to try again, then read the clue again:\n\n"
                f"{self._clue_delivery_instructions()}"
                f"{self._format_clue(word)}"
            )

        number = word["number"]
        solved_numbers = list(current.get("solved_numbers", []))
        if number not in solved_numbers:
            solved_numbers.append(number)
        current["solved_numbers"] = solved_numbers
        current["current_index"] = index + 1
        current["hints_used"] = 0
        self._save_current_puzzle(current)
        self._sync_display(current)

        result = f"Correct! The answer is {expected}."

        if current["current_index"] < len(words):
            next_word = words[current["current_index"]]
            return (
                f"{result}\n\n"
                f"{self._clue_delivery_instructions()}"
                f"{self._format_clue(next_word)}"
            )

        return f"{result}\n\nThat was the last word—they've completed the crossword!"

    def _require_current_puzzle(self) -> dict[str, Any]:
        state = self._load_state()
        current = state.get("current_puzzle")
        if not isinstance(current, dict):
            raise ValueError("No active crossword. Start a crossword first.")
        return current

    def _save_current_puzzle(self, current: dict[str, Any]) -> None:
        state = self._load_state()
        state["current_puzzle"] = current
        self._save_state(state)

    def _format_clue(self, word: dict[str, Any]) -> str:
        length = len(word["answer"])
        letter_word = "letter" if length == 1 else "letters"
        return (
            f"Number {word['number']}.\n"
            f"The clue is: {word['clue']}.\n"
            f"That is {length} {letter_word}."
        )

    def _clue_delivery_instructions(self) -> str:
        return (
            "Read each line below slowly and clearly, with a brief pause between lines. "
            "Do NOT rush, and do NOT say the answer:\n\n"
        )

    def _sync_display(self, current: dict[str, Any]) -> None:
        words = current["words"]
        solved_numbers = set(current.get("solved_numbers", []))
        current_index = current.get("current_index", 0)
        solved_count = len(solved_numbers)

        display_words: list[dict[str, Any]] = []
        for idx, word in enumerate(words):
            number = word["number"]
            solved = number in solved_numbers
            display_words.append(
                {
                    "number": number,
                    "length": len(word["answer"]),
                    "solved": solved,
                    "letters": word["answer"] if solved else None,
                    "current": idx == current_index and not solved,
                }
            )

        update_state(
            {
                "active": True,
                "puzzle_id": current["id"],
                "title": current["title"],
                "words": display_words,
                "progress": {"solved": solved_count, "total": len(words)},
                "complete": current_index >= len(words),
            }
        )

    def _is_match(self, candidate: str, expected: str) -> bool:
        normalized_candidate = self._normalize(candidate)
        normalized_expected = self._normalize(expected)
        if normalized_candidate == normalized_expected:
            return True

        candidate_without_articles = self._remove_articles(normalized_candidate)
        expected_without_articles = self._remove_articles(normalized_expected)
        return candidate_without_articles == expected_without_articles

    def _normalize(self, text: str) -> str:
        lowered = text.casefold()
        stripped = re.sub(r"[^a-z0-9\s]", "", lowered)
        return " ".join(stripped.split())

    def _remove_articles(self, text: str) -> str:
        tokens = [token for token in text.split() if token not in {"a", "an", "the"}]
        return " ".join(tokens)

    def _load_puzzles(self) -> list[dict[str, Any]]:
        if not self.puzzles_path.exists():
            raise FileNotFoundError(f"Crossword puzzles file not found: {self.puzzles_path}")

        raw_data = json.loads(self.puzzles_path.read_text(encoding="utf-8"))
        if not isinstance(raw_data, list):
            raise ValueError("Crossword puzzles file must contain a JSON array.")

        puzzles: list[dict[str, Any]] = []
        for item in raw_data:
            if not isinstance(item, dict):
                continue

            puzzle_id = item.get("id")
            title = item.get("title")
            difficulty = item.get("difficulty")
            raw_words = item.get("words")
            if (
                not isinstance(puzzle_id, str)
                or not puzzle_id.strip()
                or not isinstance(title, str)
                or not title.strip()
                or difficulty not in ("easy", "medium")
                or not isinstance(raw_words, list)
                or not raw_words
            ):
                continue

            words: list[dict[str, Any]] = []
            for raw_word in raw_words:
                if not isinstance(raw_word, dict):
                    continue
                number = raw_word.get("number")
                answer = raw_word.get("answer")
                clue = raw_word.get("clue")
                alternate_clue = raw_word.get("alternate_clue")
                if (
                    isinstance(number, int)
                    and number > 0
                    and isinstance(answer, str)
                    and answer.strip()
                    and isinstance(clue, str)
                    and clue.strip()
                ):
                    entry: dict[str, Any] = {
                        "number": number,
                        "answer": answer.strip().upper(),
                        "clue": clue.strip(),
                    }
                    if isinstance(alternate_clue, str) and alternate_clue.strip():
                        entry["alternate_clue"] = alternate_clue.strip()
                    words.append(entry)

            if words:
                puzzles.append(
                    {
                        "id": puzzle_id.strip(),
                        "title": title.strip(),
                        "difficulty": difficulty,
                        "words": sorted(words, key=lambda w: w["number"]),
                    }
                )

        if not puzzles:
            raise ValueError(f"No valid crossword puzzles found in '{self.puzzles_path}'.")

        return puzzles

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "asked_by_difficulty": {"easy": [], "medium": []},
                "current_puzzle": None,
            }

        raw_state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(raw_state, dict):
            return {
                "asked_by_difficulty": {"easy": [], "medium": []},
                "current_puzzle": None,
            }

        asked = raw_state.get("asked_by_difficulty", {})
        if not isinstance(asked, dict):
            asked = {}

        easy = asked.get("easy", [])
        medium = asked.get("medium", [])
        current_puzzle = raw_state.get("current_puzzle")
        if not isinstance(current_puzzle, dict):
            current_puzzle = None

        return {
            "asked_by_difficulty": {
                "easy": [item for item in easy if isinstance(item, str)],
                "medium": [item for item in medium if isinstance(item, str)],
            },
            "current_puzzle": current_puzzle,
        }

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
