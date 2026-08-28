"""请求/响应模型 —— 与 Java 版 DTO/VO 字段一一对应（保持驼峰，前端零改动）。"""

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PageRequest(BaseModel):
    pageNum: int = 1
    pageSize: int = 10
    sortField: str | None = None
    sortOrder: str = "descend"


class Page(BaseModel, Generic[T]):
    """MyBatis-Flex 的 Page 结构，前端按这几个字段渲染分页。"""

    records: list[T] = Field(default_factory=list)
    pageNumber: int
    pageSize: int
    totalRow: int
    totalPage: int


class DeleteRequest(BaseModel):
    id: int


# ---- 用户 ----


class UserRegisterRequest(BaseModel):
    userAccount: str
    userPassword: str
    checkPassword: str


class UserLoginRequest(BaseModel):
    userAccount: str
    userPassword: str


class UserAddRequest(BaseModel):
    userName: str | None = None
    userAccount: str
    userAvatar: str | None = None
    userProfile: str | None = None
    userRole: str = "user"


class UserUpdateRequest(BaseModel):
    id: int
    userName: str | None = None
    userAvatar: str | None = None
    userProfile: str | None = None
    userRole: str | None = None


class UserQueryRequest(PageRequest):
    id: int | None = None
    userName: str | None = None
    userAccount: str | None = None
    userProfile: str | None = None
    userRole: str | None = None


class UserVO(BaseModel):
    """对外字段是驼峰（前端约定），ORM 属性是蛇形。

    两边靠 `validation_alias` 搭桥：`from_attributes` 会按别名去 ORM 对象上取属性，
    `populate_by_name` 保证仍能用驼峰名直接构造。少了任何一个，
    `model_validate(orm_obj)` 都只会填上 id、其余字段静默变 null。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    userAccount: str | None = Field(default=None, validation_alias="user_account")
    userName: str | None = Field(default=None, validation_alias="user_name")
    userAvatar: str | None = Field(default=None, validation_alias="user_avatar")
    userProfile: str | None = Field(default=None, validation_alias="user_profile")
    userRole: str | None = Field(default=None, validation_alias="user_role")
    createTime: datetime | None = Field(default=None, validation_alias="create_time")


class LoginUserVO(UserVO):
    updateTime: datetime | None = Field(default=None, validation_alias="update_time")


# ---- 应用 ----


class AppAddRequest(BaseModel):
    initPrompt: str


class AppUpdateRequest(BaseModel):
    id: int
    appName: str | None = None


class AppAdminUpdateRequest(BaseModel):
    id: int
    appName: str | None = None
    cover: str | None = None
    priority: int | None = None


class AppDeployRequest(BaseModel):
    appId: int


class AppQueryRequest(PageRequest):
    id: int | None = None
    appName: str | None = None
    cover: str | None = None
    initPrompt: str | None = None
    codeGenType: str | None = None
    deployKey: str | None = None
    priority: int | None = None
    userId: int | None = None


class AppVO(BaseModel):
    id: int
    appName: str | None = None
    cover: str | None = None
    initPrompt: str | None = None
    codeGenType: str | None = None
    deployKey: str | None = None
    deployedTime: datetime | None = None
    priority: int | None = None
    userId: int | None = None
    createTime: datetime | None = None
    updateTime: datetime | None = None
    user: UserVO | None = None


# ---- 对话历史 ----


class ChatHistoryVO(BaseModel):
    # 同 UserVO：驼峰对外、蛇形对内，靠 validation_alias 搭桥
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    message: str
    messageType: str = Field(validation_alias="message_type")
    appId: int = Field(validation_alias="app_id")
    userId: int = Field(validation_alias="user_id")
    createTime: datetime | None = Field(default=None, validation_alias="create_time")
    updateTime: datetime | None = Field(default=None, validation_alias="update_time")


class ChatHistoryQueryRequest(PageRequest):
    id: int | None = None
    message: str | None = None
    messageType: str | None = None
    appId: int | None = None
    userId: int | None = None
    lastCreateTime: datetime | None = None
