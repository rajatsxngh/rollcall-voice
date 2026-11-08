"""Settings, read from the repo-root .env file (template: .env.example)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every model call names a job; each job has its own model on each provider.
JOBS = ("manager", "voice", "resident")
PROVIDERS = ("nvidia", "openrouter")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Models: build.nvidia.com first, OpenRouter as fallback.
    nvidia_api_keys: str = ""  # comma-separated, rotated per request
    nvidia_api_key: str = ""  # single-key convenience, merged into the list
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_thinking_style: str = "chat_template_kwargs"  # or: system_tag, none
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_thinking_style: str = "reasoning"  # or: none

    manager_model: str = "nvidia/nemotron-3.5-super-vl-120b-a12b"
    voice_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    resident_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    openrouter_manager_model: str = "nvidia/nemotron-3.5-super-vl:free"
    openrouter_voice_model: str = "nvidia/nemotron-3.5-lightning:free"
    openrouter_resident_model: str = "nvidia/nemotron-3.5-lightning:free"

    llm_max_concurrency: int = 8  # per provider
    llm_timeout_s: float = 60.0
    llm_connect_timeout_s: float = 10.0

    # Phone and texts (M1, M6).
    vapi_api_key: str = ""
    vapi_phone_number_id: str = ""  # an imported Twilio/Vonage/Telnyx number (free Vapi numbers can't dial out)
    vapi_assistant_id: str = ""  # printed by scripts.vapi_setup
    vapi_webhook_secret: str = ""  # if set, /vapi/* requires it in the X-Vapi-Secret header
    vapi_voice_provider: str = "vapi"  # scripts.vapi_setup: the assistant's voice
    vapi_voice_id: str = "Elliot"
    vapi_transcriber_provider: str = "deepgram"
    vapi_transcriber_model: str = "nova-3"
    vapi_max_call_s: int = 180  # Vapi hangs up after this many seconds
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    dispatcher_phone: str = ""
    public_base_url: str = ""

    # App.
    data_dir: Path = REPO_ROOT / "data"
    display_tz: str = "America/Chicago"
    debug_endpoints: bool = True
    approval_threshold: float = 500.0  # dollars; costlier actions wait for the coordinator (M8)

    @property
    def nvidia_keys(self) -> list[str]:
        keys = (k.strip() for k in f"{self.nvidia_api_keys},{self.nvidia_api_key}".split(","))
        return list(dict.fromkeys(k for k in keys if k))

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def live_db_path(self) -> Path:
        return self.runs_dir / "live.db"

    @property
    def llm_cache_path(self) -> Path:
        return self.data_dir / "cache" / "llm_cache.db"

    def model_for(self, provider: str, job: str) -> str:
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        if job not in JOBS:
            raise ValueError(f"unknown job {job!r}; expected one of {JOBS}")
        prefix = "" if provider == "nvidia" else "openrouter_"
        return getattr(self, f"{prefix}{job}_model")


@lru_cache
def get_settings() -> Settings:
    return Settings()
