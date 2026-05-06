import json
import random
import re
from pathlib import Path
from typing import Any


class TriviaGame:
    """Question bank manager with non-repeating selection per difficulty."""

    def __init__(self, questions_path: Path, state_path: Path) -> None:
        self.questions_path = questions_path
        self.state_path = state_path
        self._questions_by_difficulty = self._load_questions()

    def next_question(self, difficulty: str) -> str:
        normalized = difficulty if difficulty in ("easy", "hard") else "easy"
        questions = self._questions_by_difficulty[normalized]
        if not questions:
            raise ValueError(f"No questions configured for difficulty '{normalized}'.")

        state = self._load_state()
        asked_ids = set(state["asked_by_difficulty"].get(normalized, []))
        available = [q for q in questions if q["id"] not in asked_ids]

        # If the pool is exhausted, reset only that difficulty.
        if not available:
            state["asked_by_difficulty"][normalized] = []
            asked_ids = set()
            available = questions

        selected = random.choice(available)
        asked_ids.add(selected["id"])
        state["asked_by_difficulty"][normalized] = sorted(asked_ids)
        state["current_question"] = {
            "id": selected["id"],
            "difficulty": normalized,
            "question": selected["question"],
            "answer": selected["answer"],
        }
        self._save_state(state)
        return selected["question"]

    def check_answer(self, user_answer: str) -> str:
        state = self._load_state()
        current = state.get("current_question")
        if not isinstance(current, dict):
            return "No active trivia question. Start a question first."

        expected_answer = current.get("answer")
        if not isinstance(expected_answer, str):
            return "I could not load the expected answer for this question."

        is_correct = self._is_match(user_answer, expected_answer)
        if is_correct:
            return f"Correct. The answer is {expected_answer}."
        return f"Incorrect. The correct answer is {expected_answer}."

    def _is_match(self, candidate: str, expected: str) -> bool:
        normalized_candidate = self._normalize(candidate)
        normalized_expected = self._normalize(expected)
        if normalized_candidate == normalized_expected:
            return True

        # Allow common article variations like "sun" vs "the sun".
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

    def _load_questions(self) -> dict[str, list[dict[str, str]]]:
        if not self.questions_path.exists():
            raise FileNotFoundError(f"Questions file not found: {self.questions_path}")

        raw_data = json.loads(self.questions_path.read_text(encoding="utf-8"))
        if not isinstance(raw_data, list):
            raise ValueError("Questions file must contain a JSON array.")

        questions_by_difficulty: dict[str, list[dict[str, str]]] = {"easy": [], "hard": []}

        for item in raw_data:
            if not isinstance(item, dict):
                continue

            question_id = item.get("id")
            difficulty = item.get("difficulty")
            question_text = item.get("question")
            answer = item.get("answer")
            if (
                isinstance(question_id, str)
                and difficulty in ("easy", "hard")
                and isinstance(question_text, str)
                and question_text.strip()
                and isinstance(answer, str)
                and answer.strip()
            ):
                questions_by_difficulty[difficulty].append(
                    {
                        "id": question_id,
                        "question": question_text.strip(),
                        "answer": answer.strip(),
                    }
                )

        return questions_by_difficulty

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "asked_by_difficulty": {"easy": [], "hard": []},
                "current_question": None,
            }

        raw_state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(raw_state, dict):
            return {
                "asked_by_difficulty": {"easy": [], "hard": []},
                "current_question": None,
            }

        asked = raw_state.get("asked_by_difficulty", {})
        if not isinstance(asked, dict):
            asked = {}

        easy = asked.get("easy", [])
        hard = asked.get("hard", [])
        current_question = raw_state.get("current_question")
        if not isinstance(current_question, dict):
            current_question = None

        return {
            "asked_by_difficulty": {
                "easy": [item for item in easy if isinstance(item, str)],
                "hard": [item for item in hard if isinstance(item, str)],
            },
            "current_question": current_question,
        }

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
