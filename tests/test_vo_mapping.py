"""VO 与 ORM 之间的驼峰/蛇形映射。

这是一类很安静的 bug：`model_validate(orm_obj)` 不会报错，只会把对不上的字段
静默填成 None。前端拿到 `userName: null` 时，看起来像「数据没存进去」，
排查方向会完全跑偏。所以必须有测试锁住每个字段。
"""

from datetime import datetime

from app.api.schemas import AppVO, ChatHistoryVO, LoginUserVO, UserVO
from app.db.models import App, ChatHistory, User


def _user() -> User:
    return User(
        id=1,
        user_account="alice",
        user_password="hashed",
        user_name="爱丽丝",
        user_avatar="http://x/a.png",
        user_profile="简介",
        user_role="admin",
        create_time=datetime(2026, 1, 1),
        update_time=datetime(2026, 2, 2),
        is_delete=0,
    )


def test_uservo_全字段映射且不泄露密码():
    vo = UserVO.model_validate(_user())
    assert vo.id == 1
    assert vo.userAccount == "alice"
    assert vo.userName == "爱丽丝"
    assert vo.userAvatar == "http://x/a.png"
    assert vo.userProfile == "简介"
    assert vo.userRole == "admin"
    assert vo.createTime == datetime(2026, 1, 1)
    # VO 是脱敏出口，密码字段压根不该存在
    assert "userPassword" not in vo.model_dump()


def test_loginuservo_额外带更新时间():
    vo = LoginUserVO.model_validate(_user())
    assert vo.userAccount == "alice"
    assert vo.updateTime == datetime(2026, 2, 2)


def test_chathistoryvo_全字段映射():
    row = ChatHistory(
        id=7,
        message="你好",
        message_type="user",
        app_id=42,
        user_id=1,
        create_time=datetime(2026, 3, 3),
        update_time=datetime(2026, 3, 4),
        is_delete=0,
    )
    vo = ChatHistoryVO.model_validate(row)
    assert (vo.id, vo.message, vo.messageType, vo.appId, vo.userId) == (7, "你好", "user", 42, 1)
    assert vo.createTime == datetime(2026, 3, 3)


def test_vo_仍可用驼峰名直接构造():
    """populate_by_name 保证 app_service 里手工构造 VO 的写法不被别名破坏。"""
    vo = UserVO(id=1, userAccount="bob", userName="鲍勃")
    assert vo.userAccount == "bob"
    assert vo.userName == "鲍勃"


def test_appvo_手工构造字段齐全():
    app = App(
        id=9,
        app_name="我的应用",
        init_prompt="做个页面",
        code_gen_type="vue_project",
        user_id=1,
        priority=99,
        is_delete=0,
    )
    vo = AppVO(
        id=app.id,
        appName=app.app_name,
        initPrompt=app.init_prompt,
        codeGenType=app.code_gen_type,
        userId=app.user_id,
        priority=app.priority,
    )
    assert vo.appName == "我的应用"
    assert vo.codeGenType == "vue_project"
    assert vo.priority == 99
