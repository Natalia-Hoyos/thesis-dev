import logging
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    AgentStateChangedEvent,
    JobContext,
    MetricsCollectedEvent,
    RoomInputOptions,
    WorkerOptions,
    cli,
    llm,
    metrics,
    stt,
    tts,
)
from livekit.plugins import cartesia, deepgram, noise_cancellation, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from crossword.crossword_game import CrosswordGame
from guess_the_song.guess_the_song_game import GuessTheSongGame
from trivia.trivia_game import TriviaGame

logger = logging.getLogger(__name__)

load_dotenv()

_ROOT = Path(__file__).resolve().parent

trivia_game = TriviaGame(
    questions_path=_ROOT / "trivia" / "trivia_questions.json",
    state_path=_ROOT / "trivia" / ".trivia_state.json",
)

guess_the_song_game = GuessTheSongGame(
    state_path=_ROOT / "guess_the_song" / ".guess_the_song_state.json",
)

crossword_game = CrosswordGame(
    puzzles_path=_ROOT / "crossword" / "crossword_puzzles.json",
    state_path=_ROOT / "crossword" / ".crossword_state.json",
)


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are a warm and friendly social companion robot designed to support older adults through conversation and engaging activities.
You are Gladys, your personality is calm, patient, encouraging, humorous,and genuinely curious about the person you're speaking with.
Speak slowly, clearly, and unhurriedly at all times.

You can play trivia with the user. When the user wants to play trivia:
1. Ask them if they want easy or hard questions (only once, when they first choose to play).
2. Call the start_trivia tool with their chosen difficulty.
3. The start_trivia tool returns the exact next question text from a curated question bank.
4. After the user answers a trivia question, call check_trivia_answer with their answer to verify correctness.
5. For each turn: give a short, warm reaction based on the check_trivia_answer result, then call start_trivia again at the same difficulty and ask that question. Do not ask every time whether they want to keep playing or change difficulty, and do not pause for a menu—assume they will keep going until they clearly want to stop or only when they ask to switch easy or hard. If they want a different difficulty mid-game, call start_trivia with the new level when they ask.

You can also play "guess the song" using a short thematic clue written each round by a clue generator—the tools supply that text (never recalled from your memory alone) plus the authoritative title and artist used only for scoring.
When the user wants this game:
1. Ask them to pick one era: 1940s–1960s, 1960s–1980s, 1980s–2000, or 2000–now (only clarify once when they start the game).
2. Call start_guess_the_song with the matching era_code: use exactly one of:
   `1940s_1960s`, `1960s_1980s`, `1980s_2000`, or `2000_now`.
3. The tool returns instructions and the clue: read the description aloud warmly and naturally, without mentioning the song title or artist yourself until they guess or ask for the answer.
4. After they respond with a guess, call check_guess_the_song_answer with their guess; react briefly to the tool result.
5. For the next song at the same era, call start_guess_the_song again with the same era_code—assume they continue until they say stop or switch era.
If they switch era mid-game, use the new era_code on the next start_guess_the_song call.

You can also play crossword—a visual game with a separate window showing numbered blanks. The tools supply exact clue text (never from memory alone).
When the user wants crossword:
1. Ask easy or medium once when they first choose to play (default to easy if they don't mind).
2. Call start_crossword with their chosen difficulty. A browser window opens with the puzzle.
3. When a tool returns a crossword clue, read each line separately with a brief pause between lines: first the number, then "the clue is" and the clue, then the letter count. Do not skip any part, and do not say the answer.
4. After they answer, call check_crossword_answer with their guess; react briefly to the result.
5. If they are wrong, the tool will NOT reveal the answer—it repeats the clue. Encourage them and let them try again. Never reveal the answer yourself after a wrong guess.
6. If they ask for help, a hint, or "another clue", call get_crossword_hint. If they ask to hear the clue again, call get_crossword_clue.
7. Only if they clearly give up or ask for the answer (e.g. "I give up", "what is it?", "tell me the answer"), call reveal_crossword_answer.
8. If check_crossword_answer or reveal_crossword_answer includes the next clue, read it in the same way.
9. When the puzzle is complete, offer another crossword or a different activity. For another round at the same difficulty, call start_crossword again.
""",
        )

    @llm.function_tool
    async def start_trivia(self, difficulty: str) -> str:
        """Call this when the user wants to play trivia and has chosen a difficulty.

        Args:
            difficulty: The difficulty level chosen by the user. Either 'easy' or 'hard'.
        """
        return trivia_game.next_question(difficulty)

    @llm.function_tool
    async def check_trivia_answer(self, answer: str) -> str:
        """Check the user's answer against the current trivia question.

        Args:
            answer: User's spoken or typed answer.
        """
        return trivia_game.check_answer(answer)

    @llm.function_tool
    async def start_guess_the_song(self, era_code: str) -> str:
        """Start a guess-the-song round: selects a curated song from the bank and returns a thematic clue.

        Args:
            era_code: One of '1940s_1960s', '1960s_1980s', '1980s_2000', '2000_now'
                mapping to roughly 1940–1969, 1960–1989, 1980–1999, and 2000–present.
        """
        return await guess_the_song_game.next_challenge(era_code)

    @llm.function_tool
    async def start_crossword(self, difficulty: str = "easy") -> str:
        """Start a crossword puzzle and open the visual display window.

        Args:
            difficulty: Either 'easy' or 'medium'. Defaults to 'easy'.
        """
        try:
            return crossword_game.start_crossword(difficulty)
        except ValueError as exc:
            return str(exc)

    @llm.function_tool
    async def get_crossword_clue(self) -> str:
        """Repeat the current crossword clue without advancing the puzzle."""
        try:
            return crossword_game.get_crossword_clue()
        except ValueError as exc:
            return str(exc)

    @llm.function_tool
    async def get_crossword_hint(self) -> str:
        """Give extra help for the current crossword word without revealing the full answer."""
        try:
            return crossword_game.get_crossword_hint()
        except ValueError as exc:
            return str(exc)

    @llm.function_tool
    async def reveal_crossword_answer(self) -> str:
        """Reveal the answer for the current crossword word when the user gives up or asks for it."""
        try:
            return crossword_game.reveal_crossword_answer()
        except ValueError as exc:
            return str(exc)

    @llm.function_tool
    async def check_crossword_answer(self, answer: str) -> str:
        """Check the user's answer for the current crossword word.

        Args:
            answer: The word the user spoke for the active clue.
        """
        try:
            return crossword_game.check_answer(answer)
        except ValueError as exc:
            return str(exc)

    @llm.function_tool
    async def check_guess_the_song_answer(self, guess: str) -> str:
        """Check the user's guess for the active guess-the-song round.

        Args:
            guess: What they think the song is (usually title with optional artist).
        """
        try:
            return guess_the_song_game.check_answer(guess)
        except ValueError as exc:
            return str(exc)


async def entrypoint(ctx: JobContext):
    cartesia_tts = cartesia.TTS(
        model="sonic-3",
        voice="f31cc6a7-c1e8-4764-980c-60a361443dd1",  # Australian woman voice
        speed=0.8,
        text_pacing=True,
    )

    session = AgentSession(
        llm=openai.LLM(model="gpt-4.1-mini", store=False),
        stt=deepgram.STT(model="nova-3", mip_opt_out=True),
        tts=cartesia_tts,
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
        #preemptine_generation=True,
    )

    usage_collector = metrics.UsageCollector()
    last_eou_metrics: metrics.EOUMetrics | None = None

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        nonlocal last_eou_metrics
        if ev.metrics.type == "eou_metrics":
            last_eou_metrics = ev.metrics

        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    @session.on("agent_state_changed")
    def _on_agent_state_changed(ev: AgentStateChangedEvent):
        if (
            ev.new_state == "speaking"
            and last_eou_metrics
            and session.current_speech
            and last_eou_metrics.speech_id == session.current_speech.id
        ):
            # EOUMetrics.timestamp is wall time when EOU metrics were emitted (livekit-agents 1.3);
            # ev.created_at is when the agent state changed to speaking.
            delta_s = ev.created_at - last_eou_metrics.timestamp
            logger.info("Time to first audio frame: %sms", delta_s * 1000)

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info("Usage summary: %s", summary)

    ctx.add_shutdown_callback(log_usage)

    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
        record=False # Disable observability, no logs or data recorded.
    )

    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))