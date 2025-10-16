# src/providers.py
import json
from abc import ABC, abstractmethod
from typing import Dict, Any
import logging

from src.config import Config
from src.schemas import RESPONSE_SCHEMA_V1

# Importações específicas do Gemini
import google.generativeai as genai

# Importações específicas da DeepSeek
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


# --- 1. INTERFACE ABSTRATA (CONTRATO) ---
class AIProviderBase(ABC):
    # ... (Corpo da classe é o mesmo) ...
    def __init__(self, model_name: str):
        self.model_name = model_name

    @abstractmethod
    async def generate_content(
        self, prompt: str, response_schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        pass


# --- 2. IMPLEMENTAÇÃO GEMINI ---
class GeminiProvider(AIProviderBase):
    """Implementação para a API Gemini (Google Generative AI)."""

    def __init__(self, model_name: str = Config.GEMINI_MODEL):
        super().__init__(model_name)
        genai.configure(api_key=Config.GEMINI_API_KEY)
        self._model = genai.GenerativeModel(self.model_name)

    async def generate_content(
        self, prompt: str, response_schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        logger.info(f"Usando Gemini Provider com modelo: {self.model_name}")

        try:
            # O SCHEMA DE RESPOSTA é o RESPONSE_SCHEMA_V1
            response = self._model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    temperature=0.7,
                ),
            )

            generated_json_str = response.text
            content_data = json.loads(generated_json_str)
            logger.debug("Resposta Gemini parseada com sucesso.")
            return content_data

        except Exception as e:
            logger.error(f"Erro na chamada/parsing do Gemini: {e}")
            raise


# --- 3. IMPLEMENTAÇÃO DEEPSEEK ---
class DeepSeekProvider(AIProviderBase):
    """Implementação para a API DeepSeek (Compatível com OpenAI/AsyncOpenAI)."""

    def __init__(self, model_name: str = Config.DEEPSEEK_MODEL):
        super().__init__(model_name)
        self._client = AsyncOpenAI(
            api_key=Config.DEEPSEEK_API_KEY,
            base_url=Config.DEEPSEEK_BASE_URL,
        )

    async def generate_content(
        self, prompt: str, response_schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        logger.info(f"Usando DeepSeek Provider com modelo: {self.model_name}")

        try:
            # DeepSeek usa response_format. O response_schema é ignorado aqui,
            # mas o prompt é construído esperando a estrutura.
            response = await self._client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                response_format={"type": "json_object"},
            )

            generated_json_str = response.choices[0].message.content
            content_data = json.loads(generated_json_str)
            logger.debug("Resposta DeepSeek parseada com sucesso.")
            return content_data

        except Exception as e:
            logger.error(f"Erro na chamada/parsing do DeepSeek: {e}")
            raise


# --- 4. FACTORY (FUNÇÃO DE FÁBRICA) ---
def get_provider_instance(provider_name: str) -> AIProviderBase:
    # ... (Corpo da função é o mesmo) ...
    provider_name = provider_name.lower()

    if provider_name == "gemini":
        return GeminiProvider()
    elif provider_name == "deepseek":
        return DeepSeekProvider()
    else:
        logger.error(f"Provedor de IA não suportado: {provider_name}")
        raise ValueError(f"Provedor de IA '{provider_name}' não suportado.")
