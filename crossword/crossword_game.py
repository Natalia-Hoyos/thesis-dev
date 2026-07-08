import json
import random
import re
from pathlib import Path
from typing import Any

from crossword.display_server import ensure_running, update_state


class CrosswordGame:
    """Crossword with curated puzzle bank, grid display, and local display window."""

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
            "size": selected["size"],
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
        direction = word.get("direction", "across")
        direction_label = "across" if direction == "across" else "down"
        return (
            f"Number {word['number']} {direction_label}.\n"
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
        current_number = (
            words[current_index]["number"]
            if current_index < len(words)
            else None
        )

        size = current.get("size")
        if not isinstance(size, dict):
            raise ValueError("Active crossword is missing grid size.")

        rows = int(size.get("rows", 0))
        cols = int(size.get("cols", 0))
        if rows <= 0 or cols <= 0:
            raise ValueError("Active crossword has an invalid grid size.")

        cells: list[list[dict[str, Any]]] = [
            [{"type": "block"} for _ in range(cols)] for _ in range(rows)
        ]
        word_numbers_by_cell: dict[tuple[int, int], int] = {}

        for word in words:
            number = word["number"]
            answer = word["answer"]
            row = int(word["row"])
            col = int(word["col"])
            direction = word["direction"]
            delta_row, delta_col = (0, 1) if direction == "across" else (1, 0)

            if (row, col) not in word_numbers_by_cell:
                word_numbers_by_cell[(row, col)] = number

            for offset, letter in enumerate(answer):
                r = row + offset * delta_row
                c = col + offset * delta_col
                if not (0 <= r < rows and 0 <= c < cols):
                    raise ValueError(
                        f"Word {number} extends outside the {rows}x{cols} grid."
                    )

                existing = cells[r][c]
                if existing["type"] == "letter":
                    if existing["solution"] != letter:
                        raise ValueError(
                            f"Grid conflict at ({r}, {c}) for word {number}."
                        )
                    existing["word_numbers"].add(number)
                else:
                    cells[r][c] = {
                        "type": "letter",
                        "solution": letter,
                        "word_numbers": {number},
                        "display": None,
                        "solved": False,
                        "current": False,
                    }

        for r in range(rows):
            for c in range(cols):
                cell = cells[r][c]
                if cell["type"] != "letter":
                    continue

                word_nums = cell["word_numbers"]
                cell["solved"] = any(n in solved_numbers for n in word_nums)
                cell["current"] = (
                    current_number is not None and current_number in word_nums
                )
                if cell["solved"]:
                    cell["display"] = cell["solution"]
                else:
                    cell["display"] = None

                start_number = word_numbers_by_cell.get((r, c))
                cell["number"] = start_number

        flat_cells: list[dict[str, Any]] = []
        for r in range(rows):
            for c in range(cols):
                cell = cells[r][c]
                if cell["type"] == "block":
                    flat_cells.append({"type": "block"})
                else:
                    flat_cells.append(
                        {
                            "type": "letter",
                            "number": cell.get("number"),
                            "display": cell.get("display"),
                            "solved": cell.get("solved", False),
                            "current": cell.get("current", False),
                        }
                    )

        update_state(
            {
                "active": True,
                "puzzle_id": current["id"],
                "title": current["title"],
                "grid": {"rows": rows, "cols": cols, "cells": flat_cells},
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
            raw_size = item.get("size")
            if (
                not isinstance(puzzle_id, str)
                or not puzzle_id.strip()
                or not isinstance(title, str)
                or not title.strip()
                or difficulty not in ("easy", "medium")
                or not isinstance(raw_words, list)
                or not raw_words
                or not isinstance(raw_size, dict)
            ):
                continue

            rows = raw_size.get("rows")
            cols = raw_size.get("cols")
            if not isinstance(rows, int) or not isinstance(cols, int) or rows <= 0 or cols <= 0:
                continue

            words: list[dict[str, Any]] = []
            for raw_word in raw_words:
                if not isinstance(raw_word, dict):
                    continue
                number = raw_word.get("number")
                answer = raw_word.get("answer")
                clue = raw_word.get("clue")
                alternate_clue = raw_word.get("alternate_clue")
                row = raw_word.get("row")
                col = raw_word.get("col")
                direction = raw_word.get("direction")
                if (
                    isinstance(number, int)
                    and number > 0
                    and isinstance(answer, str)
                    and answer.strip()
                    and isinstance(clue, str)
                    and clue.strip()
                    and isinstance(row, int)
                    and row >= 0
                    and isinstance(col, int)
                    and col >= 0
                    and direction in ("across", "down")
                ):
                    entry: dict[str, Any] = {
                        "number": number,
                        "answer": answer.strip().upper(),
                        "clue": clue.strip(),
                        "row": row,
                        "col": col,
                        "direction": direction,
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
                        "size": {"rows": rows, "cols": cols},
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
