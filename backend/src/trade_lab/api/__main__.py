"""Run with: python -m trade_lab.api"""

import uvicorn

from trade_lab.config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "trade_lab.api.app:create_app",
        factory=True,
        host=settings.backend_host,
        port=settings.backend_port,
    )


if __name__ == "__main__":
    main()
