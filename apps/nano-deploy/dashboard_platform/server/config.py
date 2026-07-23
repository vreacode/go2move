from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "nano-dashboard-server"
    server_host: str = "0.0.0.0"
    server_port: int = 8080

    auth_username: str = "admin"
    auth_password: str = "change-me"
    auth_token: str = "change-me-ui-token"

    board_scheme: str = "http"
    board_host: str = "127.0.0.1"
    board_port: int = 9001
    agent_token: str = "change-me-agent-token"
    tailscale_host: str = ""

    frontend_dist_dir: Path = Path("dashboard_platform/web/dist")
    websocket_status_interval: float = 2.0
    log_buffer_size: int = 500

    @property
    def board_base_url(self) -> str:
        host = self.tailscale_host or self.board_host
        return f"{self.board_scheme}://{host}:{self.board_port}"
