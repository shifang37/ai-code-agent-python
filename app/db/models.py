"""SQLAlchemy 2.0 模型 —— 直接映射 Java 版已有的 MySQL 表。

列名保持驼峰（`appName`、`isDelete` …），因为要和 Java 版共用同一个库；
Python 侧属性用蛇形，通过 `mapped_column("appName")` 做映射。
`isDelete` 是逻辑删除标记，所有查询都要带 `is_delete == 0`。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_account: Mapped[str] = mapped_column("userAccount", String(256))
    user_password: Mapped[str] = mapped_column("userPassword", String(512))
    user_name: Mapped[str | None] = mapped_column("userName", String(256), nullable=True)
    user_avatar: Mapped[str | None] = mapped_column("userAvatar", String(1024), nullable=True)
    user_profile: Mapped[str | None] = mapped_column("userProfile", String(512), nullable=True)
    user_role: Mapped[str] = mapped_column("userRole", String(256), default="user")
    edit_time: Mapped[datetime | None] = mapped_column("editTime", DateTime, server_default=func.now())
    create_time: Mapped[datetime | None] = mapped_column("createTime", DateTime, server_default=func.now())
    update_time: Mapped[datetime | None] = mapped_column("updateTime", DateTime, server_default=func.now())
    is_delete: Mapped[int] = mapped_column("isDelete", Integer, default=0)


class App(Base):
    __tablename__ = "app"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    app_name: Mapped[str | None] = mapped_column("appName", String(256), nullable=True)
    cover: Mapped[str | None] = mapped_column(String(512), nullable=True)
    init_prompt: Mapped[str | None] = mapped_column("initPrompt", Text, nullable=True)
    code_gen_type: Mapped[str | None] = mapped_column("codeGenType", String(64), nullable=True)
    deploy_key: Mapped[str | None] = mapped_column("deployKey", String(64), nullable=True)
    deployed_time: Mapped[datetime | None] = mapped_column("deployedTime", DateTime, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[int] = mapped_column("userId", BigInteger)
    edit_time: Mapped[datetime | None] = mapped_column("editTime", DateTime, server_default=func.now())
    create_time: Mapped[datetime | None] = mapped_column("createTime", DateTime, server_default=func.now())
    update_time: Mapped[datetime | None] = mapped_column("updateTime", DateTime, server_default=func.now())
    is_delete: Mapped[int] = mapped_column("isDelete", Integer, default=0)


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    message: Mapped[str] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column("messageType", String(32))  # user / ai
    app_id: Mapped[int] = mapped_column("appId", BigInteger)
    user_id: Mapped[int] = mapped_column("userId", BigInteger)
    create_time: Mapped[datetime | None] = mapped_column("createTime", DateTime, server_default=func.now())
    update_time: Mapped[datetime | None] = mapped_column("updateTime", DateTime, server_default=func.now())
    is_delete: Mapped[int] = mapped_column("isDelete", Integer, default=0)


MESSAGE_TYPE_USER = "user"
MESSAGE_TYPE_AI = "ai"
