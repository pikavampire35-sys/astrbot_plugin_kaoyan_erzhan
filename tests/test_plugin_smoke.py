"""用桩替换 AstrBot，验证插件自身的接线：装饰器能不能注册、提示词拼得对不对。

真正加载进 AstrBot 的验收还得在实例上做，这个测试只保证"不会因为拼错名字而
在 import 阶段就炸"。
"""

from __future__ import annotations

import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

import kaoyan_script as ks
import kaoyan_state as stmod


def _install_stubs(tmp: Path) -> None:
    def mod(name: str, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    class _Logger:
        def _noop(self, *a, **k):
            return None

        info = debug = warning = error = exception = _noop

    class _Star:
        def __init__(self, context=None, config=None):
            self.context = context
            self.config = config

    class _StarTools:
        @classmethod
        def get_data_dir(cls, plugin_name=None):
            return tmp

    class _EventMessageType:
        ALL = "all"
        GROUP_MESSAGE = "group"
        PRIVATE_MESSAGE = "private"
        OTHER_MESSAGE = "other"

    class _Filter:
        EventMessageType = _EventMessageType

        @staticmethod
        def command(*a, **k):
            return lambda fn: fn

        @staticmethod
        def regex(*a, **k):
            return lambda fn: fn

        @staticmethod
        def on_llm_request(*a, **k):
            return lambda fn: fn

        @staticmethod
        def event_message_type(*a, **k):
            return lambda fn: fn

    mod("astrbot")
    mod("astrbot.api", logger=_Logger())
    mod("astrbot.api.event", AstrMessageEvent=object, MessageChain=lambda parts: parts, filter=_Filter)
    mod("astrbot.api.message_components", Plain=lambda text: ("plain", text))
    mod(
        "astrbot.api.star",
        Context=object,
        Star=_Star,
        StarTools=_StarTools,
        register=lambda *a, **k: (lambda cls: cls),
    )
    mod("astrbot.core")
    mod("astrbot.core.provider")
    mod("astrbot.core.provider.entities", ProviderRequest=object)


@pytest.fixture(scope="module")
def plugin(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("kaoyan")
    _install_stubs(tmp)
    for name in ("main", "kaoyan_panel", "kaoyan_state", "kaoyan_script"):
        sys.modules.pop(name, None)
    import main as plugin_main

    return plugin_main.KaoyanErzhan(
        context=None,
        config={"target_school": "测试医科大学 临床医学专硕", "tick_seconds": 1},
    )


def test_plugin_registers_and_loads(plugin):
    assert plugin.state is not None
    assert plugin.state.target == "测试医科大学 临床医学专硕"
    assert set(plugin.state.subjects) == {s.key for s in ks.SUBJECTS}


def test_build_prompt_contains_required_context(plugin):
    now = datetime(2026, 11, 16, 14, 30, tzinfo=stmod.CST)
    text = plugin.build_prompt(now)

    assert "临床医学专硕" in text
    assert "第" in text and "天" in text
    assert "压力" in text and "精力" in text and "信念" in text
    assert "生理" in text and "内科" in text
    # 硬性约束必须在场，否则模型容易开始"解说设定"
    assert "不要复述" in text
    assert "现在怎么说话" in text


def test_build_prompt_reflects_breakdown(plugin):
    now = datetime(2026, 11, 16, 17, 45, tzinfo=stmod.CST)
    plugin.state.trigger_breakdown(now, "family")
    text = plugin.build_prompt(now)
    assert "刚崩了" in text
    assert "短" in text
    plugin.state.mode = "normal"


def test_panel_renders_all_subjects(plugin):
    import kaoyan_panel as panel

    now = datetime(2026, 11, 16, 14, 30, tzinfo=stmod.CST)
    out = panel.render(plugin.state, now)
    for spec in ks.SUBJECTS:
        assert spec.name in out
    assert "西医综合" in out
    assert "%" in out


def test_tick_and_save_roundtrip(plugin):
    now = datetime(2026, 11, 16, 15, 0, tzinfo=stmod.CST)
    plugin._tick()
    plugin._save()
    assert plugin.data_file.exists()
    reloaded = stmod.KaoyanState.load(plugin.data_file)
    assert reloaded.day_index(now) == plugin.state.day_index(now)
