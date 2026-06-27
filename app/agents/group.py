# app/agents/group.py
"""Community Assistant — a deployable agent that answers inside Telegram groups.

A business deploys this to let its bot help in a group/supergroup. The bot only
replies when addressed (mentioned or replied-to — see webhooks), so it doesn't
spam the group, and it runs on a cheap/free model (set via the father agent's
preferred_model_id) since groups can be high-volume. It answers from the
Business Brain just like the base agent, with a brief group-aware tone.
"""
from typing import Optional

from app.agents.base import BaseAgent
from app.db.models import BusinessBrainConfig


class GroupAgent(BaseAgent):
    agent_name = "Community Assistant"

    def build_system_prompt(
        self,
        brain_config: Optional[BusinessBrainConfig],
        child_data: Optional[dict],
        chunks: list[dict],
    ) -> str:
        base = super().build_system_prompt(brain_config, child_data, chunks)
        return base + (
            "\n\nYou are replying inside a group chat where many people talk. "
            "Keep replies short and to the point, answer only what was asked, "
            "and don't repeat the question. If it's small talk not meant for you, "
            "a one-line friendly reply is fine."
        )


group_agent = GroupAgent()
