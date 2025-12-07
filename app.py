import os
import logging
import sys
import requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
import base64
import re
import io
import platform
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import cv2  # OpenCV 用于图像处理
from rapidocr_onnxruntime import RapidOCR

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)

# 检测环境
IS_LOW_POWER_ENV = os.environ.get('LOW_POWER_MODE', '').lower() in ('1', 'true', 'yes') or \
                   'docker' in platform.platform().lower() or \
                   os.path.exists('/.dockerenv')

app = Flask(__name__)
app.secret_key = 'lottery_master_key_final_v7'

# --- OCR 引擎初始化 (单例模式) ---
ocr_engine = None

def get_ocr_engine():
    global ocr_engine
    if ocr_engine is None:
        try:
            # 初始化参数优化：调整检测框阈值以适应彩票文字
            ocr_engine = RapidOCR(
                det_use_cuda=False,
                rec_use_cuda=False,
                det_db_thresh=0.3,      # 降低二值化阈值，更容易检出文字
                det_db_box_thresh=0.5,  # 降低框置信度阈值
                det_db_unclip_ratio=1.6 # 文本框略微扩大
            )
            logging.info('✅ RapidOCR 引擎初始化成功')
        except Exception as e:
            logging.error(f'❌ RapidOCR 初始化失败: {e}')
    return ocr_engine

# --- 智能文档扫描与图像处理核心逻辑 ---

def order_points(pts):
    """对四个点进行排序：左上、右上、右下、左下"""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)] # 左上
    rect[2] = pts[np.argmax(s)] # 右下
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)] # 右上
    rect[3] = pts[np.argmax(diff)] # 左下
    return rect

def four_point_transform(image, pts):
    """透视变换：将倾斜的四边形拉平为矩形"""
    rect = order_points(pts)
    (tl, tr, br, bl) = rect

    # 计算新图像的宽度（取上下两条边的最大值）
    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB))

    # 计算新图像的高度（取左右两条边的最大值）
    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB))

    # 构建目标点
    dst = np.array([
        [0, 0],
        [maxWidth - 1, 0],
        [maxWidth - 1, maxHeight - 1],
        [0, maxHeight - 1]], dtype="float32")

    # 计算变换矩阵并应用
    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight))
    return warped

def smart_doc_scan(image_pil):
    """
    智能文档扫描：自动识别彩票边缘并矫正（类似全能扫描王）
    """
    try:
        # PIL -> OpenCV (RGB -> BGR)
        img = cv2.cvtColor(np.array(image_pil), cv2.COLOR_RGB2BGR)
        orig = img.copy()

        # 1. 预处理：调整大小以提高边缘检测速度
        ratio = img.shape[0] / 500.0
        h = 500
        w = int(img.shape[1] / ratio)
        img_resized = cv2.resize(img, (w, h))

        # 2. 边缘检测：灰度 -> 高斯模糊 -> Canny
        gray = cv2.cvtColor(img_resized, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(gray, 75, 200)

        # 3. 寻找轮廓
        cnts = cv2.findContours(edged.copy(), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        cnts = cnts[0] if len(cnts) == 2 else cnts[1]
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:5] # 取面积最大的前5个

        screenCnt = None
        for c in cnts:
            # 轮廓近似
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)

            # 如果近似轮廓有4个点，且面积够大，认为是彩票
            if len(approx) == 4 and cv2.contourArea(c) > 2000:
                screenCnt = approx
                break

        if screenCnt is not None:
            logging.info("✅ 检测到彩票轮廓，正在执行透视矫正...")
            # 还原到原始比例进行变换
            warped = four_point_transform(orig, screenCnt.reshape(4, 2) * ratio)
            # OpenCV -> PIL (BGR -> RGB)
            return Image.fromarray(cv2.cvtColor(warped, cv2.COLOR_BGR2RGB))
        else:
            logging.info("⚠️ 未检测到明显矩形轮廓，使用原图")
            return image_pil

    except Exception as e:
        logging.warning(f"⚠️ 文档扫描矫正失败: {e}，将使用原图")
        return image_pil

def preprocess_image(image):
    """
    图像增强预处理（矫正后再增强对比度）
    """
    try:
        # 1. 尺寸调整 (限制最大边长，防止OCR过慢)
        w, h = image.size
        min_side = 960 # 适当提高分辨率
        if min(w, h) < min_side:
            ratio = min_side / min(w, h)
            image = image.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        # 2. 转灰度
        gray = image.convert('L')

        # 3. 增强对比度 (应对光照不均)
        enhancer = ImageEnhance.Contrast(gray)
        enhanced = enhancer.enhance(1.8) # 提高对比度

        # 4. 锐化 (使文字边缘更清晰)
        sharp = enhanced.filter(ImageFilter.SHARPEN)

        return sharp
    except Exception as e:
        logging.warning(f"图像增强出错: {e}, 使用原图")
        return image

def split_sticky_numbers(text):
    """
    智能拆分粘连数字
    """
    # 替换常见干扰字符
    text = text.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')

    raw_nums = re.findall(r'\d+', text)
    processed_nums = []

    for num_str in raw_nums:
        length = len(num_str)
        # 偶数长度直接切分 (如 1234 -> 12, 34)
        if length >= 2 and length % 2 == 0:
            for i in range(0, length, 2):
                processed_nums.append(int(num_str[i:i+2]))
        # 奇数长度且>=3，切分前部，丢弃最后一位 (如 123 -> 12, 3丢弃)
        elif length >= 3 and length % 2 == 1:
            for i in range(0, length - 1, 2):
                processed_nums.append(int(num_str[i:i+2]))
        # 单个数字单独保留
        elif length == 1:
            processed_nums.append(int(num_str))

    return processed_nums

def parse_lottery_lines(ocr_results):
    """
    基于坐标行归并 + 正则语义的解析算法
    """
    if not ocr_results:
        return {'tickets': [], 'startTerm': None, 'termCount': 1}

    # 1. 按垂直坐标(Y)排序
    ocr_results.sort(key=lambda x: x[0][0][1])

    # 2. 行归并
    lines = []
    current_line = []
    last_y = -1
    y_threshold = 30 # 稍微放宽行高阈值

    for item in ocr_results:
        box, text, score = item
        y = box[0][1]

        if last_y == -1 or abs(y - last_y) < y_threshold:
            current_line.append((box[0][0], text))
        else:
            lines.append(sorted(current_line, key=lambda x: x[0]))
            current_line = [(box[0][0], text)]
        last_y = y
    if current_line:
        lines.append(sorted(current_line, key=lambda x: x[0]))

    # 3. 解析每行数据
    tickets = []
    start_term = None
    term_count = 1

    pending_reds = []

    for line_items in lines:
        line_text = " ".join([item[1] for item in line_items])
        logging.info(f"[OCR解析] 处理行: {line_text}")

        # --- A. 提取期号 ---
        if not start_term:
            # 优先匹配 "第xxxxx期"
            term_match = re.search(r'第\s*(\d{5})\s*期', line_text)
            if term_match:
                start_term = int(term_match.group(1))
                logging.info(f"[OCR解析] 识别到期号: {start_term}")
            else:
                # 备选：匹配 25xxx，排除年份
                clean_text_no_year = re.sub(r'20\d{2}年', '', line_text)
                term_match_loose = re.search(r'(?:^|\D)(2[3-9]\d{3})(?:\D|$)', clean_text_no_year)
                if term_match_loose:
                    val = int(term_match_loose.group(1))
                    if val != datetime.now().year:
                        start_term = val
                        logging.info(f"[OCR解析] 识别到疑似期号: {start_term}")

        # --- B. 提取连买期数/倍数 ---
        # 匹配 "10期"
        periods_match = re.search(r'(\d+)\s*期', line_text)
        if periods_match:
            try:
                p_val = int(periods_match.group(1))
                if 1 < p_val <= 30: # 排除期号本身
                    term_count = p_val
                    logging.info(f"[OCR解析] 识别到连买期数: {term_count}")
            except: pass

        # --- C. 号码提取 ---
        # 过滤掉非号码行的明显干扰
        if any(kw in line_text for kw in ["开奖", "合计", "单式", "公益", "编号", "时间", "期号", "金额"]):
            # 如果包含大量数字则不跳过（防止误杀）
            if len(re.findall(r'\d', line_text)) < 10:
                continue

        # 智能拆分
        nums = split_sticky_numbers(line_text)
        nums = [n for n in nums if 1 <= n <= 35]

        # C-1: 单行完整号码 (5红+2蓝)
        if len(nums) >= 7:
            found_in_line = False
            for i in range(len(nums) - 6):
                reds = nums[i:i+5]
                blues = nums[i+5:i+7]

                if any(r > 35 for r in reds) or len(set(reds)) != 5: continue
                if any(b > 12 for b in blues) or len(set(blues)) != 2: continue

                # [关键修正] 返回列表而不是字符串，解决前端填充一位数的问题
                ticket = {
                    'reds': [f"{n:02d}" for n in sorted(reds)],
                    'blues': [f"{n:02d}" for n in sorted(blues)],
                    'note': 'OCR识别'
                }
                # 简单查重
                if not any(t['reds'] == ticket['reds'] and t['blues'] == ticket['blues'] for t in tickets):
                    tickets.append(ticket)
                    found_in_line = True

            if found_in_line:
                pending_reds = []

        # C-2: 跨行拼接
        elif len(pending_reds) == 5 and len(nums) >= 2:
            blues = nums[:2]
            if all(1 <= b <= 12 for b in blues) and len(set(blues)) == 2:
                ticket = {
                    'reds': [f"{n:02d}" for n in sorted(pending_reds)],
                    'blues': [f"{n:02d}" for n in sorted(blues)],
                    'note': 'OCR识别(跨行)'
                }
                if not any(t['reds'] == ticket['reds'] and t['blues'] == ticket['blues'] for t in tickets):
                    tickets.append(ticket)
                pending_reds = []
            else:
                pending_reds = []

        # C-3: 缓存红球
        elif len(nums) == 5:
            if all(1 <= r <= 35 for r in nums) and len(set(nums)) == 5:
                pending_reds = nums

    return {
        'tickets': tickets,
        'startTerm': start_term,
        'termCount': term_count,
        'needConfirm': start_term is None
    }

# --- 数据库模型 ---
db_path = os.path.join(os.path.dirname(__file__), 'data')
if not os.path.exists(db_path): os.makedirs(db_path)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(db_path, "lottery_v7.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    sckey = db.Column(db.String(100))
    is_disabled = db.Column(db.Boolean, default=False)
    is_approved = db.Column(db.Boolean, default=False)
    auto_delete_expired = db.Column(db.Boolean, default=False)
    enable_simulation = db.Column(db.Boolean, default=False)
    enable_random_generator = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    tickets = db.relationship('MyTicket', backref='owner', lazy=True, cascade='all, delete-orphan')

    def is_admin_user(self):
        admin = User.query.order_by(User.id.asc()).first()
        return admin and self.id == admin.id

class MyTicket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    red_nums = db.Column(db.String(50), nullable=False)
    blue_nums = db.Column(db.String(20), nullable=False)
    note = db.Column(db.String(50))
    start_term = db.Column(db.Integer, nullable=False, default=0)
    end_term = db.Column(db.Integer, nullable=False, default=0)
    is_simulation = db.Column(db.Boolean, default=False)

class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    push_time = db.Column(db.String(10), default="22:00")

class PasswordResetRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    new_password_hash = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(20), default='pending')
    user = db.relationship('User', backref='password_requests')

@login_manager.user_loader
def load_user(user_id): return db.session.get(User, int(user_id))

def get_admin_user(): return User.query.order_by(User.id.asc()).first()
def is_admin(): return current_user.is_authenticated and get_admin_user() and current_user.id == get_admin_user().id

# --- 业务逻辑 ---

def get_headers(): return {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36", "Referer": "https://www.lottery.gov.cn/"}

def get_latest_lottery():
    url = "https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry?gameNo=85&provinceId=0&pageSize=1&isVerify=1&pageNo=1"
    try:
        res = requests.get(url, headers=get_headers(), timeout=15).json()
        if res.get('success') and res.get('value', {}).get('list'):
            item = res['value']['list'][0]
            nums = item['lotteryDrawResult'].split(' ')
            return {'term': int(item['lotteryDrawNum']), 'date': item['lotteryDrawTime'], 'red': nums[:5], 'blue': nums[5:], 'pool': str(item.get('poolBalanceAfterdraw', '0')).replace(',', '')}
    except Exception as e: logging.error(f"API Error: {e}")
    return None

def get_recent_draws(limit=50):
    url = f"https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry?gameNo=85&provinceId=0&pageSize={limit}&isVerify=1&pageNo=1"
    draws = {}
    try:
        res = requests.get(url, headers=get_headers(), timeout=15).json()
        if res.get('success') and res.get('value', {}).get('list'):
            for item in res['value']['list']:
                term = int(item['lotteryDrawNum'])
                nums = item['lotteryDrawResult'].split(' ')
                draws[term] = {'term': term, 'date': item['lotteryDrawTime'], 'red': nums[:5], 'blue': nums[5:]}
    except: pass
    return draws

def analyze_ticket(ticket_red, ticket_blue, open_red, open_blue):
    if not open_red: return "等待开奖", 0, [], []
    u_r, u_b = set(ticket_red.split(',')), set(ticket_blue.split(','))
    o_r, o_b = set(open_red), set(open_blue)
    hit_reds = sorted(list(u_r & o_r))
    hit_blues = sorted(list(u_b & o_b))
    r_cnt, b_cnt = len(hit_reds), len(hit_blues)

    if r_cnt == 5 and b_cnt == 2: return "一等奖", 10000000, hit_reds, hit_blues
    if r_cnt == 5 and b_cnt == 1: return "二等奖", 100000, hit_reds, hit_blues
    if r_cnt == 5 and b_cnt == 0: return "三等奖", 10000, hit_reds, hit_blues
    if r_cnt == 4 and b_cnt == 2: return "四等奖", 3000, hit_reds, hit_blues
    if r_cnt == 4 and b_cnt == 1: return "五等奖", 300, hit_reds, hit_blues
    if r_cnt == 3 and b_cnt == 2: return "六等奖", 200, hit_reds, hit_blues
    if r_cnt == 4 and b_cnt == 0: return "七等奖", 100, hit_reds, hit_blues
    if r_cnt == 3 and b_cnt == 1: return "八等奖", 15, hit_reds, hit_blues
    if r_cnt == 2 and b_cnt == 2: return "八等奖", 15, hit_reds, hit_blues
    if r_cnt == 3 and b_cnt == 0: return "九等奖", 5, hit_reds, hit_blues
    if r_cnt == 1 and b_cnt == 2: return "九等奖", 5, hit_reds, hit_blues
    if r_cnt == 2 and b_cnt == 1: return "九等奖", 5, hit_reds, hit_blues
    if r_cnt == 0 and b_cnt == 2: return "九等奖", 5, hit_reds, hit_blues
    return "未中奖", 0, hit_reds, hit_blues

def run_check_for_user(user, force=False):
    if not user.sckey: return False, "未配置 Key"
    if not user.tickets: return False, "名下无号码"
    result = get_latest_lottery()
    if not result: return False, "无法获取API数据"

    is_today = (result['date'] == datetime.now().strftime("%Y-%m-%d"))
    msg_lines = []

    if not is_today and not force:
        msg_lines.append("⚠️ **【提醒】API数据滞后**\n官网未更新今日数据，建议延后推送时间。")
        msg_lines.append("---")
    elif not is_today and force:
        msg_lines.append(f"ℹ️ 官网未更新，显示最新一期 ({result['date']})。")
        msg_lines.append("---")

    msg_lines.append(f"### 📅 期号: {result['term']}")
    msg_lines.append(f"🔴 **{','.join(result['red'])}** 🔵 **{','.join(result['blue'])}**")
    msg_lines.append("---")

    normal_tickets = [t for t in user.tickets if not t.is_simulation]
    sim_tickets = [t for t in user.tickets if t.is_simulation]

    total_prize, win_count, has_active = 0, 0, False

    if normal_tickets:
        msg_lines.append("### 🎫 正式彩票")
        for t in normal_tickets:
            if t.start_term <= result['term'] <= t.end_term:
                has_active = True
                lvl, prz, hr, hb = analyze_ticket(t.red_nums, t.blue_nums, result['red'], result['blue'])
                hr_info = f"前区中{len(hr)}个: {'、'.join(hr)}" if hr else "前区未中"
                hb_info = f"后区中{len(hb)}个: {'、'.join(hb)}" if hb else "后区未中"
                prefix = "🎁 **" if prz > 0 else ""
                suffix = "**" if prz > 0 else ""
                msg_lines.append(f"- {prefix}{lvl} (￥{prz}){suffix}: {t.note or '自选'}")
                msg_lines.append(f"  `{t.red_nums} + {t.blue_nums}`")
                msg_lines.append(f"  📝 {hr_info}；{hb_info}")
                if prz > 0: win_count += 1; total_prize += prz

    if sim_tickets:
        msg_lines.append("---")
        msg_lines.append("### 🎮 模拟购彩（不计入统计）")
        for t in sim_tickets:
            if t.start_term <= result['term'] <= t.end_term:
                lvl, prz, hr, hb = analyze_ticket(t.red_nums, t.blue_nums, result['red'], result['blue'])
                hr_info = f"前区中{len(hr)}个" if hr else "前区未中"
                hb_info = f"后区中{len(hb)}个" if hb else "后区未中"
                msg_lines.append(f"- {lvl}: {t.note or '模拟'}")
                msg_lines.append(f"  `{t.red_nums} + {t.blue_nums}` | {hr_info}, {hb_info}")

    if not has_active and not sim_tickets: msg_lines.append("⚠️ 所有号码均不在本期有效范围内")

    title = f"{'[旧数据] ' if not is_today else ''}大乐透 {result['term']} 结果"
    if win_count > 0: title = f"🎉 中奖￥{total_prize} - " + title
    elif has_active: msg_lines.append("\n**本期暂未中奖，继续加油！**")

    try:
        requests.post(f"https://sctapi.ftqq.com/{user.sckey}.send", data={'title': title, 'desp': "\n\n".join(msg_lines)}, timeout=10)
        return True, "推送成功"
    except Exception as e: return False, str(e)

def job_check_all_users():
    logging.info("⏰ 定时任务触发...")
    with app.app_context():
        for user in User.query.filter_by(is_disabled=False, is_approved=True).all():
            run_check_for_user(user, force=False)

def init_scheduler():
    with app.app_context():
        db.create_all()
        setting = AppSetting.query.first()
        if not setting: setting = AppSetting(push_time="22:00"); db.session.add(setting); db.session.commit()
        t_str = setting.push_time
    try:
        h, m = t_str.split(':')
        if scheduler.get_job('auto_push'): scheduler.reschedule_job('auto_push', trigger='cron', day_of_week='mon,wed,sat', hour=h, minute=m)
        else: scheduler.add_job(job_check_all_users, 'cron', day_of_week='mon,wed,sat', hour=h, minute=m, id='auto_push')
    except Exception as e: logging.error(f"调度器错误: {e}")

scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

# --- 路由 ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            flash('❌ 请输入用户名和密码'); return render_template('login.html')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            if user.is_disabled: flash('❌ 该账户已被禁用'); return render_template('login.html')
            if not user.is_approved and not user.is_admin_user(): flash('⏳ 账户待审核'); return render_template('login.html')
            login_user(user); return redirect(url_for('index'))
        flash('❌ 用户名或密码错误')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or len(username) < 2: flash('❌ 用户名太短'); return render_template('register.html')
        if User.query.filter_by(username=username).first(): flash('❌ 用户名已存在')
        else:
            is_first = User.query.count() == 0
            db.session.add(User(username=username, password_hash=generate_password_hash(password), is_approved=is_first))
            db.session.commit()
            flash('✅ 注册成功')
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username')
        pwd = request.form.get('new_password')
        if pwd != request.form.get('confirm_password'): flash('❌ 密码不一致'); return render_template('forgot_password.html')
        user = User.query.filter_by(username=username).first()
        if not user: flash('❌ 用户不存在'); return render_template('forgot_password.html')
        if PasswordResetRequest.query.filter_by(user_id=user.id, status='pending').first():
            flash('⚠️ 已有待处理请求'); return render_template('forgot_password.html')
        db.session.add(PasswordResetRequest(user_id=user.id, new_password_hash=generate_password_hash(pwd)))
        db.session.commit()
        flash('✅ 请求已提交')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    setting = AppSetting.query.first()
    latest = get_latest_lottery()
    curr_term = latest['term'] if latest else 0

    if current_user.auto_delete_expired and curr_term > 0:
        for t in [t for t in current_user.tickets if curr_term > t.end_term]: db.session.delete(t)
        db.session.commit()

    normal_data, sim_data = [], []
    for t in current_user.tickets:
        st = {'level': 'waiting', 'prize': 0, 'state': 'unknown', 'hit_reds': [], 'hit_blues': []}
        if latest:
            if curr_term > t.end_term: st['state'] = 'expired'
            elif curr_term < t.start_term: st['state'] = 'future'
            else:
                lvl, prz, hr, hb = analyze_ticket(t.red_nums, t.blue_nums, latest['red'], latest['blue'])
                st = {'level': lvl, 'prize': prz, 'hit_reds': hr, 'hit_blues': hb, 'state': 'active'}
        (sim_data if t.is_simulation else normal_data).append({'ticket': t, 'status': st})

    return render_template('index.html', latest=latest, tickets=normal_data, simulation_tickets=sim_data,
                           user=current_user, push_time=setting.push_time if setting else "22:00",
                           user_count=User.query.count(), is_admin=is_admin())

@app.route('/update_settings', methods=['POST'])
@login_required
def update_settings():
    new_key = request.form.get('sckey')
    if new_key and '******' not in new_key: current_user.sckey = new_key.strip()
    current_user.auto_delete_expired = 'auto_delete_expired' in request.form
    current_user.enable_simulation = 'enable_simulation' in request.form
    current_user.enable_random_generator = 'enable_random_generator' in request.form
    if 'push_time' in request.form:
        s = AppSetting.query.first();
        if not s: s=AppSetting(); db.session.add(s)
        s.push_time = request.form.get('push_time')
        init_scheduler()
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/add', methods=['POST'])
@login_required
def add_ticket():
    try:
        reds = ",".join([request.form.get(f'r{i}').strip().zfill(2) for i in range(1, 6)])
        blues = ",".join([request.form.get(f'b{i}').strip().zfill(2) for i in range(1, 3)])
        db.session.add(MyTicket(user_id=current_user.id, red_nums=reds, blue_nums=blues,
                                note=request.form.get('note'), start_term=int(request.form.get('start_term')),
                                end_term=int(request.form.get('end_term')), is_simulation=request.form.get('is_simulation') == '1'))
        db.session.commit()
        flash('✅ 添加成功')
    except: flash(f'❌ 添加失败')
    return redirect(url_for('index'))

@app.route('/add_batch', methods=['POST'])
@login_required
def add_batch_tickets():
    try:
        data = request.get_json()
        start_term, end_term = int(data.get('start_term', 0)), int(data.get('end_term', 0))
        if start_term <= 0 or end_term < start_term: return {'success': False, 'error': '无效期号'}

        added, errors = 0, []
        for idx, t in enumerate(data.get('tickets', [])):
            try:
                reds = [int(n) for n in t['reds'].split(',')]
                blues = [int(n) for n in t['blues'].split(',')]
                if len(set(reds)) != 5 or any(n<1 or n>35 for n in reds): raise ValueError
                if len(set(blues)) != 2 or any(n<1 or n>12 for n in blues): raise ValueError

                db.session.add(MyTicket(user_id=current_user.id, red_nums=t['reds'], blue_nums=t['blues'],
                                        note=t.get('note', ''), start_term=start_term, end_term=end_term, is_simulation=data.get('is_simulation', False)))
                added += 1
            except: errors.append(f'第{idx+1}注格式错误')

        db.session.commit()
        return {'success': True, 'added': added, 'errors': errors}
    except Exception as e: return {'success': False, 'error': str(e)}

@app.route('/ocr', methods=['POST'])
@login_required
def ocr_recognize():
    """OCR识别核心入口 (含智能文档扫描与矫正)"""
    engine = get_ocr_engine()
    if not engine: return jsonify({'success': False, 'error': 'OCR引擎初始化失败'})

    try:
        data = request.get_json()
        if not data.get('image'): return jsonify({'success': False, 'error': '无图片数据'})

        img_str = data['image']
        if ',' in img_str:
            img_str = img_str.split(',')[1]

        img_bytes = base64.b64decode(img_str)
        image = Image.open(io.BytesIO(img_bytes))

        # [关键] 修复手机端拍照图片旋转问题
        image = ImageOps.exif_transpose(image)
        image = image.convert('RGB')

        # 1. 智能文档扫描与矫正 (新增)
        scanned_img = smart_doc_scan(image)

        # 2. 图像增强预处理 (转为 numpy 供 OCR 使用)
        processed_img = preprocess_image(scanned_img)
        img_np = np.array(processed_img)

        # 3. 执行 OCR 推理
        ocr_result, _ = engine(img_np)

        # 4. 智能解析 (含粘连分割与语义提取)
        parsed = parse_lottery_lines(ocr_result)

        if not parsed['tickets']:
            return jsonify({'success': False, 'error': '未识别到有效号码，请确保图片清晰且包含完整号码区域'})

        return jsonify({'success': True, **parsed})
    except Exception as e:
        logging.error(f"OCR Error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/delete/<int:tid>')
@login_required
def delete_ticket(tid):
    t = db.session.get(MyTicket, tid)
    if t and t.user_id == current_user.id: db.session.delete(t); db.session.commit()
    return redirect(url_for('index'))

@app.route('/edit_ticket', methods=['POST'])
@login_required
def edit_ticket():
    t = MyTicket.query.get_or_404(int(request.form.get('ticket_id')))
    if t.user_id != current_user.id: return redirect(url_for('index'))
    try:
        t.red_nums = ",".join([request.form.get(f'edit_r{i}').strip().zfill(2) for i in range(1, 6)])
        t.blue_nums = ",".join([request.form.get(f'edit_b{i}').strip().zfill(2) for i in range(1, 3)])
        t.note = request.form.get('edit_note', '')
        t.start_term, t.end_term = int(request.form.get('edit_start_term')), int(request.form.get('edit_end_term'))
        db.session.commit(); flash('✅ 修改成功')
    except: flash('❌ 修改失败')
    return redirect(url_for('index'))

@app.route('/trigger_self')
@login_required
def trigger_self():
    s, m = run_check_for_user(current_user, force=True)
    flash(f'{"✅" if s else "❌"} {m}'); return redirect(url_for('index'))

@app.route('/rules')
@login_required
def rules(): return render_template('rules.html', user=current_user)

@app.route('/history/<int:tid>')
@login_required
def history(tid):
    t = MyTicket.query.get_or_404(tid)
    if t.user_id != current_user.id: return redirect(url_for('index'))
    draws = get_recent_draws()
    hist, total = [], 0
    for term in range(t.start_term, t.end_term + 1):
        if term in draws:
            d = draws[term]; l, p, hr, hb = analyze_ticket(t.red_nums, t.blue_nums, d['red'], d['blue'])
            total += p; hist.append({'term': term, 'date': d['date'], 'draw_red': d['red'], 'draw_blue': d['blue'], 'level': l, 'prize': p, 'hit_reds': hr, 'hit_blues': hb})
    hist.sort(key=lambda x: x['term'], reverse=True)
    return render_template('history.html', ticket=t, history=hist, total_prize=total)

@app.route('/admin')
@login_required
def admin():
    if not is_admin(): return redirect(url_for('index'))
    users = User.query.all(); draws = get_recent_draws()
    stats = []
    for u in users:
        # 修复：完整构建 user_data 结构
        user_data = {'user': u, 'ticket_count': len(u.tickets), 'total_prize': 0, 'win_count': 0, 'tickets': []}
        for t in u.tickets:
            ticket_info = {'ticket': t, 'results': []}
            for term in range(t.start_term, t.end_term+1):
                if term in draws:
                    d = draws[term]
                    l, p, hr, hb = analyze_ticket(t.red_nums, t.blue_nums, d['red'], d['blue'])
                    if p > 0: user_data['total_prize'] += p; user_data['win_count'] += 1
                    ticket_info['results'].append({'term': term, 'date': d['date'], 'level': l, 'prize': p, 'hit_reds': hr, 'hit_blues': hb})
            user_data['tickets'].append(ticket_info)
        stats.append(user_data)
    return render_template('admin.html', users=users, all_tickets=MyTicket.query.all(), setting=AppSetting.query.first(), latest=get_latest_lottery(), user_stats=stats, password_resets=PasswordResetRequest.query.filter_by(status='pending').all(), user=current_user)

@app.route('/admin/toggle_user/<int:uid>')
@login_required
def toggle_user(uid):
    if not is_admin(): return redirect(url_for('index'))
    u = User.query.get_or_404(uid)
    if not u.is_admin_user(): u.is_disabled = not u.is_disabled; db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/approve_user/<int:uid>')
@login_required
def approve_user(uid):
    if not is_admin(): return redirect(url_for('index'))
    u = User.query.get_or_404(uid); u.is_approved = not u.is_approved; db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/delete_user/<int:uid>')
@login_required
def delete_user(uid):
    if not is_admin(): return redirect(url_for('index'))
    u = User.query.get_or_404(uid)
    if not u.is_admin_user() and u.is_disabled: PasswordResetRequest.query.filter_by(user_id=u.id).delete(); db.session.delete(u); db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/password_reset/<int:rid>/<action>')
@login_required
def handle_password_reset(rid, action):
    if not is_admin(): return redirect(url_for('index'))
    req = PasswordResetRequest.query.get_or_404(rid)
    if req.status == 'pending':
        if action == 'approve': db.session.get(User, req.user_id).password_hash = req.new_password_hash; req.status = 'approved'
        else: req.status = 'rejected'
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/latest_results')
@login_required
def admin_latest_results():
    if not is_admin(): return redirect(url_for('index'))
    latest = get_latest_lottery()
    if not latest: return redirect(url_for('admin'))
    results = []
    for u in User.query.filter_by(is_disabled=False).all():
        u_res, u_prz = [], 0
        for t in u.tickets:
            if t.start_term <= latest['term'] <= t.end_term:
                l, p, hr, hb = analyze_ticket(t.red_nums, t.blue_nums, latest['red'], latest['blue'])
                u_res.append({'ticket': t, 'level': l, 'prize': p, 'hit_reds': hr, 'hit_blues': hb})
                u_prz += p
        if u_res: results.append({'user': u, 'tickets': u_res, 'total_prize': u_prz})
    return render_template('admin_latest_results.html', latest=latest, results=sorted(results, key=lambda x: x['total_prize'], reverse=True), total_prize=sum(r['total_prize'] for r in results), total_wins=sum(len([t for t in r['tickets'] if t['prize']>0]) for r in results), user=current_user)

@app.route('/push_history/<int:tid>')
@login_required
def push_history(tid):
    if not current_user.sckey: return redirect(url_for('history', tid=tid))
    t = MyTicket.query.get_or_404(tid); draws = get_recent_draws()
    lines, total, wins, checked = [], 0, 0, 0
    for term in range(t.start_term, t.end_term + 1):
        if term in draws:
            checked += 1; d = draws[term]; l, p, _, _ = analyze_ticket(t.red_nums, t.blue_nums, d['red'], d['blue'])
            if p > 0: wins += 1; total += p; lines.append(f"- 第{term}期: **{l} (￥{p})**")
    requests.post(f"https://sctapi.ftqq.com/{current_user.sckey}.send", data={'title': f"汇总: {t.note or '自选'}", 'desp': "\n\n".join([f"### 🧾 {t.red_nums} + {t.blue_nums}", "---", f"**已开奖**: {checked}期", f"**中奖**: {wins}次", f"**累计**: ￥{total}", "---"] + (lines if wins else ["暂无中奖"]))})
    flash('✅ 已推送'); return redirect(url_for('history', tid=tid))

# 数据库迁移与初始化
with app.app_context():
    db.create_all()
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        cols = [c['name'] for c in inspector.get_columns('user')]
        if 'is_disabled' not in cols: db.session.execute(text('ALTER TABLE user ADD COLUMN is_disabled BOOLEAN DEFAULT 0'))
        if 'is_approved' not in cols: db.session.execute(text('ALTER TABLE user ADD COLUMN is_approved BOOLEAN DEFAULT 0'))
        if 'auto_delete_expired' not in cols: db.session.execute(text('ALTER TABLE user ADD COLUMN auto_delete_expired BOOLEAN DEFAULT 0'))
        if 'enable_simulation' not in cols: db.session.execute(text('ALTER TABLE user ADD COLUMN enable_simulation BOOLEAN DEFAULT 0'))
        if 'enable_random_generator' not in cols: db.session.execute(text('ALTER TABLE user ADD COLUMN enable_random_generator BOOLEAN DEFAULT 0'))
        if 'created_at' not in cols: db.session.execute(text('ALTER TABLE user ADD COLUMN created_at DATETIME'))
        if 'is_simulation' not in [c['name'] for c in inspector.get_columns('my_ticket')]: db.session.execute(text('ALTER TABLE my_ticket ADD COLUMN is_simulation BOOLEAN DEFAULT 0'))
        db.session.execute(text("UPDATE user SET is_approved = 1 WHERE id = (SELECT MIN(id) FROM user)"))
        db.session.commit()
    except Exception as e: logging.warning(f"Migrate warn: {e}")
    init_scheduler()
scheduler.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)