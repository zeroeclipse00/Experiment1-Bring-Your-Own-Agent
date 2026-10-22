import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    data_dir: Path = ROOT / "data"
    mode: str = "demo"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = ""
    timeout: float = 60
    max_rounds: int = 8
    max_calls: int = 24

    @property
    def live_ready(self) -> bool:
        return bool(self.api_key and self.model and self.base_url)

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("AGENT_MODE", "demo")
        if mode not in {"demo", "live"}:
            raise ValueError("AGENT_MODE 必须为 demo 或 live")
        return cls(
            mode=mode,
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            api_key=os.getenv("LLM_API_KEY", ""),
            model=os.getenv("LLM_MODEL", ""),
            timeout=float(os.getenv("LLM_TIMEOUT", "60")),
        )
