from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_, text
from typing import List, Optional
from collections import defaultdict
import bcrypt as _bcrypt
from dotenv import load_dotenv
import os
import json
import cloudinary
import cloudinary.uploader
from database import SessionLocal

import database, models
from database import get_db
from models import DEPARTMENTS, CONDITIONS, CONDITION_MAP, DEPARTMENT_GROUPS

load_dotenv()

# ── App setup ─────────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)

try:
    models.Base.metadata.create_all(bind=database.engine)
except Exception as _e:
    print(f"[WARN] create_all failed: {_e}")

# Migrations: add columns and tables if not yet present
_migrations = [
    "ALTER TABLE users ADD COLUMN line_id VARCHAR(50)",
    # conversations table (pre-order chat)
    """CREATE TABLE IF NOT EXISTS conversations (
        id INT AUTO_INCREMENT PRIMARY KEY,
        book_id INT NOT NULL,
        buyer_id INT NOT NULL,
        created_at DATETIME,
        UNIQUE KEY uq_conv_book_buyer (book_id, buyer_id),
        FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
        FOREIGN KEY (buyer_id) REFERENCES users(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    # conv_messages table
    """CREATE TABLE IF NOT EXISTS conv_messages (
        id INT AUTO_INCREMENT PRIMARY KEY,
        conv_id INT NOT NULL,
        sender_id INT NOT NULL,
        content TEXT NOT NULL,
        created_at DATETIME,
        FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE,
        FOREIGN KEY (sender_id) REFERENCES users(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]
for _sql in _migrations:
    try:
        with database.engine.connect() as _conn:
            _conn.execute(text(_sql))
            _conn.commit()
    except Exception:
        pass

app = FastAPI(title="暨大二手書平台")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "ncnu-bookweb-secret"),
    max_age=86400,
)
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

# url_for wrapper: keeps Flask-style `filename=` for 'static', passes `filename=` for 'uploaded_file'
def _url_for(name: str, **kwargs) -> str:
    if name == "static" and "filename" in kwargs:
        kwargs["path"] = kwargs.pop("filename")
    return str(app.url_path_for(name, **kwargs))

templates.env.globals["url_for"] = _url_for

def _hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()

def _verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


# ── WebSocket Connection Manager ──────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self._rooms: dict[int, list[WebSocket]] = defaultdict(list)

    async def connect(self, ws: WebSocket, room_id: int):
        await ws.accept()
        self._rooms[room_id].append(ws)

    def disconnect(self, ws: WebSocket, room_id: int):
        try:
            self._rooms[room_id].remove(ws)
        except ValueError:
            pass

    async def broadcast(self, room_id: int, data: dict):
        dead = []
        for ws in list(self._rooms[room_id]):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, room_id)


manager      = ConnectionManager()  # order-based chats
conv_manager = ConnectionManager()  # pre-order conversations


# ── Helpers ───────────────────────────────────────────────────────────────────

def flash(request: Request, message: str, category: str = "info"):
    request.session.setdefault("_flashes", []).append([category, message])


def get_session_user(request: Request, db: Session) -> Optional[models.User]:
    uid = request.session.get("user_id")
    if uid:
        return db.query(models.User).filter(models.User.id == uid).first()
    return None


def render(tpl: str, request: Request, db: Session, ctx: dict = {}):
    flashes = request.session.pop("_flashes", [])
    user = get_session_user(request, db)
    return templates.TemplateResponse(tpl, {
        "request":           request,
        "flashed_messages":  flashes,
        "current_user":      user,
        "departments":       DEPARTMENTS,
        "dept_groups":       DEPARTMENT_GROUPS,
        "conditions":        CONDITIONS,
        **ctx,
    })


def redirect(name: str, **kwargs) -> RedirectResponse:
    return RedirectResponse(url=app.url_path_for(name, **kwargs), status_code=303)


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


async def save_upload(upload: UploadFile) -> Optional[str]:
    """Upload to Cloudinary; returns secure URL or None."""
    if not upload or not upload.filename or not allowed_file(upload.filename):
        return None
    data = await upload.read()
    if not data:
        print("[WARN] save_upload: read() returned empty bytes")
        return None
    print(f"[INFO] uploading {upload.filename} ({len(data)} bytes) to Cloudinary")
    result = cloudinary.uploader.upload(
        data,
        folder="bookweb",
        resource_type="auto",
    )
    url = result.get("secure_url")
    print(f"[INFO] Cloudinary upload OK: {url}")
    return url


# ── Debug ─────────────────────────────────────────────────────────────────────

from fastapi.responses import JSONResponse

@app.get("/debug/cloudinary")
async def debug_cloudinary():
    """Check Cloudinary env vars and attempt a tiny test upload."""
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    api_key    = os.getenv("CLOUDINARY_API_KEY", "")
    api_secret = os.getenv("CLOUDINARY_API_SECRET", "")
    info = {
        "CLOUDINARY_CLOUD_NAME": cloud_name or "(not set)",
        "CLOUDINARY_API_KEY":    api_key[:6] + "..." if api_key else "(not set)",
        "CLOUDINARY_API_SECRET": "(set)" if api_secret else "(not set)",
    }
    if not (cloud_name and api_key and api_secret):
        return JSONResponse({"status": "MISSING_ENV", "vars": info})
    # Try uploading a tiny 1×1 transparent PNG
    import base64, io
    tiny_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YGD4DwABBAEAWjR/rQAAAABJRU5ErkJggg=="
    )
    try:
        result = cloudinary.uploader.upload(
            tiny_png, folder="bookweb_debug", resource_type="image",
            public_id="__debug_test__", overwrite=True
        )
        return JSONResponse({"status": "OK", "vars": info, "url": result.get("secure_url")})
    except Exception as e:
        return JSONResponse({"status": "UPLOAD_ERROR", "vars": info, "error": str(e)})


# ── Index / Search ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    q: str = "",
    department: str = "",
    condition: str = "",
    sort: str = "newest",
    price_min: str = "",
    price_max: str = "",
    db: Session = Depends(get_db),
):
    query = db.query(models.Book).filter(models.Book.status == "available")
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            models.Book.title.ilike(like),
            models.Book.author.ilike(like),
            models.Book.isbn.ilike(like),
            models.Book.description.ilike(like),
        ))
    if department:
        query = query.filter(models.Book.department == department)
    if condition:
        query = query.filter(models.Book.condition == condition)
    p_min = int(price_min) if price_min.strip().isdigit() else None
    p_max = int(price_max) if price_max.strip().isdigit() else None
    if p_min is not None:
        query = query.filter(models.Book.price >= p_min)
    if p_max is not None:
        query = query.filter(models.Book.price <= p_max)
    if sort == "price_asc":
        query = query.order_by(models.Book.price.asc())
    elif sort == "price_desc":
        query = query.order_by(models.Book.price.desc())
    else:
        query = query.order_by(models.Book.created_at.desc())
    books = query.all()

    return render("index.html", request, db, {
        "books": books,
        "q": q,
        "selected_dept": department,
        "selected_cond": condition,
        "sort": sort,
        "price_min": price_min,
        "price_max": price_max,
    })


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, db: Session = Depends(get_db)):
    if request.session.get("user_id"):
        return redirect("index")
    return render("register.html", request, db)


@app.post("/register")
async def register(
    request: Request,
    student_id: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    department: str = Form(...),
    phone: str = Form(""),
    db: Session = Depends(get_db),
):
    if not all([student_id.strip(), password, name.strip(), department]):
        flash(request, "請填寫所有必填欄位", "danger")
        return redirect("register_page")

    if db.query(models.User).filter(models.User.student_id == student_id.strip()).first():
        flash(request, "此學號已被註冊", "danger")
        return redirect("register_page")

    user = models.User(
        student_id=student_id.strip(),
        password_hash=_hash_password(password),
        name=name.strip(),
        department=department,
        phone=phone.strip(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    request.session["user_id"] = user.id
    flash(request, f"歡迎加入，{user.name}！", "success")
    return redirect("index")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    if request.session.get("user_id"):
        return redirect("index")
    return render("login.html", request, db)


@app.post("/login")
async def login(
    request: Request,
    student_id: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.student_id == student_id.strip()).first()
    if user and _verify_password(password, user.password_hash):
        request.session["user_id"] = user.id
        flash(request, f"歡迎回來，{user.name}！", "success")
        return redirect("index")
    flash(request, "學號或密碼錯誤", "danger")
    return redirect("login_page")


@app.get("/logout")
async def logout(request: Request):
    request.session.pop("user_id", None)
    flash(request, "已登出", "info")
    return redirect("index")


# ── Sell ──────────────────────────────────────────────────────────────────────

@app.get("/sell", response_class=HTMLResponse)
async def sell_page(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        flash(request, "請先登入", "warning")
        return redirect("login_page")
    return render("sell.html", request, db)


@app.post("/sell")
async def sell(
    request: Request,
    title:       str             = Form(...),
    author:      str             = Form(""),
    isbn:        str             = Form(""),
    price:       int             = Form(...),
    condition:   str             = Form(...),
    department:  str             = Form(...),
    description: str             = Form(""),
    photos:      List[UploadFile]= File(default=[]),
    slot_date:   List[str]       = Form(default=[]),
    slot_time:   List[str]       = Form(default=[]),
    slot_location: List[str]     = Form(default=[]),
    db: Session = Depends(get_db),
):
    uid = request.session.get("user_id")
    if not uid:
        flash(request, "請先登入", "warning")
        return redirect("login_page")

    if price < 0:
        flash(request, "價格不能為負數", "danger")
        return redirect("sell_page")

    book = models.Book(
        title=title.strip(), author=author.strip(), isbn=isbn.strip(),
        price=price, condition=condition, department=department,
        description=description.strip(), seller_id=uid,
    )
    db.add(book)
    db.flush()

    upload_errors = 0
    last_upload_err = ""
    for upload in photos:
        try:
            fname = await save_upload(upload)
            if fname:
                db.add(models.BookPhoto(book_id=book.id, filename=fname))
        except Exception as _ue:
            last_upload_err = str(_ue)
            print(f"[WARN] photo upload failed: {_ue}", flush=True)
            upload_errors += 1

    for d, t, l in zip(slot_date, slot_time, slot_location):
        if d.strip() and t.strip() and l.strip():
            db.add(models.TimeSlot(book_id=book.id, date=d.strip(),
                                   time_str=t.strip(), location=l.strip()))
    db.commit()

    if upload_errors:
        err_hint = f"錯誤：{last_upload_err[:120]}" if last_upload_err else "請確認圖片大小與格式"
        flash(request, f"書籍上架成功，但有 {upload_errors} 張圖片上傳失敗。{err_hint}", "warning")
    else:
        flash(request, "書籍上架成功！", "success")
    return redirect("book_detail", book_id=book.id)


# ── Book detail ───────────────────────────────────────────────────────────────

@app.get("/book/{book_id}", response_class=HTMLResponse)
async def book_detail(request: Request, book_id: int, db: Session = Depends(get_db)):
    book = db.query(models.Book).filter(models.Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="書籍不存在")
    return render("book_detail.html", request, db, {
        "book": book,
        "active_order": book.active_order,
    })


# ── Orders ────────────────────────────────────────────────────────────────────

@app.post("/book/{book_id}/order")
async def place_order(
    request: Request,
    book_id: int,
    timeslot_id: int = Form(...),
    db: Session = Depends(get_db),
):
    uid = request.session.get("user_id")
    if not uid:
        flash(request, "請先登入", "warning")
        return redirect("login_page")

    book = db.query(models.Book).filter(models.Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404)
    if book.status != "available":
        flash(request, "此書籍目前無法下單", "warning")
        return redirect("book_detail", book_id=book_id)
    if book.seller_id == uid:
        flash(request, "不能購買自己上架的書籍", "warning")
        return redirect("book_detail", book_id=book_id)

    order = models.Order(book_id=book_id, buyer_id=uid, timeslot_id=timeslot_id)
    book.status = "locked"
    db.add(order)
    db.commit()
    flash(request, "下單成功！請依約定時間地點完成面交", "success")
    return redirect("my_orders")


@app.post("/order/{order_id}/complete")
async def complete_order(request: Request, order_id: int, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        return redirect("login_page")
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order or order.book.seller_id != uid or order.status != "pending":
        flash(request, "無法執行此操作", "danger")
        return redirect("my_listings")
    order.status = "completed"
    order.book.status = "sold"
    db.commit()
    flash(request, "交易已完成！書籍已下架", "success")
    return redirect("my_listings")


@app.post("/order/{order_id}/cancel")
async def cancel_order(request: Request, order_id: int, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        return redirect("login_page")
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order or order.status != "pending":
        flash(request, "無法取消此訂單", "warning")
        return redirect("my_listings")
    if order.book.seller_id != uid and order.buyer_id != uid:
        flash(request, "無權限執行此操作", "danger")
        return redirect("index")
    order.status = "cancelled"
    order.book.status = "available"
    db.commit()
    flash(request, "交易已取消，書籍已恢復上架", "info")
    return redirect("my_listings" if order.book.seller_id == uid else "my_orders")


# ── My pages ──────────────────────────────────────────────────────────────────

@app.get("/my-listings", response_class=HTMLResponse)
async def my_listings(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        flash(request, "請先登入", "warning")
        return redirect("login_page")
    books = (db.query(models.Book)
               .filter(models.Book.seller_id == uid)
               .order_by(models.Book.created_at.desc())
               .all())
    return render("my_listings.html", request, db, {"books": books})


@app.get("/my-orders", response_class=HTMLResponse)
async def my_orders(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        flash(request, "請先登入", "warning")
        return redirect("login_page")
    orders = (db.query(models.Order)
                .filter(models.Order.buyer_id == uid)
                .order_by(models.Order.created_at.desc())
                .all())
    return render("my_orders.html", request, db, {"orders": orders})


@app.get("/guide", response_class=HTMLResponse)
async def guide(request: Request, db: Session = Depends(get_db)):
    return render("guide.html", request, db)


# ── Profile ───────────────────────────────────────────────────────────────────

@app.get("/profile/edit", response_class=HTMLResponse)
async def profile_edit_page(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        flash(request, "請先登入", "warning")
        return redirect("login_page")
    user = db.query(models.User).filter(models.User.id == uid).first()
    return render("profile_edit.html", request, db, {"edit_user": user})


@app.post("/profile/edit")
async def profile_edit(
    request: Request,
    name: str = Form(...),
    department: str = Form(...),
    phone: str = Form(""),
    line_id: str = Form(""),
    db: Session = Depends(get_db),
):
    uid = request.session.get("user_id")
    if not uid:
        return redirect("login_page")
    user = db.query(models.User).filter(models.User.id == uid).first()
    if not user:
        return redirect("index")
    if not name.strip():
        flash(request, "姓名不能為空", "danger")
        return redirect("profile_edit_page")
    user.name = name.strip()
    user.department = department
    user.phone = phone.strip()
    user.line_id = line_id.strip()
    db.commit()
    flash(request, "個人資料已更新！", "success")
    return redirect("profile_edit_page")


# ── Edit book ─────────────────────────────────────────────────────────────────

@app.get("/book/{book_id}/edit", response_class=HTMLResponse)
async def edit_book_page(request: Request, book_id: int, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        flash(request, "請先登入", "warning")
        return redirect("login_page")
    book = db.query(models.Book).filter(models.Book.id == book_id).first()
    if not book or book.seller_id != uid:
        flash(request, "無權限編輯此書籍", "danger")
        return redirect("my_listings")
    if book.status == "sold":
        flash(request, "已售出的書籍無法編輯", "warning")
        return redirect("my_listings")

    existing_slots = [
        {"date": s.date, "time": s.time_str, "location": s.location}
        for s in book.timeslots
        if book.status == "available"  # only pre-fill for available; locked shows existing as read-only
    ]
    default_loc = book.timeslots[0].location if book.timeslots else ""
    return render("edit_book.html", request, db, {
        "book": book,
        "existing_slots_json": json.dumps(existing_slots),
        "default_location_json": json.dumps(default_loc),
    })


@app.post("/book/{book_id}/edit")
async def edit_book(
    request: Request,
    book_id: int,
    title: str = Form(...),
    author: str = Form(""),
    isbn: str = Form(""),
    price: int = Form(...),
    condition: str = Form(...),
    department: str = Form(...),
    description: str = Form(""),
    slot_date: List[str] = Form(default=[]),
    slot_time: List[str] = Form(default=[]),
    slot_location: List[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    uid = request.session.get("user_id")
    if not uid:
        return redirect("login_page")
    book = db.query(models.Book).filter(models.Book.id == book_id).first()
    if not book or book.seller_id != uid:
        flash(request, "無權限編輯此書籍", "danger")
        return redirect("my_listings")
    if book.status == "sold":
        flash(request, "已售出的書籍無法編輯", "warning")
        return redirect("my_listings")
    if price < 0:
        flash(request, "價格不能為負數", "danger")
        return redirect("edit_book_page", book_id=book_id)

    book.title = title.strip()
    book.author = author.strip()
    book.isbn = isbn.strip()
    book.price = price
    book.condition = condition
    book.department = department
    book.description = description.strip()

    new_slots = [(d.strip(), t.strip(), l.strip())
                 for d, t, l in zip(slot_date, slot_time, slot_location)
                 if d.strip() and t.strip() and l.strip()]

    if book.status == "available":
        for slot in list(book.timeslots):
            db.delete(slot)
        db.flush()
        for d, t, l in new_slots:
            db.add(models.TimeSlot(book_id=book.id, date=d, time_str=t, location=l))
    elif book.status == "locked":
        for d, t, l in new_slots:
            db.add(models.TimeSlot(book_id=book.id, date=d, time_str=t, location=l))

    db.commit()
    flash(request, "書籍資訊已更新！", "success")
    return redirect("book_detail", book_id=book_id)


# ── Change timeslot ───────────────────────────────────────────────────────────

@app.post("/order/{order_id}/change-timeslot")
async def change_timeslot(
    request: Request,
    order_id: int,
    timeslot_id: int = Form(...),
    db: Session = Depends(get_db),
):
    uid = request.session.get("user_id")
    if not uid:
        return redirect("login_page")
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order or order.buyer_id != uid or order.status != "pending":
        flash(request, "無法修改此訂單", "warning")
        return redirect("my_orders")
    slot = db.query(models.TimeSlot).filter(
        models.TimeSlot.id == timeslot_id,
        models.TimeSlot.book_id == order.book_id,
    ).first()
    if not slot:
        flash(request, "所選時段不存在", "danger")
        return redirect("my_orders")
    order.timeslot_id = timeslot_id
    db.commit()
    flash(request, "面交時間已更新！", "success")
    return redirect("my_orders")


@app.post("/book/{book_id}/delete")
async def delete_book(request: Request, book_id: int, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        return redirect("login_page")
    book = db.query(models.Book).filter(models.Book.id == book_id).first()
    if not book or book.seller_id != uid:
        flash(request, "無權限刪除此書籍", "danger")
        return redirect("my_listings")
    if book.status == "locked":
        flash(request, "交易中的書籍無法刪除，請先取消交易", "warning")
        return redirect("my_listings")
    for photo in book.photos:
        # filename now stores a Cloudinary secure URL; extract public_id to delete
        url = photo.filename
        if url and "cloudinary.com" in url:
            # e.g. .../image/upload/v123/bookweb/abc.jpg → public_id = bookweb/abc
            try:
                part = url.split("/image/upload/")[1]
                public_id = "/".join(part.split("/")[1:]).rsplit(".", 1)[0]
                cloudinary.uploader.destroy(public_id)
            except Exception:
                pass
    db.delete(book)
    db.commit()
    flash(request, "書籍已刪除", "info")
    return redirect("my_listings")


# ── Chat ──────────────────────────────────────────────────────────────────────

@app.get("/chat/{order_id}", response_class=HTMLResponse)
async def chat_page(request: Request, order_id: int, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        flash(request, "請先登入", "warning")
        return redirect("login_page")
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="訂單不存在")
    if order.buyer_id != uid and order.book.seller_id != uid:
        flash(request, "無權限查看此聊天室", "danger")
        return redirect("index")
    if order.status == "cancelled":
        flash(request, "已取消的訂單無法使用聊天室", "warning")
        return redirect("my_orders" if order.buyer_id == uid else "my_listings")

    other_user = order.book.seller if order.buyer_id == uid else order.buyer
    is_buyer = (order.buyer_id == uid)
    back_url = str(app.url_path_for("my_orders")) if is_buyer else str(app.url_path_for("my_listings"))
    return render("chat.html", request, db, {
        "chat_mode":    "order",
        "chat_room_id": order.id,
        "other_user":   other_user,
        "book":         order.book,
        "messages":     order.messages,
        "can_send":     order.status != "cancelled",
        "is_buyer":     is_buyer,
        "order":        order,
        "back_url":     back_url,
    })


# ── Pre-order Conversations ───────────────────────────────────────────────────

@app.get("/book/{book_id}/chat", response_class=HTMLResponse)
async def start_chat(request: Request, book_id: int, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        flash(request, "請先登入才能聯絡賣家", "warning")
        return redirect("login_page")
    book = db.query(models.Book).filter(models.Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404)
    if book.seller_id == uid:
        flash(request, "不能與自己聊天", "warning")
        return redirect("book_detail", book_id=book_id)

    conv = (db.query(models.Conversation)
              .filter_by(book_id=book_id, buyer_id=uid)
              .first())
    if not conv:
        conv = models.Conversation(book_id=book_id, buyer_id=uid)
        db.add(conv)
        db.commit()
        db.refresh(conv)
    return redirect("conv_page", conv_id=conv.id)


@app.get("/conv/{conv_id}", response_class=HTMLResponse)
async def conv_page(request: Request, conv_id: int, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        flash(request, "請先登入", "warning")
        return redirect("login_page")
    conv = db.query(models.Conversation).filter(models.Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="對話不存在")
    if conv.buyer_id != uid and conv.book.seller_id != uid:
        flash(request, "無權限查看此對話", "danger")
        return redirect("index")

    is_buyer = (conv.buyer_id == uid)
    other_user = conv.book.seller if is_buyer else conv.buyer
    back_url = str(app.url_path_for("my_chats"))
    return render("chat.html", request, db, {
        "chat_mode":    "conv",
        "chat_room_id": conv.id,
        "other_user":   other_user,
        "book":         conv.book,
        "messages":     conv.messages,
        "can_send":     True,
        "is_buyer":     is_buyer,
        "order":        None,
        "back_url":     back_url,
    })


@app.websocket("/ws/conv/{conv_id}")
async def websocket_conv(websocket: WebSocket, conv_id: int):
    uid = websocket.session.get("user_id")
    if not uid:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    db = SessionLocal()
    try:
        conv = db.query(models.Conversation).filter(models.Conversation.id == conv_id).first()
        if not conv or (conv.buyer_id != uid and conv.book.seller_id != uid):
            await websocket.close(code=1008, reason="Forbidden")
            return

        user = db.query(models.User).filter(models.User.id == uid).first()
        await conv_manager.connect(websocket, conv_id)

        try:
            while True:
                content = (await websocket.receive_text()).strip()
                if not content or len(content) > 1000:
                    continue
                msg = models.ConvMessage(conv_id=conv_id, sender_id=uid, content=content)
                db.add(msg)
                db.commit()
                db.refresh(msg)
                await conv_manager.broadcast(conv_id, {
                    "sender_id":   uid,
                    "sender_name": user.name,
                    "content":     content,
                    "time":        msg.created_at.strftime("%m/%d %H:%M"),
                })
        except WebSocketDisconnect:
            conv_manager.disconnect(websocket, conv_id)
    finally:
        db.close()


@app.get("/my-chats", response_class=HTMLResponse)
async def my_chats(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        flash(request, "請先登入", "warning")
        return redirect("login_page")

    buyer_convs = (db.query(models.Conversation)
                   .filter(models.Conversation.buyer_id == uid)
                   .order_by(models.Conversation.created_at.desc())
                   .all())

    seller_convs = (db.query(models.Conversation)
                    .join(models.Book)
                    .filter(models.Book.seller_id == uid)
                    .order_by(models.Conversation.created_at.desc())
                    .all())

    return render("my_chats.html", request, db, {
        "buyer_convs":  buyer_convs,
        "seller_convs": seller_convs,
    })


@app.websocket("/ws/chat/{order_id}")
async def websocket_chat(websocket: WebSocket, order_id: int):
    uid = websocket.session.get("user_id")
    if not uid:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    db = SessionLocal()
    try:
        order = db.query(models.Order).filter(models.Order.id == order_id).first()
        if not order or (order.buyer_id != uid and order.book.seller_id != uid):
            await websocket.close(code=1008, reason="Forbidden")
            return
        if order.status == "cancelled":
            await websocket.close(code=1008, reason="Order cancelled")
            return

        user = db.query(models.User).filter(models.User.id == uid).first()
        await manager.connect(websocket, order_id)

        try:
            while True:
                content = (await websocket.receive_text()).strip()
                if not content or len(content) > 1000:
                    continue
                msg = models.Message(order_id=order_id, sender_id=uid, content=content)
                db.add(msg)
                db.commit()
                db.refresh(msg)
                await manager.broadcast(order_id, {
                    "sender_id":   uid,
                    "sender_name": user.name,
                    "content":     content,
                    "time":        msg.created_at.strftime("%m/%d %H:%M"),
                })
        except WebSocketDisconnect:
            manager.disconnect(websocket, order_id)
    finally:
        db.close()
