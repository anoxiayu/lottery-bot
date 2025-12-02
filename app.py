import os
import logging
import sys
import requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)

app = Flask(__name__)
app.secret_key = 'lottery_master_key_final_v7'

db_path = os.path.join(os.path.dirname(__file__), 'data')
if not os.path.exists(db_path): os.makedirs(db_path)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(db_path, "lottery_v7.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

# --- 模型 ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    sckey = db.Column(db.String(100))
    tickets = db.relationship('MyTicket', backref='owner', lazy=True)

class MyTicket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    red_nums = db.Column(db.String(50), nullable=False)
    blue_nums = db.Column(db.String(20), nullable=False)
    note = db.Column(db.String(50))
    start_term = db.Column(db.Integer, nullable=False, default=0)
    end_term = db.Column(db.Integer, nullable=False, default=0)

class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    push_time = db.Column(db.String(10), default="22:00")

@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

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
    
    total_prize, win_count, has_active = 0, 0, False
    for t in user.tickets:
        if t.start_term <= result['term'] <= t.end_term:
            has_active = True
            lvl, prz, hr, hb = analyze_ticket(t.red_nums, t.blue_nums, result['red'], result['blue'])
            
            # --- V7.8 新增：详细命中信息构建 ---
            # 格式：前区买中了X个号码，分别是：A、B，后区...
            if hr:
                hr_info = f"前区中了{len(hr)}个，分别是：{'、'.join(hr)}"
            else:
                hr_info = "前区未中号码"
            
            if hb:
                hb_info = f"后区中了{len(hb)}个，分别是：{'、'.join(hb)}"
            else:
                hb_info = "后区未中号码"
                
            hit_detail_str = f"{hr_info}；{hb_info}。"
            # --------------------------------

            prefix = "🎁 **" if prz > 0 else ""
            suffix = "**" if prz > 0 else ""
            
            msg_lines.append(f"- {prefix}{lvl} (￥{prz}){suffix}: {t.note or '自选'}")
            msg_lines.append(f"  `{t.red_nums} + {t.blue_nums}`")
            msg_lines.append(f"  📝 {hit_detail_str}") # 添加命中详情行
            
            if prz > 0: win_count += 1; total_prize += prz
            
    if not has_active: msg_lines.append("⚠️ 所有号码均不在本期有效范围内")
    
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
        for user in User.query.all(): run_check_for_user(user, force=False)

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
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password_hash, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
        flash('❌ 错误')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if User.query.filter_by(username=request.form.get('username')).first(): flash('❌ 用户名已存在')
        else:
            db.session.add(User(username=request.form.get('username'), password_hash=generate_password_hash(request.form.get('password'))))
            db.session.commit()
            return redirect(url_for('login'))
    return render_template('register.html')

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
    data = []
    for t in current_user.tickets:
        st = {'level': 'waiting', 'prize': 0, 'state': 'unknown'}
        if latest:
            if curr_term > t.end_term: st['state'] = 'expired'
            elif curr_term < t.start_term: st['state'] = 'future'
            else:
                lvl, prz, hr, hb = analyze_ticket(t.red_nums, t.blue_nums, latest['red'], latest['blue'])
                st = {'level': lvl, 'prize': prz, 'hit_reds': hr, 'hit_blues': hb, 'state': 'active'}
        data.append({'ticket': t, 'status': st})
    return render_template('index.html', latest=latest, tickets=data, user=current_user, push_time=push_time, user_count=user_count)

@app.route('/update_settings', methods=['POST'])
@login_required
def update_settings():
    new_key = request.form.get('sckey')
    if new_key and new_key.strip() and '******' not in new_key:
        current_user.sckey = new_key.strip()
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
        db.session.add(MyTicket(user_id=current_user.id, red_nums=reds, blue_nums=blues, note=request.form.get('note'), start_term=int(request.form.get('start_term')), end_term=int(request.form.get('end_term'))))
        db.session.commit()
    except: flash('❌ 添加失败')
    return redirect(url_for('index'))

@app.route('/delete/<int:tid>')
@login_required
def delete_ticket(tid):
    t = MyTicket.query.get(tid)
    if t and t.user_id == current_user.id: db.session.delete(t); db.session.commit()
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        init_scheduler()
    scheduler.start()
    app.run(host='0.0.0.0', port=5000)