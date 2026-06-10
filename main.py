import os
import uvicorn
import requests
from datetime import datetime, date
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles 
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from database import init_db, SessionLocal, User, Task, NudgeCard, SurveyResult, UserNudgeLog
import seed_nudges
import rag_service
import sentiment_service
# pyrefly: ignore [missing-import]
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI(title="Digital Buddy - KMG Onboarding")
scheduler = BackgroundScheduler()

os.makedirs("img", exist_ok=True)
app.mount("/img", StaticFiles(directory="img"), name="img")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    if "X-Frame-Options" in response.headers:
        del response.headers["X-Frame-Options"]
    return response

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def call_bitrix(method: str, params: dict):
    webhook_url = os.getenv("BITRIX_REST_URL")
    if not webhook_url:
        return None
    try:
        if method in ["tasks.task.add", "tasks.task.get"]:
            response = requests.post(f"{webhook_url}{method}", json=params)
        else:
            response = requests.post(f"{webhook_url}{method}", data=params)
        return response.json()
    except Exception as e:
        print(f"[ERROR] Bitrix24 метод {method} упал: {e}")
        return None

# --- КРОН-ПРОВЕРКА 16:00 ---

def check_tasks_final_cron_1600():
    print("\n⏰ [CRON 16:00] Запуск итоговой проверки выполнения задач...")
    db = SessionLocal()
    try:
        active_users = db.query(User).filter(User.current_day == 1).all()
        for emp in active_users:
            incomplete_tasks = []
            for t in emp.tasks:
                try:
                    res_json = call_bitrix("tasks.task.get", {"id": t.id})
                    if isinstance(res_json, dict) and res_json.get("result"):
                        task_data = res_json["result"].get("task", {})
                        if isinstance(task_data, dict):
                            status = task_data.get("status")
                            if status != "5" and status != 5:
                                incomplete_tasks.append(t.title)
                except Exception:
                    continue
            
            if incomplete_tasks:
                tasks_str = ", ".join([f"«{name}»" for name in incomplete_tasks])
                hr_msg = (
                    f"🚨 [Финальный Крон 16:00]\n"
                    f"Сотрудник [b]{emp.fullname}[/b] (ID: {emp.id}) не успевает завершить онбординг!\n"
                    f"Невыполненные задачи: {tasks_str}."
                )
                call_bitrix("im.notify.system.add", {"USER_ID": os.getenv("HR_USER_ID", "1"), "MESSAGE": hr_msg})
    finally:
        db.close()

@app.on_event("startup")
def on_startup():
    print("🚀 Жесткая проверка таблиц PostgreSQL...")
    init_db()
    seed_nudges.seed_data()
    rag_service.init_mock_vnd_data()
    
    scheduler.add_job(check_tasks_final_cron_1600, 'cron', hour=16, minute=0)
    scheduler.start()

# --- ВЕБХУКИ ОНБОРДИНГА ---

@app.post("/bot/welcome")
@app.post("/webhooks/login")
@app.post("/webhooks/on_user_login")
async def handle_on_user_login(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    incoming_id = (
        form_data.get("data[FIELDS_AFTER][ID]") or 
        form_data.get("user_id") or 
        form_data.get("data[USER][ID]") or
        form_data.get("data[PARAMS][FROM_USER_ID]")
    )
    if not incoming_id:
        return {"status": "error", "message": "Missing ID"}
        
    user_id = int(incoming_id)
    user = db.query(User).filter(User.id == user_id).first()
    
    name = "Новый сотрудник"
    user_res = call_bitrix("user.get", {"id": user_id})
    if isinstance(user_res, dict) and user_res.get("result") and len(user_res["result"]) > 0:
        name = user_res["result"].get("NAME", "Сотрудник")

    # Симулируем генерацию 5 задач
    db.query(Task).filter(Task.employee_id == user_id).delete()
    if not user:
        user = User(id=user_id, fullname=name, start_date=date.today(), current_day=1, sent_today=True)
        db.add(user)
    else:
        user.current_day = 1
    db.commit()

    tasks = ["Инструктаж по ТБ", "Инструктаж по ИБ", "Ознакомление с пропускным режимом", "Кодекс деловой этики", "Модуль Комплаенс"]
    for t_title in tasks:
        res = call_bitrix("tasks.task.add", {
            "fields": {
                "TITLE": f"{t_title} для нового сотрудника",
                "RESPONSIBLE_ID": user_id,
                "DEADLINE": f"{datetime.now().strftime('%Y-%m-%d')}T18:00:00+05:00"
            }
        })
        if isinstance(res, dict) and res.get("result"):
            t_id = res["result"].get("task", {}).get("id")
            if t_id:
                db.add(Task(id=str(t_id), title=t_title, employee_id=user_id))
    db.commit()

    welcome_rich_message = (
        f"🚀 [b]Добро пожаловать в АО «НК «КазМунайГаз», {name}![/b]\n\n"
        f"Я подготовил для вас персональный маршрутный лист онбординга и нарезал 5 обязательных стартовых задач.\n\n"
        f"🎯 [b][URL=/market/placement/kmg_digital_buddy_front/]НАЖМИТЕ СЮДА, ЧТОБЫ ОТКРЫТЬ ИНТЕРАКТИВНУЮ ПАНЕЛЬ[/URL][/b] и запустить видеообращение Председателя Правления!"
    )
    call_bitrix("imbot.message.add", {"BOT_ID": "10", "DIALOG_ID": user_id, "MESSAGE": welcome_rich_message})
    return {"status": "success", "tasks": "created"}

# --- ИИ ЧАТ + ДЕМО-РЕЖИМЫ ---

@app.post("/bot/message")
@app.post("/webhooks/on_bot_message")
async def handle_on_bot_message(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    user_id_raw = form_data.get("data[PARAMS][FROM_USER_ID]") or form_data.get("user_id")
    message = (form_data.get("data[PARAMS][MESSAGE]") or form_data.get("message", "")).strip().lower()
    dialog_id = form_data.get("data[PARAMS][DIALOG_ID]")
    bot_id = form_data.get("data[PARAMS][TO_USER_ID]") or "10"
    
    if not user_id_raw or not message:
        return {"status": "ignored"}

    user_id = int(user_id_raw)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id, fullname="Сотрудник КМГ", current_day=1, sent_today=False)
        db.add(user)
        db.commit()

    # СЕКРЕТНЫЙ ТРИГГЕР 1: ДЕМО СИМУЛЯЦИЯ 5 ЗАДАЧ
    if message == "тест задачи":
        await handle_on_user_login(request, db)
        return {"status": "ok"}

    # СЕКРЕТНЫЙ ТРИГГЕР 2: ДЕМО СИМУЛЯЦИЯ 23 КАРТОЧЕК КУЛЬТУРЫ
    if message == "тест культура":
        user.current_day += 1
        if user.current_day > 23:
            user.current_day = 1
        db.commit()
        
        nudge = db.query(NudgeCard).filter(NudgeCard.day == user.current_day).first()
        card_text = f"💡 [b]ДЕНЬ {user.current_day}: {nudge.theme}[/b]\n\n{nudge.text}\n\n📖 Источник: {nudge.source}" if nudge else "Карточка культуры загружается..."
        
        call_bitrix("im.notify.system.add", {"USER_ID": user_id, "MESSAGE": card_text})
        call_bitrix("imbot.message.add", {"BOT_ID": bot_id, "DIALOG_ID": dialog_id or user_id, "MESSAGE": f"⚙️ [Демо-Режим]: Переключаю вас на День {user.current_day} адаптации. Карточка корпоративной культуры отправлена вам в уведомления (колокольчик)!"})
        return {"status": "ok"}

    # Стандартный ответ ИИ по коду
    lang = "kk" if any(char in set("әғқңөұүһіӘҒҚҢӨҰҮҺІ") for char in message) else "ru"
    answer = rag_service.query_rag(message, lang=lang)
    call_bitrix("imbot.message.add", {"BOT_ID": bot_id, "DIALOG_ID": dialog_id or user_id, "MESSAGE": answer})
    return {"status": "ok"}

# --- КОНТЕКСТНОЕ ОКНО DIGITAL BUDDY (IFRAME) ---

@app.api_route("/iframe", methods=["GET", "POST"], response_class=HTMLResponse)
async def get_iframe_page(request: Request):
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <script src="https://api.bitrix24.ru/api/v1/"></script>
        <style>
            body { font-family: "Open Sans", Arial, sans-serif; background: #f4f7f9; margin: 0; padding: 20px; display: flex; justify-content: center; }
            .popup-card { background: #ffffff; border-radius: 12px; box-shadow: 0 6px 20px rgba(0,0,0,0.1); width: 100%; max-width: 450px; border-top: 6px solid #003366; overflow: hidden; }
            .header { display: flex; align-items: center; padding: 20px; background: #fafbfc; border-bottom: 1px solid #eef2f4; }
            .logo-container img { width: 60px; height: 80px; object-fit: contain; margin-right: 15px; }
            .header-title h2 { margin: 0; color: #003366; font-size: 20px; font-weight: 700; }
            .header-title p { margin: 3px 0 0 0; color: #707e8c; font-size: 11px; font-weight: 600; text-transform: uppercase; }
            .content { padding: 24px; }
            .greeting { font-size: 15px; color: #333333; line-height: 1.5; margin: 0 0 20px 0; }
            .video-box { background: #fff9e6; border: 1px solid #ffeeba; border-radius: 8px; padding: 15px; text-align: center; margin-bottom: 20px; }
            .video-box p { margin: 0 0 10px 0; font-size: 13px; color: #856404; font-weight: 600; }
            .video-btn { display: inline-block; background: #dc3545; color: #ffffff; text-decoration: none; padding: 10px 18px; border-radius: 6px; font-weight: bold; font-size: 13px; }
            .task-box { background: #f8f9fa; border-left: 4px solid #007bff; padding: 12px 15px; border-radius: 0 8px 8px 0; margin-bottom: 20px; }
            .task-box h4 { margin: 0 0 5px 0; color: #495057; font-size: 14px; font-weight: 700; }
            .task-box p { margin: 0; color: #333333; font-size: 13px; }
            .progress-container { margin-bottom: 25px; }
            .progress-labels { display: flex; justify-content: space-between; font-size: 13px; font-weight: 600; color: #495057; margin-bottom: 6px; }
            .progress-bar-bg { width: 100%; background: #e9ecef; height: 10px; border-radius: 5px; overflow: hidden; }
            .progress-bar-fill { background: #28a745; height: 100%; width: 0%; transition: width 0.5s ease-out; }
            .footer-buttons { display: flex; gap: 12px; }
            .btn { flex: 1; padding: 12px; border: none; border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer; text-align: center; }
            .btn-close { background: #6c757d; color: #ffffff; }
            .btn-action { background: #003366; color: #ffffff; }
        </style>
    </head>
    <body>
        <div class="popup-card">
            <div class="header">
                <div class="logo-container"><img src="/img/DB_logo.png" alt="Digital Buddy"></div>
                <div class="header-title">
                    <h2>Digital Buddy</h2>
                    <p>ИИ-Наставник КМГ</p>
                </div>
            </div>
            <div class="content">
                <p class="greeting" id="greeting_text">🔄 Синхронизация профиля...</p>
                <div class="video-box">
                    <p>🎬 Обязательное обращение Председателя Правления КМГ:</p>
                    <a href="https://team.kmg.kz/video-pp" target="_blank" class="video-btn">▶️ Смотреть видеообращение</a>
                </div>
                <div class="task-box">
                    <h4>📌 Ближайшая незакрытая задача маршрута:</h4>
                    <p id="task_title">-</p>
                </div>
                <div class="progress-container">
                    <div class="progress-labels">
                        <span>Прогресс онбординга</span>
                        <span id="progress_text">Выполнено 0 из 5 задач</span>
                    </div>
                    <div class="progress-bar-bg"><div class="progress-bar-fill" id="progress_bar"></div></div>
                </div>
                <div class="footer-buttons">
                    <button class="btn btn-close" onclick="closeApp()">Понятно</button>
                    <button class="btn btn-action" onclick="openChat()">Задать вопрос</button>
                </div>
            </div>
        </div>

        <script>
            var dataLoaded = false;
            var currentBotId = "10";

            function loadPopupData() {
                if (dataLoaded) return;
                var currentUserId = 1;
                try {
                    if (typeof BX24 !== 'undefined' && BX24.getAuth) {
                        var auth = BX24.getAuth();
                        if (auth && auth.user_id) currentUserId = auth.user_id;
                    }
                } catch(e) {}

                fetch(window.location.origin + "/api/popup-data?user_id=" + currentUserId)
                    .then(res => res.json())
                    .then(data => {
                        if (data.status === "success") {
                            dataLoaded = true;
                            currentBotId = data.bot_id || "10";
                            document.getElementById('greeting_text').innerHTML = data.greeting;
                            document.getElementById('task_title').innerText = data.next_task.title;
                            document.getElementById('progress_text').innerText = data.progress.text;
                            document.getElementById('progress_bar').style.width = ((data.progress.completed / data.progress.total) * 100) + "%";
                        }
                    });
            }

            if (typeof BX24 !== 'undefined') BX24.init(loadPopupData);
            else window.onload = loadPopupData;

            function closeApp() { 
                if (typeof BX24 !== 'undefined' && BX24.closeApplication) BX24.closeApplication();
                else alert("Окно закрыто.");
            }

            function openChat() { 
                if (typeof BX24 !== 'undefined' && BX24.im && BX24.im.openMessenger) {
                    BX24.im.openMessenger('user' + currentBotId);
                    setTimeout(function() { BX24.closeApplication(); }, 300);
                } else {
                    alert("Переход в чат к боту.");
                }
            }
        </script>
    </body>
    </html>
    """
    return html_content


@app.get("/api/popup-data")
async def get_popup_data(user_id: str):
    db = SessionLocal()
    try:
        numeric_id = int(user_id) if user_id.isdigit() else 1
        user = db.query(User).filter(User.id == numeric_id).first()
        first_name = "Сотрудник"
        
        user_res = call_bitrix("user.get", {"id": numeric_id})
        if isinstance(user_res, dict) and user_res.get("result") and len(user_res["result"]) > 0:
            first_name = user_res["result"].get("NAME", "Сотрудник")

        current_day = user.current_day if user else 1
        completed = db.query(Task).filter(Task.employee_id == numeric_id, Task.is_completed == True).count()
        uncompleted = db.query(Task).filter(Task.employee_id == numeric_id, Task.is_completed == False).first()
        
        return {
            "status": "success",
            "bot_id": "10",
            "greeting": f"Добрый день, {first_name}! День {current_day} вашей адаптации в КМГ.",
            "next_task": {"title": uncompleted.title if uncompleted else "Все задачи успешно закрыты!"},
            "progress": {"text": f"Выполнено {completed} из 5 задач", "completed": completed, "total": 5}
        }
    finally:
        db.close()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)