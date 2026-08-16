import json
import random
import re
from pathlib import Path
from typing import Any

from crossword.display_server import ensure_running, update_state


def find_layout_errors(rows: int, cols: int, words: list[dict[str, Any]]) -> list[str]:
    """Report layout problems that would make the grid read as a non-crossword.

    Any cell no word occupies is a black square, so two entries that sit side by side
    with no empty cell between them merge into a single unbroken run of letters. This
    checks the real crossword rule: every maximal run of two or more adjacent letters,
    in either direction, must be exactly one of the puzzle's declared words.
    """
    errors: list[str] = []
    letters: dict[tuple[int, int], str] = {}

    for word in words:
        number = word["number"]
        answer = word["answer"]
        delta_row, delta_col = (0, 1) if word["direction"] == "across" else (1, 0)
        last_offset = len(answer) - 1
        end_row = word["row"] + last_offset * delta_row
        end_col = word["col"] + last_offset * delta_col
        if end_row >= rows or end_col >= cols:
            errors.append(
                f"Word {number} ({answer}) runs outside the {rows}x{cols} grid."
            )
            continue

        for offset, letter in enumerate(answer):
            position = (word["row"] + offset * delta_row, word["col"] + offset * delta_col)
            existing = letters.get(position)
            if existing is not None and existing != letter:
                errors.append(
                    f"Word {number} ({answer}) needs '{letter}' at row {position[0]}, "
                    f"column {position[1]}, but another word puts '{existing}' there."
                )
            else:
                letters[position] = letter

    if errors:
        # Run scanning would only produce noise on top of broken coordinates.
        return errors

    declared = {
        (word["row"], word["col"], word["direction"], len(word["answer"]))
        for word in words
    }

    for direction, delta_row, delta_col in (("across", 0, 1), ("down", 1, 0)):
        for row in range(rows):
            for col in range(cols):
                if (row, col) not in letters:
                    continue
                if (row - delta_row, col - delta_col) in letters:
                    continue

                length = 0
                while (row + length * delta_row, col + length * delta_col) in letters:
                    length += 1
                if length < 2 or (row, col, direction, length) in declared:
                    continue

                run = "".join(
                    letters[(row + i * delta_row, col + i * delta_col)]
                    for i in range(length)
                )
                span = "columns" if direction == "across" else "rows"
                line = f"Row {row}" if direction == "across" else f"Column {col}"
                start = col if direction == "across" else row
                errors.append(
                    f"{line} {span} {start}-{start + length - 1} spell '{run}' {direction}, "
                    f"which is not one of this puzzle's {direction} words. Leave an empty "
                    "cell between entries that are not meant to join."
                )

    return errors


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
        word = self._current_word(current)
        if word is None:
            return "This crossword is already complete. Start a new one if they'd like another."

        return f"{self._clue_delivery_instructions()}{self._format_clue(word)}"

    def select_word(self, word_number: int) -> str:
        """Switch focus to a specific unsolved word by its clue number."""
        current = self._require_current_puzzle()
        words = current["words"]
        solved_numbers = set(current.get("solved_numbers", []))

        target_index = self._index_for_number(words, word_number)
        if target_index is None:
            available = [
                str(w["number"]) for w in words if w["number"] not in solved_numbers
            ]
            available_text = ", ".join(available) if available else "none"
            return (
                f"There is no word numbered {word_number} in this puzzle. "
                f"Unsolved numbers are: {available_text}."
            )

        if word_number in solved_numbers:
            next_index = self._next_unsolved_index(current, after_index=target_index - 1)
            if next_index is None:
                return "That word is already filled in, and the crossword is complete!"
            next_word = words[next_index]
            current["current_index"] = next_index
            current["hints_used"] = 0
            self._save_current_puzzle(current)
            self._sync_display(current)
            return (
                f"Number {word_number} is already filled in. "
                f"Switching to unsolved number {next_word['number']} instead:\n\n"
                f"{self._clue_delivery_instructions()}"
                f"{self._format_clue(next_word)}"
            )

        current["current_index"] = target_index
        current["hints_used"] = 0
        self._save_current_puzzle(current)
        self._sync_display(current)

        word = words[target_index]
        return (
            f"Switching to number {word['number']}.\n\n"
            f"{self._clue_delivery_instructions()}"
            f"{self._format_clue(word)}"
        )

    def skip_word(self) -> str:
        """Skip the current word and move to the next unsolved one."""
        current = self._require_current_puzzle()
        words = current["words"]
        index = current.get("current_index", 0)

        if self._is_complete(current):
            return "This crossword is already complete. Start a new one if they'd like another."

        # If current index is past the end or on a solved word, still find next unsolved.
        next_index = self._next_unsolved_index(current, after_index=index)
        if next_index is None:
            return "There are no unsolved words left—they've completed the crossword!"

        if next_index == index and words[index]["number"] not in set(
            current.get("solved_numbers", [])
        ):
            # Only one unsolved word left, and it's the current one.
            return (
                "This is the only unsolved word left, so we can't skip it. "
                "Encourage them to keep trying, ask for a hint, or give up on this word.\n\n"
                f"{self._clue_delivery_instructions()}"
                f"{self._format_clue(words[index])}"
            )

        current["current_index"] = next_index
        current["hints_used"] = 0
        self._save_current_puzzle(current)
        self._sync_display(current)

        word = words[next_index]
        return (
            f"Skipping for now. Moving to number {word['number']}.\n\n"
            f"{self._clue_delivery_instructions()}"
            f"{self._format_clue(word)}"
        )

    def get_crossword_hint(self) -> str:
        current = self._require_current_puzzle()
        word = self._current_word(current)
        if word is None:
            return "This crossword is already complete. Start a new one if they'd like another."

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
        word = self._current_word(current)

        if word is None:
            return "This crossword is already complete. Start a new one if they'd like another."

        expected = word["answer"]
        number = word["number"]

        solved_numbers = list(current.get("solved_numbers", []))
        if number not in solved_numbers:
            solved_numbers.append(number)
        current["solved_numbers"] = solved_numbers
        current["hints_used"] = 0

        next_index = self._next_unsolved_index(current, after_index=index)
        if next_index is None:
            current["current_index"] = len(words)
            self._save_current_puzzle(current)
            self._sync_display(current)
            return (
                f"The answer was {expected}.\n\n"
                "That was the last word—they've completed the crossword!"
            )

        current["current_index"] = next_index
        self._save_current_puzzle(current)
        self._sync_display(current)

        next_word = words[next_index]
        return (
            f"The answer was {expected}.\n\n"
            f"{self._clue_delivery_instructions()}"
            f"{self._format_clue(next_word)}"
        )

    def check_answer(self, answer: str) -> str:
        current = self._require_current_puzzle()
        index = current["current_index"]
        words = current["words"]
        word = self._current_word(current)

        if word is None:
            return "This crossword is already complete. Start a new one if they'd like another."

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
        current["hints_used"] = 0

        next_index = self._next_unsolved_index(current, after_index=index)
        if next_index is None:
            current["current_index"] = len(words)
            self._save_current_puzzle(current)
            self._sync_display(current)
            return (
                f"Correct! The answer is {expected}.\n\n"
                "That was the last word—they've completed the crossword!"
            )

        current["current_index"] = next_index
        self._save_current_puzzle(current)
        self._sync_display(current)

        next_word = words[next_index]
        return (
            f"Correct! The answer is {expected}.\n\n"
            f"{self._clue_delivery_instructions()}"
            f"{self._format_clue(next_word)}"
        )

    def _require_current_puzzle(self) -> dict[str, Any]:
        state = self._load_state()
        current = state.get("current_puzzle")
        if not isinstance(current, dict):
            raise ValueError("No active crossword. Start a crossword first.")
        return current

    def _is_complete(self, current: dict[str, Any]) -> bool:
        words = current.get("words", [])
        solved = set(current.get("solved_numbers", []))
        return bool(words) and all(w["number"] in solved for w in words)

    def _current_word(self, current: dict[str, Any]) -> dict[str, Any] | None:
        words = current.get("words", [])
        if not words or self._is_complete(current):
            return None
        index = current.get("current_index", 0)
        if not isinstance(index, int) or not (0 <= index < len(words)):
            index = self._next_unsolved_index(current, after_index=-1)
            if index is None:
                return None
        word = words[index]
        if word["number"] in set(current.get("solved_numbers", [])):
            index = self._next_unsolved_index(current, after_index=index)
            if index is None:
                return None
            word = words[index]
        return word

    def _index_for_number(self, words: list[dict[str, Any]], word_number: int) -> int | None:
        for idx, word in enumerate(words):
            if word["number"] == word_number:
                return idx
        return None

    def _next_unsolved_index(self, current: dict[str, Any], *, after_index: int) -> int | None:
        words = current.get("words", [])
        solved = set(current.get("solved_numbers", []))
        if not words:
            return None
        n = len(words)
        for offset in range(1, n + 1):
            idx = (after_index + offset) % n
            if words[idx]["number"] not in solved:
                return idx
        return None

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
                layout_errors = find_layout_errors(rows, cols, words)
                if layout_errors:
                    details = "\n  - ".join(layout_errors)
                    raise ValueError(
                        f"Crossword '{puzzle_id}' has an invalid grid layout:\n  - {details}"
                    )

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
