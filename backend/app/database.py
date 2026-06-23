import json
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Text, DateTime, Float, Integer, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = "sqlite:///./layercut.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    projects = relationship("Project", back_populates="owner")


class Project(Base):
    __tablename__ = "projects"
    id = Column(String(8), primary_key=True)
    title = Column(String(200), nullable=False)
    topic = Column(Text, default="")
    match = Column(String(200), default="")
    match_date = Column(String(20), default="")
    category = Column(String(50), default="")
    tags = Column(Text, default="")
    config = Column(Text, default="{}")
    layers = Column(Text, default="{}")
    layer_info = Column(Text, default="{}")
    output = Column(String(500), default=None)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="projects")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "topic": self.topic,
            "match": self.match,
            "match_date": self.match_date,
            "category": self.category,
            "tags": json.loads(self.tags) if self.tags else [],
            "config": json.loads(self.config) if self.config else {},
            "layers": json.loads(self.layers) if self.layers else {},
            "layer_info": json.loads(self.layer_info) if self.layer_info else {},
            "output": self.output,
            "owner_id": self.owner_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TaskRecord(Base):
    __tablename__ = "tasks"
    id = Column(String(36), primary_key=True)
    project_id = Column(String(8), ForeignKey("projects.id"), nullable=False)
    task_type = Column(String(50), nullable=False)
    status = Column(String(20), default="pending")
    progress = Column(Float, default=0.0)
    result = Column(Text, default=None)
    error = Column(Text, default=None)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ShareLink(Base):
    __tablename__ = "share_links"
    id = Column(String(16), primary_key=True)
    project_id = Column(String(8), ForeignKey("projects.id"), nullable=False)
    token = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    views = Column(Integer, default=0)

    project = relationship("Project")


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(8), ForeignKey("projects.id"), nullable=False)
    platform = Column(String(20), nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String(20), default="pending")
    meta = Column(Text, default="{}")
    result = Column(Text, default=None)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project")


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
