"""Separate local application for the owner, with mandatory authentication."""

import html
import json
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .storage import get_calculation, list_calculations

STYLE = "body{max-width:1200px;margin:40px auto;padding:0 24px;font:15px/1.6 system-ui;color:#234537;background:#f4f6f0}a{color:#17603c}table{width:100%;border-collapse:collapse;background:white}td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:22px}h1{font-size:28px}.notice{background:#e5eedb;padding:16px;border-radius:10px}"


def create_owner_app(password: str, username: str = "owner"):
    if len(password) < 12:
        raise ValueError("Для закрытой программы нужен пароль не короче 12 символов")
    basic = HTTPBasic()

    def authorize(credentials: Annotated[HTTPBasicCredentials, Depends(basic)]):
        valid_user = secrets.compare_digest(credentials.username.encode(), username.encode())
        valid_password = secrets.compare_digest(credentials.password.encode(), password.encode())
        if not (valid_user and valid_password):
            raise HTTPException(401, "Неверные данные доступа", headers={"WWW-Authenticate": "Basic"})

    app = FastAPI(
        title="Закрытый журнал агривольтаики",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(authorize)],
    )

    @app.get("/", response_class=HTMLResponse)
    def index():
        rows = "".join(
            f'<tr><td><a href="/calculation/{r["id"]}">{html.escape(r["id"][:12])}</a></td>'
            f"<td>{html.escape(r['created_at'])}</td><td>{html.escape(r['sector'])}</td>"
            f"<td>{html.escape(r['product'])}</td></tr>"
            for r in list_calculations()
        )
        return f'<html lang="ru"><meta charset="utf-8"><title>Журнал конфигураций</title><style>{STYLE}</style><h1>Закрытый журнал конфигураций</h1><p class="notice">Этот интерфейс запускается отдельно от сайта. Здесь хранятся геометрия, выбор пользователя, исходные коэффициенты и сценарные проверки.</p><table><tr><th>Расчёт</th><th>Дата UTC</th><th>Направление</th><th>Продукция</th></tr>{rows}</table></html>'

    @app.get("/calculation/{calculation_id}", response_class=HTMLResponse)
    def detail(calculation_id: str):
        record = get_calculation(calculation_id)
        if record is None:
            raise HTTPException(404, "Расчёт не найден")
        rows = ""
        for i, variant in enumerate(record["variants"], 1):
            g = variant["configuration"]
            chosen = "Выбран" if record["selected_variant_id"] == variant["id"] else ""
            rows += (
                f"<tr><td>{i} {chosen}</td><td>{g['pitch_m']:.2f}</td><td>{g['height_m']:.2f}</td>"
                f"<td>{g['tilt_deg']:.1f}</td><td>{100 * g['gcr']:.1f}%</td><td>{g['num_panels']}</td>"
                f"<td>{variant['economics']['npv_rub']:,.0f}</td></tr>"
            )
        content = html.escape(json.dumps(record, ensure_ascii=False, indent=2))
        return f'<html lang="ru"><meta charset="utf-8"><title>Конфигурации расчёта</title><style>{STYLE}</style><a href="/">← Все расчёты</a><h1>{html.escape(record["inputs"]["product_name"])}</h1><p>NSGA-II: приближённые компромиссы, выбор пользователя сохранён отдельно.</p><table><tr><th>Вариант</th><th>Шаг, м</th><th>Высота центра, м</th><th>Наклон, °</th><th>GCR</th><th>Панели</th><th>NPV, руб</th></tr>{rows}</table><p><a href="/api/calculations/{calculation_id}">Полный JSON</a></p><details><summary>Все расчёты, коэффициенты и допущения</summary><pre>{content}</pre></details></html>'

    @app.get("/api/calculations")
    def records():
        return list_calculations()

    @app.get("/api/calculations/{calculation_id}")
    def record(calculation_id: str):
        result = get_calculation(calculation_id)
        if result is None:
            raise HTTPException(404, "Расчёт не найден")
        return result

    return app
