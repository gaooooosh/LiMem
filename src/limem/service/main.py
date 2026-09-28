"""Run the LiMem FastAPI service with one Uvicorn worker."""

import os

import uvicorn

from .app import create_app

app = create_app()


def main() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port, workers=1)


if __name__ == "__main__":
    main()
