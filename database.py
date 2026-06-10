import os
import sys
from datetime import datetime, date
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Date, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная DATABASE_URL не найдена!")
    sys.exit(1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 1. Профиль сотрудника
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    fullname = Column(String, default="Новый сотрудник")
    start_date = Column(Date, default=date.today)
    current_day = Column(Integer, default=1)
    sent_today = Column(Boolean, default=False)
    at_risk = Column(Boolean, default=False)
    # ВОЗВРАЩЕНО: Поле для предотвращения бесконечного спама в колокольчик HR
    alerted_hr = Column(Boolean, default=False) 
    registered_at = Column(DateTime, default=datetime.utcnow)

    tasks = relationship("Task", back_populates="user", cascade="all, delete-orphan")

# 2. Таблица контроля задач
class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    employee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    is_completed = Column(Boolean, default=False)

    user = relationship("User", back_populates="tasks")

# 3. Карточки вовлечения
class NudgeCard(Base):
    __tablename__ = "nudge_cards"

    day = Column(Integer, primary_key=True)
    theme = Column(String, nullable=False)
    text = Column(String, nullable=False)
    source = Column(String, nullable=True, default="ВНД КМГ")

# 4. Результаты пульс-опросов
class SurveyResult(Base):
    __tablename__ = "survey_results"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    survey_type = Column(Integer)
    score = Column(Integer)
    answers = Column(String, nullable=True)

# 5. Логи отправки карточек
class UserNudgeLog(Base):
    __tablename__ = "user_nudge_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    day_sent = Column(Integer)
    date_sent = Column(Date, default=date.today)

def init_db():
    Base.metadata.create_all(bind=engine)