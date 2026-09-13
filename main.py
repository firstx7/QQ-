# =========================================================
# astrbot_plugin_rpg
# main.py
# RPG 游戏系统主程序
# =========================================================

import os
import asyncio
import re
import sys
import random
from datetime import datetime, timedelta

# =========================================================
# 插件目录
# =========================================================

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

# =========================================================
# AstrBot
# =========================================================

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api.web import json_response, error_response, request
import astrbot.api.message_components as Comp

# =========================================================
# 数据库
# =========================================================

from database.database import RPGDatabase

# =========================================================
# 菜单
# =========================================================

from qq_menu import (
    main_menu,
    game_menu,
    economy_menu,
    shop_menu,
    battle_menu,
    inventory_menu,
    profile_menu,
    help_menu,
    rank_menu,
)


class RPGPlugin(Star):

    def __init__(self, context: Context):

        super().__init__(context)

        self.db = RPGDatabase()
        self._admin_pending = {}
        self._admin_password = os.getenv("RPG_ADMIN_PASSWORD", "12345678")
        self._lottery_task = None
        try:
            self._register_web_api()
        except Exception as e:
            logger.error(f"RPG Web 管理面板注册失败：{e}")
        try:
            self._lottery_task = asyncio.create_task(self._lottery_loop())
        except Exception:
            self._lottery_task = None

        logger.info("================================")
        logger.info("RPG 游戏插件初始化完成")
        logger.info(f"RPG 数据库：{self.db.db_path}")
        logger.info("================================")

    # =====================================================
    # Web 管理面板
    # =====================================================
    def _register_web_api(self):
        prefix = "/astrbot_plugin_rpg"
        self.context.register_web_api(f"{prefix}/admin/stats", self.web_stats, ["GET"], "RPG 管理统计")
        self.context.register_web_api(f"{prefix}/admin/items", self.web_items, ["GET"], "RPG 商城物品")
        self.context.register_web_api(f"{prefix}/admin/codes", self.web_codes, ["GET"], "RPG 兑换码记录")
        self.context.register_web_api(f"{prefix}/admin/code", self.web_create_code, ["POST"], "RPG 生成兑换码")
        self.context.register_web_api(f"{prefix}/admin/bundle", self.web_create_bundle, ["POST"], "RPG 生成多奖励礼包兑换码")
        self.context.register_web_api(f"{prefix}/admin/lottery", self.web_lottery, ["GET"], "RPG 彩票奖池")
        self.context.register_web_api(f"{prefix}/admin/lottery/pool", self.web_set_lottery_pool, ["POST"], "RPG 调整彩票奖池")
        self.context.register_web_api(f"{prefix}/admin/bank", self.web_bank, ["GET"], "RPG 银行设置")
        self.context.register_web_api(f"{prefix}/admin/bank/save", self.web_set_bank, ["POST"], "RPG 保存银行利率")
        self.context.register_web_api(f"{prefix}/admin/lottery/save", self.web_set_lottery_pool, ["POST"], "RPG 保存彩票奖池")

    async def web_stats(self):
        data = self.db.admin_code_stats()
        try:
            data["lottery_pool"] = self.db.get_lottery_pool()
        except Exception:
            data["lottery_pool"] = 5000
        return json_response(data)

    async def web_lottery(self):
        try:
            return json_response({
                "pool": self.db.get_lottery_pool(),
                "base_pool": int(self.db.get_system_value("lottery_base_pool", "5000") or 5000),
                "last_draw": self.db.get_system_value("lottery_last_draw", ""),
                "last_reset": self.db.get_system_value("lottery_last_reset", ""),
            })
        except Exception as e:
            logger.error(f"RPG Web 彩票奖池读取失败：{e}")
            return error_response(f"读取失败：{e}", status_code=500)

    async def web_set_lottery_pool(self):
        try:
            payload = await request.json(default={})
            if "pool" not in payload:
                return error_response("缺少 pool", status_code=400)
            pool = int(payload.get("pool"))
            if pool < 0 or pool > 2147483647:
                return error_response("奖池范围：0-2147483647", status_code=400)
            self.db.set_system_value("lottery_pool", str(pool))
            if "base_pool" in payload:
                base = int(payload.get("base_pool"))
                if base < 0 or base > 2147483647:
                    return error_response("基础奖池范围：0-2147483647", status_code=400)
                self.db.set_system_value("lottery_base_pool", str(base))
            return json_response({
                "saved": True,
                "pool": self.db.get_lottery_pool(),
                "base_pool": int(self.db.get_system_value("lottery_base_pool", "5000") or 5000),
            })
        except Exception as e:
            logger.error(f"RPG Web 彩票奖池修改失败：{e}")
            return error_response(f"修改失败：{e}", status_code=500)

    async def web_bank(self):
        try:
            rate = float(self.db.get_system_value("bank_interest_rate", "0.005") or 0.005)
            return json_response({"rate": rate, "rate_percent": round(rate * 100, 4), "description": "每日复利银行利息"})
        except Exception as e:
            return error_response(f"读取银行设置失败：{e}", status_code=500)

    async def web_set_bank(self):
        try:
            payload = await request.json(default={})
            rate = float(payload.get("rate"))
            if rate < 0 or rate > 0.1:
                return error_response("银行利率范围：0-10%/天", status_code=400)
            self.db.set_system_value("bank_interest_rate", str(rate))
            return json_response({"saved": True, "rate": rate, "rate_percent": round(rate * 100, 4)})
        except Exception as e:
            return error_response(f"保存银行设置失败：{e}", status_code=500)

    async def web_items(self):
        return json_response([dict(x) for x in self.db.get_shop_items()])

    async def web_codes(self):
        return json_response(self.db.admin_codes(100))

    async def web_create_code(self):
        try:
            payload = await request.json(default={})
            kind = str(payload.get("type", "item")).lower()
            amount = int(payload.get("amount", 1))
            if amount <= 0 or amount > 999999999:
                return error_response("数量范围：1-999999999", status_code=400)
            max_uses = int(payload.get("max_uses", 1))
            expires_at = payload.get("expires_at") or None
            if max_uses < 0:
                return error_response("使用次数不能小于0，0表示无限", status_code=400)
            if kind == "currency":
                ok, result = self.db.create_currency_redemption_code(str(payload.get("currency", "money")), amount, max_uses, expires_at)
                if not ok: return error_response(result, status_code=400)
                return json_response({"code": result, "reward": str(payload.get("currency", "money")), "amount": amount, "max_uses": max_uses, "expires_at": expires_at})
            item_id = str(payload.get("item_id", ""))
            ok, result = self.db.create_redemption_code(item_id, amount, max_uses, expires_at)
            if not ok: return error_response(result, status_code=400)
            item = self.db.get_shop_item(item_id)
            return json_response({"code": result, "reward": item["item_name"], "amount": amount, "max_uses": max_uses, "expires_at": expires_at})
        except Exception as e:
            logger.error(f"RPG Web 单项兑换码生成失败：{e}")
            return error_response(f"生成失败：{e}", status_code=500)

    async def web_create_bundle(self):
        try:
            payload = await request.json(default={})
            rewards = payload.get("rewards", [])
            if not isinstance(rewards, list):
                return error_response("rewards 必须是数组", status_code=400)
            max_uses = int(payload.get("max_uses", 1))
            expires_at = payload.get("expires_at") or None
            if max_uses < 0:
                return error_response("使用次数不能小于0，0表示无限", status_code=400)
            maker = getattr(self.db, "create_bundle_redemption_code", None)
            if callable(maker):
                try:
                    ok, result = maker(rewards, max_uses, expires_at)
                except TypeError:
                    ok, result = maker(rewards)
            else:
                ok, result = self._create_bundle_redemption_code_compat(rewards, max_uses, expires_at)
            if not ok:
                return error_response(result, status_code=400)
            return json_response({"code": result, "reward": "礼包", "rewards": rewards, "max_uses": max_uses, "expires_at": expires_at})
        except Exception as e:
            logger.exception(f"RPG Web 礼包兑换码生成失败：{e}")
            return error_response(f"生成失败：{e}", status_code=500)

    def _ensure_redemption_schema(self):
        """确保旧数据库具有礼包兑换所需字段。"""
        c = self.db.connect()
        try:
            c.execute("CREATE TABLE IF NOT EXISTS redemption_codes (code TEXT PRIMARY KEY,item_id TEXT NOT NULL,item_name TEXT NOT NULL,amount INTEGER NOT NULL,reward_type TEXT NOT NULL DEFAULT 'item',currency_type TEXT DEFAULT NULL,currency_amount INTEGER NOT NULL DEFAULT 0,rewards_json TEXT DEFAULT NULL,max_uses INTEGER NOT NULL DEFAULT 1,use_count INTEGER NOT NULL DEFAULT 0,expires_at TEXT DEFAULT NULL,used INTEGER NOT NULL DEFAULT 0,used_by TEXT DEFAULT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,used_at TEXT DEFAULT NULL)")
            for sql in [
                "ALTER TABLE redemption_codes ADD COLUMN reward_type TEXT NOT NULL DEFAULT 'item'",
                "ALTER TABLE redemption_codes ADD COLUMN currency_type TEXT DEFAULT NULL",
                "ALTER TABLE redemption_codes ADD COLUMN currency_amount INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE redemption_codes ADD COLUMN rewards_json TEXT DEFAULT NULL",
                "ALTER TABLE redemption_codes ADD COLUMN max_uses INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE redemption_codes ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE redemption_codes ADD COLUMN expires_at TEXT DEFAULT NULL",
            ]:
                try: c.execute(sql)
                except Exception: pass
            c.commit()
        finally:
            c.close()

    def _create_bundle_redemption_code_compat(self, rewards, max_uses=1, expires_at=None):
        import secrets, json as _json
        self._ensure_redemption_schema()
        try:
            max_uses=int(max_uses)
        except Exception:
            return False, "使用次数必须是整数"
        if max_uses < 0 or max_uses > 999999999:
            return False, "使用次数范围：0-999999999，0表示无限"
        expires_at=str(expires_at or "").strip() or None
        if expires_at:
            try:
                expires_at=datetime.fromisoformat(expires_at).isoformat(timespec="seconds")
                if datetime.fromisoformat(expires_at) <= datetime.now(): return False, "使用期限必须晚于当前时间"
            except Exception:
                return False, "使用期限格式错误"
        clean=[]
        for r in rewards:
            if not isinstance(r, dict): return False, "礼包奖励格式错误"
            typ=str(r.get("type", "item")).lower()
            try: amount=int(r.get("amount", 0))
            except Exception: return False, "奖励数量必须是数字"
            if amount <= 0 or amount > 999999999: return False, "每项数量必须在1-999999999之间"
            if typ == "currency":
                cur=str(r.get("currency", "money")).lower()
                names={"money":"金币", "diamonds":"钻石"}
                if cur not in names: return False, "货币只支持金币或钻石"
                clean.append({"type":"currency","currency":cur,"amount":amount,"name":names[cur]})
            elif typ == "item":
                item_id=str(r.get("item_id", ""))
                item=self.db.get_shop_item(item_id)
                if not item: return False, f"商品不存在：{item_id}"
                clean.append({"type":"item","item_id":item_id,"amount":amount,"name":item["item_name"]})
            else:
                return False, "奖励类型只能是 item 或 currency"
        if not clean: return False, "礼包至少需要1项奖励"
        if len(clean)>50: return False, "一个礼包最多50种奖励"
        code="RPG-"+secrets.token_hex(4).upper()
        summary=" + ".join(f'{x["name"]}×{x["amount"]}' for x in clean)
        payload=_json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
        with self.db.lock:
            c=self.db.connect()
            try:
                c.execute("INSERT INTO redemption_codes(code,item_id,item_name,amount,reward_type,rewards_json,max_uses,use_count,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",(code,"bundle",summary,1,"bundle",payload,max_uses,0,expires_at))
                c.commit()
            finally: c.close()
        return True, code

    # =====================================================
    # 基础工具
    # =====================================================

    def _uid(self, event):

        try:
            uid = event.get_sender_id()

            if uid is not None:
                return str(uid)

        except Exception:
            pass

        try:
            sender = event.message_obj.sender

            if hasattr(sender, "user_id"):
                return str(sender.user_id)

            if hasattr(sender, "id"):
                return str(sender.id)

        except Exception:
            pass

        return "unknown"

    def _name(self, event):

        try:
            name = event.get_sender_name()

            if name:
                return str(name)

        except Exception:
            pass

        try:
            sender = event.message_obj.sender

            if hasattr(sender, "nickname") and sender.nickname:
                return str(sender.nickname)

            if hasattr(sender, "card") and sender.card:
                return str(sender.card)

        except Exception:
            pass

        return self._uid(event)

    def _text(self, event):

        try:
            return str(event.message_str or "").strip()
        except Exception:
            return ""

    def _ensure_user(self, event):

        return self.db.ensure_user(
            self._uid(event),
            self._name(event)
        )

    # =====================================================
    # 获取 @ 用户 QQ
    # =====================================================

    def _get_at_user_id(self, event):

        # -------------------------------------------------
        # AstrBot 消息组件
        # -------------------------------------------------

        try:

            message = event.message_obj.message

            for comp in message:

                # CQ / At
                if hasattr(comp, "qq"):

                    qq = getattr(comp, "qq", None)

                    if qq:

                        qq = str(qq)

                        try:
                            self_id = str(
                                event.message_obj.self_id
                            )

                            if qq == self_id:
                                continue

                        except Exception:
                            pass

                        return qq

                # 某些版本使用 user_id
                if hasattr(comp, "user_id"):

                    uid = getattr(
                        comp,
                        "user_id",
                        None
                    )

                    if uid:

                        uid = str(uid)

                        try:
                            self_id = str(
                                event.message_obj.self_id
                            )

                            if uid == self_id:
                                continue

                        except Exception:
                            pass

                        return uid

        except Exception:
            pass

        # -------------------------------------------------
        # CQ Code
        # -------------------------------------------------

        try:

            raw = str(
                getattr(
                    event.message_obj,
                    "raw_message",
                    ""
                )
            )

            match = re.search(
                r"\[CQ:at,qq=(\d+)\]",
                raw
            )

            if match:

                target = match.group(1)

                try:

                    self_id = str(
                        event.message_obj.self_id
                    )

                    if target == self_id:
                        return None

                except Exception:
                    pass

                return target

        except Exception:
            pass

        # -------------------------------------------------
        # 普通 @数字
        # -------------------------------------------------

        text = self._text(event)

        match = re.search(
            r"@(\d{5,12})",
            text
        )

        if match:
            return match.group(1)

        return None

    # =====================================================
    # RPG 主菜单
    # =====================================================

    @filter.command("rpg")
    async def rpg(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        yield event.plain_result(
            main_menu()
            + "\n\n"
            + f"👤 {user['name']}\n"
            + f"🆔 RPG ID：{user['rpg_id']}"
        )

    # =====================================================
    # 游戏中心
    # =====================================================

    @filter.command("游戏中心")
    async def game_center(self, event: AstrMessageEvent):

        self._ensure_user(event)

        yield event.plain_result(
            game_menu()
        )

    # =====================================================
    # 商城交易
    # =====================================================

    @filter.command("商城交易")
    async def shop_center(self, event: AstrMessageEvent):

        self._ensure_user(event)

        yield event.plain_result(
            shop_menu()
        )

    # =====================================================
    # 经济银行
    # =====================================================

    @filter.command("经济银行")
    async def economy_center(self, event: AstrMessageEvent):

        self._ensure_user(event)

        yield event.plain_result(
            economy_menu()
        )

    # =====================================================
    # 娱乐中心
    # =====================================================

    @filter.command("娱乐中心")
    async def entertainment_center(self, event: AstrMessageEvent):
        self._ensure_user(event)
        from qq_menu import entertainment_menu
        yield event.plain_result(entertainment_menu())

    @filter.command("娱乐")
    async def entertainment_center_alias(self, event: AstrMessageEvent):
        self._ensure_user(event)
        from qq_menu import entertainment_menu
        yield event.plain_result(entertainment_menu())

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def entertainment_menu_click(self, event: AstrMessageEvent):
        """兼容QQ菜单把带emoji的分类原样回传的情况。"""
        text = self._text(event).strip()
        normalized = text.replace(" ", "").replace("　", "")
        if normalized in ("🎲娱乐中心", "🎲娱乐"):
            self._ensure_user(event)
            from qq_menu import entertainment_menu
            yield event.plain_result(entertainment_menu())

    # =====================================================
    # 战斗养成
    # =====================================================

    @filter.command("战斗养成")
    async def battle_center(self, event: AstrMessageEvent):

        self._ensure_user(event)

        yield event.plain_result(
            battle_menu()
        )

    # =====================================================
    # 我的背包
    # =====================================================

    @filter.command("我的背包")
    async def inventory_center(self, event: AstrMessageEvent):

        self._ensure_user(event)

        yield event.plain_result(
            inventory_menu()
        )

    # =====================================================
    # 我的资料
    # =====================================================

    @filter.command("我的资料")
    async def profile_center(self, event: AstrMessageEvent):

        self._ensure_user(event)

        yield event.plain_result(
            profile_menu()
        )

    # =====================================================
    # RPG 排行榜菜单
    # =====================================================

    @filter.command("RPG排行榜")
    async def rank_center(self, event: AstrMessageEvent):

        self._ensure_user(event)

        yield event.plain_result(
            rank_menu()
        )

    # =====================================================
    # 帮助
    # =====================================================

    @filter.command("帮助")
    async def help_command(self, event: AstrMessageEvent):

        yield event.plain_result(
            help_menu()
        )

    # =====================================================
    # RPG帮助
    # =====================================================

    @filter.command("rpg帮助")
    async def rpg_help(self, event: AstrMessageEvent):

        yield event.plain_result(
            help_menu()
        )

    # =====================================================
    # RPG注册
    # =====================================================

    @filter.command("注册")
    async def register(self, event: AstrMessageEvent):

        uid = self._uid(event)
        name = self._name(event)

        old_user = self.db.get_user(uid)

        if old_user:

            yield event.plain_result(
                "━━━━━━━━━━━━━━━━\n"
                "📋 RPG 注册信息\n"
                "━━━━━━━━━━━━━━━━\n\n"
                f"👤 玩家：{old_user['name']}\n"
                f"🆔 RPG ID：{old_user['rpg_id']}\n"
                f"💰 金币：{old_user['money']}\n\n"
                "你已经注册过 RPG 了。"
            )

            return

        user = self.db.ensure_user(
            uid,
            name
        )

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "🎉 RPG 注册成功\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"👤 玩家：{user['name']}\n"
            f"🆔 RPG ID：{user['rpg_id']}\n"
            f"💰 初始金币：{user['money']}\n\n"
            "欢迎来到 RPG 世界！"
        )

    # =====================================================
    # RPG ID
    # =====================================================

    @filter.command("RPGID")
    async def rpg_id(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "🆔 RPG 身份\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"👤 玩家：{user['name']}\n"
            f"🆔 RPG ID：{user['rpg_id']}\n"
            f"📱 QQ ID：{user['uid']}"
        )

    @filter.command("RPG ID")
    async def rpg_id_space(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "🆔 RPG 身份\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"👤 玩家：{user['name']}\n"
            f"🆔 RPG ID：{user['rpg_id']}"
        )

    # =====================================================
    # 查询别人 RPG ID
    # =====================================================

    @filter.command("查询ID")
    async def query_id(self, event: AstrMessageEvent):

        self._ensure_user(event)

        target_uid = self._get_at_user_id(event)

        if target_uid:

            target = self.db.get_user(target_uid)

            if not target:

                yield event.plain_result(
                    "❌ 对方还没有注册 RPG。\n\n"
                    "请让对方先发送：注册"
                )

                return

            yield event.plain_result(
                "━━━━━━━━━━━━━━━━\n"
                "🔎 RPG ID 查询\n"
                "━━━━━━━━━━━━━━━━\n\n"
                f"👤 玩家：{target['name']}\n"
                f"🆔 RPG ID：{target['rpg_id']}"
            )

            return

        parts = self._text(event).split()

        if len(parts) >= 2:

            target_value = parts[1].strip()

            target = None

            if target_value.isdigit():

                target = self.db.get_user_by_rpg_id(
                    int(target_value)
                )

                if not target:
                    target = self.db.get_user(
                        target_value
                    )

            if target:

                yield event.plain_result(
                    "━━━━━━━━━━━━━━━━\n"
                    "🔎 RPG ID 查询\n"
                    "━━━━━━━━━━━━━━━━\n\n"
                    f"👤 玩家：{target['name']}\n"
                    f"🆔 RPG ID：{target['rpg_id']}"
                )

                return

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "🔎 RPG ID 查询\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "用法：\n"
            "查询ID @某人\n"
            "查询ID 10001"
        )

    # =====================================================
    # 经济银行
    # =====================================================

    @filter.command("银行")
    async def bank(self, event: AstrMessageEvent):
        user = self._ensure_user(event)
        info = self.db.get_bank_info(user["uid"])
        rate = info["rate"] * 100
        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "🏦 经济银行\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"👤 {user['name']}\n"
            f"🆔 RPG ID：{user['rpg_id']}\n\n"
            f"💵 现金：{user['money']} 金币\n"
            f"🏦 银行存款：{info['bank']} 金币\n"
            f"📈 每日利率：{rate:g}%\n\n"
            "操作：\n"
            "存款 500\n"
            "取款 500\n"
            "余额\n"
            "转账 RPGID 金额\n\n"
            "💡 银行利息每天自动结算。"
        )

    @filter.command("利息")
    async def bank_interest(self, event: AstrMessageEvent):
        user = self._ensure_user(event)
        ok, interest, msg = self.db.apply_bank_interest(user["uid"])
        info = self.db.get_bank_info(user["uid"])
        if not ok:
            yield event.plain_result("❌ " + msg); return
        yield event.plain_result(
            "🏦 银行利息\n\n"
            f"📈 本次结算：+{interest} 金币\n"
            f"🏦 当前存款：{info['bank']} 金币\n"
            f"💹 日利率：{info['rate']*100:g}%\n\n"
            f"{msg}"
        )

    # =====================================================
    # 余额
    # =====================================================

    @filter.command("余额")
    async def balance(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        total = (
            int(user["money"])
            + int(user["bank"])
        )

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "💰 财富信息\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"👤 {user['name']}\n"
            f"🆔 RPG ID：{user['rpg_id']}\n\n"
            f"💵 现金：{user['money']}\n"
            f"💎 钻石：{user['diamonds']}\n"
            f"🏦 银行：{user['bank']}\n"
            f"💰 总资产：{total} 金币"
        )

    # =====================================================
    # 属性
    # =====================================================

    @filter.command("属性")
    async def profile(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        need = self.db.exp_required(
            user["level"]
        )

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "👤 RPG 属性\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"玩家：{user['name']}\n"
            f"RPG ID：{user['rpg_id']}\n\n"
            f"⭐ 等级：Lv.{user['level']}\n"
            f"✨ 经验：{user['exp']}/{need}\n"
            f"❤️ 生命：{user['hp']}/{user['max_hp']}\n"
            f"⚔️ 攻击：{user['attack']}\n"
            f"🛡️ 防御：{user['defense']}\n"
            f"🔥 连签：{user['streak']}"
        )

    # =====================================================
    # 签到
    # =====================================================

    @filter.command("签到")
    async def sign_in(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        success, result = self.db.sign_in(
            user["uid"]
        )

        if not success:

            yield event.plain_result(
                f"❌ {result}"
            )

            return

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "🎁 每日签到\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"👤 {user['name']}\n"
            f"💰 获得：{result['reward']} 金币\n"
            f"🔥 连续签到：{result['streak']} 天\n\n"
            "明天继续签到吧！"
        )

    # =====================================================
    # 连签
    # =====================================================

    @filter.command("连签")
    async def streak(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "🔥 连续签到\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"👤 {user['name']}\n"
            f"🔥 当前连签：{user['streak']} 天\n\n"
            "签到奖励：\n"
            "第 1-2 天：100金币\n"
            "第 3-6 天：150金币\n"
            "第 7 天及以后：200金币"
        )

    # =====================================================
    # 打工
    # =====================================================

    @filter.command("打工")
    async def work(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        success, result = self.db.work(
            user["uid"]
        )

        if not success:

            yield event.plain_result(
                f"❌ {result}"
            )

            return

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "🔨 打工完成\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"👤 {user['name']}\n"
            f"💰 工资：{result} 金币\n\n"
            "今天辛苦了！"
        )

    # =====================================================
    # 转账
    # =====================================================

    @filter.command("转账")
    async def transfer(self, event: AstrMessageEvent):

        sender = self._ensure_user(event)

        text = self._text(event)

        # -------------------------------------------------
        # 去掉 CQ @
        # -------------------------------------------------

        clean_text = re.sub(
            r"\[CQ:at,qq=\d+\]",
            "",
            text
        )

        # -------------------------------------------------
        # 去掉普通 @数字
        # -------------------------------------------------

        clean_text = re.sub(
            r"@\d{5,12}",
            "",
            clean_text
        )

        parts = clean_text.split()

        if len(parts) < 2:

            yield event.plain_result(
                "❌ 转账格式错误\n\n"
                "转账 10001 500\n"
                "或者：\n"
                "转账 @某人 500"
            )

            return

        # -------------------------------------------------
        # 获取金额
        # -------------------------------------------------

        amount = None

        for p in parts[1:]:

            if p.isdigit():

                value = int(p)

                # RPG ID 和金额同时存在时：
                # 最后一个数字优先作为金额
                amount = value

        if amount is None or amount <= 0:

            yield event.plain_result(
                "❌ 金额错误。\n\n"
                "例如：\n"
                "转账 10001 500\n"
                "转账 @某人 500"
            )

            return

        # -------------------------------------------------
        # 优先 @
        # -------------------------------------------------

        target = None

        target_uid = self._get_at_user_id(event)

        if target_uid:

            target = self.db.get_user(
                target_uid
            )

        # -------------------------------------------------
        # RPG ID
        # -------------------------------------------------

        if target is None:

            for p in parts[1:]:

                if p.isdigit():

                    possible = self.db.get_user_by_rpg_id(
                        int(p)
                    )

                    if possible:

                        # 避免把金额当 RPG ID
                        if int(possible["rpg_id"]) != amount:
                            target = possible
                            break

        if target is None:

            yield event.plain_result(
                "❌ 找不到收款人。\n\n"
                "请使用：\n"
                "转账 RPGID 金额\n"
                "或者：\n"
                "转账 @某人 金额"
            )

            return

        success, message = self.db.transfer(
            sender["uid"],
            target["uid"],
            amount
        )

        if not success:

            yield event.plain_result(
                f"❌ {message}"
            )

            return

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "💸 转账成功\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"👤 付款人：{sender['name']}\n"
            f"🆔 RPG ID：{sender['rpg_id']}\n\n"
            f"👤 收款人：{target['name']}\n"
            f"🆔 RPG ID：{target['rpg_id']}\n\n"
            f"💰 转账：{amount} 金币"
        )

    # =====================================================
    # 存款
    # =====================================================

    @filter.command("存款")
    async def deposit(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        parts = self._text(event).split()

        if (
            len(parts) < 2
            or not parts[1].isdigit()
        ):

            yield event.plain_result(
                "用法：存款 500"
            )

            return

        amount = int(parts[1])

        success, message = self.db.deposit(
            user["uid"],
            amount
        )

        if not success:

            yield event.plain_result(
                f"❌ {message}"
            )

            return

        yield event.plain_result(
            "🏦 存款成功\n\n"
            f"存入：{amount} 金币"
        )

    # =====================================================
    # 取款
    # =====================================================

    @filter.command("取款")
    async def withdraw(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        parts = self._text(event).split()

        if (
            len(parts) < 2
            or not parts[1].isdigit()
        ):

            yield event.plain_result(
                "用法：取款 500"
            )

            return

        amount = int(parts[1])

        success, message = self.db.withdraw(
            user["uid"],
            amount
        )

        if not success:

            yield event.plain_result(
                f"❌ {message}"
            )

            return

        yield event.plain_result(
            "🏦 取款成功\n\n"
            f"取出：{amount} 金币"
        )

    # =====================================================
    # 背包
    # =====================================================

    @filter.command("背包")
    async def inventory(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        items = self.db.get_inventory(
            user["uid"]
        )

        if not items:

            yield event.plain_result(
                "━━━━━━━━━━━━━━━━\n"
                "🎒 我的背包\n"
                "━━━━━━━━━━━━━━━━\n\n"
                "背包还是空的。\n\n"
                "可以前往：商城"
            )

            return

        lines = [
            "━━━━━━━━━━━━━━━━",
            "🎒 我的背包",
            "━━━━━━━━━━━━━━━━",
            ""
        ]

        for item in items:

            lines.append(
                f"📦 {item['item_name']} × {item['amount']}"
            )

            lines.append(
                f"   ID：{item['item_id']}"
            )

            lines.append("")

        yield event.plain_result(
            "\n".join(lines)
        )

    # =====================================================
    # 使用物品
    # =====================================================

    @filter.command("使用")
    async def use_item(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        parts = self._text(event).split()

        if len(parts) < 2:

            yield event.plain_result(
                "用法：\n"
                "使用 hp_potion\n"
                "使用 bread\n"
                "使用 attack_book\n"
                "使用 defense_book"
            )

            return

        item_id = parts[1]

        success, result = self.db.use_item(
            user["uid"],
            item_id
        )

        if not success:

            yield event.plain_result(
                f"❌ {result}"
            )

            return

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "🎒 使用物品\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"📦 {item_id}\n"
            f"✅ {result['message']}"
        )

    # =====================================================
    # 商城
    # =====================================================

    @filter.command("商城")
    async def shop(self, event: AstrMessageEvent):

        self._ensure_user(event)

        items = self.db.get_shop_items()

        lines = [
            "━━━━━━━━━━━━━━━━",
            "🏪 RPG 商城",
            "━━━━━━━━━━━━━━━━",
            ""
        ]

        for idx, item in enumerate(items, 1):
            lines.append(
                f"【{idx}】📦 {item['item_name']}\n"
                f"💰 买入：{item['price']}　💵 卖出：{item['sell_price']}\n"
                f"📝 作用：{item['description']}\n"
            )

        lines.append(
            "购买格式：购买 序号 数量\n例如：购买 3 2"
        )

        yield event.plain_result(
            "\n".join(lines)
        )

    # =====================================================
    # 购买
    # =====================================================

    @filter.command("购买")
    async def buy(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        parts = self._text(event).split()

        if len(parts) < 2:

            yield event.plain_result(
                "用法：购买 商品ID 数量\n\n"
                "例如：\n"
                "购买 hp_potion 1"
            )

            return

        selector = parts[1]
        items = self.db.get_shop_items()
        if selector.isdigit():
            idx = int(selector)
            if idx < 1 or idx > len(items):
                yield event.plain_result("❌ 商品序号不存在，请先发送：商城")
                return
            item_id = items[idx - 1]["item_id"]
        else:
            item_id = selector

        amount = 1
        if len(parts) >= 3 and parts[2].isdigit():
            amount = int(parts[2])

        success, result = self.db.buy_item(
            user["uid"],
            item_id,
            amount
        )

        if not success:

            yield event.plain_result(
                f"❌ {result}"
            )

            return

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "🛒 购买成功\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"📦 商品：{result['item']}\n"
            f"🔢 数量：{result['amount']}\n"
            f"💰 花费：{result['total']} 金币"
        )

    # =====================================================
    # 出售
    # =====================================================

    @filter.command("出售")
    async def sell(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        parts = self._text(event).split()

        if len(parts) < 2:

            yield event.plain_result(
                "用法：出售 商品ID 数量\n\n"
                "例如：\n"
                "出售 hp_potion 1"
            )

            return

        selector = parts[1]
        items = self.db.get_shop_items()
        if selector.isdigit():
            idx = int(selector)
            if idx < 1 or idx > len(items):
                yield event.plain_result("❌ 商品序号不存在，请先发送：商城")
                return
            item_id = items[idx - 1]["item_id"]
        else:
            item_id = selector

        amount = 1
        if len(parts) >= 3 and parts[2].isdigit():
            amount = int(parts[2])

        success, result = self.db.sell_item(
            user["uid"],
            item_id,
            amount
        )

        if not success:

            yield event.plain_result(
                f"❌ {result}"
            )

            return

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "💵 出售成功\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"📦 商品：{result['item']}\n"
            f"🔢 数量：{result['amount']}\n"
            f"💰 获得：{result['total']} 金币"
        )

    # =====================================================
    # 冒险
    # =====================================================

    @filter.command("冒险")
    async def adventure(self, event: AstrMessageEvent):

        user = self._ensure_user(event)
        parts = self._text(event).split()
        rounds = 1
        if len(parts) >= 2 and parts[1].isdigit():
            rounds = max(1, min(20, int(parts[1])))

        battle = self.db.get_battle(user["uid"])

        if battle:

            yield event.plain_result(
                "⚔️ 你已经在战斗中！\n\n"
                f"👹 敌人：{battle['enemy_name']}\n"
                f"❤️ 敌方生命："
                f"{battle['enemy_hp']}/"
                f"{battle['enemy_max_hp']}\n\n"
                "发送：攻击\n"
                "或者：逃跑"
            )

            return

        if int(user["hp"]) <= 0:

            yield event.plain_result(
                "💀 你的生命值已经归零。\n\n"
                "请先使用生命药水恢复生命。"
            )

            return

        success, result = self.db.start_battle(
            user["uid"]
        )

        if not success:

            yield event.plain_result(
                f"❌ {result}"
            )

            return

        if rounds == 1:
            yield event.plain_result(
                "━━━━━━━━━━━━━━━━\n🗺️ 冒险遭遇\n━━━━━━━━━━━━━━━━\n\n"
                f"👹 你遇到了：{result['name']}\n"
                f"❤️ HP：{result['hp']}/{result['max_hp']}\n"
                f"⚔️ 攻击：{result['attack']}　🛡️ 防御：{result['defense']}\n\n"
                "发送：攻击 / 逃跑"
            )
            return

        # 冒险 N：自动进行 N 个攻击回合，最多20回合。
        logs = [f"🗺️ 多回合冒险：最多 {rounds} 回合", f"👹 {result['name']} HP {result['hp']}/{result['max_hp']}"]
        for i in range(1, rounds + 1):
            ok, r = self.db.battle_attack(user["uid"])
            if not ok:
                logs.append(f"\n❌ 第{i}回合：{r}")
                break
            logs.append(f"\n⚔️ 第{i}回合：你造成 {r['player_damage']}，敌人反击 {r['enemy_damage']}" )
            if r["result"] == "win":
                logs.append(f"🎉 胜利！金币 +{r['money']}，经验 +{r['exp']}")
                break
            if r["result"] == "lose":
                logs.append("💀 你被击败了。")
                break
            logs.append(f"❤️ 你的HP {r['player_hp']}　👹 敌人HP {r['enemy_hp']}")
        if self.db.get_battle(user["uid"]):
            logs.append("\n继续：攻击　或　冒险 5（开始下一场需先结束当前战斗）")
        yield event.plain_result("\n".join(logs))

    # =====================================================
    # 攻击
    # =====================================================

    @filter.command("攻击")
    async def attack(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        success, result = self.db.battle_attack(
            user["uid"]
        )

        if not success:

            yield event.plain_result(
                f"❌ {result}"
            )

            return

        if result["result"] == "win":

            exp_result = self.db.add_exp(
                user["uid"],
                result["exp"]
            )

            message = (
                "━━━━━━━━━━━━━━━━\n"
                "🎉 战斗胜利！\n"
                "━━━━━━━━━━━━━━━━\n\n"
                f"⚔️ 你造成：{result['player_damage']} 伤害\n"
                f"💰 金币 +{result['money']}\n"
                f"✨ 经验 +{result['exp']}\n"
            )

            if (
                exp_result
                and exp_result["level_ups"] > 0
            ):

                message += (
                    "\n🎊 恭喜升级！\n"
                    f"⭐ Lv.{exp_result['old_level']}"
                    f" → Lv.{exp_result['level']}\n\n"
                    f"❤️ 最大生命 +"
                    f"{exp_result['level_ups'] * 10}\n"
                    f"⚔️ 攻击 +"
                    f"{exp_result['level_ups'] * 2}\n"
                    f"🛡️ 防御 +"
                    f"{exp_result['level_ups']}"
                )

            yield event.plain_result(message)

            return

        if result["result"] == "lose":

            yield event.plain_result(
                "━━━━━━━━━━━━━━━━\n"
                "💀 战斗失败\n"
                "━━━━━━━━━━━━━━━━\n\n"
                f"⚔️ 你造成：{result['player_damage']} 伤害\n"
                f"👹 敌人造成：{result['enemy_damage']} 伤害\n\n"
                "你的生命值已经归零。\n"
                "可以使用物品恢复生命。"
            )

            return

        yield event.plain_result(
            "━━━━━━━━━━━━━━━━\n"
            "⚔️ 战斗进行中\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"⚔️ 你造成：{result['player_damage']} 伤害\n"
            f"👹 敌人反击：{result['enemy_damage']} 伤害\n\n"
            f"❤️ 你的生命：{result['player_hp']}\n"
            f"👹 敌人生命：{result['enemy_hp']}\n\n"
            "继续发送：攻击"
        )

    # =====================================================
    # 逃跑
    # =====================================================

    @filter.command("逃跑")
    async def flee(self, event: AstrMessageEvent):

        user = self._ensure_user(event)

        success, message = self.db.flee_battle(
            user["uid"]
        )

        if not success:

            yield event.plain_result(
                f"❌ {message}"
            )

            return

        yield event.plain_result(
            "🏃 你逃离了战斗。"
        )

    # =====================================================
    # 排行榜
    # =====================================================

    @filter.command("排行榜")
    async def ranking(self, event: AstrMessageEvent):

        self._ensure_user(event)

        parts = self._text(event).split()

        money_rank = (
            len(parts) >= 2
            and parts[1] in (
                "金币",
                "财富",
                "钱"
            )
        )

        if money_rank:

            rows = self.db.get_money_rank(10)

            title = "💰 财富排行榜"

        else:

            rows = self.db.get_level_rank(10)

            title = "🏆 等级排行榜"

        if not rows:

            yield event.plain_result(
                "目前还没有玩家。"
            )

            return

        lines = [
            "━━━━━━━━━━━━━━━━",
            title,
            "━━━━━━━━━━━━━━━━",
            ""
        ]

        medals = [
            "🥇",
            "🥈",
            "🥉"
        ]

        for index, row in enumerate(rows):

            if index < 3:
                medal = medals[index]
            else:
                medal = f"{index + 1:02d}."

            name = row.get("name", "未知玩家")
            level = row.get("level", 1)
            exp = row.get("exp", 0)
            lines.append(f"{medal} {name}  Lv.{level}  ⭐ {exp} EXP")

        yield event.plain_result("\n".join(lines))

    # =====================================================
    # 每日运势
    # =====================================================

    def _fortune_data(self, uid):
        today = datetime.now().strftime("%Y-%m-%d")
        rng = random.Random(f"{uid}:{today}")
        pool = [
            ("大吉", 5, "今天状态拉满，适合主动出击，重要事情成功率明显提升。"),
            ("吉", 4, "整体运势顺畅，稳扎稳打容易收获不错的结果。"),
            ("中吉", 3, "运势平稳，机会藏在细节里，耐心一点会更好。"),
            ("小吉", 3, "有一点小波折，但问题不大，调整节奏即可。"),
            ("平", 2, "普通而稳定的一天，保持自己的节奏就是最好的选择。"),
        ]
        title, stars, desc = rng.choice(pool)
        return {
            "date": today,
            "title": title,
            "stars": stars,
            "desc": desc,
            "career": rng.choice(["★★★★☆ 顺利", "★★★★☆ 有进展", "★★★☆☆ 稳步推进", "★★★☆☆ 注意细节"]),
            "money": rng.choice(["★★★★★ 财运不错", "★★★★☆ 小有收获", "★★★☆☆ 收支平稳", "★★☆☆☆ 不宜冲动消费"]),
            "love": rng.choice(["★★★★☆ 沟通顺畅", "★★★★☆ 容易遇到惊喜", "★★★☆☆ 多听少猜", "★★★☆☆ 平淡稳定"]),
            "health": rng.choice(["★★★★☆ 状态不错", "★★★★☆ 注意规律休息", "★★★☆☆ 别熬夜", "★★★☆☆ 注意压力"]),
            "color": rng.choice(["蓝色", "绿色", "红色", "紫色", "白色", "金色"]),
            "numbers": rng.sample(range(1, 10), 2),
            "tip": rng.choice(["先完成最重要的一件事。", "稳一点，今天不适合急躁。", "主动沟通，机会往往就在交流里。", "少一点内耗，多一点行动。"]),
        }

    def _fortune_text(self, user):
        d = self._fortune_data(user["uid"])
        stars = "⭐" * d["stars"] + "☆" * (5 - d["stars"])
        return (
            "━━━━━━━━━━━━━━━━\n"
            "        🔮 今日运势\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"👤 {user['name']}   🆔 {user['rpg_id']}\n"
            f"📅 {d['date']}\n\n"
            f"✨ 综合：{d['title']}  {stars}\n"
            f"📖 {d['desc']}\n\n"
            f"💼 事业：{d['career']}\n"
            f"💰 财运：{d['money']}\n"
            f"❤️ 感情：{d['love']}\n"
            f"🩺 健康：{d['health']}\n\n"
            f"🎨 幸运色：{d['color']}\n"
            f"🔢 幸运数字：{d['numbers'][0]}、{d['numbers'][1]}\n"
            f"💡 今日建议：{d['tip']}\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "🌙 每天 00:00 自动刷新"
        )

    # =====================================================
    # 娱乐中心：猜拳 / 骰子 / 彩票 / 管理兑换码
    # =====================================================
    def _group_origin(self,event):
        try:
            origin=str(event.unified_msg_origin)
            return origin if "group" in origin.lower() else ""
        except Exception: return ""

    def _entertainment_rate(self):
        pool=self.db.get_lottery_pool()
        # 奖池动态概率：5000为基础，低池更难、高池更容易，但设置硬上限。
        return max(0.02,min(0.18,0.08*(pool/5000)))

    def _gamble_win(self,uid):
        luck=int(self.db.get_system_value(f"luck:{uid}","0") or 0)
        rate=min(0.22,self._entertainment_rate()+luck*0.01)
        if luck: self.db.set_system_value(f"luck:{uid}",str(max(0,luck-1)))
        return random.random()<rate

    def _lose_to_pool(self,amount):
        self.db.add_lottery_pool(max(0,int(amount)))

    @filter.command("猜拳")
    async def rps(self,event:AstrMessageEvent):
        user=self._ensure_user(event); parts=self._text(event).split(); choices={"石头":"石头","剪刀":"剪刀","布":"布"}
        if len(parts)<2 or parts[1] not in choices:
            yield event.plain_result("用法：猜拳 石头 / 剪刀 / 布\n每局100金币，输掉的100金币进入彩票奖池。") ; return
        if not self.db.remove_money(user["uid"],100): yield event.plain_result("❌ 金币不足"); return
        mine=parts[1]; bot=random.choice(list(choices.values())); win=(mine,bot) in [("石头","剪刀"),("剪刀","布"),("布","石头")]
        if mine==bot: self.db.add_money(user["uid"],100); result="平局，退回本金"
        elif win: reward=180 if self._gamble_win(user["uid"]) else 100; self.db.add_money(user["uid"],reward); result=f"你赢了，获得{reward}金币"
        else: self._lose_to_pool(100); result="你输了，100金币进入彩票奖池"
        yield event.plain_result(f"🎲 猜拳\n你：{mine}\n机器人：{bot}\n{result}")

    @filter.command("骰子")
    async def dice(self,event:AstrMessageEvent):
        user=self._ensure_user(event); parts=self._text(event).split(); guess=int(parts[1]) if len(parts)>1 and parts[1].isdigit() and 1<=int(parts[1])<=6 else 0
        if not guess: yield event.plain_result("用法：骰子 1-6\n每局100金币，输掉的100金币进入彩票奖池"); return
        if not self.db.remove_money(user["uid"],100): yield event.plain_result("❌ 金币不足"); return
        roll=random.randint(1,6)
        if roll==guess and self._gamble_win(user["uid"]): self.db.add_money(user["uid"],500); result="🎉 猜中，获得500金币"
        else: self._lose_to_pool(100); result="❌ 没猜中，100金币进入彩票奖池"
        yield event.plain_result(f"🎲 骰子结果：{roll}\n{result}")

    @filter.command("老虎机")
    async def slot_machine(self, event: AstrMessageEvent):
        user = self._ensure_user(event)
        parts = self._text(event).split()
        stake = 100
        if len(parts) >= 2 and parts[1].isdigit():
            stake = int(parts[1])
        if stake < 10 or stake > 100000:
            yield event.plain_result("用法：老虎机 [下注金额]，范围 10-100000 金币")
            return
        if not self.db.remove_money(user["uid"], stake):
            yield event.plain_result("❌ 金币不足")
            return

        symbols = ["🍒", "🍋", "🔔", "⭐", "7️⃣"]
        # 奖池越高，中奖机会越高；奖池低于基础值则逐步降低。
        win = self._gamble_win(user["uid"])
        if win:
            if random.random() < 0.72:
                a = random.choice(symbols); b = a; c = random.choice([x for x in symbols if x != a])
                multiplier = 2
            else:
                a = random.choice(symbols); b = a; c = a
                multiplier = 5 if a == "7️⃣" else 3
            pool = self.db.get_lottery_pool()
            # 奖励不能凭空超过奖池；中奖最多领取当前奖池的可用金额。
            reward = min(stake * multiplier, pool)
            if reward <= 0:
                self._lose_to_pool(stake)
                result = f"❌ 奖池当前不足，{stake} 金币进入彩票奖池"
                yield event.plain_result(f"🎰 幸运老虎机\n\n【 {a} │ {b} │ {c} 】\n\n{result}")
                return
            result = f"🎉 中奖！获得 {reward} 金币（{multiplier}倍）"
        else:
            a, b, c = random.choices(symbols, k=3)
            self._lose_to_pool(stake)
            result = f"❌ 未中奖，{stake} 金币进入彩票奖池"
        yield event.plain_result(f"🎰 幸运老虎机\n\n【 {a} │ {b} │ {c} 】\n\n{result}")

    @filter.command("买彩票")
    async def buy_lottery(self,event:AstrMessageEvent):
        user=self._ensure_user(event); parts=self._text(event).split()
        if not self._group_origin(event): yield event.plain_result("❌ 彩票只能在群聊购买。"); return
        if len(parts)<2: yield event.plain_result("用法：买彩票 12345678\n中奖码必须为8位数字，每张200金币。"); return
        ok,result=self.db.buy_lottery_ticket(user["uid"],self._group_origin(event),parts[1])
        yield event.plain_result(("🎟️ 彩票购买成功\n开奖时间："+result) if ok else "❌ "+result)

    @filter.command("彩票")
    async def lottery_help(self,event:AstrMessageEvent):
        yield event.plain_result("🎟️ 彩票\n\n每3小时开奖一次，仅06:00-21:00开奖。\n中奖码为8位数字。\n连续匹配末尾4位起才有奖励：4位=1倍、5位=2倍、6位=3倍、7-8位=4倍。\n奖池金额不会公开。\n购买：买彩票 12345678")

    @filter.command("获取兑换码")
    async def get_code(self,event:AstrMessageEvent):
        uid=self._uid(event); self._admin_pending[uid]=datetime.now().timestamp()+300
        yield event.plain_result("🔐 管理兑换码中心\n请输入管理密码：\n直接发送密码即可，不需要加任何前缀。")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def direct_admin_password(self,event:AstrMessageEvent):
        """QQ聊天端只生成永久、单奖励、默认一次使用的兑换码。"""
        uid=self._uid(event); text=self._text(event)
        pending=self._admin_pending.get(uid)
        if pending=="ok":
            import re as _re
            m=_re.fullmatch(r"(.+?)\*(\d+)", text)
            if not m:
                return
            target,count=m.group(1).strip(),int(m.group(2))
            if count<=0 or count>999999999:
                yield event.plain_result("❌ 数量范围：1-999999999"); return
            if target in ("金币","gold","money"):
                ok,code=self.db.create_currency_redemption_code("money",count,1,None); reward="金币"
            elif target in ("钻石","diamond","diamonds"):
                ok,code=self.db.create_currency_redemption_code("diamonds",count,1,None); reward="钻石"
            elif target.isdigit():
                idx=int(target); items=self.db.get_redeemable_items()
                if idx<1 or idx>len(items): yield event.plain_result("❌ 物品编号不存在"); return
                item_id=items[idx-1]["item_id"]; reward=items[idx-1]["item_name"]
                ok,code=self.db.create_redemption_code(item_id,count,1,None)
            else:
                item=self.db.get_shop_item(target)
                if not item: yield event.plain_result("❌ 物品不存在："+target); return
                ok,code=self.db.create_redemption_code(item["item_id"],count,1,None); reward=item["item_name"]
            if not ok: yield event.plain_result("❌ "+code); return
            self._admin_pending.pop(uid,None)
            yield event.plain_result(f"🎫 永久兑换码生成成功\n\n🎁 奖励：{reward} × {count}\n🔑 兑换码：{code}\n♾️ 有效期：永久\n👤 使用次数：1次\n\n玩家发送：兑换 {code}")
            return
        if not pending or not isinstance(pending,(int,float)):
            return
        if datetime.now().timestamp()>pending:
            self._admin_pending.pop(uid,None); return
        if text.isdigit() and text==self._admin_password:
            self._admin_pending[uid]="ok"
            items=self.db.get_redeemable_items(); lines=["🎁 管理兑换码中心","","QQ聊天生成规则：永久 + 单一奖励 + 1次使用","输入：1*10 / 金币*10000 / 钻石*100",""]
            for i,x in enumerate(items,1): lines.append(f"{i}. {x['item_name']} | {x['description']}")
            lines += ["", "💰 金币*10000", "💎 钻石*100"]
            yield event.plain_result("\n".join(lines))


    @filter.command("管理密码")
    async def admin_password(self,event:AstrMessageEvent):
        uid=self._uid(event); parts=self._text(event).split()
        if uid not in self._admin_pending or datetime.now().timestamp()>self._admin_pending[uid]:
            yield event.plain_result("❌ 请先发送：获取兑换码"); return
        if len(parts)<2 or parts[1]!=self._admin_password:
            yield event.plain_result("❌ 管理密码错误"); return
        self._admin_pending[uid]="ok"
        items=self.db.get_redeemable_items(); lines=["🎁 管理兑换码中心","", "QQ聊天生成：永久 + 单一奖励 + 1次使用", "输入 1*数量 / 金币*数量 / 钻石*数量", ""]
        for i,x in enumerate(items,1): lines.append(f"{i}. {x['item_name']} | {x['description']}")
        lines += ["", "💰 金币*10000", "💎 钻石*100"]
        yield event.plain_result("\n".join(lines))

    @filter.command("生成兑换码")
    async def create_code(self,event:AstrMessageEvent):
        uid=self._uid(event)
        if self._admin_pending.get(uid)!="ok":
            yield event.plain_result("❌ 请先发送：获取兑换码，然后直接输入管理密码"); return
        parts=self._text(event).split()
        if len(parts)<2:
            yield event.plain_result("用法：1*10 / 金币*10000 / 钻石*100"); return
        spec=parts[1]
        import re as _re
        m=_re.fullmatch(r"(.+?)\*(\d+)",spec)
        if not m:
            yield event.plain_result("❌ 格式错误，请使用：1*10、金币*10000 或 钻石*100"); return
        target,count=m.group(1),int(m.group(2))
        if count<=0 or count>999999999:
            yield event.plain_result("❌ 数量范围：1-999999999"); return
        if target in ("金币","gold","money"):
            ok,code=self.db.create_currency_redemption_code("money",count); reward="金币"
        elif target in ("钻石","diamond","diamonds"):
            ok,code=self.db.create_currency_redemption_code("diamonds",count); reward="钻石"
        elif target.isdigit():
            idx=int(target); items=self.db.get_redeemable_items()
            if idx<1 or idx>len(items): yield event.plain_result("❌ 物品编号不存在"); return
            item_id=items[idx-1]["item_id"]; reward=items[idx-1]["item_name"]
            ok,code=self.db.create_redemption_code(item_id,count)
        else:
            ok,code=self.db.create_redemption_code(target,count); reward=(self.db.get_shop_item(target) or {"item_name":target})["item_name"]
        if not ok: yield event.plain_result("❌ "+code); return
        self._admin_pending.pop(uid,None)
        yield event.plain_result(f"🎫 兑换码生成成功\n\n🎁 奖励：{reward} × {count}\n🔑 兑换码：{code}\n\n玩家发送：兑换 {code}")

    @filter.command("生成金币兑换码")
    async def create_money_code(self,event:AstrMessageEvent):
        uid=self._uid(event)
        if self._admin_pending.get(uid)!="ok":
            yield event.plain_result("❌ 请先发送：获取兑换码，然后直接输入管理密码")
            return
        parts=self._text(event).split()
        if len(parts)<2 or not parts[1].isdigit() or int(parts[1])<=0:
            yield event.plain_result("用法：生成金币兑换码 10000")
            return
        amount=int(parts[1])
        ok,code=self.db.create_currency_redemption_code("money",amount)
        if not ok:
            yield event.plain_result("❌ "+code); return
        self._admin_pending.pop(uid,None)
        yield event.plain_result(f"🎫 金币兑换码生成成功\n\n💰 金币：+{amount}\n🔑 兑换码：{code}\n\n玩家发送：兑换 {code}")

    @filter.command("生成钻石兑换码")
    async def create_diamond_code(self,event:AstrMessageEvent):
        uid=self._uid(event)
        if self._admin_pending.get(uid)!="ok":
            yield event.plain_result("❌ 请先发送：获取兑换码，然后直接输入管理密码")
            return
        parts=self._text(event).split()
        if len(parts)<2 or not parts[1].isdigit() or int(parts[1])<=0:
            yield event.plain_result("用法：生成钻石兑换码 100")
            return
        amount=int(parts[1])
        ok,code=self.db.create_currency_redemption_code("diamonds",amount)
        if not ok:
            yield event.plain_result("❌ "+code); return
        self._admin_pending.pop(uid,None)
        yield event.plain_result(f"🎫 钻石兑换码生成成功\n\n💎 钻石：+{amount}\n🔑 兑换码：{code}\n\n玩家发送：兑换 {code}")

    @filter.command("兑换")
    async def redeem(self,event:AstrMessageEvent):
        user=self._ensure_user(event); parts=self._text(event).split()
        if len(parts)<2: yield event.plain_result("用法：兑换 RPG-XXXXXXXX"); return
        ok,result=self.db.redeem_code(user["uid"],parts[1])
        if ok:
            if result.get("type")=="currency":
                msg=f"🎉 兑换成功\n💰 {result['item']} +{result['amount']}"
            else:
                msg=f"🎉 兑换成功\n📦 {result['item']} × {result['amount']}\n📝 作用：商城中的该物品可以直接使用。"
            if result.get("max_uses",1)==0:
                msg += "\n♾️ 剩余次数：无限"
            else:
                msg += f"\n🎟️ 使用次数：{result.get('use_count',1)}/{result.get('max_uses',1)}"
            if result.get("expires_at"):
                msg += f"\n⏰ 有效期至：{result['expires_at']}"
            else:
                msg += "\n♾️ 有效期：永久"
            yield event.plain_result(msg)
        else:
            yield event.plain_result("❌ "+result)

    async def _lottery_loop(self):
        while True:
            try:
                self.db.reset_lottery_pool_if_low()
                now=datetime.now(); slots={6,9,12,15,18,21}
                if now.hour in slots and now.minute==0:
                    key=now.strftime("%Y%m%d%H")
                    if self.db.get_system_value("lottery_last_draw","")!=key:
                        result=self.db.draw_lottery(key)
                        if result:
                            for origin in result["origins"]:
                                if not origin: continue
                                text=f"🎟️ 彩票开奖\n中奖号码：{result['number']}\n\n"
                                if result["winners"]:
                                    ws=[w for w in result["winners"] if w[1]==origin]
                                    text += "\n".join(f"🏆 {self.db.get_user(w[0])['name']}：匹配{w[2]}位，获得{w[4]}金币" for w in ws) if ws else "本群本期无人中奖。"
                                else: text += "本期无人中奖。"
                                try: await self.context.send_message(origin, [Comp.Plain(text=text)])
                                except Exception: pass
            except Exception as e: logger.error(f"彩票自动开奖异常：{e}")
            await asyncio.sleep(30)

    @filter.command("运势")
    async def fortune(self, event: AstrMessageEvent):
        user = self._ensure_user(event)
        yield event.plain_result(self._fortune_text(user))

    @filter.command("今日运势")
    async def today_fortune(self, event: AstrMessageEvent):
        user = self._ensure_user(event)
        yield event.plain_result(self._fortune_text(user))

    @filter.command("fortune")
    async def fortune_en(self, event: AstrMessageEvent):
        user = self._ensure_user(event)
        yield event.plain_result(self._fortune_text(user))