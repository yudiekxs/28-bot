import asyncio
import json
import os
import random
import time
import urllib.request

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telethon import TelegramClient, errors

# ================= 配置（从环境变量读） =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
API_URL = "https://pc28.help/api/kj.json?nbr=1"

DEFAULT_DELAY = 5
DEFAULT_PREFIX = "我带着希望"

# session 存到本地文件（持久化）
SESSION_PATH = os.getenv("SESSION_PATH", "./sessions")

# ================= 算法 =================
def get_combination(open_num):
    if open_num is None: return None
    if open_num in (1,3,5,7,9,11,13): return '小单'
    elif open_num in (0,2,4,6,8,10,12): return '小双'
    elif open_num in (14,16,18,20,22,24,26): return '大双'
    elif open_num in (15,17,19,21,23,25,27): return '大单'
    return '小单'

def predict(history):
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

# ================= API =================
async def fetch_kj_data():
    try:
        req = urllib.request.Request(API_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
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

# ================= 键盘 =================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 登录账号", callback_data="login")],
        [InlineKeyboardButton("👥 群组管理", callback_data="grouplist")],
        [InlineKeyboardButton("🔤 广告词", callback_data="prefixmenu")],
        [InlineKeyboardButton("⏱ 发送延迟", callback_data="delaymenu")],
        [InlineKeyboardButton("▶ 开启", callback_data="start_rep"), InlineKeyboardButton("⏹ 停", callback_data="stop_rep")],
        [InlineKeyboardButton("📊 状态", callback_data="status")],
    ])

def code_keyboard(code=""):
    display = ""
    for i in range(5):
        display += code[i] if i < len(code) else "_"
        if i < 4: display += " "
    kb = [
        [InlineKeyboardButton("1",callback_data="d1"),InlineKeyboardButton("2",callback_data="d2"),InlineKeyboardButton("3",callback_data="d3")],
        [InlineKeyboardButton("4",callback_data="d4"),InlineKeyboardButton("5",callback_data="d5"),InlineKeyboardButton("6",callback_data="d6")],
        [InlineKeyboardButton("7",callback_data="d7"),InlineKeyboardButton("8",callback_data="d8"),InlineKeyboardButton("9",callback_data="d9")],
        [InlineKeyboardButton("🔄",callback_data="resend"),InlineKeyboardButton("0",callback_data="d0"),InlineKeyboardButton("⌫",callback_data="del")],
        [InlineKeyboardButton("✅ 确认", callback_data="num_submit")],
        [InlineKeyboardButton("🔙", callback_data="back")],
    ]
    return InlineKeyboardMarkup(kb), display

def back_row():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back")]])

# ================= 客户端 =================
async def get_alive_client(uid, u):
    tg = u.setdefault("tg", {})
    if tg.get("client") and tg["client"].is_connected():
        return tg["client"]

    os.makedirs(SESSION_PATH, exist_ok=True)
    session_file = os.path.join(SESSION_PATH, str(uid))
    client = TelegramClient(session_file, API_ID, API_HASH)
    try:
        await client.connect()
    except Exception as e:
        print(f"连接失败:{e}")
        return None

    try:
        if await client.is_user_authorized():
            tg["client"] = client
            u["logged_in"] = True
            u["state"] = "logged_in"
            return client
    except Exception:
        pass

    phone = tg.get("phone")
    if not phone:
        return None
    try:
        if not tg.get("phone_code_hash"):
            sent = await client.send_code_request(phone)
            tg["phone_code_hash"] = sent.phone_code_hash
            tg["time"] = time.time()
        tg["client"] = client
        return client
    except errors.AuthRestartError:
        await client.disconnect()
        return None
    except Exception as e:
        print(f"发码失败:{e}")
        return None

# ================= 登录 =================
async def do_sign_in(update, context, query):
    u = context.user_data
    tg = u.get("tg", {})
    client = tg.get("client")
    phone = tg.get("phone", "")
    code = tg.get("code", "")
    phone_code_hash = tg.get("phone_code_hash", "")

    if not client:
        kb,_ = code_keyboard("")
        await query.edit_message_text("❌ 客户端丢失，重/login", reply_markup=kb)
        return
    if not phone or not code or not phone_code_hash:
        kb,_ = code_keyboard("")
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
        await query.edit_message_text(f"✅ 登录成功！\n{me.first_name}\n\n现在发群链接或群ID绑群：", reply_markup=main_menu())
    except Exception as e:
        err = str(e)
        if "PASSWORD" in err or "Two-step" in err or "two-step" in err.lower():
            u["state"] = "wait_password"
            tg["wait_password"] = True
            await query.edit_message_text("🔐 两步验证已开启\n\n请在聊天框直接发送你的登录密码：", reply_markup=back_row())
        elif "PHONE_CODE_INVALID" in err:
            tg["code"] = ""
            kb,_ = code_keyboard("")
            await query.edit_message_text("❌ 验证码错误，重新输入\n\n输入：_ _ _ _ _", reply_markup=kb)
        elif "PHONE_CODE_EXPIRED" in err:
            tg["code"] = ""
            kb,_ = code_keyboard("")
            await query.edit_message_text("⚠️ 过期，点🔄重发", reply_markup=kb)
        else:
            tg["code"] = ""
            kb,_ = code_keyboard("")
            await query.edit_message_text(f"❌ {err}\n\n重新输入", reply_markup=kb)

# ================= 群组列表 =================
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

# ================= 前缀管理 =================
async def show_prefix_menu(query, u):
    prefixes = u.get("prefixes", [])
    current = u.get("current_prefix", "")
    text = f"🔤 前缀管理\n\n当前使用：{current if current else DEFAULT_PREFIX}\n\n点前缀设为固定，或添加新前缀："
    kb = []
    for i, p in enumerate(prefixes):
        mark = " ✅" if p == current else ""
        kb.append([InlineKeyboardButton(f"{p}{mark}", callback_data=f"pf_set_{i}")])
    kb.append([InlineKeyboardButton("🎲 随机模式", callback_data="pf_random")])
    kb.append([InlineKeyboardButton("➕ 添加前缀", callback_data="pf_add")])
    kb.append([InlineKeyboardButton("🗑 清空所有", callback_data="pf_clear")])
    kb.append([InlineKeyboardButton("🔙 返回", callback_data="back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

# ================= 延迟管理 =================
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

# ================= 报数循环 =================
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
                        res = predict(history)
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
                        res = predict(history)
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
                                    print(f"发送炸了:{e}，重连重试...")
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

# ================= /start =================
async def start(update, context):
    uid = update.effective_user.id
    u = context.user_data
    u.setdefault("target_groups", [])
    u.setdefault("tg", {})
    u.setdefault("state", "init")
    u.setdefault("prefixes", [])
    u.setdefault("current_prefix", "")
    u.setdefault("delay", DEFAULT_DELAY)

    client = await get_alive_client(uid, u)
    if client and u.get("logged_in"):
        await update.message.reply_text("✅ 已登录，直接操作", reply_markup=main_menu())
    else:
        await update.message.reply_text("测试公益阶段", reply_markup=main_menu())

# ================= 文字 =================
async def on_message(update, context):
    txt = update.message.text.strip()
    u = context.user_data
    state = u.get("state", "init")

    if state == "set_delay":
        try:
            sec = int(txt)
            if sec < 0:
                await update.message.reply_text("❌ 不能小于0", reply_markup=main_menu())
            else:
                u["delay"] = sec
                u["state"] = "logged_in"
                await update.message.reply_text(f"✅ 已设置延迟 {sec} 秒", reply_markup=main_menu())
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
                await update.message.reply_text(f"✅ 两步验证通过！{me.first_name}\n\n现在发群链接绑群", reply_markup=main_menu())
            except Exception as e:
                await update.message.reply_text(f"❌ 密码错误:{e}\n再发一次：", reply_markup=back_row())
        else:
            await update.message.reply_text("❌ 客户端丢失", reply_markup=main_menu())
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
            kb,_ = code_keyboard("")
            await update.message.reply_text("📲 已发验证码\n\n输入：_ _ _ _ _", reply_markup=kb)
        else:
            await update.message.reply_text("❌ 发码失败，重试", reply_markup=main_menu())
            u["state"] = "init"
        return

    if state == "pf_add":
        prefixes = u.get("prefixes", [])
        prefixes.append(txt)
        u["prefixes"] = prefixes
        u["state"] = "logged_in"
        await update.message.reply_text(f"✅ 已添加前缀:\n{txt}", reply_markup=main_menu())
        return

    if u.get("logged_in"):
        if txt in ["开播", "停播"] or txt.startswith("/"):
            pass
        else:
            u.setdefault("target_groups", [])
            try:
                client = await get_alive_client(update.effective_user.id, u)
                if not client:
                    await update.message.reply_text("❌ 客户端异常，重/login", reply_markup=main_menu())
                    return
                ent = await client.get_entity(txt)
                u["target_groups"].append(ent)
                name = getattr(ent, 'title', getattr(ent, 'username', str(ent.id)))
                await update.message.reply_text(f"✅ 已绑群: {name}\n可点【开启】", reply_markup=main_menu())
            except Exception as e:
                await update.message.reply_text(f"❌ 解析失败:{e}\n\n发群ID或 t.me/链接", reply_markup=main_menu())
            return

    await update.message.reply_text("请 /start", reply_markup=main_menu())

# ================= 回调 =================
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
    tg = u["tg"]

    if data == "login":
        u["state"] = "wait_phone"
        await query.edit_message_text("发手机号(如+86)：", reply_markup=back_row())

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
        await query.edit_message_text(f"✅ 已启动（延迟 {u.get('delay', DEFAULT_DELAY)}秒）", reply_markup=back_row())

    elif data == "stop_rep":
        if u.get("reporter_task"):
            u["reporter_task"].cancel()
            u["reporter_task"] = None
        await query.edit_message_text("⏹ 已停", reply_markup=back_row())

    elif data == "status":
        logged = u.get("logged_in", False)
        groups = len(u.get("target_groups", []))
        running = bool(u.get("reporter_task") and not u["reporter_task"].done())
        text = f"📊 状态\n\n登录：{'✅' if logged else '❌'}\n群：{groups}个\n延迟：{u.get('delay', DEFAULT_DELAY)}秒\n报数：{'🟢' if running else '⭕'}"
        await query.edit_message_text(text, reply_markup=back_row())

    elif data == "back":
        u["state"] = "logged_in" if u.get("logged_in") else "init"
        await query.edit_message_text("菜单", reply_markup=main_menu())

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
            kb,_ = code_keyboard("")
            await query.edit_message_text("🔄 已重发\n\n输入：_ _ _ _ _", reply_markup=kb)
        except Exception as e:
            await query.edit_message_text(f"失败:{e}", reply_markup=back_row())

    elif data == "num_submit":
        code = tg.get("code","")
        if len(code) != 5:
            kb,_ = code_keyboard(code)
            await query.edit_message_text(f"需5位\n\n输入：{code or '_ _ _ _ _'}", reply_markup=kb)
            return
        await query.edit_message_text("⏳ 验证...")
        await do_sign_in(update, context, query)

    elif data == "del":
        tg["code"] = tg.get("code","")[:-1]
        kb,_ = code_keyboard(tg["code"])
        await query.edit_message_text(f"输入：{tg['code'] or '_ _ _ _ _'}", reply_markup=kb)

    elif data.startswith("d"):
        n = data[1:]
        cur = tg.get("code","")
        if len(cur) < 5:
            tg["code"] = cur + n
        kb,_ = code_keyboard(tg["code"])
        await query.edit_message_text(f"输入：{tg['code'] or '_ _ _ _ _'}", reply_markup=kb)

# ================= 启动 =================
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(on_callback))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

print("机器人启动")
app.run_polling()
