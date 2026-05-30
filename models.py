from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime

CONDITION_MAP = {
    'new':        '全新',
    'like_new':   '近全新',
    'good':       '良好',
    'fair':       '普通',
    'acceptable': '堪用',
}

CONDITIONS = list(CONDITION_MAP.items())

DEPARTMENT_GROUPS = [
    ('教育學院', [
        '教育政策與行政學系',
        '輔導與諮商研究所',
        '終身學習與人力資源發展碩士班',
    ]),
    ('人文學院', [
        '中國語文學系',
        '歷史學系',
        '東南亞學系',
    ]),
    ('管理學院', [
        '國際企業學系',
        '財務金融學系',
        '資訊管理學系',
        '經濟學系',
        '社會政策與社會工作學系',
    ]),
    ('理工學院', [
        '資訊工程學系',
        '土木工程學系',
        '應用化學系',
        '電機工程學系',
    ]),
    ('其他', [
        '通識課程',
        '其他',
    ]),
]

DEPARTMENTS = [dept for _, depts in DEPARTMENT_GROUPS for dept in depts]


class User(Base):
    __tablename__ = 'users'

    id            = Column(Integer, primary_key=True, index=True)
    student_id    = Column(String(20), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    name          = Column(String(50), nullable=False)
    department    = Column(String(100), nullable=False)
    phone         = Column(String(20))
    line_id       = Column(String(50))
    created_at    = Column(DateTime, default=datetime.utcnow)

    books  = relationship('Book',  back_populates='seller', foreign_keys='Book.seller_id')
    orders = relationship('Order', back_populates='buyer',  foreign_keys='Order.buyer_id')


class Book(Base):
    __tablename__ = 'books'

    id          = Column(Integer, primary_key=True, index=True)
    title       = Column(String(200), nullable=False)
    author      = Column(String(100))
    isbn        = Column(String(20))
    price       = Column(Integer, nullable=False)
    condition   = Column(String(20), nullable=False)
    department  = Column(String(100), nullable=False)
    description = Column(Text)
    seller_id   = Column(Integer, ForeignKey('users.id'), nullable=False)
    status      = Column(String(20), default='available')  # available / locked / sold
    created_at  = Column(DateTime, default=datetime.utcnow)

    seller        = relationship('User',         back_populates='books',         foreign_keys=[seller_id])
    photos        = relationship('BookPhoto',    back_populates='book',          cascade='all, delete-orphan')
    timeslots     = relationship('TimeSlot',     back_populates='book',          cascade='all, delete-orphan')
    orders        = relationship('Order',        back_populates='book')
    conversations = relationship('Conversation', back_populates='book',          cascade='all, delete-orphan')

    @property
    def condition_label(self):
        return CONDITION_MAP.get(self.condition, self.condition)

    @property
    def status_label(self):
        return {'available': '上架中', 'locked': '交易中', 'sold': '已售出'}.get(self.status, self.status)

    @property
    def first_photo(self):
        return self.photos[0].filename if self.photos else None

    @property
    def active_order(self):
        return next((o for o in self.orders if o.status == 'pending'), None)


class BookPhoto(Base):
    __tablename__ = 'book_photos'

    id       = Column(Integer, primary_key=True)
    book_id  = Column(Integer, ForeignKey('books.id'), nullable=False)
    filename = Column(String(200), nullable=False)

    book = relationship('Book', back_populates='photos')


class TimeSlot(Base):
    __tablename__ = 'time_slots'

    id       = Column(Integer, primary_key=True)
    book_id  = Column(Integer, ForeignKey('books.id'), nullable=False)
    date     = Column(String(50))
    time_str = Column(String(50))
    location = Column(String(200))

    book = relationship('Book', back_populates='timeslots')

    @property
    def display(self):
        return f"{self.date}  {self.time_str}  @  {self.location}"


class Order(Base):
    __tablename__ = 'orders'

    id               = Column(Integer, primary_key=True)
    book_id          = Column(Integer, ForeignKey('books.id'),    nullable=False)
    buyer_id         = Column(Integer, ForeignKey('users.id'),    nullable=False)
    timeslot_id      = Column(Integer, ForeignKey('time_slots.id'))
    status           = Column(String(20), default='pending')
    created_at       = Column(DateTime, default=datetime.utcnow)
    buyer_last_read  = Column(DateTime, nullable=True)
    seller_last_read = Column(DateTime, nullable=True)

    book     = relationship('Book',     back_populates='orders')
    buyer    = relationship('User',     back_populates='orders', foreign_keys=[buyer_id])
    timeslot = relationship('TimeSlot')
    messages = relationship('Message',  back_populates='order',
                            cascade='all, delete-orphan',
                            order_by='Message.created_at')


class Message(Base):
    __tablename__ = 'messages'

    id         = Column(Integer, primary_key=True)
    order_id   = Column(Integer, ForeignKey('orders.id'), nullable=False)
    sender_id  = Column(Integer, ForeignKey('users.id'),  nullable=False)
    content    = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    order  = relationship('Order', back_populates='messages')
    sender = relationship('User',  foreign_keys=[sender_id])


class Conversation(Base):
    __tablename__ = 'conversations'
    __table_args__ = (UniqueConstraint('book_id', 'buyer_id', name='uq_conv_book_buyer'),)

    id               = Column(Integer, primary_key=True)
    book_id          = Column(Integer, ForeignKey('books.id'), nullable=False)
    buyer_id         = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at       = Column(DateTime, default=datetime.utcnow)
    buyer_last_read  = Column(DateTime, nullable=True)
    seller_last_read = Column(DateTime, nullable=True)

    book     = relationship('Book', back_populates='conversations')
    buyer    = relationship('User', foreign_keys=[buyer_id])
    messages = relationship('ConvMessage', back_populates='conv',
                            cascade='all, delete-orphan',
                            order_by='ConvMessage.created_at')


class ConvMessage(Base):
    __tablename__ = 'conv_messages'

    id         = Column(Integer, primary_key=True)
    conv_id    = Column(Integer, ForeignKey('conversations.id'), nullable=False)
    sender_id  = Column(Integer, ForeignKey('users.id'),         nullable=False)
    content    = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    conv   = relationship('Conversation', back_populates='messages')
    sender = relationship('User', foreign_keys=[sender_id])
