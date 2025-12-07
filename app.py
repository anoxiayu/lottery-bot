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
import os
import platform
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
import cv2
from rapidocr_onnxruntime import RapidOCR

# 检测是否为低功耗处理器环境（Docker/NAS）
IS_LOW_POWER_ENV = os.environ.get('LOW_POWER_MODE', '').lower() in ('1', 'true', 'yes') or \
                   'docker' in platform.platform().lower() or \
                   os.path.exists('/.dockerenv')

# 初始化 RapidOCR引擎（优化参数）
def create_ocr_engine():
    """创建 OCR 引擎，使用优化参数提升彩票识别精度"""
    try:
        # 使用优化参数：提升文字检测和识别精度
        engine = RapidOCR(
            det_use_cuda=False,
            rec_use_cuda=False,
            # 文字检测参数优化
            det_db_thresh=0.3,      # 降低检测阈值，检测更多文字
            det_db_box_thresh=0.5,  # 文本框阈值
            det_db_unclip_ratio=1.8, # 文本框扩张比例
            # 文字识别参数
            rec_batch_num=6,
        )
        logging.info('RapidOCR 引擎初始化成功 (优化参数)')
        return engine
    except Exception as e:
        # 如果优化参数失败，回退到默认参数
        logging.warning(f'RapidOCR 优化参数初始化失败: {e}，使用默认参数')
        try:
            engine = RapidOCR()
            logging.info('RapidOCR 引擎初始化成功 (默认参数)')
            return engine
        except Exception as e2:
            logging.error(f'RapidOCR 初始化失败: {e2}')
            return None

ocr_engine = create_ocr_engine()


def resize_image_for_ocr(image):
    """调整图片尺寸到适合OCR的范围"""
    try:
        width, height = image.size
        
        # 放大小图片到最小尺寸
        min_size = 1200
        if width < min_size or height < min_size:
            scale = max(min_size / width, min_size / height)
            new_size = (int(width * scale), int(height * scale))
            image = image.resize(new_size, Image.LANCZOS)
        
        # 限制最大尺寸（太大会降低识别速度）
        max_size = 2500
        if image.width > max_size or image.height > max_size:
            scale = min(max_size / image.width, max_size / image.height)
            new_size = (int(image.width * scale), int(image.height * scale))
            image = image.resize(new_size, Image.LANCZOS)
        
        return image
    except Exception as e:
        logging.warning(f'尺寸调整失败: {e}')
        return image


def preprocess_standard(image):
    """策略1: 标准处理 - 对比度增强+锐化"""
    try:
        # 增强对比度 (1.5倍)
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.5)
        # 锐化
        image = image.filter(ImageFilter.SHARPEN)
        return image
    except Exception as e:
        logging.warning(f'标准预处理失败: {e}')
        return image


def preprocess_high_contrast(image):
    """策略2: 高对比度处理 - 适合颜色淡的彩票"""
    try:
        # 高对比度增强 (2.2倍)
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.2)
        # 亮度微调
        brightness = ImageEnhance.Brightness(image)
        image = brightness.enhance(1.1)
        # 双重锐化
        image = image.filter(ImageFilter.SHARPEN)
        image = image.filter(ImageFilter.SHARPEN)
        return image
    except Exception as e:
        logging.warning(f'高对比度预处理失败: {e}')
        return image


def preprocess_binarize(image):
    """策略3: 二值化处理 - 适合背景复杂的图片"""
    try:
        # 转为灰度
        gray = image.convert('L')
        # 转为numpy数组
        img_array = np.array(gray)
        # Otsu自适应阈值二值化
        _, binary = cv2.threshold(img_array, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # 转回PIL Image
        return Image.fromarray(binary).convert('RGB')
    except Exception as e:
        logging.warning(f'二值化预处理失败: {e}')
        return image


def preprocess_denoise(image):
    """策略4: 降噪处理 - 适合噪点多的图片"""
    try:
        img_array = np.array(image)
        # 非局部均值降噪
        denoised = cv2.fastNlMeansDenoisingColored(img_array, None, 10, 10, 7, 21)
        # 转回PIL增强对比度
        image = Image.fromarray(denoised)
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.6)
        # 锐化
        image = image.filter(ImageFilter.SHARPEN)
        return image
    except Exception as e:
        logging.warning(f'降噪预处理失败: {e}')
        return image


def preprocess_adaptive(image):
    """策略5: 自适应阈值处理 - 适合光照不均的图片"""
    try:
        # 转为灰度
        gray = image.convert('L')
        img_array = np.array(gray)
        # 自适应阈值
        adaptive = cv2.adaptiveThreshold(
            img_array, 255, 
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
            cv2.THRESH_BINARY, 11, 2
        )
        return Image.fromarray(adaptive).convert('RGB')
    except Exception as e:
        logging.warning(f'自适应阈值预处理失败: {e}')
        return image


def evaluate_ocr_result(ocr_data, text):
    """评估OCR识别结果质量，返回评分"""
    score = 0
    
    if not ocr_data or not text:
        return 0
    
    # 1. 基础分：识别到文字的行数
    score += len(ocr_data) * 2
    
    # 2. 检测到期号格式 (+20分)
    if re.search(r'第\s*\d{5}\s*期', text):
        score += 20
    
    # 3. 检测到彩票号码格式 (+30分)
    # 匹配 xx xx xx xx xx + xx xx 格式
    lottery_pattern = r'\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\s*[\+\s]\s*\d{2}\s+\d{2}'
    if re.search(lottery_pattern, text):
        score += 30
    
    # 4. 检测到多个两位数字序列 (+15分)
    two_digit_nums = re.findall(r'\b\d{2}\b', text)
    valid_nums = [n for n in two_digit_nums if 1 <= int(n) <= 35]
    if len(valid_nums) >= 7:
        score += 15
    if len(valid_nums) >= 14:  # 可能识别到多注
        score += 10
    
    # 5. 平均置信度加分
    confidences = []
    for item in ocr_data:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            confidences.append(item[2])
    if confidences:
        avg_conf = sum(confidences) / len(confidences)
        score += int(avg_conf * 20)  # 最高+20分
    
    # 6. 检测到"大乐透"关键词 (+5分)
    if '大乐透' in text or '超级大乐透' in text:
        score += 5
    
    return score


def multi_strategy_ocr(image, ocr_engine):
    """
    多策略OCR识别：尝试多种预处理方案，选择最佳结果
    返回: (最佳ocr_data, 合并文本, 使用的策略名称)
    """
    strategies = [
        ('标准处理', preprocess_standard),
        ('高对比度', preprocess_high_contrast),
        ('二值化', preprocess_binarize),
        ('降噪处理', preprocess_denoise),
        ('自适应阈值', preprocess_adaptive),
    ]
    
    best_result = None
    best_score = -1
    best_strategy = '无'
    best_text = ''
    
    # 先调整尺寸
    original_size = image.size
    image = resize_image_for_ocr(image)
    logging.info(f'[OCR] 图片尺寸: {original_size} -> {image.size}')
    
    for strategy_name, preprocess_func in strategies:
        try:
            # 预处理图片
            processed_image = preprocess_func(image.copy())
            img_array = np.array(processed_image)
            
            # OCR识别
            result = ocr_engine(img_array)
            
            if result is None or (isinstance(result, tuple) and result[0] is None):
                logging.info(f'[OCR] 策略「{strategy_name}」: 无识别结果')
                continue
            
            ocr_data = result[0] if isinstance(result, tuple) else result
            
            # 合并文本
            text_lines = []
            for item in ocr_data:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    text_lines.append(str(item[1]))
            text = ' '.join(text_lines)
            
            # 评估结果质量
            score = evaluate_ocr_result(ocr_data, text)
            logging.info(f'[OCR] 策略「{strategy_name}」: 评分={score}, 识别行数={len(ocr_data)}')
            
            if score > best_score:
                best_score = score
                best_result = ocr_data
                best_strategy = strategy_name
                best_text = text
                
        except Exception as e:
            logging.warning(f'[OCR] 策略「{strategy_name}」执行失败: {e}')
            continue
    
    logging.info(f'[OCR] 最佳策略: {best_strategy} (评分: {best_score})')
    return best_result, best_text, best_strategy


def preprocess_image_for_ocr(image):
    """图片预处理（兼容旧接口）"""
    return resize_image_for_ocr(image)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)

app = Flask(__name__)
app.secret_key = 'lottery_master_key_final_v7'

def get_admin_user():
    """获取管理员用户（第一个注册的用户）"""
    return User.query.order_by(User.id.asc()).first()

def is_admin():
    """检查当前用户是否是管理员"""
    if not current_user.is_authenticated:
        return False
    admin = get_admin_user()
    return admin and current_user.id == admin.id

db_path = os.path.join(os.path.dirname(__file__), 'data')
if not os.path.exists(db_path): os.makedirs(db_path)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(db_path, "lottery_v7.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = '请先登录后再访问此页面'

scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

# --- 模型 ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    sckey = db.Column(db.String(100))
    is_disabled = db.Column(db.Boolean, default=False)  # 账户禁用状态
    is_approved = db.Column(db.Boolean, default=False)  # 是否审核通过
    auto_delete_expired = db.Column(db.Boolean, default=False)  # 自动删除过期彩票
    enable_simulation = db.Column(db.Boolean, default=False)  # 启用模拟购彩
    enable_random_generator = db.Column(db.Boolean, default=False)  # 启用随机号码生成器
    created_at = db.Column(db.DateTime, default=datetime.now)  # 注册时间
    tickets = db.relationship('MyTicket', backref='owner', lazy=True, cascade='all, delete-orphan')
    
    def is_admin_user(self):
        """检查是否是管理员"""
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
    is_simulation = db.Column(db.Boolean, default=False)  # 是否模拟购彩

class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    push_time = db.Column(db.String(10), default="22:00")

class PasswordResetRequest(db.Model):
    """密码重置请求"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    new_password_hash = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    user = db.relationship('User', backref='password_requests')

@login_manager.user_loader
def load_user(user_id): return db.session.get(User, int(user_id))

# --- 工具 ---
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
    # 将集合转为列表并排序，方便后续展示
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
    msg_lines.append(f"🔴 **{','.join(result['red'])}**  🔵 **{','.join(result['blue'])}**")
    msg_lines.append("---")
    
    # 区分正式彩票和模拟彩票
    normal_tickets = [t for t in user.tickets if not t.is_simulation]
    sim_tickets = [t for t in user.tickets if t.is_simulation]
    
    total_prize, win_count, has_active = 0, 0, False
    
    # 处理正式彩票
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
    
    # 处理模拟彩票
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
        # 只处理已审核且未禁用的用户
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
        logging.info(f"📅 调度器已设定: 周一三六 {t_str}")
    except Exception as e: logging.error(f"调度器错误: {e}")

# --- 路由 ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            flash('❌ 请输入用户名和密码')
            return render_template('login.html')
        
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            if user.is_disabled:
                flash('❌ 该账户已被禁用')
                return render_template('login.html')
            # 管理员无需审核，普通用户需要审核
            if not user.is_approved and not user.is_admin_user():
                flash('⏳ 账户待审核，请等待管理员审核通过')
                return render_template('login.html')
            login_user(user)
            return redirect(url_for('index'))
        flash('❌ 用户名或密码错误')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        # 输入验证
        if not username or len(username) < 2:
            flash('❌ 用户名至少2个字符')
            return render_template('register.html')
        if not password or len(password) < 4:
            flash('❌ 密码至少4个字符')
            return render_template('register.html')
        # 防止特殊字符注入
        if not username.replace('_', '').replace('-', '').isalnum():
            flash('❌ 用户名只能包含字母、数字、下划线和连字符')
            return render_template('register.html')
        
        if User.query.filter_by(username=username).first():
            flash('❌ 用户名已存在')
        else:
            # 检查是否是第一个用户（管理员）
            is_first_user = User.query.count() == 0
            new_user = User(
                username=username,
                password_hash=generate_password_hash(password),
                is_approved=is_first_user  # 第一个用户自动审核通过
            )
            db.session.add(new_user)
            db.session.commit()
            if is_first_user:
                flash('✅ 管理员账户创建成功，请登录')
            else:
                flash('✅ 注册成功！请等待管理员审核后登录')
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    """密码找回"""
    if request.method == 'POST':
        username = request.form.get('username')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if new_password != confirm_password:
            flash('❌ 两次密码输入不一致')
            return render_template('forgot_password.html')
        
        user = User.query.filter_by(username=username).first()
        if not user:
            flash('❌ 用户名不存在')
            return render_template('forgot_password.html')
        
        # 检查是否已有待处理的请求
        existing = PasswordResetRequest.query.filter_by(user_id=user.id, status='pending').first()
        if existing:
            flash('⚠️ 您已有一个待审核的密码重置请求，请等待管理员处理')
            return render_template('forgot_password.html')
        
        # 创建密码重置请求
        reset_request = PasswordResetRequest(
            user_id=user.id,
            new_password_hash=generate_password_hash(new_password)
        )
        db.session.add(reset_request)
        db.session.commit()
        
        flash('✅ 密码重置请求已提交，请等待管理员审核')
        return redirect(url_for('login'))
    
    return render_template('forgot_password.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    setting = AppSetting.query.first()
    push_time = setting.push_time if setting else "22:00"
    latest = get_latest_lottery()
    curr_term = latest['term'] if latest else 0
    user_count = User.query.count()
    
    # 自动删除过期彩票
    if current_user.auto_delete_expired and curr_term > 0:
        expired_tickets = [t for t in current_user.tickets if curr_term > t.end_term]
        for t in expired_tickets:
            db.session.delete(t)
        if expired_tickets:
            db.session.commit()
    
    # 区分正常彩票和模拟彩票
    normal_data = []
    simulation_data = []
    for t in current_user.tickets:
        st = {'level': 'waiting', 'prize': 0, 'state': 'unknown', 'hit_reds': [], 'hit_blues': []}
        if latest:
            if curr_term > t.end_term: st['state'] = 'expired'
            elif curr_term < t.start_term: st['state'] = 'future'
            else:
                lvl, prz, hr, hb = analyze_ticket(t.red_nums, t.blue_nums, latest['red'], latest['blue'])
                st = {'level': lvl, 'prize': prz, 'hit_reds': hr, 'hit_blues': hb, 'state': 'active'}
        item = {'ticket': t, 'status': st}
        if t.is_simulation:
            simulation_data.append(item)
        else:
            normal_data.append(item)
    
    return render_template('index.html', 
                          latest=latest, 
                          tickets=normal_data,
                          simulation_tickets=simulation_data,
                          user=current_user, 
                          push_time=push_time, 
                          user_count=user_count, 
                          is_admin=is_admin())

@app.route('/update_settings', methods=['POST'])
@login_required
def update_settings():
    new_key = request.form.get('sckey')
    # 只有当用户输入了新key才更新（不包含打码的******）
    if new_key and new_key.strip() and '******' not in new_key:
        current_user.sckey = new_key.strip()
    
    # 用户个人设置开关
    current_user.auto_delete_expired = 'auto_delete_expired' in request.form
    current_user.enable_simulation = 'enable_simulation' in request.form
    current_user.enable_random_generator = 'enable_random_generator' in request.form
    
    if 'push_time' in request.form:
        setting = AppSetting.query.first()
        if not setting: setting = AppSetting(); db.session.add(setting)
        setting.push_time = request.form.get('push_time')
        init_scheduler()
    db.session.commit()
    flash('✅ 设置已保存')
    return redirect(url_for('index'))

@app.route('/add', methods=['POST'])
@login_required
def add_ticket():
    try:
        reds = ",".join([request.form.get(f'r{i}').strip().zfill(2) for i in range(1, 6)])
        blues = ",".join([request.form.get(f'b{i}').strip().zfill(2) for i in range(1, 3)])
        is_sim = request.form.get('is_simulation') == '1'
        db.session.add(MyTicket(
            user_id=current_user.id, 
            red_nums=reds, 
            blue_nums=blues, 
            note=request.form.get('note'), 
            start_term=int(request.form.get('start_term')), 
            end_term=int(request.form.get('end_term')),
            is_simulation=is_sim
        ))
        db.session.commit()
        flash('✅ 添加成功' + ('（模拟）' if is_sim else ''))
    except Exception as e:
        flash(f'❌ 添加失败')
    return redirect(url_for('index'))

@app.route('/add_batch', methods=['POST'])
@login_required
def add_batch_tickets():
    """OCR批量添加彩票"""
    try:
        data = request.get_json()
        logging.info(f'收到批量添加请求: {data}')
        
        tickets = data.get('tickets', [])
        start_term = int(data.get('start_term', 0))
        end_term = int(data.get('end_term', 0))
        is_sim = data.get('is_simulation', False)
        
        logging.info(f'期号: {start_term} - {end_term}, 彩票数: {len(tickets)}')
        
        # 验证期号
        if start_term <= 0 or end_term <= 0 or end_term < start_term:
            logging.error(f'无效期号: start={start_term}, end={end_term}')
            return {'success': False, 'error': '无效的期号'}
        
        # 警告期号范围
        if start_term < 23000 or start_term > 26000:
            logging.warning(f'期号范围可能不合理: {start_term}')
        
        added = 0
        errors = []
        for idx, ticket in enumerate(tickets):
            reds = ticket.get('reds', '')
            blues = ticket.get('blues', '')
            note = ticket.get('note', '')
            
            if not reds or not blues:
                continue
            
            # 验证红球
            red_list = reds.split(',')
            if len(red_list) != 5:
                errors.append(f'第{idx+1}注: 红球数量不正确')
                continue
            try:
                red_nums = [int(r) for r in red_list]
                if any(n < 1 or n > 35 for n in red_nums):
                    errors.append(f'第{idx+1}注: 红球超出范围(01-35)')
                    continue
                if len(set(red_nums)) != 5:
                    errors.append(f'第{idx+1}注: 红球重复')
                    continue
            except:
                errors.append(f'第{idx+1}注: 红球格式错误')
                continue
            
            # 验证蓝球
            blue_list = blues.split(',')
            if len(blue_list) != 2:
                errors.append(f'第{idx+1}注: 蓝球数量不正确')
                continue
            try:
                blue_nums = [int(b) for b in blue_list]
                if any(n < 1 or n > 12 for n in blue_nums):
                    errors.append(f'第{idx+1}注: 蓝球超出范围(01-12)')
                    continue
                if len(set(blue_nums)) != 2:
                    errors.append(f'第{idx+1}注: 蓝球重复')
                    continue
            except:
                errors.append(f'第{idx+1}注: 蓝球格式错误')
                continue
            
            # 添加彩票
            db.session.add(MyTicket(
                user_id=current_user.id,
                red_nums=reds,
                blue_nums=blues,
                note=note,
                start_term=start_term,
                end_term=end_term,
                is_simulation=is_sim
            ))
            added += 1
        
        db.session.commit()
        logging.info(f'成功添加 {added} 注彩票')
        
        if added == 0 and errors:
            return {'success': False, 'error': '; '.join(errors)}
        
        return {'success': True, 'added': added, 'errors': errors if errors else None}
    except Exception as e:
        logging.error(f'批量添加彩票失败: {e}')
        db.session.rollback()
        return {'success': False, 'error': str(e)}

@app.route('/ocr', methods=['POST'])
@login_required
def ocr_recognize():
    """OCR识别彩票图片 - 使用多策略识别提升准确度"""
    try:
        if ocr_engine is None:
            return jsonify({'success': False, 'error': 'OCR引擎未初始化，请检查依赖安装'})
        
        data = request.get_json()
        image_data = data.get('image', '')
        
        if not image_data:
            return jsonify({'success': False, 'error': '未提供图片数据'})
        
        # 移除 base64 前缀
        if ',' in image_data:
            image_data = image_data.split(',')[1]
        
        # 解码 base64 图片
        image_bytes = base64.b64decode(image_data)
        image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        logging.info(f'[OCR] 原始图片尺寸: {image.size}')
        
        # 使用多策略OCR识别
        ocr_data, text, strategy_used = multi_strategy_ocr(image, ocr_engine)
        
        # 检查识别结果
        if ocr_data is None or not text.strip():
            logging.warning('[OCR] 所有策略均未能识别图片内容')
            return jsonify({'success': False, 'error': '未能识别图片内容，请确保图片清晰并包含彩票信息'})
        
        # 详细日志输出每行识别结果
        logging.info(f'[OCR] ========== 识别结果 (策略: {strategy_used}) ==========')
        for idx, item in enumerate(ocr_data):
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                line_text = str(item[1])
                confidence = round(item[2], 3) if len(item) >= 3 else 'N/A'
                logging.info(f'[OCR] 行{idx+1}: "{line_text}" (置信度: {confidence})')
        logging.info('[OCR] ====================================')
        logging.info(f'[OCR] 合并文本: {text}')
        
        # 解析彩票信息
        parsed_result = parse_lottery_text(text)
        
        logging.info(f'[OCR] 解析结果: 期号={parsed_result["startTerm"]}, 期数={parsed_result["termCount"]}, 彩票数={len(parsed_result["tickets"])}')
        for idx, ticket in enumerate(parsed_result['tickets']):
            logging.info(f'[OCR] 彩票{idx+1}: 红球={ticket["reds"]} 蓝球={ticket["blues"]}')
        
        return jsonify({
            'success': True,
            'text': text,
            'tickets': parsed_result['tickets'],
            'startTerm': parsed_result['startTerm'],
            'termCount': parsed_result['termCount'],
            'needConfirm': parsed_result.get('needConfirm', False),
            'strategy': strategy_used  # 返回使用的策略，便于调试
        })
        
    except Exception as e:
        logging.error(f'[OCR] 识别失败: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

def parse_lottery_text(text):
    """解析彩票文本，提取号码和期号（增强版 - 多策略解析）"""
    result = {
        'tickets': [],
        'startTerm': None,
        'termCount': 1,
        'needConfirm': False
    }
    
    # 清理文本
    text = text.replace('\r', '\n')
    single_line = ' '.join(text.split())
    logging.info(f'[OCR解析] 原始文本: {single_line}')
    
    # 识别期号: 第XXXXX期 (支持多种格式)
    term_patterns = [
        r'第\s*(\d{5})\s*期',           # 第25001期
        r'(\d{5})\s*期',                   # 25001期
        r'期\s*号[:：]?\s*(\d{5})',     # 期号:25001
    ]
    for pattern in term_patterns:
        term_match = re.search(pattern, single_line)
        if term_match:
            result['startTerm'] = int(term_match.group(1))
            logging.info(f'[OCR解析] 识别到期号: {result["startTerm"]}')
            break
    
    if result['startTerm'] is None:
        result['needConfirm'] = True
        logging.info('[OCR解析] 未识别到期号')
    
    # 识别多期: XX期 X倍 (支持多种格式)
    multi_patterns = [
        r'(\d{1,2})\s*期\s*\d*\s*倍',     # 5期1倍
        r'连续\s*(\d{1,2})\s*期',       # 连续5期
        r'(\d{1,2})\s*期购买',           # 5期购买
    ]
    for pattern in multi_patterns:
        multi_match = re.search(pattern, single_line)
        if multi_match:
            count = int(multi_match.group(1))
            if 2 <= count <= 30:
                result['termCount'] = count
                logging.info(f'[OCR解析] 识别到多期: {count}期')
                break
    
    # === 策略零: 直接匹配标准彩票格式 ===
    # 匹配 xx xx xx xx xx + xx xx 或者 xx xx xx xx xx xx xx 格式
    lottery_patterns = [
        # 标准格式: 01 02 03 04 05 + 06 07
        r'(\d{2})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s*[\+、]\s*(\d{2})\s+(\d{2})',
        # 无加号格式: 01 02 03 04 05 06 07
        r'(\d{2})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+(\d{2})',
    ]
    
    for pattern in lottery_patterns:
        matches = re.finditer(pattern, single_line)
        for match in matches:
            nums = [match.group(i) for i in range(1, 8)]
            reds = [int(n) for n in nums[:5]]
            blues = [int(n) for n in nums[5:7]]
            
            # 验证号码有效性
            valid_reds = all(1 <= n <= 35 for n in reds) and len(set(reds)) == 5
            valid_blues = all(1 <= n <= 12 for n in blues) and len(set(blues)) == 2
            
            if valid_reds and valid_blues:
                ticket = {
                    'reds': [str(n).zfill(2) for n in sorted(reds)],
                    'blues': [str(n).zfill(2) for n in sorted(blues)]
                }
                # 检查是否重复
                if ticket not in result['tickets']:
                    result['tickets'].append(ticket)
                    logging.info(f'[OCR解析] 标准格式匹配: 红{sorted(reds)} 蓝{sorted(blues)}')
    
    # 如果标准格式已匹配到结果，直接返回
    if result['tickets']:
        logging.info(f'[OCR解析] 标准格式匹配成功，共{len(result["tickets"])}注')
        return result
    
    # === 移除干扰信息（更全面） ===
    clean_text = single_line
    # 日期格式
    clean_text = re.sub(r'\d{4}[-/.]\d{1,2}[-/.]\d{1,2}', ' ', clean_text)
    clean_text = re.sub(r'\d{4}年\d{1,2}月\d{1,2}日?', ' ', clean_text)
    clean_text = re.sub(r'\d{1,2}月\d{1,2}日', ' ', clean_text)
    # 期号
    clean_text = re.sub(r'第\d{5}期', ' ', clean_text)
    clean_text = re.sub(r'\d{5}期', ' ', clean_text)
    # 时间格式
    clean_text = re.sub(r'\d{1,2}:\d{2}(:\d{2})?', ' ', clean_text)
    # 年份范围
    clean_text = re.sub(r'\d{4}-\d{4}', ' ', clean_text)
    clean_text = re.sub(r'20\d{2}年?', ' ', clean_text)
    # 金额
    clean_text = re.sub(r'\d+\.?\d*元', ' ', clean_text)
    # 长数字串（票号、序列号）
    clean_text = re.sub(r'\d{8,}', ' ', clean_text)
    clean_text = re.sub(r'\d{6,7}', ' ', clean_text)  # 6-7位数字也可能是票号
    # 英文字母
    clean_text = re.sub(r'[a-zA-Z]+', ' ', clean_text)
    # 特殊字符
    clean_text = re.sub(r'[\*\#\@\!\$\%\^\&]+', ' ', clean_text)
    
    logging.info(f'[OCR解析] 清理后文本: {clean_text}')
    
    # === 策略一: 数字序列提取 ===
    # 方法1: 匹配空格分隔的两位数字序列
    spaced_nums = re.findall(r'(?:^|\s)(\d{2})(?=\s|$)', clean_text)
    
    # 方法2: 匹配所有1-2位数字
    all_digit_nums = re.findall(r'\d{1,2}', clean_text)
    
    # 选择更好的结果
    if len(spaced_nums) >= 7:
        logging.info(f'[OCR解析] 方法1匹配的数字: {spaced_nums}')
        all_nums = spaced_nums
    else:
        logging.info(f'[OCR解析] 方法2匹配的数字: {all_digit_nums}')
        all_nums = all_digit_nums
    
    # 过滤有效数字（1-35）
    all_nums = [n.zfill(2) for n in all_nums if 1 <= int(n) <= 35]
    logging.info(f'[OCR解析] 有效数字(1-35): {all_nums}')
    
    # === 策略二: 滑动窗口匹配 ===
    i = 0
    while i <= len(all_nums) - 7:
        reds = [int(n) for n in all_nums[i:i+5]]
        blues = [int(n) for n in all_nums[i+5:i+7]]
        
        # 红球验证: 1-35, 5个不重复
        valid_reds = all(1 <= n <= 35 for n in reds) and len(set(reds)) == 5
        # 蓝球验证: 1-12, 2个不重复
        valid_blues = all(1 <= n <= 12 for n in blues) and len(set(blues)) == 2
        
        if valid_reds and valid_blues:
            ticket = {
                'reds': [str(n).zfill(2) for n in sorted(reds)],
                'blues': [str(n).zfill(2) for n in sorted(blues)]
            }
            if ticket not in result['tickets']:
                result['tickets'].append(ticket)
                logging.info(f'[OCR解析] 滑动窗口匹配: 红{sorted(reds)} 蓝{sorted(blues)}')
            i += 7
        else:
            i += 1
    
    if result['tickets']:
        return result
    
    # === 策略三: 宽松匹配 ===
    if len(all_nums) >= 7:
        logging.info('[OCR解析] 严格匹配失败，尝试宽松匹配...')
        
        # 尝试从前7个数字组合
        reds = [int(n) for n in all_nums[:5]]
        blues = [int(n) for n in all_nums[5:7]]
        
        # 放宽验证：只检查范围
        valid_reds = all(1 <= n <= 35 for n in reds)
        valid_blues = all(1 <= n <= 12 for n in blues)
        
        if valid_reds and valid_blues:
            ticket = {
                'reds': [str(n).zfill(2) for n in sorted(reds)],
                'blues': [str(n).zfill(2) for n in sorted(blues)]
            }
            result['tickets'].append(ticket)
            result['needConfirm'] = True
            logging.info(f'[OCR解析] 宽松匹配: 红{sorted(reds)} 蓝{sorted(blues)} (需确认)')
    
    # === 策略四: 最宽松匹配 ===
    if not result['tickets'] and len(all_nums) >= 5:
        logging.info('[OCR解析] 尝试最宽松匹配...')
        reds = [int(n) for n in all_nums[:5]]
        if all(1 <= n <= 35 for n in reds):
            blues = []
            # 在剩余数字中找蓝球候选
            for n in all_nums[5:]:
                num = int(n)
                if 1 <= num <= 12 and num not in blues:
                    blues.append(num)
                    if len(blues) >= 2:
                        break
            
            if len(blues) >= 1:
                ticket = {
                    'reds': [str(n).zfill(2) for n in sorted(reds)],
                    'blues': [str(n).zfill(2) for n in sorted(blues)]
                }
                result['tickets'].append(ticket)
                result['needConfirm'] = True
                logging.info(f'[OCR解析] 最宽松匹配: 红{sorted(reds)} 蓝{sorted(blues)} (需确认)')
    
    if not result['tickets']:
        result['needConfirm'] = True
        logging.info('[OCR解析] 未能匹配到任何彩票号码')
    
    return result

@app.route('/delete/<int:tid>')
@login_required
def delete_ticket(tid):
    t = db.session.get(MyTicket, tid)
    if t and t.user_id == current_user.id: db.session.delete(t); db.session.commit()
    return redirect(url_for('index'))

@app.route('/edit_ticket', methods=['POST'])
@login_required
def edit_ticket():
    """编辑彩票"""
    try:
        tid = int(request.form.get('ticket_id'))
        t = MyTicket.query.get_or_404(tid)
        if t.user_id != current_user.id:
            flash('❌ 无权限修改')
            return redirect(url_for('index'))
        
        # 更新红球和蓝球
        reds = ",".join([request.form.get(f'edit_r{i}').strip().zfill(2) for i in range(1, 6)])
        blues = ",".join([request.form.get(f'edit_b{i}').strip().zfill(2) for i in range(1, 3)])
        
        t.red_nums = reds
        t.blue_nums = blues
        t.note = request.form.get('edit_note', '')
        t.start_term = int(request.form.get('edit_start_term'))
        t.end_term = int(request.form.get('edit_end_term'))
        
        db.session.commit()
        flash('✅ 修改成功')
    except Exception as e:
        flash(f'❌ 修改失败: {str(e)}')
    return redirect(url_for('index'))

@app.route('/trigger_self')
@login_required
def trigger_self():
    success, msg = run_check_for_user(current_user, force=True)
    flash(f'{"✅" if success else "❌"} {msg}')
    return redirect(url_for('index'))

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
            total += p
            hist.append({'term': term, 'date': d['date'], 'draw_red': d['red'], 'draw_blue': d['blue'], 'level': l, 'prize': p, 'hit_reds': hr, 'hit_blues': hb})
    hist.sort(key=lambda x: x['term'], reverse=True)
    return render_template('history.html', ticket=t, history=hist, total_prize=total)

@app.route('/admin')
@login_required
def admin():
    """管理员后台"""
    if not is_admin():
        flash('❌ 无权限访问管理后台')
        return redirect(url_for('index'))
    
    # 获取所有用户
    users = User.query.all()
    # 获取所有彩票
    all_tickets = MyTicket.query.all()
    # 获取应用设置
    setting = AppSetting.query.first()
    # 获取最新开奖结果
    latest = get_latest_lottery()
    # 获取最近开奖历史
    draws = get_recent_draws()
    
    # 统计每个用户的中奖情况
    user_stats = []
    for user in users:
        user_data = {
            'user': user,
            'ticket_count': len(user.tickets),
            'total_prize': 0,
            'win_count': 0,
            'tickets': []
        }
        for ticket in user.tickets:
            ticket_info = {
                'ticket': ticket,
                'results': []
            }
            # 检查每期的中奖情况
            for term in range(ticket.start_term, ticket.end_term + 1):
                if term in draws:
                    d = draws[term]
                    level, prize, hit_reds, hit_blues = analyze_ticket(
                        ticket.red_nums, ticket.blue_nums, d['red'], d['blue']
                    )
                    if prize > 0:
                        user_data['total_prize'] += prize
                        user_data['win_count'] += 1
                    ticket_info['results'].append({
                        'term': term,
                        'date': d['date'],
                        'level': level,
                        'prize': prize,
                        'hit_reds': hit_reds,
                        'hit_blues': hit_blues
                    })
            user_data['tickets'].append(ticket_info)
        user_stats.append(user_data)
    
    # 获取待处理的密码重置请求
    password_resets = PasswordResetRequest.query.filter_by(status='pending').all()
    
    return render_template('admin.html', 
                          users=users,
                          all_tickets=all_tickets,
                          setting=setting,
                          latest=latest,
                          user_stats=user_stats,
                          password_resets=password_resets,
                          user=current_user)

@app.route('/admin/toggle_user/<int:uid>')
@login_required
def toggle_user(uid):
    """禁用/启用用户"""
    if not is_admin():
        flash('❌ 无权限操作')
        return redirect(url_for('index'))
    
    user = User.query.get_or_404(uid)
    # 不能禁用管理员账户
    if user.is_admin_user():
        flash('❌ 不能禁用管理员账户')
        return redirect(url_for('admin'))
    
    user.is_disabled = not user.is_disabled
    db.session.commit()
    status = '禁用' if user.is_disabled else '启用'
    flash(f'✅ 用户 {user.username} 已{status}')
    return redirect(url_for('admin'))

@app.route('/admin/approve_user/<int:uid>')
@login_required
def approve_user(uid):
    """审核用户"""
    if not is_admin():
        flash('❌ 无权限操作')
        return redirect(url_for('index'))
    
    user = User.query.get_or_404(uid)
    user.is_approved = not user.is_approved
    db.session.commit()
    status = '已审核' if user.is_approved else '待审核'
    flash(f'✅ 用户 {user.username} {status}')
    return redirect(url_for('admin'))

@app.route('/admin/delete_user/<int:uid>')
@login_required
def delete_user(uid):
    """删除用户（必须先禁用）"""
    if not is_admin():
        flash('❌ 无权限操作')
        return redirect(url_for('index'))
    
    user = User.query.get_or_404(uid)
    # 不能删除管理员账户
    if user.is_admin_user():
        flash('❌ 不能删除管理员账户')
        return redirect(url_for('admin'))
    
    # 必须先禁用才能删除
    if not user.is_disabled:
        flash('❌ 请先禁用该用户后再删除')
        return redirect(url_for('admin'))
    
    username = user.username
    # 先删除用户的密码重置请求
    PasswordResetRequest.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f'✅ 用户 {username} 已删除')
    return redirect(url_for('admin'))

@app.route('/admin/password_reset/<int:rid>/<action>')
@login_required
def handle_password_reset(rid, action):
    """处理密码重置请求"""
    if not is_admin():
        flash('❌ 无权限操作')
        return redirect(url_for('index'))
    
    reset_req = PasswordResetRequest.query.get_or_404(rid)
    if reset_req.status != 'pending':
        flash('❌ 该请求已处理')
        return redirect(url_for('admin'))
    
    if action == 'approve':
        # 批准：更新用户密码
        user = db.session.get(User, reset_req.user_id)
        user.password_hash = reset_req.new_password_hash
        reset_req.status = 'approved'
        db.session.commit()
        flash(f'✅ 已批准 {user.username} 的密码重置请求')
    elif action == 'reject':
        reset_req.status = 'rejected'
        db.session.commit()
        flash(f'❌ 已拒绝密码重置请求')
    
    return redirect(url_for('admin'))

@app.route('/admin/latest_results')
@login_required
def admin_latest_results():
    """最新一期中奖结果展示"""
    if not is_admin():
        flash('❌ 无权限访问')
        return redirect(url_for('index'))
    
    latest = get_latest_lottery()
    if not latest:
        flash('❌ 无法获取最新开奖数据')
        return redirect(url_for('admin'))
    
    # 统计所有用户在最新一期的中奖情况
    users = User.query.filter_by(is_disabled=False).all()
    results = []
    total_prize = 0
    total_wins = 0
    
    for user in users:
        user_results = []
        user_prize = 0
        for ticket in user.tickets:
            if ticket.start_term <= latest['term'] <= ticket.end_term:
                level, prize, hit_reds, hit_blues = analyze_ticket(
                    ticket.red_nums, ticket.blue_nums, latest['red'], latest['blue']
                )
                user_results.append({
                    'ticket': ticket,
                    'level': level,
                    'prize': prize,
                    'hit_reds': hit_reds,
                    'hit_blues': hit_blues
                })
                if prize > 0:
                    user_prize += prize
                    total_wins += 1
        
        if user_results:
            results.append({
                'user': user,
                'tickets': user_results,
                'total_prize': user_prize
            })
            total_prize += user_prize
    
    # 按中奖金额排序
    results.sort(key=lambda x: x['total_prize'], reverse=True)
    
    return render_template('admin_latest_results.html',
                          latest=latest,
                          results=results,
                          total_prize=total_prize,
                          total_wins=total_wins,
                          user=current_user)

@app.route('/push_history/<int:tid>')
@login_required
def push_history(tid):
    if not current_user.sckey: flash('❌ 无Key'); return redirect(url_for('history', tid=tid))
    t = MyTicket.query.get_or_404(tid); draws = get_recent_draws()
    lines, total, wins, checked = [], 0, 0, 0
    for term in range(t.start_term, t.end_term + 1):
        if term in draws:
            checked += 1; d = draws[term]; l, p, _, _ = analyze_ticket(t.red_nums, t.blue_nums, d['red'], d['blue'])
            if p > 0: wins += 1; total += p; lines.append(f"- 第{term}期: **{l} (￥{p})**")
    title = f"汇总: {t.note or '自选'}"
    content = [f"### 🧾 {t.red_nums} + {t.blue_nums}", "---", f"**已开奖**: {checked}期", f"**中奖**: {wins}次", f"**累计**: ￥{total}", "---"] + (lines if wins else ["暂无中奖"])
    requests.post(f"https://sctapi.ftqq.com/{current_user.sckey}.send", data={'title': title, 'desp': "\n\n".join(content)})
    flash('✅ 已推送'); return redirect(url_for('history', tid=tid))

# 初始化数据库和调度器
with app.app_context():
    db.create_all()
    # 数据库迁移：添加新字段
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        
        # User表迁移
        user_columns = [col['name'] for col in inspector.get_columns('user')]
        migrations = [
            ('is_disabled', 'ALTER TABLE user ADD COLUMN is_disabled BOOLEAN DEFAULT 0'),
            ('is_approved', 'ALTER TABLE user ADD COLUMN is_approved BOOLEAN DEFAULT 0'),
            ('auto_delete_expired', 'ALTER TABLE user ADD COLUMN auto_delete_expired BOOLEAN DEFAULT 0'),
            ('enable_simulation', 'ALTER TABLE user ADD COLUMN enable_simulation BOOLEAN DEFAULT 0'),
            ('enable_random_generator', 'ALTER TABLE user ADD COLUMN enable_random_generator BOOLEAN DEFAULT 0'),
            ('created_at', 'ALTER TABLE user ADD COLUMN created_at DATETIME'),
        ]
        with db.engine.connect() as conn:
            for col_name, sql in migrations:
                if col_name not in user_columns:
                    conn.execute(text(sql))
                    logging.info(f'✅ 数据库迁移: 添加 user.{col_name}')
            
            # MyTicket表迁移
            ticket_columns = [col['name'] for col in inspector.get_columns('my_ticket')]
            if 'is_simulation' not in ticket_columns:
                conn.execute(text('ALTER TABLE my_ticket ADD COLUMN is_simulation BOOLEAN DEFAULT 0'))
                logging.info('✅ 数据库迁移: 添加 my_ticket.is_simulation')
            
            # 第一个用户自动审核通过（管理员）
            conn.execute(text("UPDATE user SET is_approved = 1 WHERE id = (SELECT MIN(id) FROM user)"))
            conn.commit()
    except Exception as e:
        logging.warning(f'数据库迁移检查: {e}')
    init_scheduler()
scheduler.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)