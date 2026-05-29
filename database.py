from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv
import os

load_dotenv()

# 嘗試多種 Railway 可能給的變數名稱
DATABASE_URL = (
    os.getenv("DATABASE_URL") or
    os.getenv("MYSQL_URL") or
    os.getenv("MYSQL_PRIVATE_URL")
)

if DATABASE_URL:
    # Railway 給的 mysql:// 或 mysql2:// 需換成 mysql+pymysql://
    for prefix in ("mysql2://", "mysql://"):
        if DATABASE_URL.startswith(prefix):
            DATABASE_URL = "mysql+pymysql://" + DATABASE_URL[len(prefix):]
            break
else:
    # 從個別環境變數組合（Railway MySQL plugin 也會提供這些）
    host     = os.getenv("MYSQLHOST",     os.getenv("MYSQL_HOST",     "localhost"))
    port     = os.getenv("MYSQLPORT",     os.getenv("MYSQL_PORT",     "3306"))
    user     = os.getenv("MYSQLUSER",     os.getenv("MYSQL_USER",     "root"))
    password = os.getenv("MYSQLPASSWORD", os.getenv("MYSQL_PASSWORD", ""))
    database = os.getenv("MYSQLDATABASE", os.getenv("MYSQL_DATABASE", "bookweb"))
    DATABASE_URL = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
