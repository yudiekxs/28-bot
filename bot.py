import asyncio
import json
import os
import random
import time
import urllib.request
import math
import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telethon import TelegramClient, errors
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")
API_URL = "https://pc28.help/api/kj.json?nbr=1"

DEFAULT_DELAY = 5
DEFAULT_PREFIX = "我带着希望"
SESSION_PATH = "./sessions"

session_lock = asyncio.Lock()
client_instances = {}

# ========== 算法定义 ==========
ALGORITHMS = {
    "1": {"name": "算法一（原版）"},
    "2": {"name": "算法二（3Y）"},
    "3": {"name": "算法三（时间π）"},
}
DEFAULT_ALGO = "1"

# ========== 组合判定 ==========
def get_combination(open_num):
    if open_num is None: return None
    if open_num in (1,3,5,7,9,11,13): return '小单'
    elif open_num in (0,2,4,6,8,10,12): return '小双'
    elif open_num in (14,16,18,20,22,24,26): return '大双'
    elif open_num in (15,17,19,21,23,25,27): return '大单'
    return '小单'

def opposite_combo(combo):
    opp = {
        '大单': '小双', '小双': '大单',
        '大双': '小单', '小单': '大双',
    }
    return opp.get(combo, '小双')

# ========== 算法一：原版 ==========
def predict_v1(history):
    if not history: return None
    latest = history[-1]
    a, c, open_num = latest[1], latest[3], latest[4]
    if open_num is None: return None
    val = a + c + (open_num % 10)
    if val in (1,3,5,7,9,11,13): return '大双', ['小双', '大单']
    elif val in (0,2,4,6,8,10,12): return '大单', ['小单', '大双']
    elif val in (14,16,18,20,22,24,26): return '小单', ['小双', '大单']
    elif val in (15,17,19,21,23,25,27): return '小双', ['小单', '大双']
    return '大双', ['小双', '大双']

# ========== 算法二：3Y 同组均值 + 1 ==========
def predict_v2(history):
    if not history:
        return '大双', ['小双', '大单']
    latest = history[-1]
    a, b, c, open_num = latest[1], latest[2], latest[3], latest[4]

    s = a + b + c
    group = (c + 3) % 3

    same_group = []
    for rec in reversed(history):
        rec_sum = rec[1] + rec[2] + rec[3]
        if rec_sum % 3 == group:
            same_group.append(rec_sum)
        if len(same_group) >= 3:
            break

    if not same_group:
        avg = s
    else:
        avg = sum(same_group) / len(same_group)

    result = round(avg + 1)
    result = max(0, min(27, result))

    big = result >= 14
    odd = result % 2 == 1
    if big and odd:    combo = '大单'
    elif big and not odd: combo = '大双'
    elif not big and odd: combo = '小单'
    else:              combo = '小双'

    if combo in ('大单', '大双'):
        group_kill = ['小单', '小双']
    else:
        group_kill = ['大单', '大双']

    return combo, group_kill

# ========== 算法三：时间π ==========
def predict_v3(history):
    if not history:
        return '大双', ['小双', '大单']

    latest = history[-1]
    a, b, c, open_num = latest[1], latest[2], latest[3], latest[4]
    if open_num is None:
        return '大双', ['小双', '大单']

    s = a + b + c
    if s == 0:
        s = 1

    time_num = int(datetime.datetime.now().strftime("%H%M"))
    calc = time_num / s * math.pi
    calc_str = str(calc).replace('.', '').replace('-', '')
    digit_sum = sum(int(d) for d in calc_str if d.isdigit())

    while digit_sum > 27:
        digit_sum = sum(int(d) for d in str(digit_sum))

    result = digit_sum
    big = result >= 14
    odd = result % 2 == 1

    if big and odd:      kill = '大单'
    elif big and not odd: kill = '大双'
    elif not big and odd: kill = '小单'
    else:                kill = '小双'

    if kill == '大双':
        push = ['小双', '大单']
    elif kill == '大单':
        push = ['小单', '大双']
    elif kill == '小双':
        push = ['大双', '小单']
    else:
        push = ['大单', '小双']

    return kill, push

# ========== 统一入口 ==========
def predict(history, algo_key=None):
    if algo_key is None:
        algo_key = DEFAULT_ALGO
    if algo_key == "2":
        return predict_v2(history)
    elif algo_key == "3":
        return predict_v3(history)
    return predict_v1(history)

def build_line(pred_num, pred_type, double_group, history, open_result=None):
    short_num = str(pred_num)[-2:]
    double_str = double_group[0] + double_group[1]
    if open_result is None:
        return f"{short_num}期杀{pred_type} {double_str}"
    combo = get_combination(open_result)
    if combo == pred_type:
        tail = f"🍉杀{open_result}"
    elif combo in double_group:
        tail = f"🀄组{open_result}"
    else:
        tail = f"🀄杀{open_result}"
    return f"{short_num}期杀{pred_type} {double_str}{tail}"

async def fetch_kj_data():
    try:
        req = urllib.request.Request(API_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
    except Exception as e:
        print("API失败:", e)
        return []

def parse_api_record(item):
    try:
        num = int(item["nbr"])
        parts = item["number"].split("+")
        if len(parts) != 3: return None
        a = int(parts[0]); b = int(parts[1]); c = int(parts[2])
        open_num = int(item["num"])
        return (num, a, b, c, open_num)
    except: return None

# ========== 算法菜单 ==========
def algo_menu(u):
    current = u.get("current_algo", DEFAULT_ALGO)
    kb = []
    for key, algo in ALGORITHMS.items():
        mark = " ✅" if key == current else ""
        kb.append([InlineKeyboardButton(f"{algo['name']}{mark}", callback_data=f"algo_set_{key}")])
    kb.append([InlineKeyboardButton("🔙 返回", callback_data="back")])
    return InlineKeyboardMarkup(kb)

def main_menu(u):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("登录账号", callback_data="login")],
        [InlineKeyboardButton("添加群组", callback_data="grouplist")],
        [InlineKeyboardButton("修改广告词", callback_data="prefixmenu")],
        [InlineKeyboardButton("⏱ 发送延迟", callback_data="delaymenu")],
        [InlineKeyboardButton("切换算法", callback_data="algo_menu")],
        [InlineKeyboardButton("开启", callback_data="start_rep"), InlineKeyboardButton("停", callback_data="stop_rep")],
        [InlineKeyboardButton("📊 状态", callback_data="status")],
    ])

def code_keyboard(code=""):
    display = " ".join(code) if code else "　"
    kb = [
        [InlineKeyboardButton("1",callback_data="d1"),InlineKeyboardButton("2",callback_data="d2"),InlineKeyboardButton("3",callback_data="d3")],
        [InlineKeyboardButton("4",callback_data="d4"),InlineKeyboardButton("5",callback_data="d5"),InlineKeyboardButton("6",callback_data="d6")],
        [InlineKeyboardButton("7",callback_data="d7"),InlineKeyboardButton("8",callback_data="d8"),InlineKeyboardButton("9",callback_data="d9")],
        [InlineKeyboardButton("🔄重发",callback_data="resend"),InlineKeyboardButton("0",callback_data="d0"),InlineKeyboardButton("⌫删除",callback_data="del")],
        [InlineKeyboardButton("✅ 确认提交", callback_data="num_submit")],
        [InlineKeyboardButton("🔙 返回", callback_data="back")],
    ]
    return InlineKeyboardMarkup(kb), display

def back_row():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back")]])

async def get_alive_client(uid, u):
    global client_instances
    tg = u.setdefault("tg", {})
    existing = client_instances.get(uid)
    if existing and existing.is_connected():
        return existing
    async with session_lock:
        existing = client_instances.get(uid)
        if existing and existing.is_connected():
            return existing
        os.makedirs(SESSION_PATH, exist_ok=True)
        session_file = os.path.join(SESSION_PATH, str(uid))
        if existing:
            try:
                await existing.disconnect()
            except:
                pass
        client = TelegramClient(session_file, API_ID, API_HASH)
        try:
            await asyncio.wait_for(client.connect(), timeout=20)
        except asyncio.TimeoutError:
            print("连接超时")
            return None
        except Exception as e:
            print(f"连接失败:{e}")
            return None
        try:
            if await client.is_user_authorized():
                client_instances[uid] = client
                tg["client"] = client
                u["logged_in"] = True
                u["state"] = "logged_in"
                return client
        except Exception:
            pass
        phone = tg.get("phone")
        if not phone:
            return client
        try:
            if not tg.get("phone_code_hash"):
                sent = await client.send_code_request(phone)
                tg["phone_code_hash"] = sent.phone_code_hash
                tg["time"] = time.time()
            client_instances[uid] = client
            tg["client"] = client
            return client
        except errors.AuthRestartError:
            try:
                await client.disconnect()
            except:
                pass
            if os.path.exists(session_file):
                os.remove(session_file)
                for ext in ['-journal', '-wal', '-shm']:
                    jf = session_file + ext
                    if os.path.exists(jf):
                        os.remove(jf)
            return None
        except Exception as e:
            print(f"发码失败:{e}")
            return None

async def do_sign_in(update, context, query):
    u = context.user_data
    tg = u.get("tg", {})
    client = tg.get("client")
    phone = tg.get("phone", "")
    code = tg.get("code", "")
    phone_code_hash = tg.get("phone_code_hash", "")
    if not client:
        kb, _ = code_keyboard("")
        await query.edit_message_text("❌ 客户端丢失，重/login", reply_markup=kb)
        return
    if not phone or not code or not phone_code_hash:
        kb, _ = code_keyboard("")
        await query.edit_message_text("❌ 数据丢失，重新登录", reply_markup=kb)
        return
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        me = await client.get_me()
        u["logged_in"] = True
        u["state"] = "logged_in"
        tg.pop("phone_code_hash", None)
        tg.pop("code", None)
        tg.pop("wait_password", None)
        tg.pop("password_input", None)
        tg["client"] = client
        await query.edit_message_text(f"✅ 登录成功！\n{me.first_name}\n\n现在发群链接或群ID绑群：", reply_markup=main_menu(u))
    except Exception as e:
        err = str(e)
        if "PASSWORD" in err or "Two-step" in err or "two-step" in err.lower():
            u["state"] = "wait_password"
            tg["wait_password"] = True
            await query.edit_message_text("🔐 两步验证已开启\n\n请在聊天框直接发送你的登录密码：", reply_markup=back_row())
        elif "PHONE_CODE_INVALID" in err:
            tg["code"] = ""
            kb, _ = code_keyboard("")
            await query.edit_message_text("❌ 验证码错误，请重新输入", reply_markup=kb)
        elif "PHONE_CODE_EXPIRED" in err:
            tg["code"] = ""
            kb, _ = code_keyboard("")
            await query.edit_message_text("⚠️ 已过期，点🔄重发", reply_markup=kb)
        else:
            tg["code"] = ""
            kb, _ = code_keyboard("")
            await query.edit_message_text(f"❌ {err}\n\n请重新输入", reply_markup=kb)

async def show_group_list(query, u):
    groups = u.get("target_groups", [])
    if not groups:
        await query.edit_message_text("❌ 还没有绑定任何群\n\n已登录状态下直接发群链接或群ID", reply_markup=back_row())
        return
    kb = []
    for i, g in enumerate(groups):
        name = getattr(g, 'title', getattr(g, 'username', str(g.id)))
        kb.append([InlineKeyboardButton(f"❌ {name}", callback_data=f"delg_{i}")])
    kb.append([InlineKeyboardButton("🔙 返回", callback_data="back")])
    await query.edit_message_text(f"共 {len(groups)} 个群，点❌删除：", reply_markup=InlineKeyboardMarkup(kb))

async def show_prefix_menu(query, u):
    prefixes = u.get("prefixes", [])
    current = u.get("current_prefix", "")
    text = f"🔤 前缀管理\n\n当前使用：{current if current else DEFAULT_PREFIX}\n\n点前缀设为固定，或添加新前缀："
    kb = []
    for i, p in enumerate(prefixes):
        mark = " ✅" if p == current else ""
        kb.append([InlineKeyboardButton(f"{p}{mark}", callback_data=f"pf_set_{i}")])
    kb.append([InlineKeyboardButton("🎲 随机广告词", callback_data="pf_random")])
    kb.append([InlineKeyboardButton("➕ 添加广告词", callback_data="pf_add")])
    kb.append([InlineKeyboardButton("🗑 清空所有", callback_data="pf_clear")])
    kb.append([InlineKeyboardButton("🔙 返回", callback_data="back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def show_delay_menu(query, u):
    delay = u.get("delay", DEFAULT_DELAY)
    text = f"⏱ 发送延迟设置\n\n当前：开奖后延迟 <b>{delay}</b> 秒发送\n\n点快捷设置或发一个数字自定义："
    kb = [
        [InlineKeyboardButton("0秒", callback_data="delay_0"), InlineKeyboardButton("3秒", callback_data="delay_3")],
        [InlineKeyboardButton("5秒", callback_data="delay_5"), InlineKeyboardButton("10秒", callback_data="delay_10")],
        [InlineKeyboardButton("15秒", callback_data="delay_15"), InlineKeyboardButton("30秒", callback_data="delay_30")],
        [InlineKeyboardButton("🔙 返回", callback_data="back")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def run_reporter(uid, context):
    u = context.user_data
    tg = u.get("tg", {})
    groups = u.get("target_groups", [])
    prefixes = u.get("prefixes", [])
    history = []
    results = []
    processed_nums = set()
    current_prefix = ""
    try:
        while True:
            try:
                task = u.get("reporter_task")
                if not task or task.done(): break
                delay = u.get("delay", DEFAULT_DELAY)
                algo_key = u.get("current_algo", DEFAULT_ALGO)
                records = await fetch_kj_data()
                if not records:
                    await asyncio.sleep(10)
                    continue
                if delay > 0:
                    await asyncio.sleep(delay)
                records = sorted(records, key=lambda x: int(x["nbr"]))
                for item in records:
                    p = parse_api_record(item)
                    if not p: continue
                    num = p[0]
                    if num in processed_nums: continue
                    processed_nums.add(num)
                    if len(processed_nums) > 50:
                        processed_nums = set(list(processed_nums)[-30:])
                    if history:
                        last = history[-1]
                        if last[0] == p[0] and last[4] == p[4]: continue
                    history.append(p)
                    if len(history) > 30: history = history[-30:]
                    should_clear = False
                    latest_open = p[4]
                    if latest_open is not None:
                        for (pn, pt, dg) in list(results):
                            if pn == p[0]:
                                combo = get_combination(latest_open)
                                if combo == pt:
                                    should_clear = True
                                break
                    client = await get_alive_client(uid, u)
                    if not client:
                        await asyncio.sleep(10)
                        continue
                    tg["client"] = client
                    if should_clear or not current_prefix:
                        if u.get("current_prefix"):
                            current_prefix = u["current_prefix"]
                        elif prefixes:
                            current_prefix = random.choice(prefixes)
                        else:
                            current_prefix = DEFAULT_PREFIX
                    if should_clear:
                        results = []
                        pred_num = p[0] + 1
                        res = predict(history, algo_key)
                        if res is None:
                            await asyncio.sleep(10)
                            continue
                        pred_type, double_group = res
                        results.append((pred_num, pred_type, double_group))
                        line = f"{current_prefix}\n{build_line(pred_num, pred_type, double_group, history)}"
                        for g in groups:
                            try: await client.send_message(g, line)
                            except Exception as e: print(f"发送炸了:{e}")
                    else:
                        batch_lines = []
                        for (pn, pt, dg) in list(results):
                            opened = None
                            for rec in history:
                                if rec[0] == pn and rec[4] is not None:
                                    opened = rec[4]
                                    break
                            if opened is None: continue
                            batch_lines.append(build_line(pn, pt, dg, history, opened))
                        pred_num = p[0] + 1
                        res = predict(history, algo_key)
                        if res is None:
                            await asyncio.sleep(10)
                            continue
                        pred_type, double_group = res
                        results.append((pred_num, pred_type, double_group))
                        batch_lines.append(build_line(pred_num, pred_type, double_group, history))
                        if batch_lines and groups:
                            full_msg = f"{current_prefix}\n" + "\n".join(batch_lines)
                            for g in groups:
                                try: await client.send_message(g, full_msg)
                                except Exception as e:
                                    print(f"发送炸了:{e}")
                                    try: await client.disconnect()
                                    except: pass
                                    client = await get_alive_client(uid, u)
                                    if client:
                                        tg["client"] = client
                                        try: await client.send_message(g, full_msg)
                                        except Exception as e2: print(f"重试也失败:{e2}")
                await asyncio.sleep(10)
            except asyncio.CancelledError: break
            except Exception as e:
                print("循环错:", e)
                await asyncio.sleep(10)
    except asyncio.CancelledError: pass
    finally:
        u["reporter_task"] = None

async def start(update, context):
    uid = update.effective_user.id
    u = context.user_data
    u.setdefault("target_groups", [])
    u.setdefault("tg", {})
    u.setdefault("state", "init")
    u.setdefault("prefixes", [])
    u.setdefault("current_prefix", "")
    u.setdefault("delay", DEFAULT_DELAY)
    u.setdefault("current_algo", DEFAULT_ALGO)
    client = await get_alive_client(uid, u)
    if client and u.get("logged_in"):
        await update.message.reply_text("✅ 已登录，直接操作", reply_markup=main_menu(u))
    else:
        await update.message.reply_text("点【登录TG】开始", reply_markup=main_menu(u))

async def on_message(update, context):
    txt = update.message.text.strip()
    u = context.user_data
    state = u.get("state", "init")
    if state == "set_delay":
        try:
            sec = int(txt)
            if sec < 0:
                await update.message.reply_text("❌ 不能小于0", reply_markup=main_menu(u))
            else:
                u["delay"] = sec
                u["state"] = "logged_in"
                await update.message.reply_text(f"✅ 已设置延迟 {sec} 秒", reply_markup=main_menu(u))
        except ValueError:
            await update.message.reply_text("❌ 请输入数字（秒）", reply_markup=back_row())
        return
    if state == "wait_password":
        u["tg"]["password_input"] = txt
        u["tg"]["code"] = txt
        await update.message.reply_text("⏳ 验证密码中...")
        client = u["tg"].get("client")
        if client:
            try:
                await client.sign_in(password=txt)
                me = await client.get_me()
                u["logged_in"] = True
                u["state"] = "logged_in"
                u["tg"].pop("wait_password", None)
                u["tg"].pop("password_input", None)
                await update.message.reply_text(f"✅ 两步验证通过！{me.first_name}\n\n现在发群链接绑群", reply_markup=main_menu(u))
            except Exception as e:
                await update.message.reply_text(f"❌ 密码错误:{e}\n再发一次：", reply_markup=back_row())
        else:
            await update.message.reply_text("❌ 客户端丢失", reply_markup=main_menu(u))
        return
    if state == "wait_code":
        u["tg"]["code"] = txt
        await update.message.reply_text("⏳ 验证中...")
        class FakeQuery:
            async def edit_message_text(self, text, reply_markup=None):
                await update.message.reply_text(text, reply_markup=reply_markup)
            async def answer(self): pass
        await do_sign_in(update, context, FakeQuery())
        return
    if state == "wait_phone":
        if not txt.startswith("+"):
            await update.message.reply_text("❌ 带+国家码")
            return
        await update.message.reply_text("⏳ 发码中...")
        u["tg"]["phone"] = txt
        u["tg"]["code"] = ""
        client = await get_alive_client(update.effective_user.id, u)
        if client:
            u["state"] = "wait_code"
            kb, _ = code_keyboard("")
            await update.message.reply_text("📲 请输入验证码：\n\n　", reply_markup=kb)
        else:
            await update.message.reply_text("❌ 发码失败，重试", reply_markup=main_menu(u))
            u["state"] = "init"
        return
    if state == "pf_add":
        prefixes = u.get("prefixes", [])
        prefixes.append(txt)
        u["prefixes"] = prefixes
        u["state"] = "logged_in"
        await update.message.reply_text(f"✅ 已添加前缀:\n{txt}", reply_markup=main_menu(u))
        return
    if u.get("logged_in"):
        if txt in ["开启", "停"] or txt.startswith("/"):
            pass
        else:
            u.setdefault("target_groups", [])
            try:
                client = await get_alive_client(update.effective_user.id, u)
                if not client:
                    await update.message.reply_text("❌ 客户端异常，重/login", reply_markup=main_menu(u))
                    return
                ent = await client.get_entity(txt)
                gid = ent.id
                if any(g.id == gid for g in u["target_groups"]):
                    await update.message.reply_text("⚠️ 这个群已经绑过了", reply_markup=main_menu(u))
                else:
                    u["target_groups"].append(ent)
                    name = getattr(ent, 'title', getattr(ent, 'username', str(gid)))
                    await update.message.reply_text(f"✅ 已绑群：{name}\n当前共 {len(u['target_groups'])} 个群", reply_markup=main_menu(u))
            except Exception as e:
                await update.message.reply_text(f"❌ 解析失败:{e}\n\n发群ID或 t.me/链接", reply_markup=main_menu(u))
            return
    await update.message.reply_text("请 /start", reply_markup=main_menu(u))

async def on_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = update.effective_user.id
    u = context.user_data
    u.setdefault("tg", {})
    u.setdefault("prefixes", [])
    u.setdefault("current_prefix", "")
    u.setdefault("target_groups", [])
    u.setdefault("delay", DEFAULT_DELAY)
    u.setdefault("current_algo", DEFAULT_ALGO)
    tg = u["tg"]

    if data == "login":
        u["state"] = "wait_phone"
        await query.edit_message_text("发TG账号)：", reply_markup=back_row())
    elif data == "grouplist":
        await show_group_list(query, u)
    elif data.startswith("delg_"):
        idx = int(data.split("_")[1])
        groups = u.get("target_groups", [])
        if 0 <= idx < len(groups):
            removed = groups.pop(idx)
            name = getattr(removed, 'title', getattr(removed, 'username', str(removed.id)))
            await query.answer(f"已删除 {name}")
            await show_group_list(query, u)
        else:
            await query.edit_message_text("❌ 索引错误", reply_markup=back_row())
    elif data == "prefixmenu":
        await show_prefix_menu(query, u)
    elif data.startswith("pf_set_"):
        idx = int(data.split("_")[2])
        prefixes = u.get("prefixes", [])
        if 0 <= idx < len(prefixes):
            u["current_prefix"] = prefixes[idx]
            await query.answer(f"已设为：{prefixes[idx]}")
            await show_prefix_menu(query, u)
    elif data == "pf_random":
        u["current_prefix"] = ""
        await query.answer("已切换为随机模式")
        await show_prefix_menu(query, u)
    elif data == "pf_add":
        u["state"] = "pf_add"
        await query.edit_message_text("请在聊天框发送新前缀：", reply_markup=back_row())
    elif data == "pf_clear":
        u["prefixes"] = []
        u["current_prefix"] = ""
        await show_prefix_menu(query, u)
    elif data == "delaymenu":
        await show_delay_menu(query, u)
    elif data.startswith("delay_"):
        sec = int(data.split("_")[1])
        u["delay"] = sec
        await query.answer(f"已设置 {sec} 秒")
        await show_delay_menu(query, u)
    elif data == "algo_menu":
        current = u.get("current_algo", DEFAULT_ALGO)
        name = ALGORITHMS.get(current, ALGORITHMS[DEFAULT_ALGO])["name"]
        await query.edit_message_text(
            f"🧮 切换算法\n\n当前：{name}\n点下面切换：",
            reply_markup=algo_menu(u)
        )
    elif data == "algo_set_1":
        u["current_algo"] = "1"
        await query.answer("✅ 已切换到 算法一")
        await query.edit_message_text(
            f"✅ 已切换到：算法一（原版）\n\n开播后将用此算法预测",
            reply_markup=algo_menu(u)
        )
    elif data == "algo_set_2":
        u["current_algo"] = "2"
        await query.answer("✅ 已切换到 算法二")
        await query.edit_message_text(
            f"✅ 已切换到：算法二（3Y同组均值+1）\n\n开播后将用此算法预测",
            reply_markup=algo_menu(u)
        )
    elif data == "algo_set_3":
        u["current_algo"] = "3"
        await query.answer("✅ 已切换到 算法三")
        await query.edit_message_text(
            f"✅ 已切换到：算法三（时间π）\n\n开播后将用此算法预测",
            reply_markup=algo_menu(u)
        )
    elif data == "start_rep":
        if not u.get("logged_in"):
            await query.edit_message_text("❌ 先登录", reply_markup=back_row())
            return
        if not u.get("target_groups"):
            await query.edit_message_text("❌ 先绑群\n已登录直接发群链接", reply_markup=back_row())
            return
        if u.get("reporter_task") and not u["reporter_task"].done():
            await query.edit_message_text("运行中", reply_markup=back_row())
            return
        u["reporter_task"] = asyncio.create_task(run_reporter(uid, context))
        algo_name = ALGORITHMS[u.get("current_algo", DEFAULT_ALGO)]["name"]
        await query.edit_message_text(f"✅ 已启动（延迟 {u.get('delay', DEFAULT_DELAY)}秒，{algo_name}）", reply_markup=back_row())
    elif data == "stop_rep":
        if u.get("reporter_task"):
            u["reporter_task"].cancel()
            u["reporter_task"] = None
        await query.edit_message_text("⏹ 已停", reply_markup=back_row())
    elif data == "status":
        logged = u.get("logged_in", False)
        groups = len(u.get("target_groups", []))
        running = bool(u.get("reporter_task") and not u["reporter_task"].done())
        algo_name = ALGORITHMS[u.get("current_algo", DEFAULT_ALGO)]["name"]
        text = f"📊 状态\n\n登录：{'✅' if logged else '❌'}\n群：{groups}个\n延迟：{u.get('delay', DEFAULT_DELAY)}秒\n算法：{algo_name}\n报数：{'🟢' if running else '⭕'}"
        await query.edit_message_text(text, reply_markup=back_row())
    elif data == "back":
        u["state"] = "logged_in" if u.get("logged_in") else "init"
        await query.edit_message_text("菜单", reply_markup=main_menu(u))
    elif data == "resend":
        try:
            client = await get_alive_client(uid, u)
            if not client:
                await query.edit_message_text("❌ 客户端异常", reply_markup=back_row())
                return
            phone = tg.get("phone", "")
            if not phone:
                await query.edit_message_text("❌ 没手机号", reply_markup=back_row())
                return
            sent = await client.send_code_request(phone)
            tg["phone_code_hash"] = sent.phone_code_hash
            tg["time"] = time.time()
            tg["code"] = ""
            kb, _ = code_keyboard("")
            await query.edit_message_text("🔄 已重发\n📲 请输入验证码：\n\n　", reply_markup=kb)
        except errors.AuthRestartError:
            await query.edit_message_text("⚠️ 需要重新登录，点【登录TG】", reply_markup=main_menu(u))
        except Exception as e:
            await query.edit_message_text(f"失败:{e}", reply_markup=back_row())
    elif data == "num_submit":
        code = tg.get("code", "")
        if not code:
            kb, _ = code_keyboard("")
            await query.edit_message_text("📲 请输入验证码：\n\n　", reply_markup=kb)
            return
        await query.edit_message_text("⏳ 正在验证...")
        await do_sign_in(update, context, query)
    elif data == "del":
        tg["code"] = tg.get("code", "")[:-1]
        kb, display = code_keyboard(tg["code"])
        await query.edit_message_text(f"📲 请输入验证码：\n\n{display}", reply_markup=kb)
    elif data.startswith("d"):
        n = data[1:]
        cur = tg.get("code", "")
        tg["code"] = cur + n
        kb, display = code_keyboard(tg["code"])
        await query.edit_message_text(f"📲 请输入验证码：\n\n{display}", reply_markup=kb)

app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(on_callback))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

print("机器人启动")
app.run_polling()
