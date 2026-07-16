# app/agents/base.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import (
    BusinessBrainConfig,
    ChatMessage,
    Conversation,
    MessageRole,
)
from app.services.model_router import model_router
from app.services.rag_service import rag_service
from app.services.telegram_service import MessageEnvelope

logger = get_logger(__name__)


def _humanize_value(v) -> str:
    """Render a JSON value as readable prose for the system prompt (no raw
    Python/JSON repr like ['a','b'] or {'k': 'v'})."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, dict):
        return "; ".join(f"{k.replace('_', ' ')}: {_humanize_value(val)}" for k, val in v.items())
    if isinstance(v, (list, tuple)):
        return ", ".join(_humanize_value(x) for x in v)
    return str(v)


@dataclass
class AgentResponse:
    text: str
    model_id: str
    input_tokens: int
    output_tokens: int
    chunks_retrieved: list[dict]


class BaseAgent:
    """
    Foundation for all Ethiogram agents.

    Process flow per message:
      1. Load conversation history (last N turns)
      2. RAG search against business brain
      3. Build full prompt: system + child_data + knowledge context + history + message
      4. Call model_router.execute_with_fallback
      5. Return AgentResponse

    Subclasses override build_system_prompt() and/or pre_process() to specialise
    behaviour without duplicating the core pipeline.
    """

    agent_name: str = "Assistant"

    async def process(
        self,
        envelope: MessageEnvelope,
        conversation: Conversation,
        brain_config: Optional[BusinessBrainConfig],
        child_data: Optional[dict],
        db: AsyncSession,
        agent_model_id: Optional[str] = None,
        business_preferred_model_id: Optional[str] = None,
    ) -> AgentResponse:
        text = await self.pre_process(envelope, db)

        # 1. Conversation history
        history = await self._load_history(conversation, db)

        # 2. RAG retrieval
        chunks: list[dict] = []
        if text and brain_config:
            chunks = await rag_service.search(
                query=text,
                business_id=str(conversation.business_id),
                db=db,
                top_k=brain_config.rag_top_k,
                similarity_threshold=brain_config.rag_similarity_threshold,
            )

        # 3. Build prompt
        system_prompt = self.build_system_prompt(brain_config, child_data, chunks)

        # 3b. Inject the structured catalog (knowledge_items). Unlike documents,
        # these are curated rows (products, services, FAQs) the bot should always
        # see — they are not retrieved by similarity, they are appended verbatim.
        if brain_config:
            items = await rag_service.get_knowledge_items(
                str(conversation.business_id), db, active_only=True
            )
            items_block = rag_service.format_knowledge_items_for_prompt(items)
            if items_block:
                system_prompt = system_prompt + "\n\n" + items_block

        # 4. Assemble messages: history + current turn
        messages = history + [{"role": "user", "content": text or ""}]

        # 5. Model call with failover (Amharic speakers route to Gemini first)
        response_text, tokens, model_id = await model_router.execute_with_fallback(
            messages=messages,
            system_prompt=system_prompt,
            business_id=str(conversation.business_id) if conversation.business_id else None,
            agent_model_id=agent_model_id,
            business_preferred_model_id=business_preferred_model_id,
            language=getattr(conversation, "detected_language", None),
        )

        logger.info(
            "Agent processed message",
            agent=self.agent_name,
            model_id=model_id,
            chunks=len(chunks),
            input_tokens=tokens.get("input_tokens", 0),
            output_tokens=tokens.get("output_tokens", 0),
            business_id=str(conversation.business_id),
        )

        return AgentResponse(
            text=response_text,
            model_id=model_id,
            input_tokens=tokens.get("input_tokens", 0),
            output_tokens=tokens.get("output_tokens", 0),
            chunks_retrieved=chunks,
        )

    # ------------------------------------------------------------------
    # Override points for subclasses
    # ------------------------------------------------------------------

    async def pre_process(self, envelope: MessageEnvelope, db: AsyncSession) -> str:
        """
        Called before RAG search. Returns the text to embed and send to the model.
        Subclasses can mutate or enrich the text (e.g. OCR an attached image).
        """
        return envelope.text or ""

    def build_system_prompt(
        self,
        brain_config: Optional[BusinessBrainConfig],
        child_data: Optional[dict],
        chunks: list[dict],
    ) -> str:
        parts: list[str] = []

        # Persona and tone from brain config
        if brain_config:
            persona = brain_config.persona_name
            tone = brain_config.persona_tone
            parts.append(
                f"You are {persona}, a {tone} AI assistant for this business. "
                f"Answer in the customer's language. Be concise and helpful."
            )
            if brain_config.system_prompt_extra:
                parts.append(brain_config.system_prompt_extra)
        else:
            parts.append(
                "You are a helpful AI assistant for this business. "
                "Be concise and answer in the customer's language."
            )

        # Father Agent system prompt (child_data carries the decrypted prompt)
        if child_data:
            father_prompt = child_data.get("_father_prompt", "")
            if father_prompt:
                parts.append(f"\n{father_prompt}")

            # Business-specific context filled by the owner (Child layer).
            # Only non-"_" keys are rendered, so reserved/sensitive slots
            # (_father_prompt, _secrets, ...) never reach the model prompt.
            child_context = {k: v for k, v in child_data.items() if not k.startswith("_")}
            if child_context:
                lines = [
                    f"- {k.replace('_', ' ').strip().capitalize()}: {_humanize_value(v)}"
                    for k, v in child_context.items()
                ]
                parts.append("Business-specific details:\n" + "\n".join(lines))

        # RAG context
        if chunks:
            context_block = rag_service.build_context_string(chunks)
            if context_block:
                parts.append(f"\n{context_block}")

        # Fallback instruction
        if brain_config and brain_config.fallback_message:
            parts.append(
                f"\nIf you cannot answer from the provided information, respond with: "
                f'"{brain_config.fallback_message}"'
            )

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # History loader
    # ------------------------------------------------------------------

    async def _load_history(
        self,
        conversation: Conversation,
        db: AsyncSession,
        max_turns: int = settings.max_conversation_history,
    ) -> list[dict]:
        """
        Load the last `max_turns` messages from the conversation,
        returned as OpenAI-style message dicts for use in the prompt.
        """
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(max_turns)
        )
        rows = list(reversed(result.scalars().all()))

        history: list[dict] = []
        for msg in rows:
            if msg.role == MessageRole.user:
                history.append({"role": "user", "content": msg.content})
            elif msg.role == MessageRole.assistant:
                history.append({"role": "assistant", "content": msg.content})
        return history


# Default general-purpose Q&A agent (used directly and as the router fallback).
base_agent = BaseAgent()
