from __future__ import annotations

import asyncio

from models import PrivacyLevel
from privacy_filter import check_privacy, desensitize


def test_s1_blocks_cloud():
    result = asyncio.run(check_privacy([{"role": "user", "content": "机密：工资单总计 120 万"}]))
    assert result.level == PrivacyLevel.S1
    assert result.block_cloud is True


def test_s2_desensitizes_pii():
    text, mapping = desensitize("客户张三的身份证 320101198801011234，合同金额 1280万元")
    assert "320101198801011234" not in text
    assert "1280万元" not in text
    assert mapping


def test_s3_public_prompt():
    result = asyncio.run(check_privacy([{"role": "user", "content": "你好，解释一下 TCP"}]))
    assert result.level == PrivacyLevel.S3
    assert result.block_cloud is False

