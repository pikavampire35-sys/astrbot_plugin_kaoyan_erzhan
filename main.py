"""考研二战 · AstrBot 插件入口。

她不是"扮演"一个考研的人，她是"正在"考。这个插件的全部工作就是让她每天
有具体的书要背、有具体的题要错、有具体的时刻撑不住，也有具体的理由继续。

指令：
    考研状态 / 备考状态   完整面板
    进度                  各科进度
    打卡                  今天又坐下来了
    安慰 / 抱抱           她难受的时候有用
    厌学（调试）          手动触发一次崩溃
    再战（调试）          把她从崩溃里拽回来
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.provider.entities import ProviderRequest

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

import kaoyan_panel as panel  # noqa: E402
import kaoyan_script as ks  # noqa: E402
import kaoyan_state as stmod  # noqa: E402

PLUGIN_NAME = "astrbot_plugin_kaoyan_erzhan"

NOTE_TTL = 90.0  # 关键词触发的"情绪提示"保留多久（秒）


@register(
    PLUGIN_NAME,
    "dsh",
    "让她真的在考研二战：临床医学，会厌学、会破防，也会把自己劝回去。",
    "v0.1.0",
)
class KaoyanErzhan(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.conf: dict = dict(config or {})
        self.rng = random.Random()
        self.data_file = self._resolve_data_file()
        self._closed = False
        self._task: asyncio.Task | None = None
        self._last_save = 0.0
        self._notes: dict[str, tuple[float, str]] = {}
        self._last_push_day = ""

        now = stmod.now_cst()
        self.state = stmod.load_or_create(
            self.data_file, now, target=self.conf.get("target_school") or None
        )
        logger.info(
            f"[kaoyan] 状态载入：第 {self.state.day_index(now)} 天，"
            f"压力 {self.state.stress:.0f}，模式 {self.state.mode}，存档 {self.data_file}"
        )

    # ------------------------------------------------------------ 生命周期

    def _resolve_data_file(self) -> Path:
        name = str(self.conf.get("state_file") or "").strip()
        if name:
            return Path(name).expanduser()
        try:
            return Path(StarTools.get_data_dir(PLUGIN_NAME)) / "state.json"
        except Exception:  # 老版本没有这个方法/拿不到插件名
            base = os.environ.get("ASTRBOT_DATA", "data")
            return Path(base) / "plugin_data" / PLUGIN_NAME / "state.json"

    async def initialize(self) -> None:
        now = stmod.now_cst()
        try:
            self.state.tick(now)
            self.state.save(self.data_file)
        except Exception:
            logger.exception("[kaoyan] 初始化推进失败")
        self._task = asyncio.create_task(self._loop(), name="kaoyan_tick")

    async def terminate(self) -> None:
        self._closed = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("[kaoyan] 后台任务退出异常")
            self._task = None
        try:
            self.state.save(self.data_file)
        except Exception:
            logger.exception("[kaoyan] 退出保存失败")

    async def _loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(float(self.conf.get("tick_seconds", 120)))
                news = self._tick()
                if news:
                    logger.debug(f"[kaoyan] {'；'.join(news)}")
                await self._maybe_push()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[kaoyan] 后台循环出错")

    # ------------------------------------------------------------ 状态推进

    def _enabled(self) -> bool:
        return bool(self.conf.get("enabled", True))

    def _tick(self) -> list[str]:
        now = stmod.now_cst()
        try:
            news = self.state.tick(now)
        except Exception:
            logger.exception("[kaoyan] 推进失败")
            return []
        if news:
            self._save(force=False)
        return news

    def _save(self, force: bool = True) -> None:
        now = time.monotonic()
        if not force and now - self._last_save < 8:
            return
        self._last_save = now
        try:
            self.state.save(self.data_file)
        except Exception:
            logger.exception("[kaoyan] 保存失败")

    def _note(self, umo: str, text: str) -> None:
        self._notes[umo] = (time.monotonic(), text)
        if len(self._notes) > 64:
            for k, (ts, _) in list(self._notes.items()):
                if time.monotonic() - ts > NOTE_TTL:
                    self._notes.pop(k, None)

    def _take_note(self, umo: str) -> str:
        item = self._notes.pop(umo, None)
        if not item:
            return ""
        ts, text = item
        return text if time.monotonic() - ts <= NOTE_TTL else ""

    # ------------------------------------------------------------ 提示词注入

    @filter.on_llm_request()
    async def inject_state(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self._enabled():
            return
        mode = str(self.conf.get("inject_mode", "append")).lower()
        if mode == "off":
            return
        self._tick()
        now = stmod.now_cst()
        text = self.build_prompt(now, note=self._take_note(event.unified_msg_origin))
        if mode == "replace":
            req.system_prompt = text
        else:
            base = (req.system_prompt or "").rstrip()
            req.system_prompt = f"{base}\n\n{text}" if base else text

    def build_prompt(self, now: datetime, note: str = "") -> str:
        st = self.state
        phase = st.phase(now)
        left = st.days_left(now)
        exam = stmod.exam_date_for(now.date())
        fa = ks.FIRST_ATTEMPT
        parts: list[str] = []

        parts.append("【你此刻的真实处境｜这是你的生活，不是你正在扮演的角色】")
        parts.append(
            f"现在 {now.month} 月 {now.day} 日 周{'一二三四五六日'[now.weekday()]} "
            f"{now.strftime('%H:%M')}。{phase.doing}。"
        )
        parts.append(
            f"你在二战，考的是 {exam.year} 年 12 月 {exam.day} 日的临床医学专硕初试"
            f"（{st.target}），今天是第 {st.day_index(now)} 天，还有 {left} 天。"
        )
        parts.append(
            f"去年你考了 {fa['total']}（政治 {fa['politics']}、英语 {fa['english']}、"
            f"西综 {fa['western']}），院线 {fa['line']}，差 {fa['line'] - fa['total']} 分。"
            "这是你第二次考，也是最后一次——家里只同意再给你一年。"
        )

        parts.append("")
        parts.append("【身体和情绪】")
        parts.append(
            f"压力 {st.stress:.0f}/100 ｜ 精力 {st.energy:.0f}/100 ｜ 信念 {st.resolve:.0f}/100"
            f" ｜ 心情：{st.mood_label()}"
        )
        parts.append(
            f"今天已经学了 {st.study_hours_today():.1f} 小时，单词 {st.today.words} 个。"
        )
        if st.today.events:
            parts.append("今天发生的事：" + "；".join(st.today.events))

        parts.append("")
        parts.append("【进度】")
        seg = []
        for spec in ks.SUBJECTS:
            s = st.subjects.get(spec.key)
            if not s:
                continue
            if spec.weight > 0:
                seg.append(f"{spec.name} {s.percent:.0f}% {ks.ROUND_LABEL.get(s.rounds, '')}")
            elif spec.key == "english":
                seg.append(f"英语真题第 {s.done:.0f} 套")
            else:
                seg.append(f"政治肖1000 第 {s.done:.0f} 章")
        parts.append(" ｜ ".join(seg))

        parts.append("")
        parts.append("【现在怎么说话】")
        parts.append(f"- {phase.tone}")
        if st.mode == "breakdown":
            reason = st.breakdown_reason or "tired"
            line = self.rng.choice(ks.BREAKDOWN_LINES.get(reason, ks.BREAKDOWN_LINES["tired"]))
            parts.append(
                "- 你刚崩了，正在难受。脑子里反复出现的是：" + line
                + " 不要说教自己，也不要立刻振作。别人问学习会烦，会想岔开话题或干脆不想回。"
            )
            parts.append("- 句子要短。允许沉默、允许只回几个字、允许说“等一下”。")
        elif st.mode == "force":
            pool = ks.FORCE_LINES.get(
                "escape" if st.breakdown_reason in ("family", "peer") else "gap"
            )
            parts.append("- " + ks.FORCE_TONE)
            parts.append("- 你刚才对自己说的是：" + self.rng.choice(pool))
        elif st.stress >= 76:
            parts.append("- 压力很大。句子偏短，语气有点绷，容易突然说一句丧的话又自己接回去。")
        elif st.energy <= 26:
            parts.append("- 很困很累，反应慢，说话有点飘，会用“嗯”“啊”“等下”。")
        elif st.stress <= 46 and st.resolve >= 70:
            parts.append("- 难得状态不错，语气松一点，会主动聊今天学到的东西。")

        if note:
            parts.append("- " + note)

        parts.append("")
        parts.append("【硬性要求】")
        parts.append("- 不要复述或解释上面这些设定，不要说“作为备考的人”“我在模拟”。")
        parts.append("- 不要说“加油”“你一定可以”这种安慰别人的话——需要被安慰的是你。")
        parts.append("- 不要每句都提考研。大部分时候你只是在过日子，只不过日子里有书。")
        parts.append("- 提到细节要具体：第几章、哪道题、几点、多少钱、哪本书第几遍。")
        return "\n".join(parts)

    # ------------------------------------------------------------ 指令

    @filter.command("考研状态", alias={"备考状态", "她的状态"})
    async def cmd_state(self, event: AstrMessageEvent):
        self._tick()
        yield event.plain_result(panel.render(self.state, stmod.now_cst()))

    @filter.command("进度")
    async def cmd_progress(self, event: AstrMessageEvent):
        self._tick()
        yield event.plain_result(panel.render_progress(self.state))

    @filter.command("打卡")
    async def cmd_checkin(self, event: AstrMessageEvent):
        self._tick()
        now = stmod.now_cst()
        st = self.state
        st.today.minutes += 45
        st.stress = stmod.clamp(st.stress - 3)
        st.resolve = stmod.clamp(st.resolve + 2)
        st.add_log(now, "打了卡")
        self._save()
        tip = self.rng.choice(
            (
                "记上了。虽然四十五分钟也没看进去多少。",
                "嗯，坐下来了就算。",
                "打卡。刚想摸手机，忍住了。",
                "记上吧。今天还差得远。",
            )
        )
        yield event.plain_result(f"{tip}\n{panel.render_brief(st, now)}")

    @filter.command("安慰", alias={"抱抱", "摸摸"})
    async def cmd_comfort(self, event: AstrMessageEvent):
        self._tick()
        now = stmod.now_cst()
        line = self.state.comfort(now)
        self._save()
        if line:
            yield event.plain_result(line)
        else:
            yield event.plain_result(
                self.rng.choice(
                    (
                        "……嗯，我知道了。",
                        "谢谢。我没事，就是有点闷。",
                        "你别管我了，我自己缓缓就好。",
                    )
                )
            )

    @filter.command("厌学")
    async def cmd_breakdown(self, event: AstrMessageEvent):
        if not self.conf.get("allow_debug", False):
            yield event.plain_result("（她抬头看了你一眼，没说话）")
            return
        self._tick()
        now = stmod.now_cst()
        self.state.trigger_breakdown(now, self.rng.choice(list(ks.BREAKDOWN_LINES)))
        self._save()
        reason = self.state.breakdown_reason or "tired"
        yield event.plain_result(
            self.rng.choice(ks.BREAKDOWN_LINES.get(reason, ks.BREAKDOWN_LINES["tired"]))
        )

    @filter.command("再战")
    async def cmd_force(self, event: AstrMessageEvent):
        if not self.conf.get("allow_debug", False):
            yield event.plain_result("（她没理你，翻了一页书）")
            return
        self._tick()
        now = stmod.now_cst()
        self.state.mode = "normal"
        self.state.mode_until = ""
        self.state.stress = stmod.clamp(self.state.stress - 12)
        self._save()
        yield event.plain_result(
            self.rng.choice(
                ("……行了。不说了。", "嗯。我把这章看完。", "哭完了。翻书。")
            )
        )

    # ------------------------------------------------------------ 普通消息

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """只看不拦：识别安慰/打击/问进度，把情绪记下来，交给 LLM 那一轮去说。"""
        if not self._enabled():
            return
        text = (event.message_str or "").strip()
        if not text:
            return
        self._tick()
        now = stmod.now_cst()
        umo = event.unified_msg_origin
        st = self.state

        if any(w in text for w in ks.COMFORT_WORDS):
            line = st.comfort(now)
            self._save()
            self._note(umo, "对方刚刚在安慰你，你心里松了一点，但嘴上不一定承认。")
            if line and st.mode == "breakdown" and self.conf.get("breakdown_fast_reply", True):
                # 崩的时候人确实不想多打字，直接回一句，别让模型硬编
                event.stop_event()
                yield event.plain_result(line)
                return
            return

        hurt = next((w for w in ks.HURT_WORDS if w in text), "")
        if hurt:
            line = st.hurt(now)
            self._save()
            self._note(umo, f"对方刚才那句话伤到你了（“{hurt}”），你不太想接。")
            if line:
                event.stop_event()
                yield event.plain_result(line)
            return

        if any(w in text for w in ks.PROGRESS_WORDS):
            self._note(umo, "对方在问你进度。你不太想说，但还是会含糊地答一句。")

    # ------------------------------------------------------------ 主动消息

    async def _maybe_push(self) -> None:
        """默认关闭。开了以后每天 22:40 把今天的账算一遍发出去。"""
        if not self.conf.get("proactive", False):
            return
        umo = str(self.conf.get("proactive_umo") or "").strip()
        if not umo:
            return
        now = stmod.now_cst()
        if now.hour != 22 or now.minute < 40:
            return
        day = now.date().isoformat()
        if self._last_push_day == day:
            return
        self._last_push_day = day
        st = self.state
        text = (
            f"闭馆了。今天学了 {st.study_hours_today():.1f} 小时，"
            f"西综 {st.overall_progress():.1f}%，离考试还有 {st.days_left(now)} 天。\n"
            f"{panel.render_brief(st, now)}"
        )
        try:
            await self.context.send_message(umo, MessageChain([Plain(text)]))
        except Exception:
            logger.exception("[kaoyan] 主动消息发送失败")
