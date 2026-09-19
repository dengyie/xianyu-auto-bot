"""生产 pin 的 slidex 必须携带「指纹一致性」修复。

2026-09-19 深挖结论（运维文档「滑块硬拒绝根因深挖」节）：密码登录滑块
20+ 天 400+ 次全败（阿里 punish 页 error:hwR4mj）的根因是有头浏览器指纹
自相矛盾——userAgent=池内 Chrome/119（Windows）vs navigator.platform=真实
Linux x86_64 vs userAgentData.brands=真实内核 Chromium/147 vs
plugins=数字数组。风控 JS 读三个只读属性即零成本判自动化，轨迹再准也没用。

修复落在 slidex 6b3190a（_get_headful_stealth_script + userAgentData 一致性
+ 删除数字 plugins 注入）。本文件钉死两件事：

1. 两处 pin（Dockerfile / requirements.txt）必须指向同一 commit，防止改一漏一；
2. 实际安装的 slidex 必须真的带该修复——任何人把 pin 挪到没有修复的版本
   （含上游回退），这里直接失败。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PIN_PATTERN = r"slidex @ git\+https://github\.com/dengyie/slidex\.git@([0-9a-f]+)"


def _pins():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    return (
        re.search(PIN_PATTERN, dockerfile),
        re.search(PIN_PATTERN, requirements),
    )


def test_slidex_pin_present_in_both_locations_and_identical():
    docker_pin, requirements_pin = _pins()

    assert docker_pin, "Dockerfile 缺少 slidex pin"
    assert requirements_pin, "requirements.txt 缺少 slidex pin"
    assert docker_pin.group(1) == requirements_pin.group(1), (
        f"两处 pin 不一致: {docker_pin.group(1)} vs {requirements_pin.group(1)}"
    )


@pytest.fixture(scope="module")
def slider():
    from slidex.stealth import XianyuSliderStealth

    return XianyuSliderStealth(user_id="ut-pin-consistency")


FEATURES = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    ),
    "platform": "Win32",
    "vendor": "Google Inc.",
    "locale": "zh-CN",
    "is_mobile": False,
    "viewport_width": 1920,
    "viewport_height": 1200,
}


def test_pinned_slidex_has_headful_consistency_script(slider):
    from slidex.stealth import XianyuSliderStealth

    assert hasattr(XianyuSliderStealth, "_get_headful_stealth_script"), (
        "安装的 slidex 缺少 _get_headful_stealth_script——pin 回退到了没有指纹修复的版本"
    )

    script = slider._get_headful_stealth_script(FEATURES)
    assert "Navigator.prototype, 'platform'" in script
    assert "Navigator.prototype, 'userAgentData'" in script
    assert "getHighEntropyValues" in script
    # brands 必须与 UA 池版本（119）一致，不能漏出真实内核版本
    assert '"version": "119"' in script


def test_pinned_slidex_has_no_numeric_plugins_anywhere():
    import slidex.stealth as stealth_module

    source = Path(stealth_module.__file__).read_text(encoding="utf-8")
    assert "1, 2, 3, 4, 5" not in source, (
        "安装的 slidex 仍有数字数组 plugins 注入——指纹破绽回来了"
    )
