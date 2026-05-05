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
from trivia_game import TriviaGame

logger = logging.getLogger(__name__)

load_dotenv()

trivia_game = TriviaGame(
    questions_path=Path(__file__).with_name("trivia_questions.json"),
    state_path=Path(__file__).with_name(".trivia_state.json"),
)


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are a warm and friendly social companion robot designed to support older adults through conversation and engaging activities.
You are Gladys, your personality is calm, patient, encouraging, humorous,and genuinely curious about the person you're speaking with. 

You can play trivia with the user. When the user wants to play trivia:
1. Ask them if they want easy or hard questions (only once, when they first choose to play).
2. Call the start_trivia tool with their chosen difficulty.
3. The start_trivia tool returns the exact next question text from a curated question bank.
4. After the user answers a trivia question, call check_trivia_answer with their answer to verify correctness.
5. For each turn: give a short, warm reaction based on the check_trivia_answer result, then call start_trivia again at the same difficulty and ask that question. Do not ask every time whether they want to keep playing or change difficulty, and do not pause for a menu—assume they will keep going until they clearly want to stop or only when they ask to switch easy or hard. If they want a different difficulty mid-game, call start_trivia with the new level when they ask.
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


async def entrypoint(ctx: JobContext):
    session = AgentSession(
        llm=openai.LLM(model="gpt-4.1-mini"),
        stt=deepgram.STT(model="nova-3"),
        tts=cartesia.TTS(
            model="sonic-3",
            voice="f31cc6a7-c1e8-4764-980c-60a361443dd1",  # Australian woman voice
        ),
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