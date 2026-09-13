# =========================================================
# RPG Database
# database/database.py
# =========================================================

import os
import random
import sqlite3
import threading
import json
from datetime import date, datetime, timedelta


class RPGDatabase:

    # =====================================================
    # 初始化
    # =====================================================

    def __init__(self, db_path=None):

        if db_path is None:

            plugin_dir = os.path.dirname(
                os.path.dirname(
                    os.path.abspath(__file__)
                )
            )

            data_dir = os.path.join(
                plugin_dir,
                "data"
            )

            os.makedirs(
                data_dir,
                exist_ok=True
            )

            db_path = os.path.join(
                data_dir,
                "rpg.db"
            )

        self.db_path = db_path

        self.lock = threading.RLock()

        self.init_database()

    # =====================================================
    # 数据库连接
    # =====================================================

    def connect(self):

        conn = sqlite3.connect(
            self.db_path,
            timeout=30,
            check_same_thread=False
        )

        conn.row_factory = sqlite3.Row

        return conn

    # =====================================================
    # 初始化数据库
    # =====================================================

    def init_database(self):

        with self.lock:

            conn = self.connect()

            try:

                # -------------------------------------------------
                # 用户
                # -------------------------------------------------

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        rpg_id INTEGER UNIQUE NOT NULL,
                        uid TEXT UNIQUE NOT NULL,
                        name TEXT NOT NULL DEFAULT '',
                        money INTEGER NOT NULL DEFAULT 1000,
                        diamonds INTEGER NOT NULL DEFAULT 0,
                        bank INTEGER NOT NULL DEFAULT 0,
                        bank_last_interest TEXT DEFAULT NULL,
                        hp INTEGER NOT NULL DEFAULT 100,
                        max_hp INTEGER NOT NULL DEFAULT 100,
                        attack INTEGER NOT NULL DEFAULT 10,
                        defense INTEGER NOT NULL DEFAULT 5,
                        level INTEGER NOT NULL DEFAULT 1,
                        exp INTEGER NOT NULL DEFAULT 0,
                        streak INTEGER NOT NULL DEFAULT 0,
                        last_sign_date TEXT DEFAULT NULL,
                        last_work_date TEXT DEFAULT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # -------------------------------------------------
                # 背包
                # -------------------------------------------------

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS inventory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        rpg_id INTEGER NOT NULL,
                        item_id TEXT NOT NULL,
                        item_name TEXT NOT NULL,
                        amount INTEGER NOT NULL DEFAULT 0,
                        UNIQUE(rpg_id, item_id)
                    )
                """)

                # -------------------------------------------------
                # 商城
                # -------------------------------------------------

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS shop_items (
                        item_id TEXT PRIMARY KEY,
                        item_name TEXT NOT NULL,
                        price INTEGER NOT NULL DEFAULT 0,
                        sell_price INTEGER NOT NULL DEFAULT 0,
                        description TEXT DEFAULT ''
                    )
                """)

                # -------------------------------------------------
                # PVE 战斗
                # -------------------------------------------------

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS battles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        uid TEXT UNIQUE NOT NULL,
                        enemy_type TEXT NOT NULL,
                        enemy_name TEXT NOT NULL,
                        enemy_hp INTEGER NOT NULL,
                        enemy_max_hp INTEGER NOT NULL,
                        enemy_attack INTEGER NOT NULL,
                        enemy_defense INTEGER NOT NULL,
                        reward_money INTEGER NOT NULL DEFAULT 0,
                        reward_exp INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # -------------------------------------------------
                # 娱乐 / 彩票 / 兑换码（兼容旧数据库自动升级）
                # -------------------------------------------------
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS rpg_system (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS lottery_tickets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        uid TEXT NOT NULL,
                        group_origin TEXT NOT NULL,
                        number TEXT NOT NULL,
                        amount INTEGER NOT NULL DEFAULT 1,
                        draw_key TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS redemption_codes (
                        code TEXT PRIMARY KEY,
                        item_id TEXT NOT NULL,
                        item_name TEXT NOT NULL,
                        amount INTEGER NOT NULL,
                        reward_type TEXT NOT NULL DEFAULT 'item',
                        currency_type TEXT DEFAULT NULL,
                        currency_amount INTEGER NOT NULL DEFAULT 0,
                        rewards_json TEXT DEFAULT NULL,
                        max_uses INTEGER NOT NULL DEFAULT 1,
                        use_count INTEGER NOT NULL DEFAULT 0,
                        expires_at TEXT DEFAULT NULL,
                        used INTEGER NOT NULL DEFAULT 0,
                        used_by TEXT DEFAULT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        used_at TEXT DEFAULT NULL
                    )
                """)
                # 兼容旧数据库
                for alter in [
                    "ALTER TABLE users ADD COLUMN diamonds INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE users ADD COLUMN bank_last_interest TEXT DEFAULT NULL",
                    "ALTER TABLE redemption_codes ADD COLUMN reward_type TEXT NOT NULL DEFAULT 'item'",
                    "ALTER TABLE redemption_codes ADD COLUMN currency_type TEXT DEFAULT NULL",
                    "ALTER TABLE redemption_codes ADD COLUMN currency_amount INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE redemption_codes ADD COLUMN rewards_json TEXT DEFAULT NULL",
                    "ALTER TABLE redemption_codes ADD COLUMN max_uses INTEGER NOT NULL DEFAULT 1",
                    "ALTER TABLE redemption_codes ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE redemption_codes ADD COLUMN expires_at TEXT DEFAULT NULL",
                ]:
                    try:
                        conn.execute(alter)
                    except Exception:
                        pass
                # 旧版兑换码只有 used 字段：已使用的旧码应视为已经消耗1次，避免升级后再次被兑换。
                try:
                    conn.execute("UPDATE redemption_codes SET use_count=1 WHERE used=1 AND COALESCE(use_count,0)=0")
                except Exception:
                    pass
                defaults = {
                    "lottery_pool": "5000",
                    "lottery_base_pool": "5000",
                    "lottery_last_reset": datetime.now().isoformat(timespec="seconds"),
                    "lottery_last_draw": "",
                    "lottery_last_low_check": datetime.now().isoformat(timespec="seconds"),
                    "bank_interest_rate": "0.005",
                }
                for k,v in defaults.items():
                    conn.execute("INSERT OR IGNORE INTO rpg_system(key,value) VALUES (?,?)", (k,v))

                self.init_shop(conn)

                conn.commit()

            finally:

                conn.close()

    # =====================================================
    # 初始化商城
    # =====================================================

    def init_shop(self, conn):
        # 所有物品都能在商城购买，并且都具有实际效果。
        items = [
            ("hp_potion", "小型生命药水", 100, 50, "使用后恢复30点生命值。"),
            ("bread", "面包", 30, 10, "使用后恢复15点生命值。"),
            ("attack_book", "攻击秘籍", 500, 250, "使用后永久攻击力+2。"),
            ("defense_book", "防御秘籍", 500, 250, "使用后永久防御力+1。"),
            ("full_potion", "高级生命药水", 350, 175, "使用后恢复80点生命值。"),
            ("maxhp_book", "生命强化书", 800, 400, "使用后最大生命值永久+10，并立即增加10点当前生命。"),
            ("attack_potion", "狂战药剂", 600, 300, "使用后本场/下一次PVE战斗攻击力临时+5。"),
            ("defense_potion", "铁壁药剂", 600, 300, "使用后本场/下一次PVE战斗防御力临时+5。"),
            ("lucky_ticket", "幸运券", 300, 150, "使用后获得一次娱乐玩法的额外幸运加成。"),
            ("lottery_ticket", "彩票券", 200, 100, "用于彩票：也可直接用“买彩票 12345678”。"),
        ]
        for item in items:
            conn.execute("""
                INSERT INTO shop_items(item_id,item_name,price,sell_price,description)
                VALUES (?,?,?,?,?)
                ON CONFLICT(item_id) DO UPDATE SET
                    item_name=excluded.item_name, price=excluded.price,
                    sell_price=excluded.sell_price, description=excluded.description
            """, item)

    # =====================================================
    # RPG ID
    # =====================================================

    def next_rpg_id(self, conn):

        row = conn.execute("""
            SELECT MAX(rpg_id) AS max_id
            FROM users
        """).fetchone()

        if (
            not row
            or row["max_id"] is None
        ):
            return 10001

        return max(
            10001,
            int(row["max_id"]) + 1
        )

    # =====================================================
    # 获取用户
    # =====================================================

    def get_user(self, uid):

        uid = str(uid)

        with self.lock:

            conn = self.connect()

            try:

                return conn.execute("""
                    SELECT *
                    FROM users
                    WHERE uid = ?
                """, (uid,)).fetchone()

            finally:

                conn.close()

    # =====================================================
    # 创建 / 确保用户
    # =====================================================

    def ensure_user(self, uid, name=""):

        uid = str(uid)
        name = str(name or uid)

        with self.lock:

            conn = self.connect()

            try:

                user = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE uid = ?
                """, (uid,)).fetchone()

                if user:

                    conn.execute("""
                        UPDATE users
                        SET name = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE uid = ?
                    """, (
                        name,
                        uid
                    ))

                    conn.commit()

                    return conn.execute("""
                        SELECT *
                        FROM users
                        WHERE uid = ?
                    """, (uid,)).fetchone()

                rpg_id = self.next_rpg_id(conn)

                conn.execute("""
                    INSERT INTO users
                    (
                        rpg_id,
                        uid,
                        name
                    )
                    VALUES (?, ?, ?)
                """, (
                    rpg_id,
                    uid,
                    name
                ))

                conn.commit()

                return conn.execute("""
                    SELECT *
                    FROM users
                    WHERE uid = ?
                """, (uid,)).fetchone()

            finally:

                conn.close()

    # =====================================================
    # 兼容旧接口
    # =====================================================

    def create_user(self, uid, name=""):
        return self.ensure_user(uid, name)

    def get_player(self, uid):
        return self.get_user(uid)

    def get_user_by_uid(self, uid):
        return self.get_user(uid)

    # =====================================================
    # 根据 RPG ID 获取用户
    # =====================================================

    def get_user_by_rpg_id(self, rpg_id):

        try:
            rpg_id = int(rpg_id)
        except Exception:
            return None

        with self.lock:

            conn = self.connect()

            try:

                return conn.execute("""
                    SELECT *
                    FROM users
                    WHERE rpg_id = ?
                """, (rpg_id,)).fetchone()

            finally:

                conn.close()

    # =====================================================
    # 修改昵称
    # =====================================================

    def update_name(self, uid, name):

        with self.lock:

            conn = self.connect()

            try:

                conn.execute("""
                    UPDATE users
                    SET name = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE uid = ?
                """, (
                    str(name),
                    str(uid)
                ))

                conn.commit()

            finally:

                conn.close()

    # =====================================================
    # 增加金币
    # =====================================================

    def add_money(self, uid, amount):

        amount = int(amount)

        with self.lock:

            conn = self.connect()

            try:

                conn.execute("""
                    UPDATE users
                    SET money = money + ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE uid = ?
                """, (
                    amount,
                    str(uid)
                ))

                conn.commit()

            finally:

                conn.close()

    # =====================================================
    # 扣除金币
    # =====================================================

    def remove_money(self, uid, amount):

        amount = int(amount)

        if amount <= 0:
            return False

        with self.lock:

            conn = self.connect()

            try:

                user = conn.execute("""
                    SELECT money
                    FROM users
                    WHERE uid = ?
                """, (
                    str(uid),
                )).fetchone()

                if not user:
                    return False

                if int(user["money"]) < amount:
                    return False

                conn.execute("""
                    UPDATE users
                    SET money = money - ?
                    WHERE uid = ?
                """, (
                    amount,
                    str(uid)
                ))

                conn.commit()

                return True

            finally:

                conn.close()

    # =====================================================
    # 银行利息
    # =====================================================

    def apply_bank_interest(self, uid):
        uid = str(uid)
        now = datetime.now()
        with self.lock:
            conn = self.connect()
            try:
                user = conn.execute("SELECT bank, bank_last_interest FROM users WHERE uid=?", (uid,)).fetchone()
                if not user:
                    return False, 0, "用户不存在"
                last = user["bank_last_interest"]
                if last:
                    try:
                        last_dt = datetime.fromisoformat(str(last))
                    except Exception:
                        last_dt = now
                else:
                    last_dt = now
                days = max(0, (now.date() - last_dt.date()).days)
                if not last:
                    conn.execute("UPDATE users SET bank_last_interest=?, updated_at=CURRENT_TIMESTAMP WHERE uid=?", (now.isoformat(timespec="seconds"), uid))
                    conn.commit()
                    return True, 0, "银行利息已开始计算"
                if days <= 0:
                    return True, 0, "今日利息已结算"
                rate = float(self.get_system_value("bank_interest_rate", "0.005") or 0.005)
                rate = max(0.0, min(rate, 0.1))
                balance = int(user["bank"] or 0)
                interest = 0
                for _ in range(days):
                    interest += int(balance * rate)
                    balance += int(balance * rate)
                if interest > 0:
                    conn.execute("UPDATE users SET bank=?, bank_last_interest=?, updated_at=CURRENT_TIMESTAMP WHERE uid=?", (balance, now.isoformat(timespec="seconds"), uid))
                else:
                    conn.execute("UPDATE users SET bank_last_interest=?, updated_at=CURRENT_TIMESTAMP WHERE uid=?", (now.isoformat(timespec="seconds"), uid))
                conn.commit()
                return True, interest, f"已结算{days}天利息"
            finally:
                conn.close()

    def get_bank_info(self, uid):
        uid = str(uid)
        self.apply_bank_interest(uid)
        user = self.get_user(uid)
        if not user:
            return None
        rate = float(self.get_system_value("bank_interest_rate", "0.005") or 0.005)
        return {
            "bank": int(user["bank"]),
            "rate": rate,
            "last_interest": user["bank_last_interest"],
        }

    # =====================================================
    # 转账
    # =====================================================

    def transfer(
        self,
        from_uid,
        to_uid,
        amount
    ):

        amount = int(amount)

        from_uid = str(from_uid)
        to_uid = str(to_uid)

        if amount <= 0:
            return False, "金额必须大于0"

        if from_uid == to_uid:
            return False, "不能给自己转账"

        with self.lock:

            conn = self.connect()

            try:

                conn.execute("BEGIN")

                sender = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE uid = ?
                """, (
                    from_uid,
                )).fetchone()

                receiver = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE uid = ?
                """, (
                    to_uid,
                )).fetchone()

                if not sender:

                    conn.rollback()

                    return False, "付款方不存在"

                if not receiver:

                    conn.rollback()

                    return False, "收款方不存在"

                if int(sender["money"]) < amount:

                    conn.rollback()

                    return False, "余额不足"

                conn.execute("""
                    UPDATE users
                    SET money = money - ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE uid = ?
                """, (
                    amount,
                    from_uid
                ))

                conn.execute("""
                    UPDATE users
                    SET money = money + ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE uid = ?
                """, (
                    amount,
                    to_uid
                ))

                conn.commit()

                return True, "转账成功"

            except Exception:

                conn.rollback()

                raise

            finally:

                conn.close()

    # =====================================================
    # 签到
    # =====================================================

    def sign_in(self, uid):

        today = date.today().isoformat()

        uid = str(uid)

        with self.lock:

            conn = self.connect()

            try:

                user = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                if not user:
                    return False, "用户不存在"

                last_sign = user["last_sign_date"]

                if last_sign == today:

                    return False, "今天已经签到过了"

                old_streak = int(
                    user["streak"] or 0
                )

                streak = old_streak + 1

                # -------------------------------------------------
                # 判断是否连续
                # -------------------------------------------------

                if last_sign:

                    try:

                        last_date = date.fromisoformat(
                            str(last_sign)
                        )

                        diff = (
                            date.today()
                            - last_date
                        ).days

                        if diff > 1:
                            streak = 1

                    except Exception:

                        streak = 1

                if streak >= 7:
                    reward = 200

                elif streak >= 3:
                    reward = 150

                else:
                    reward = 100

                conn.execute("""
                    UPDATE users
                    SET money = money + ?,
                        streak = ?,
                        last_sign_date = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE uid = ?
                """, (
                    reward,
                    streak,
                    today,
                    uid
                ))

                conn.commit()

                return True, {
                    "reward": reward,
                    "streak": streak
                }

            finally:

                conn.close()

    # =====================================================
    # 打工
    # =====================================================

    def work(self, uid):

        today = date.today().isoformat()

        uid = str(uid)

        with self.lock:

            conn = self.connect()

            try:

                user = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                if not user:
                    return False, "用户不存在"

                if user["last_work_date"] == today:

                    return False, "今天已经打工过了"

                reward = random.randint(
                    80,
                    200
                )

                conn.execute("""
                    UPDATE users
                    SET money = money + ?,
                        last_work_date = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE uid = ?
                """, (
                    reward,
                    today,
                    uid
                ))

                conn.commit()

                return True, reward

            finally:

                conn.close()

    # =====================================================
    # 银行存款
    # =====================================================

    def deposit(self, uid, amount):

        amount = int(amount)

        uid = str(uid)

        if amount <= 0:
            return False, "金额必须大于0"

        with self.lock:

            conn = self.connect()

            try:

                user = conn.execute("""
                    SELECT money
                    FROM users
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                if not user:
                    return False, "用户不存在"

                if int(user["money"]) < amount:

                    return False, "现金余额不足"

                conn.execute("""
                    UPDATE users
                    SET money = money - ?,
                        bank = bank + ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE uid = ?
                """, (
                    amount,
                    amount,
                    uid
                ))

                conn.commit()

                return True, "存款成功"

            finally:

                conn.close()

    # =====================================================
    # 银行取款
    # =====================================================

    def withdraw(self, uid, amount):

        amount = int(amount)

        uid = str(uid)

        if amount <= 0:
            return False, "金额必须大于0"

        with self.lock:

            conn = self.connect()

            try:

                user = conn.execute("""
                    SELECT bank
                    FROM users
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                if not user:
                    return False, "用户不存在"

                if int(user["bank"]) < amount:

                    return False, "银行余额不足"

                conn.execute("""
                    UPDATE users
                    SET bank = bank - ?,
                        money = money + ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE uid = ?
                """, (
                    amount,
                    amount,
                    uid
                ))

                conn.commit()

                return True, "取款成功"

            finally:

                conn.close()

    # =====================================================
    # 属性更新
    # =====================================================

    def update_stats(
        self,
        uid,
        hp=None,
        max_hp=None,
        attack=None,
        defense=None,
        level=None,
        exp=None
    ):

        fields = []
        values = []

        if hp is not None:

            fields.append("hp = ?")
            values.append(int(hp))

        if max_hp is not None:

            fields.append("max_hp = ?")
            values.append(int(max_hp))

        if attack is not None:

            fields.append("attack = ?")
            values.append(int(attack))

        if defense is not None:

            fields.append("defense = ?")
            values.append(int(defense))

        if level is not None:

            fields.append("level = ?")
            values.append(int(level))

        if exp is not None:

            fields.append("exp = ?")
            values.append(int(exp))

        if not fields:
            return

        fields.append(
            "updated_at = CURRENT_TIMESTAMP"
        )

        values.append(str(uid))

        with self.lock:

            conn = self.connect()

            try:

                sql = (
                    "UPDATE users SET "
                    + ", ".join(fields)
                    + " WHERE uid = ?"
                )

                conn.execute(
                    sql,
                    values
                )

                conn.commit()

            finally:

                conn.close()

    # =====================================================
    # 经验需求
    # =====================================================

    def exp_required(self, level):

        level = max(
            1,
            int(level)
        )

        return 100 + (
            level - 1
        ) * 75

    # =====================================================
    # 增加经验
    # =====================================================

    def add_exp(self, uid, amount):

        amount = int(amount)

        if amount <= 0:

            return {
                "exp": 0,
                "level": 1,
                "level_ups": 0,
                "old_level": 1
            }

        uid = str(uid)

        with self.lock:

            conn = self.connect()

            try:

                user = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                if not user:
                    return None

                old_level = int(
                    user["level"]
                )

                level = old_level

                exp = (
                    int(user["exp"])
                    + amount
                )

                level_ups = 0

                while exp >= self.exp_required(level):

                    exp -= self.exp_required(level)

                    level += 1

                    level_ups += 1

                max_hp = int(
                    user["max_hp"]
                )

                attack = int(
                    user["attack"]
                )

                defense = int(
                    user["defense"]
                )

                if level_ups > 0:

                    max_hp += (
                        level_ups * 10
                    )

                    attack += (
                        level_ups * 2
                    )

                    defense += level_ups

                    hp = max_hp

                else:

                    hp = min(
                        int(user["hp"]),
                        max_hp
                    )

                conn.execute("""
                    UPDATE users
                    SET level = ?,
                        exp = ?,
                        max_hp = ?,
                        hp = ?,
                        attack = ?,
                        defense = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE uid = ?
                """, (
                    level,
                    exp,
                    max_hp,
                    hp,
                    attack,
                    defense,
                    uid
                ))

                conn.commit()

                return {
                    "exp": exp,
                    "level": level,
                    "level_ups": level_ups,
                    "old_level": old_level,
                    "max_hp": max_hp,
                    "attack": attack,
                    "defense": defense
                }

            finally:

                conn.close()

    # =====================================================
    # 背包
    # =====================================================

    def get_inventory(self, uid):

        uid = str(uid)

        with self.lock:

            conn = self.connect()

            try:

                return conn.execute("""
                    SELECT inventory.*
                    FROM inventory
                    INNER JOIN users
                    ON users.rpg_id = inventory.rpg_id
                    WHERE users.uid = ?
                    AND inventory.amount > 0
                    ORDER BY inventory.id ASC
                """, (
                    uid,
                )).fetchall()

            finally:

                conn.close()

    # =====================================================
    # 背包物品
    # =====================================================

    def get_inventory_item(
        self,
        uid,
        item_id
    ):

        uid = str(uid)
        item_id = str(item_id)

        with self.lock:

            conn = self.connect()

            try:

                return conn.execute("""
                    SELECT inventory.*
                    FROM inventory
                    INNER JOIN users
                    ON users.rpg_id = inventory.rpg_id
                    WHERE users.uid = ?
                    AND inventory.item_id = ?
                    AND inventory.amount > 0
                """, (
                    uid,
                    item_id
                )).fetchone()

            finally:

                conn.close()

    # =====================================================
    # 添加物品
    # =====================================================

    def add_item(
        self,
        uid,
        item_id,
        item_name,
        amount=1
    ):

        amount = int(amount)

        if amount <= 0:
            return False

        uid = str(uid)

        with self.lock:

            conn = self.connect()

            try:

                user = conn.execute("""
                    SELECT rpg_id
                    FROM users
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                if not user:
                    return False

                conn.execute("""
                    INSERT INTO inventory
                    (
                        rpg_id,
                        item_id,
                        item_name,
                        amount
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(rpg_id, item_id)
                    DO UPDATE SET
                        amount = inventory.amount
                        + excluded.amount
                """, (
                    user["rpg_id"],
                    str(item_id),
                    str(item_name),
                    amount
                ))

                conn.commit()

                return True

            finally:

                conn.close()

    # =====================================================
    # 删除物品
    # =====================================================

    def remove_item(
        self,
        uid,
        item_id,
        amount=1
    ):

        amount = int(amount)

        if amount <= 0:
            return False

        uid = str(uid)

        with self.lock:

            conn = self.connect()

            try:

                user = conn.execute("""
                    SELECT rpg_id
                    FROM users
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                if not user:
                    return False

                item = conn.execute("""
                    SELECT amount
                    FROM inventory
                    WHERE rpg_id = ?
                    AND item_id = ?
                """, (
                    user["rpg_id"],
                    str(item_id)
                )).fetchone()

                if not item:
                    return False

                if int(item["amount"]) < amount:
                    return False

                conn.execute("""
                    UPDATE inventory
                    SET amount = amount - ?
                    WHERE rpg_id = ?
                    AND item_id = ?
                """, (
                    amount,
                    user["rpg_id"],
                    str(item_id)
                ))

                conn.execute("""
                    DELETE FROM inventory
                    WHERE amount <= 0
                """)

                conn.commit()

                return True

            finally:

                conn.close()

    # =====================================================
    # 使用物品
    # =====================================================

    def use_item(self, uid, item_id):
        uid, item_id = str(uid), str(item_id)
        item = self.get_inventory_item(uid, item_id)
        user = self.get_user(uid)
        if not item: return False, "你没有这个物品"
        if not user: return False, "用户不存在"

        effects = {
            "hp_potion": (30, "❤️ 恢复30点生命"),
            "bread": (15, "🍞 恢复15点生命"),
            "full_potion": (80, "🧪 恢复80点生命"),
        }
        if item_id in effects:
            if int(user["hp"]) >= int(user["max_hp"]): return False, "你的生命值已经满了"
            heal=min(effects[item_id][0], int(user["max_hp"])-int(user["hp"]))
            if not self.remove_item(uid,item_id,1): return False,"物品使用失败"
            self.update_stats(uid,hp=int(user["hp"])+heal)
            return True,{"message":f"{effects[item_id][1]}（实际 +{heal}）"}
        if item_id == "attack_book":
            if not self.remove_item(uid,item_id,1): return False,"物品使用失败"
            self.update_stats(uid,attack=int(user["attack"])+2); return True,{"message":"⚔️ 攻击永久 +2"}
        if item_id == "defense_book":
            if not self.remove_item(uid,item_id,1): return False,"物品使用失败"
            self.update_stats(uid,defense=int(user["defense"])+1); return True,{"message":"🛡️ 防御永久 +1"}
        if item_id == "maxhp_book":
            if not self.remove_item(uid,item_id,1): return False,"物品使用失败"
            self.update_stats(uid,max_hp=int(user["max_hp"])+10,hp=min(int(user["max_hp"])+10,int(user["hp"])+10))
            return True,{"message":"❤️ 最大生命值永久 +10，并恢复10点生命"}
        if item_id in ("attack_potion","defense_potion"):
            if not self.remove_item(uid,item_id,1): return False,"物品使用失败"
            key="temp_attack" if item_id=="attack_potion" else "temp_defense"
            self.set_system_value(f"buff:{uid}:{key}","5")
            return True,{"message":"⚔️ 本次/下一次PVE战斗获得 +5 攻击" if key=="temp_attack" else "🛡️ 本次/下一次PVE战斗获得 +5 防御"}
        if item_id == "lucky_ticket":
            if not self.remove_item(uid,item_id,1): return False,"物品使用失败"
            self.set_system_value(f"luck:{uid}",str(int(self.get_system_value(f"luck:{uid}","0"))+1))
            return True,{"message":"🍀 幸运+1，下一次娱乐玩法生效"}
        if item_id == "lottery_ticket":
            return False,"彩票券请使用：买彩票 12345678"
        return False,"这个物品暂时无法使用"

    # ---------------- 娱乐 / 彩票 / 兑换码 ----------------
    def get_system_value(self,key,default=""):
        with self.lock:
            c=self.connect()
            try:
                r=c.execute("SELECT value FROM rpg_system WHERE key=?",(str(key),)).fetchone()
                return r["value"] if r else default
            finally: c.close()

    def set_system_value(self,key,value):
        with self.lock:
            c=self.connect(); c.execute("INSERT INTO rpg_system(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(key),str(value))); c.commit(); c.close()

    def add_lottery_pool(self,amount):
        # 支持增加/扣除奖池，扣除后最低为0。
        amount=int(amount)
        new_pool=max(0,self.get_lottery_pool()+amount)
        self.set_system_value("lottery_pool",str(new_pool))
        return new_pool
    def get_lottery_pool(self): return int(self.get_system_value("lottery_pool","5000") or 5000)
    def reset_lottery_pool_if_low(self):
        now=datetime.now(); last=self.get_system_value("lottery_last_low_check","")
        try: old=datetime.fromisoformat(last)
        except Exception: old=now
        if (now-old).total_seconds() >= 3*86400 and self.get_lottery_pool()<int(self.get_system_value("lottery_base_pool","5000") or 5000):
            self.set_system_value("lottery_pool", self.get_system_value("lottery_base_pool","5000") or "5000")
        self.set_system_value("lottery_last_low_check",now.isoformat(timespec="seconds"))
        return self.get_lottery_pool()

    def buy_lottery_ticket(self,uid,group_origin,number,price=200):
        number=str(number)
        if len(number)!=8 or not number.isdigit(): return False,"中奖码必须是8位数字"
        if not self.remove_money(uid,price): return False,"金币不足"
        now=datetime.now(); draw_key=now.strftime("%Y%m%d%H")
        # 下一个有效开奖时段：3小时一开，仅06-21点
        h=now.hour
        slots=[6,9,12,15,18,21]
        slot=max([x for x in slots if x<=h],default=6)
        if h<6: slot=6
        if h>=22: slot=6; draw_key=(now+timedelta(days=1)).strftime("%Y%m%d"); draw_key+=f"{slot:02d}"
        else: draw_key=now.strftime("%Y%m%d")+f"{slot:02d}"
        with self.lock:
            c=self.connect(); c.execute("INSERT INTO lottery_tickets(uid,group_origin,number,amount,draw_key) VALUES(?,?,?,?,?)",(str(uid),str(group_origin),number,1,draw_key)); c.commit(); c.close()
        self.add_lottery_pool(price); return True,draw_key

    def draw_lottery(self,draw_key):
        with self.lock:
            c=self.connect(); tickets=c.execute("SELECT * FROM lottery_tickets WHERE draw_key=?",(draw_key,)).fetchall()
            if not tickets: c.close(); return None
            winning=f"{random.randint(0,99999999):08d}"; pool=self.get_lottery_pool(); winners=[]; paid=0
            for t in tickets:
                n=t["number"]; matches=0
                for a,b in zip(reversed(n),reversed(winning)):
                    if a==b: matches+=1
                    else: break
                if matches>=4:
                    mult={4:1,5:2,6:3,7:4,8:4}[matches]; reward=min(pool//max(1,len([x for x in tickets if x["number"]==n])),pool*mult//10)
                    reward=max(0,reward); self._add_money_conn(c,t["uid"],reward); paid+=reward; winners.append((t["uid"],t["group_origin"],matches,mult,reward))
            self.set_system_value("lottery_pool",str(max(0,pool-paid)))
            self.set_system_value("lottery_last_draw",draw_key)
            c.commit(); c.close(); return {"number":winning,"pool_before":pool,"paid":paid,"winners":winners,"origins":list(dict.fromkeys(t["group_origin"] for t in tickets))}

    def _add_money_conn(self,c,uid,amount): c.execute("UPDATE users SET money=money+?,updated_at=CURRENT_TIMESTAMP WHERE uid=?",(int(amount),str(uid)))

    def _validate_code_options(self, max_uses=1, expires_at=None):
        """校验兑换码使用次数和有效期。0=无限次；None=永久。"""
        try:
            max_uses = int(max_uses)
        except Exception:
            return False, "使用次数必须是整数"
        if max_uses < 0 or max_uses > 999999999:
            return False, "使用次数范围：0-999999999，0表示无限"
        expires_at = str(expires_at or "").strip() or None
        if expires_at:
            try:
                dt = datetime.fromisoformat(expires_at)
                if dt <= datetime.now():
                    return False, "使用期限必须晚于当前时间"
                expires_at = dt.isoformat(timespec="seconds")
            except Exception:
                return False, "使用期限格式错误，请使用 YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DDTHH:MM"
        return True, (max_uses, expires_at)

    def create_redemption_code(self,item_id,amount,max_uses=1,expires_at=None):
        import secrets
        item=self.get_shop_item(item_id)
        if not item: return False,"商品不存在"
        amount=int(amount)
        if amount<=0 or amount>999999999: return False,"数量范围：1-999999999"
        ok,opt=self._validate_code_options(max_uses,expires_at)
        if not ok: return False,opt
        max_uses,expires_at=opt
        code="RPG-"+secrets.token_hex(4).upper()
        with self.lock:
            c=self.connect()
            c.execute("INSERT INTO redemption_codes(code,item_id,item_name,amount,reward_type,max_uses,use_count,expires_at) VALUES(?,?,?,?,?,?,?,?)",(code,item_id,item["item_name"],amount,"item",max_uses,0,expires_at))
            c.commit(); c.close()
        return True,code

    def create_currency_redemption_code(self,currency_type,amount,max_uses=1,expires_at=None):
        import secrets
        currency_type=str(currency_type).lower()
        names={"money":"金币", "diamonds":"钻石"}
        if currency_type not in names: return False,"只支持：money(金币) 或 diamonds(钻石)"
        amount=int(amount)
        if amount<=0 or amount>999999999: return False,"数量范围：1-999999999"
        ok,opt=self._validate_code_options(max_uses,expires_at)
        if not ok: return False,opt
        max_uses,expires_at=opt
        code="RPG-"+secrets.token_hex(4).upper()
        with self.lock:
            c=self.connect()
            c.execute("INSERT INTO redemption_codes(code,item_id,item_name,amount,reward_type,currency_type,currency_amount,rewards_json,max_uses,use_count,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(code,f"currency:{currency_type}",names[currency_type],amount,"currency",currency_type,amount,None,max_uses,0,expires_at))
            c.commit(); c.close()
        return True,code

    def create_bundle_redemption_code(self,rewards,max_uses=1,expires_at=None):
        """生成多奖励礼包兑换码。Web可配置次数/期限；QQ聊天生成的礼包功能不开放。"""
        import secrets
        ok,opt=self._validate_code_options(max_uses,expires_at)
        if not ok: return False,opt
        max_uses,expires_at=opt
        clean=[]
        for r in rewards or []:
            if not isinstance(r,dict): return False,"礼包奖励格式错误"
            try: amount=int(r.get("amount",0))
            except Exception: return False,"奖励数量必须是数字"
            if amount<=0 or amount>999999999: return False,"每项数量必须在1-999999999之间"
            typ=str(r.get("type","item")).lower()
            if typ=="currency":
                cur=str(r.get("currency","money")).lower(); names={"money":"金币","diamonds":"钻石"}
                if cur not in names: return False,"货币只支持金币或钻石"
                clean.append({"type":"currency","currency":cur,"amount":amount,"name":names[cur]})
            elif typ=="item":
                item_id=str(r.get("item_id","")); item=self.get_shop_item(item_id)
                if not item: return False,f"商品不存在：{item_id}"
                clean.append({"type":"item","item_id":item_id,"amount":amount,"name":item["item_name"]})
            else: return False,"奖励类型只能是 item 或 currency"
        if not clean: return False,"礼包至少需要1项奖励"
        if len(clean)>50: return False,"一个礼包最多50种奖励"
        payload=json.dumps(clean,ensure_ascii=False,separators=(",",":"))
        code="RPG-"+secrets.token_hex(4).upper()
        summary=" + ".join(f'{x["name"]}×{x["amount"]}' for x in clean)
        with self.lock:
            c=self.connect()
            c.execute("INSERT INTO redemption_codes(code,item_id,item_name,amount,reward_type,rewards_json,max_uses,use_count,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",(code,"bundle",summary,1,"bundle",payload,max_uses,0,expires_at))
            c.commit(); c.close()
        return True,code

    def create_bundle_code(self,rewards,max_uses=1,expires_at=None):
        return self.create_bundle_redemption_code(rewards,max_uses,expires_at)

    def add_diamonds(self,uid,amount):
        with self.lock:
            c=self.connect(); c.execute("UPDATE users SET diamonds=diamonds+?,updated_at=CURRENT_TIMESTAMP WHERE uid=?",(int(amount),str(uid))); c.commit(); c.close()

    def get_currency_balance(self,uid,currency_type):
        field="diamonds" if str(currency_type).lower()=="diamonds" else "money"
        with self.lock:
            c=self.connect(); r=c.execute(f"SELECT {field} AS value FROM users WHERE uid=?",(str(uid),)).fetchone(); c.close()
        return int(r["value"]) if r else 0

    def redeem_code(self,uid,code):
        code=str(code).upper().strip()
        with self.lock:
            c=self.connect()
            try:
                c.execute("BEGIN IMMEDIATE")
                r=c.execute("SELECT * FROM redemption_codes WHERE code=?",(code,)).fetchone()
                if not r:
                    c.rollback(); return False,"兑换码不存在"
                max_uses=int(r["max_uses"] if r["max_uses"] is not None else 1)
                use_count=int(r["use_count"] if r["use_count"] is not None else (1 if r["used"] else 0))
                if max_uses != 0 and use_count >= max_uses:
                    c.rollback(); return False,"兑换码使用次数已耗尽"
                expires_at=r["expires_at"] if "expires_at" in r.keys() else None
                if expires_at:
                    try:
                        if datetime.now() >= datetime.fromisoformat(str(expires_at)):
                            c.rollback(); return False,"兑换码已过期"
                    except Exception:
                        c.rollback(); return False,"兑换码有效期数据异常"
                user=c.execute("SELECT * FROM users WHERE uid=?",(str(uid),)).fetchone()
                if not user:
                    c.rollback(); return False,"请先注册RPG"
                rewards=[]
                if r["reward_type"]=="bundle" and r["rewards_json"]:
                    try: rewards=json.loads(r["rewards_json"])
                    except Exception: c.rollback(); return False,"兑换码礼包数据损坏"
                elif r["reward_type"]=="currency":
                    rewards=[{"type":"currency","currency":r["currency_type"],"amount":int(r["currency_amount"]),"name":r["item_name"]}]
                else:
                    rewards=[{"type":"item","item_id":r["item_id"],"amount":int(r["amount"]),"name":r["item_name"]}]
                for x in rewards:
                    amount=int(x.get("amount",0)); typ=x.get("type")
                    if amount<=0: c.rollback(); return False,"兑换码奖励数量异常"
                    if typ=="currency":
                        field="diamonds" if x.get("currency")=="diamonds" else "money"
                        c.execute(f"UPDATE users SET {field}={field}+?,updated_at=CURRENT_TIMESTAMP WHERE uid=?",(amount,str(uid)))
                    elif typ=="item":
                        item=self.get_shop_item(str(x.get("item_id","")))
                        if not item: c.rollback(); return False,f"礼包包含不存在的物品：{x.get('item_id')}"
                        c.execute("INSERT INTO inventory(rpg_id,item_id,item_name,amount) VALUES(?,?,?,?) ON CONFLICT(rpg_id,item_id) DO UPDATE SET amount=inventory.amount+excluded.amount",(user["rpg_id"],item["item_id"],item["item_name"],amount))
                    else:
                        c.rollback(); return False,"兑换码奖励类型异常"
                new_count=use_count+1
                is_used=1 if max_uses != 0 and new_count >= max_uses else 0
                cur=c.execute("UPDATE redemption_codes SET use_count=?,used=?,used_by=?,used_at=CURRENT_TIMESTAMP WHERE code=? AND use_count=?",(new_count,is_used,str(uid),code,use_count))
                if cur.rowcount != 1:
                    c.rollback(); return False,"兑换码已被其他人抢先使用"
                c.commit()
                return True,{"type":r["reward_type"],"item":r["item_name"],"amount":int(r["amount"]),"rewards":rewards,"use_count":new_count,"max_uses":max_uses,"expires_at":expires_at}
            except Exception:
                c.rollback()
                raise
            finally:
                c.close()

    def admin_code_stats(self):
        with self.lock:
            c=self.connect()
            unused=c.execute("SELECT COUNT(*) n FROM redemption_codes WHERE used=0 AND (expires_at IS NULL OR expires_at>datetime('now')) AND (max_uses=0 OR use_count<max_uses)").fetchone()["n"]
            used=c.execute("SELECT COUNT(*) n FROM redemption_codes WHERE used=1 OR (expires_at IS NOT NULL AND expires_at<=datetime('now')) OR (max_uses>0 AND use_count>=max_uses)").fetchone()["n"]
            users=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            pool=self.get_lottery_pool(); c.close()
        return {"unused_codes":int(unused),"used_codes":int(used),"users":int(users),"lottery_pool":int(pool)}

    def admin_codes(self,limit=100):
        with self.lock:
            c=self.connect(); rows=c.execute("SELECT code,item_name,amount,reward_type,currency_type,rewards_json,max_uses,use_count,expires_at,used,used_by,created_at,used_at FROM redemption_codes ORDER BY created_at DESC LIMIT ?",(int(limit),)).fetchall(); c.close()
        return [dict(r) for r in rows]

    def get_redeemable_items(self): return self.get_shop_items()

    # =====================================================
    # 商城
    # =====================================================

    def get_shop_items(self):

        with self.lock:

            conn = self.connect()

            try:

                return conn.execute("""
                    SELECT *
                    FROM shop_items
                    ORDER BY price ASC
                """).fetchall()

            finally:

                conn.close()

    # =====================================================
    # 商城商品
    # =====================================================

    def get_shop_item(self, item_id):

        with self.lock:

            conn = self.connect()

            try:

                return conn.execute("""
                    SELECT *
                    FROM shop_items
                    WHERE item_id = ?
                """, (
                    str(item_id),
                )).fetchone()

            finally:

                conn.close()

    # =====================================================
    # 购买
    # =====================================================

    def buy_item(
        self,
        uid,
        item_id,
        amount=1
    ):

        amount = int(amount)

        if amount <= 0:
            return False, "数量必须大于0"

        uid = str(uid)
        item_id = str(item_id)

        with self.lock:

            conn = self.connect()

            try:

                conn.execute("BEGIN")

                user = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                item = conn.execute("""
                    SELECT *
                    FROM shop_items
                    WHERE item_id = ?
                """, (
                    item_id,
                )).fetchone()

                if not user:

                    conn.rollback()

                    return False, "用户不存在"

                if not item:

                    conn.rollback()

                    return False, "商品不存在"

                total = (
                    int(item["price"])
                    * amount
                )

                if int(user["money"]) < total:

                    conn.rollback()

                    return False, "余额不足"

                conn.execute("""
                    UPDATE users
                    SET money = money - ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE uid = ?
                """, (
                    total,
                    uid
                ))

                conn.execute("""
                    INSERT INTO inventory
                    (
                        rpg_id,
                        item_id,
                        item_name,
                        amount
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(rpg_id, item_id)
                    DO UPDATE SET
                        amount = inventory.amount
                        + excluded.amount
                """, (
                    user["rpg_id"],
                    item["item_id"],
                    item["item_name"],
                    amount
                ))

                conn.commit()

                return True, {
                    "item": item["item_name"],
                    "amount": amount,
                    "total": total
                }

            except Exception:

                conn.rollback()

                raise

            finally:

                conn.close()

    # =====================================================
    # 出售
    # =====================================================

    def sell_item(
        self,
        uid,
        item_id,
        amount=1
    ):

        amount = int(amount)

        if amount <= 0:
            return False, "数量必须大于0"

        uid = str(uid)
        item_id = str(item_id)

        with self.lock:

            conn = self.connect()

            try:

                conn.execute("BEGIN")

                user = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                item = conn.execute("""
                    SELECT *
                    FROM shop_items
                    WHERE item_id = ?
                """, (
                    item_id,
                )).fetchone()

                if not user:

                    conn.rollback()

                    return False, "用户不存在"

                if not item:

                    conn.rollback()

                    return False, "商品不存在"

                inventory = conn.execute("""
                    SELECT amount
                    FROM inventory
                    WHERE rpg_id = ?
                    AND item_id = ?
                """, (
                    user["rpg_id"],
                    item_id
                )).fetchone()

                if not inventory:

                    conn.rollback()

                    return False, "你没有这个物品"

                if int(inventory["amount"]) < amount:

                    conn.rollback()

                    return False, "物品数量不足"

                total = (
                    int(item["sell_price"])
                    * amount
                )

                conn.execute("""
                    UPDATE inventory
                    SET amount = amount - ?
                    WHERE rpg_id = ?
                    AND item_id = ?
                """, (
                    amount,
                    user["rpg_id"],
                    item_id
                ))

                conn.execute("""
                    DELETE FROM inventory
                    WHERE amount <= 0
                """)

                conn.execute("""
                    UPDATE users
                    SET money = money + ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE uid = ?
                """, (
                    total,
                    uid
                ))

                conn.commit()

                return True, {
                    "item": item["item_name"],
                    "amount": amount,
                    "total": total
                }

            except Exception:

                conn.rollback()

                raise

            finally:

                conn.close()

    # =====================================================
    # 随机怪物
    # =====================================================

    def random_monster(self, level):

        level = max(
            1,
            int(level)
        )

        monsters = [

            {
                "type": "slime",
                "name": "史莱姆",
                "hp": 50,
                "attack": 7,
                "defense": 2,
                "money": (40, 80),
                "exp": (20, 35)
            },

            {
                "type": "wolf",
                "name": "狂暴野狼",
                "hp": 80,
                "attack": 12,
                "defense": 4,
                "money": (60, 110),
                "exp": (30, 50)
            },

            {
                "type": "goblin",
                "name": "哥布林",
                "hp": 110,
                "attack": 15,
                "defense": 6,
                "money": (80, 140),
                "exp": (40, 65)
            },

            {
                "type": "orc",
                "name": "兽人战士",
                "hp": 160,
                "attack": 20,
                "defense": 9,
                "money": (120, 200),
                "exp": (60, 90)
            }

        ]

        monster = random.choice(
            monsters
        )

        scale = max(
            0,
            level - 1
        )

        hp = (
            monster["hp"]
            + scale * 15
        )

        attack = (
            monster["attack"]
            + scale * 2
        )

        defense = (
            monster["defense"]
            + scale
        )

        money = (
            random.randint(
                *monster["money"]
            )
            + scale * 10
        )

        exp = (
            random.randint(
                *monster["exp"]
            )
            + scale * 5
        )

        return {
            "type": monster["type"],
            "name": monster["name"],
            "hp": hp,
            "max_hp": hp,
            "attack": attack,
            "defense": defense,
            "money": money,
            "exp": exp
        }

    # =====================================================
    # 获取当前战斗
    # =====================================================

    def get_battle(self, uid):

        uid = str(uid)

        with self.lock:

            conn = self.connect()

            try:

                return conn.execute("""
                    SELECT *
                    FROM battles
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

            finally:

                conn.close()

    # =====================================================
    # 开始 PVE
    # =====================================================

    def start_battle(self, uid):

        uid = str(uid)

        with self.lock:

            conn = self.connect()

            try:

                existing = conn.execute("""
                    SELECT *
                    FROM battles
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                if existing:

                    return False, "你已经在战斗中"

                user = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                if not user:

                    return False, "用户不存在"

                if int(user["hp"]) <= 0:

                    return False, "你的生命值为0，请先恢复生命"

                monster = self.random_monster(
                    user["level"]
                )

                conn.execute("""
                    INSERT INTO battles
                    (
                        uid,
                        enemy_type,
                        enemy_name,
                        enemy_hp,
                        enemy_max_hp,
                        enemy_attack,
                        enemy_defense,
                        reward_money,
                        reward_exp
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    uid,
                    monster["type"],
                    monster["name"],
                    monster["hp"],
                    monster["max_hp"],
                    monster["attack"],
                    monster["defense"],
                    monster["money"],
                    monster["exp"]
                ))

                conn.commit()

                return True, monster

            finally:

                conn.close()

    # =====================================================
    # 战斗攻击
    # =====================================================

    def battle_attack(self, uid):

        uid = str(uid)

        with self.lock:

            conn = self.connect()

            try:

                conn.execute("BEGIN")

                user = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                battle = conn.execute("""
                    SELECT *
                    FROM battles
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                if not user:

                    conn.rollback()

                    return False, "用户不存在"

                if not battle:

                    conn.rollback()

                    return False, "你当前没有战斗"

                player_attack = int(
                    user["attack"]
                )

                player_damage = max(
                    1,
                    player_attack
                    + random.randint(-2, 3)
                    - int(battle["enemy_defense"])
                )

                enemy_hp = max(
                    0,
                    int(battle["enemy_hp"])
                    - player_damage
                )

                # -------------------------------------------------
                # 玩家击杀
                # -------------------------------------------------

                if enemy_hp <= 0:

                    money = int(
                        battle["reward_money"]
                    )

                    exp = int(
                        battle["reward_exp"]
                    )

                    conn.execute("""
                        UPDATE users
                        SET money = money + ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE uid = ?
                    """, (
                        money,
                        uid
                    ))

                    conn.execute("""
                        DELETE FROM battles
                        WHERE uid = ?
                    """, (
                        uid,
                    ))

                    conn.commit()

                    return True, {
                        "result": "win",
                        "player_damage": player_damage,
                        "enemy_damage": 0,
                        "player_hp": int(user["hp"]),
                        "enemy_hp": 0,
                        "money": money,
                        "exp": exp
                    }

                # -------------------------------------------------
                # 敌人反击
                # -------------------------------------------------

                enemy_damage = max(
                    1,
                    int(battle["enemy_attack"])
                    + random.randint(-2, 2)
                    - int(user["defense"])
                )

                player_hp = max(
                    0,
                    int(user["hp"])
                    - enemy_damage
                )

                # -------------------------------------------------
                # 玩家死亡
                # -------------------------------------------------

                if player_hp <= 0:

                    conn.execute("""
                        UPDATE users
                        SET hp = 0,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE uid = ?
                    """, (
                        uid,
                    ))

                    conn.execute("""
                        DELETE FROM battles
                        WHERE uid = ?
                    """, (
                        uid,
                    ))

                    conn.commit()

                    return True, {
                        "result": "lose",
                        "player_damage": player_damage,
                        "enemy_damage": enemy_damage,
                        "player_hp": 0,
                        "enemy_hp": enemy_hp,
                        "money": 0,
                        "exp": 0
                    }

                # -------------------------------------------------
                # 战斗继续
                # -------------------------------------------------

                conn.execute("""
                    UPDATE users
                    SET hp = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE uid = ?
                """, (
                    player_hp,
                    uid
                ))

                conn.execute("""
                    UPDATE battles
                    SET enemy_hp = ?
                    WHERE uid = ?
                """, (
                    enemy_hp,
                    uid
                ))

                conn.commit()

                return True, {
                    "result": "continue",
                    "player_damage": player_damage,
                    "enemy_damage": enemy_damage,
                    "player_hp": player_hp,
                    "enemy_hp": enemy_hp,
                    "money": 0,
                    "exp": 0
                }

            except Exception:

                conn.rollback()

                raise

            finally:

                conn.close()

    # =====================================================
    # 逃跑
    # =====================================================

    def flee_battle(self, uid):

        uid = str(uid)

        with self.lock:

            conn = self.connect()

            try:

                battle = conn.execute("""
                    SELECT id
                    FROM battles
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                if not battle:

                    return False, "你当前没有战斗"

                conn.execute("""
                    DELETE FROM battles
                    WHERE uid = ?
                """, (
                    uid,
                ))

                conn.commit()

                return True, "逃跑成功"

            finally:

                conn.close()

    # =====================================================
    # 等级排行榜
    # =====================================================

    def get_level_rank(self, limit=10):

        limit = max(
            1,
            int(limit)
        )

        with self.lock:

            conn = self.connect()

            try:

                return conn.execute("""
                    SELECT *
                    FROM users
                    ORDER BY level DESC,
                             exp DESC,
                             rpg_id ASC
                    LIMIT ?
                """, (
                    limit,
                )).fetchall()

            finally:

                conn.close()

    # =====================================================
    # 财富排行榜
    # =====================================================

    def get_money_rank(self, limit=10):

        limit = max(
            1,
            int(limit)
        )

        with self.lock:

            conn = self.connect()

            try:

                return conn.execute("""
                    SELECT *,
                           (money + bank) AS total_money
                    FROM users
                    ORDER BY total_money DESC,
                             rpg_id ASC
                    LIMIT ?
                """, (
                    limit,
                )).fetchall()

            finally:

                conn.close()

    # =====================================================
    # 获取所有玩家
    # =====================================================

    def get_all_users(self):

        with self.lock:

            conn = self.connect()

            try:

                return conn.execute("""
                    SELECT *
                    FROM users
                    ORDER BY rpg_id ASC
                """).fetchall()

            finally:

                conn.close()

    # =====================================================
    # 删除用户
    # =====================================================

    def delete_user(self, uid):

        uid = str(uid)

        with self.lock:

            conn = self.connect()

            try:

                user = conn.execute("""
                    SELECT rpg_id
                    FROM users
                    WHERE uid = ?
                """, (
                    uid,
                )).fetchone()

                if not user:
                    return False

                rpg_id = user["rpg_id"]

                conn.execute("""
                    DELETE FROM inventory
                    WHERE rpg_id = ?
                """, (
                    rpg_id,
                ))

                conn.execute("""
                    DELETE FROM battles
                    WHERE uid = ?
                """, (
                    uid,
                ))

                conn.execute("""
                    DELETE FROM users
                    WHERE uid = ?
                """, (
                    uid,
                ))

                conn.commit()

                return True

            finally:

                conn.close()