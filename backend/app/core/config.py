"""Environment-backed configuration for NOVA.

Secrets are intentionally only read by the backend. The frontend receives a
small, explicitly safe public configuration object from the API.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings with safe local-first defaults."""

    model_config = SettingsConfigDict(
        # Keep the historical project-root file and also support the backend
        # runtime file used by packaged/development backend launches. The
        # backend file is last so its provider credentials/settings win.
        env_file=(Path(__file__).resolve().parents[3] / ".env", Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "NOVA"
    app_version: str = "0.1.0"
    nova_env: str = "development"
    host: str = Field("127.0.0.1", validation_alias=AliasChoices("NOVA_HOST", "HOST"))
    port: int = Field(8742, validation_alias=AliasChoices("NOVA_PORT", "PORT"))
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    llm_provider: str = "ollama"
    ollama_enabled: bool = True
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b-q4_K_M"
    openrouter_enabled: bool = True
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = "openrouter/free"
    gemini_enabled: bool = True
    gemini_api_key: SecretStr | None = None
    gemini_model: str = ""
    # Google documents these exact model IDs as having Free Tier pricing:
    # https://ai.google.dev/gemini-api/docs/pricing
    # Runtime discovery must still confirm that each ID is currently served.
    gemini_free_model_allowlist: str = (
        "gemini-3.8-flash,gemini-3.5-flash,gemini-3.5-flash-lite,"
        "gemini-3.1-flash-lite,gemini-2.5-flash,gemini-2.5-flash-lite"
    )
    model_routing_mode: Literal["LOCAL_ONLY", "FREE_ONLY", "BALANCED", "BEST_AVAILABLE", "OFFLINE"] = "BALANCED"
    zero_budget_mode: bool = True
    free_only: bool = True
    strict_local: bool = False
    privacy_mode: Literal["LOCAL_FIRST", "STRICT_LOCAL", "CLOUD_ALLOWED"] = "LOCAL_FIRST"
    jury_mode: bool = False
    preferred_local_model: str = ""
    preferred_cloud_model: str = ""
    llm_routing_policy: str = "local_first"
    llm_request_timeout: float = 60.0
    llm_max_tokens: int = Field(512, ge=1, le=32768)
    llm_simple_max_tokens: int = Field(256, ge=1, le=32768)
    llm_complex_max_tokens: int = Field(1024, ge=1, le=32768)
    llm_temperature: float = 0.7
    llm_thinking_mode: Literal["off", "on", "smart"] = "smart"
    llm_thinking_default: bool = False
    conversation_history_limit: int = 12
    llm_verbose_logging: bool = False

    database_path: str = "backend/data/nova.db"
    voice_enabled: bool = True
    voice_name: str = "Samantha"
    voice_speed: int = 185
    stt_enabled: bool = True
    tts_enabled: bool = True
    whisper_provider: str = "local"
    whisper_model: str = "small"
    whisper_model_path: str = ""
    whisper_binary: str = "whisper-cli"
    whisper_language: str = "auto"
    stt_timeout: float = 120
    tts_provider: str = "macos"
    tts_voice: str = ""
    tts_language_mode: Literal["auto", "english", "hindi", "hinglish"] = "auto"
    tts_voice_english: str = ""
    tts_voice_hindi: str = ""
    tts_voice_fallback: str = ""
    tts_rate: int = 180
    voice_auto_stop: bool = False
    voice_silence_timeout_ms: int = Field(1200,validation_alias=AliasChoices("VOICE_SILENCE_TIMEOUT_MS","SILENCE_TIMEOUT_MS"))
    voice_max_recording_seconds: int = 60
    voice_shortcut: str = "Alt+Space"
    wake_word_enabled: bool = False
    wake_whisper_model: Literal["tiny","base"] = "tiny"
    wake_model_path: str = ""
    conversation_followup_window_seconds: int = Field(10,ge=0,le=60)
    microphone_device: str = ""
    auto_gain: bool = True
    pre_roll_ms: int = Field(350,ge=0,le=1500)
    post_roll_ms: int = Field(250,ge=0,le=1000)
    screen_context_enabled: bool = False
    memory_enabled: bool = True
    memory_auto_extraction: bool = True
    memory_min_confidence: float = Field(0.75, ge=0.75, le=1)
    memory_top_k: int = Field(5, ge=1, le=20)
    save_chat_history: bool = True
    computer_use_enabled: bool = False
    computer_use_simulation: bool = True
    computer_agent_mode: Literal["ASSISTED", "SEMI_AUTONOMOUS", "AUTONOMOUS"] = "SEMI_AUTONOMOUS"
    computer_screen_access: bool = False
    computer_accessibility_access: bool = False
    computer_browser_access: bool = True
    computer_filesystem_access: bool = True
    computer_terminal_access: bool = False
    computer_vision_enabled: bool = True
    computer_require_confirmation: bool = False
    task_timeout_seconds: int = Field(300, ge=10, le=3600)
    max_task_steps: int = Field(24, ge=1, le=100)
    computer_allow_cloud_planning: bool = False
    command_logging_enabled: bool = True
    native_app_enabled: bool = False
    native_data_root: str = ""
    native_session_token: SecretStr | None = None
    live_task_view_enabled: bool = False
    vision_enabled: bool = True
    connectors_enabled: bool = False
    mcp_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str = "http://127.0.0.1:8742/api/connectors/google/callback"
    google_oauth_scopes: str = "https://www.googleapis.com/auth/gmail.readonly"
    github_client_id: str = ""
    github_client_secret: SecretStr | None = None
    github_redirect_uri: str = "http://127.0.0.1:8742/api/connectors/github/callback"
    github_oauth_scopes: str = "read:user public_repo"
    notion_api_token: SecretStr | None = None
    notion_version: str = "2022-06-28"
    speech_models_directory: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        """Return normalized origins for FastAPI's CORS middleware."""

        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def project_root(self) -> Path:
        return Path(self.native_data_root).expanduser() if self.native_data_root else Path(__file__).resolve().parents[3]

    @property
    def openrouter_configured(self) -> bool:
        return bool(self.openrouter_enabled and self.openrouter_api_key and self.openrouter_api_key.get_secret_value())

    @property
    def gemini_free_model_ids(self) -> tuple[str, ...]:
        """Return exact, operator-updateable Gemini Free Tier model IDs."""

        return tuple(dict.fromkeys(
            model.strip().removeprefix("models/")
            for model in self.gemini_free_model_allowlist.split(",")
            if model.strip()
        ))

    def public_dict(self) -> dict[str, object]:
        """Expose only non-sensitive configuration to the UI."""

        return {
            "app_name": self.app_name,
            "app_version": self.app_version,
            "environment": self.nova_env,
            "llm_provider": self.llm_provider,
            "ollama_enabled": self.ollama_enabled,
            "ollama_model": self.ollama_model,
            "openrouter_enabled": self.openrouter_enabled,
            "openrouter_model": self.openrouter_model,
            "llm_routing_policy": self.llm_routing_policy,
            "llm_max_tokens": self.llm_max_tokens,
            "llm_simple_max_tokens": self.llm_simple_max_tokens,
            "llm_complex_max_tokens": self.llm_complex_max_tokens,
            "llm_temperature": self.llm_temperature,
            "llm_thinking_mode": self.llm_thinking_mode,
            "openrouter_configured": self.openrouter_configured,
            "gemini_configured": bool(self.gemini_enabled and self.gemini_api_key and self.gemini_api_key.get_secret_value()),
            "gemini_free_model_allowlist": list(self.gemini_free_model_ids),
            "model_routing_mode": self.model_routing_mode,
            "zero_budget_mode": self.zero_budget_mode,
            "free_only": self.free_only,
            "strict_local": self.strict_local,
            "voice_enabled": self.voice_enabled,
            "wake_word_enabled": self.wake_word_enabled,
            "screen_context_enabled": self.screen_context_enabled,
            "memory_enabled": self.memory_enabled,
            "google_oauth_configured": bool(self.google_client_id and self.google_client_secret and self.google_client_secret.get_secret_value() and self.google_redirect_uri),
            "github_oauth_configured": bool(self.github_client_id and self.github_client_secret and self.github_client_secret.get_secret_value() and self.github_redirect_uri),
            "features": {"native_app": self.native_app_enabled, "wake_word": self.wake_word_enabled,
                "computer_use": self.computer_use_enabled, "live_task_view": self.live_task_view_enabled,
                "vision": self.vision_enabled, "connectors": self.connectors_enabled, "mcp": self.mcp_enabled},
        }


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-for-runtime settings object."""

    return Settings()
