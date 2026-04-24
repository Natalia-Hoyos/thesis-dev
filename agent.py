import logging

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
    inference,
    llm,
    metrics,
    stt,
    tts,
)
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger(__name__)

load_dotenv()


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are a warm and friendly social companion robot designed to support older adults through conversation and engaging activities.
Your personality is calm, patient, encouraging, humorous,and genuinely curious about the person you're speaking with. You speak clearly and at a comfortable pace. 

You can play trivia with the user. When the user wants to play trivia:
1. Ask them if they want easy or hard questions (only once, when they first choose to play).
2. Call the start_trivia tool with their chosen difficulty.
3. For each turn: give a short, warm reaction to their answer, then go straight to the next question at the same difficulty. Do not ask every time whether they want to keep playing or change difficulty, and do not pause for a menu—assume they will keep going until they clearly want to stop or only when they ask to switch easy or hard. If they want a different difficulty mid-game, call start_trivia with the new level when they ask.
""",
        )

    @llm.function_tool
    async def start_trivia(self, difficulty: str) -> str:
        """Call this when the user wants to play trivia and has chosen a difficulty.

        Args:
            difficulty: The difficulty level chosen by the user. Either 'easy' or 'hard'.
        """
        if difficulty not in ("easy", "hard"):
            difficulty = "easy"

        prompts = {
            "easy": (
                "Generate a single easy-difficulty trivia question. "
                "Topics can include history, nature, food, music, or general knowledge. "
                "Ask only the question, nothing else."
            ),
            "hard": (
                "Generate a single hard-difficulty trivia question. "
                "Topics can include science, world history, literature, or geography. "
                "Ask only the question, nothing else."
            ),
        }

        return prompts[difficulty]


async def entrypoint(ctx: JobContext):
    session = AgentSession(
        llm=llm.FallbackAdapter(
            [
                inference.LLM.from_model_string("openai/gpt-4.1-mini"),
                inference.LLM.from_model_string("google/gemini-2.5-flash"),
            ]
        ),
        stt=stt.FallbackAdapter(
            [
                inference.STT.from_model_string("deepgram/nova-3"),
                inference.STT.from_model_string("assemblyai/universal-streaming"),
            ]
        ),
        tts=tts.FallbackAdapter(
            [
                inference.TTS.from_model_string(
                    "cartesia/sonic-3:f31cc6a7-c1e8-4764-980c-60a361443dd1" # Australian woman voice
                ),
                inference.TTS.from_model_string("inworld/inworld-tts-1"),
            ]
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
        record=False # Disable observanility, no logs or data recorded.
    )

    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))