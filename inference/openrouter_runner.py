"""
openrouter_runner.py — Async OpenRouter LLM runner for agricultural advisories.

Connects to the OpenRouter API to fetch agronomic explanations for single-crop predictions
and multi-crop recommendations. Degrades gracefully if API keys are missing or calls fail.
"""

import os
import logging
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI

logger = logging.getLogger("harvestgate.llm")

SYSTEM_PROMPT = "You are a professional, direct agronomic advisor. Output ONLY the practical, final advice directly. Never think out loud, write preambles, introduce your text, or add conversational filler. Start directly with the analysis."

EXPLANATION_PROMPT = """Based on the following agricultural parameters and predicted yield, write a brief agronomic explanation and one specific recommendation for the farmer.

Context parameters:
- Crop: {crop}
- State: {state}
- Soil Type: {soil_type}
- Nitrogen (N): {N:.2f} Kg/ha
- Phosphorus (P): {P:.2f} Kg/ha
- Potassium (K): {K:.2f} Kg/ha
- Annual Rainfall: {annual_rainfall:.2f} mm
- Irrigation Ratio: {irrigation_ratio:.2f}
- Predicted Yield: {predicted_yield:.2f} Kg/ha

Required Output Format:
Explanation: [Write 2-3 sentences explaining the yield based on soil water retention, nutrients, and rainfall]
Recommendation: [Write 1 actionable practice the farmer should do to optimize yield]

Strict constraint: Do NOT write any introduction, thinking process, preamble, or filler. Start your response directly with the word 'Explanation:'."""

RECOMMEND_PROMPT = """Based on the following regional parameters and the Top 5 recommended crops, write a suitability summary and one general practice for the farmer.

Context parameters:
- State: {state}
- District: {district}
- Soil Type: {soil_type}
- Top 5 Crop Recommendations:
{crop_list}

Required Output Format:
Suitability Summary: [Write 2-3 sentences explaining why these crops match the region's climate/soil profile]
Recommendation: [Write 1 general soil/crop management practice the farmer should follow]

Strict constraint: Do NOT write any introduction, thinking process, preamble, or filler. Start your response directly with the word 'Suitability Summary:'."""


class OpenRouterRunner:
    """Async runner that handles all LLM advisory requests using OpenRouter API."""

    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        self.base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.model = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-nano-9b-v2:free")
        
        self.client: Optional[AsyncOpenAI] = None
        
        if not self.api_key or self.api_key == "your_openrouter_api_key_here":
            logger.warning("OPENROUTER_API_KEY is not set. OpenRouterRunner will run in Graceful Degradation mode.")
        else:
            try:
                import httpx
                # Use a custom httpx AsyncClient to prevent HTTP proxy injection issues
                # in library constructors (especially on Windows / sandbox runtimes)
                http_client = httpx.AsyncClient(timeout=30.0)
                self.client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    http_client=http_client
                )
                logger.info(f"OpenRouterRunner initialized successfully using model: {self.model}")
            except Exception as e:
                logger.error(f"Failed to initialize AsyncOpenAI client: {e}")

    @property
    def is_configured(self) -> bool:
        """Returns True if the runner is fully configured to execute API calls."""
        return self.client is not None

    async def explain_prediction(self, env_profile: Dict[str, Any], crop: str, predicted_yield: float) -> str:
        """
        Generate a natural language explanation for a single-crop yield prediction.
        """
        if not self.is_configured:
            return "Agronomic explanation is temporarily unavailable (API key not configured)."

        prompt = EXPLANATION_PROMPT.format(
            crop=crop,
            state=env_profile.get("State Name", "N/A"),
            soil_type=env_profile.get("Primary Soil Type", "N/A"),
            N=env_profile.get("N (Kg/ha)", 0.0),
            P=env_profile.get("P (Kg/ha)", 0.0),
            K=env_profile.get("K (Kg/ha)", 0.0),
            annual_rainfall=env_profile.get("Annual Rainfall (mm)", 0.0),
            irrigation_ratio=env_profile.get("Irrigation Ratio", 0.0),
            predicted_yield=predicted_yield
        )
        return await self._call_llm(prompt)

    async def explain_recommendation(self, state: str, district: Optional[str], soil_type: str, recommendations: List[Any]) -> str:
        """
        Generate a natural language advisory for a list of top crop recommendations.
        """
        if not self.is_configured:
            return "Agronomic advisory is temporarily unavailable (API key not configured)."

        # Format crop list for prompt context
        crop_entries = []
        for i, rec in enumerate(recommendations, 1):
            crop_entries.append(
                f"{i}. {rec.crop} | Suitability: {rec.suitability_percentage:.1f}% | Est. Yield: {rec.expected_yield_kg_per_ha:.1f} Kg/ha"
            )
        crop_list_str = "\n".join(crop_entries)

        prompt = RECOMMEND_PROMPT.format(
            state=state,
            district=district if district else "All Districts",
            soil_type=soil_type,
            crop_list=crop_list_str
        )
        return await self._call_llm(prompt)

    async def _call_llm(self, prompt: str) -> str:
        """Executes API completion request with custom headers, model routing, and error boundaries."""
        if not self.client:
            return "Agronomic advisory is temporarily unavailable."

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=400,      # Constrain token generation to limit response size
                temperature=0.7,     # Balances factual precision and natural phrasing
                extra_headers={
                    "HTTP-Referer": "https://github.com/Biswajit-17/HarvestGate---ML-Inference-Gateway",
                    "X-Title": "HarvestGate Crop Inference Gateway"
                }
            )
            raw_text = response.choices[0].message.content
            if not raw_text:
                # Fallback for reasoning models (like Nemotron/DeepSeek) that put output in reasoning field
                msg = response.choices[0].message
                raw_text = getattr(msg, "reasoning", None) or "No explanation content returned by the model."

            # Truncate response context strictly to 500 characters to prevent overflow / resource billing attacks
            explanation = raw_text.strip()
            if len(explanation) > 500:
                explanation = explanation[:497] + "..."
            return explanation
        except Exception as e:
            logger.error(f"OpenRouter API call failed: {e}")
            return "Agronomic advisory is temporarily unavailable due to external API latency."
