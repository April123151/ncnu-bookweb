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

models.Base.metadata.create_all(bind=database.engine)

# Migration: add line_id column if not exists
try:
    with database.engine.connect() as _conn:
        _conn.execute(text("ALTER TABLE users ADD COLUMN line_id VARCHAR(50)"))
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

    async def connect(self, ws: WebSocket, order_id: int):
        await ws.accept()
        self._rooms[order_id].append(ws)

    def disconnect(self, ws: WebSocket, order_id: int):
        try:
            self._rooms[order_id].remove(ws)
        except ValueError:
            pass

    async def broadcast(self, order_id: int, data: dict):
        dead = []
        for ws in list(self._rooms[order_id]):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, order_id)

    def room_size(self, order_id: int) -> int:
        return len(self._rooms[order_id])


manager = ConnectionManager()


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


def save_upload(upload: UploadFile) -> Optional[str]:
    """Upload to Cloudinary; returns secure URL or None."""
    if not upload or not upload.filename or not allowed_file(upload.filename):
        return None
    result = cloudinary.uploader.upload(
        upload.file,
        folder="bookweb",
        resource_type="image",
    )
    return result["secure_url"]


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

    for upload in photos:
        fname = save_upload(upload)
        if fname:
            db.add(models.BookPhoto(book_id=book.id, filename=fname))

    for d, t, l in zip(slot_date, slot_time, slot_location):
        if d.strip() and t.strip() and l.strip():
            db.add(models.TimeSlot(book_id=book.id, date=d.strip(),
                                   time_str=t.strip(), location=l.strip()))
    db.commit()
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
    return render("edit_book.html", request, db, {"book": book})


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
    return render("chat.html", request, db, {
        "order": order,
        "other_user": other_user,
        "is_buyer": is_buyer,
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
