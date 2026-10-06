"""Run a separate private owner interface. The public app never mounts it."""

import argparse
import getpass
import os

import uvicorn

from agrivoltaic.owner import create_owner_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    password = os.environ.get("AGRIVOLTAIC_OWNER_PASSWORD") or getpass.getpass(
        "Пароль закрытой программы (минимум 12 символов): "
    )
    app = create_owner_app(password)
    print(f"Откройте http://127.0.0.1:{args.port}; имя пользователя: owner")
    uvicorn.run(app, host="127.0.0.1", port=args.port)
