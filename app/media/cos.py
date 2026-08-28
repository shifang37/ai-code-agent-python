"""腾讯云 COS 上传 —— 移植 manager/CosManager。

未配置 COS 时所有方法直接返回 None（调用方按「拿不到 URL」处理），
不抛异常、不阻断主流程。
"""

import asyncio
import logging
from functools import lru_cache
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache
def _client():
    if not settings.cos_enabled:
        return None
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        logger.warning("未安装 cos-python-sdk-v5（pip install '.[media]'），COS 上传不可用")
        return None
    config = CosConfig(
        Region=settings.cos_region,
        SecretId=settings.cos_secret_id,
        SecretKey=settings.cos_secret_key,
    )
    return CosS3Client(config)


async def upload_file(key: str, local_file: Path) -> str | None:
    """上传本地文件，返回可访问 URL；不可用或失败时返回 None。"""
    client = _client()
    if client is None:
        return None
    try:
        await asyncio.to_thread(
            client.upload_file,
            Bucket=settings.cos_bucket,
            Key=key.lstrip("/"),
            LocalFilePath=str(local_file),
        )
    except Exception as e:
        logger.error("COS 上传失败 key=%s: %s", key, e)
        return None
    return f"{settings.cos_host.rstrip('/')}/{key.lstrip('/')}"
