from flask import Flask, request, jsonify, render_template, session, redirect, url_for, g, make_response
from flask_cors import CORS
import os
import json
import time
import sys
import threading
# Force UTF-8 output on Windows to support emoji in print statements
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import random
import string
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from supabase import create_client, Client
from werkzeug.security import generate_password_hash, check_password_hash
import resend
import bcrypt
import jwt
from functools import wraps
from markupsafe import escape
# Pre-import heavy data libs at startup (avoids per-request import overhead)
import pandas as pd
import yfinance as yf
try:
    from google import genai as google_genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    try:
        import google.generativeai as genai_legacy
        GEMINI_AVAILABLE = True
    except ImportError:
        GEMINI_AVAILABLE = False
        print('WARNING: google-genai not installed. Run: pip install google-genai')
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False
    print('WARNING: APScheduler not installed. Run: pip install APScheduler')

# ── Load Environment Variables ────────────────────────────────────────────────
load_dotenv()
SUPABASE_URL    = os.getenv("SUPABASE_URL")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY")
RESEND_API_KEY  = os.getenv("RESEND_API_KEY")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
FROM_EMAIL      = "onboarding@resend.dev"

if not SUPABASE_URL or not SUPABASE_KEY:
    print("ERROR: Supabase credentials not found in .env")
    sys.exit(1)

if not RESEND_API_KEY:
    print("WARNING: RESEND_API_KEY not set - emails will not be sent")

resend.api_key = RESEND_API_KEY or ""

# ── Gemini AI Setup ───────────────────────────────────────────────────────────
gemini_client = None
if GEMINI_AVAILABLE and GEMINI_API_KEY:
    try:
        gemini_client = google_genai.Client(api_key=GEMINI_API_KEY)
        print('[OK] Gemini AI (ZenBot 2.0) ready - google-genai SDK')
    except Exception as e:
        print(f'WARNING: Gemini init failed: {e}')
else:
    print('INFO: ZenBot running in rule-based mode (no Gemini key)')
# Alias for chat calls
gemini_model = gemini_client

# ── Initialize Supabase Client ───────────────────────────────────────────────
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Add skills to path ────────────────────────────────────────────────────────
SKILLS_DIR = os.path.join(os.path.dirname(__file__), '.claude', 'skills')
sys.path.insert(0, os.path.join(SKILLS_DIR, 'stock-data-fetch', 'scripts'))
sys.path.insert(0, os.path.join(SKILLS_DIR, 'ai-prediction-engine', 'scripts'))
sys.path.insert(0, os.path.join(SKILLS_DIR, 'news-sentiment-analyzer', 'scripts'))

from fetch_stock import fetch_stock_data, fetch_backtest_data
from predict import predict_stock
from sentiment import fetch_news_sentiment

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "zenvest_ai_secret_2026_premium")
app.config['SESSION_COOKIE_SECURE'] = True if os.getenv('FLASK_ENV') == 'production' else False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
CORS(app)

@app.before_request
def load_user():
    token = request.cookies.get("token")
    if not token:
        g.user = None
        return
    try:
        data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
        g.user = data
    except Exception:
        g.user = None

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not getattr(g, 'user', None):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper

@app.errorhandler(Exception)
def handle_exception(e):
    print(f"SERVER ERROR: {str(e)}")
    return jsonify({"error": "An unexpected server error occurred"}), 500

# ── Sector & Market Setup ─────────────────────────────────────────────────────
NIFTY50 = [
    'RELIANCE.NS','TCS.NS','HDFCBANK.NS','INFY.NS','HINDUNILVR.NS',
    'ICICIBANK.NS','KOTAKBANK.NS','SBIN.NS','BHARTIARTL.NS','ITC.NS',
    'AXISBANK.NS','LT.NS','WIPRO.NS','HCLTECH.NS','ASIANPAINT.NS',
    'MARUTI.NS','TITAN.NS','M&M.NS','SUNPHARMA.NS','BAJFINANCE.NS',
    'NTPC.NS','ONGC.NS','POWERGRID.NS','TECHM.NS','NESTLEIND.NS',
    'ADANIENT.NS','ADANIPORTS.NS','ULTRACEMCO.NS','GRASIM.NS','DIVISLAB.NS',
    'CIPLA.NS','DRREDDY.NS','EICHERMOT.NS','BAJAJFINSV.NS','JSWSTEEL.NS',
    'TATASTEEL.NS','TATAMOTORS.NS','COALINDIA.NS','BPCL.NS','HEROMOTOCO.NS'
]

# Expanded universe for full screener (superset of Nifty50)
FULL_UNIVERSE = NIFTY50 + [
    'ZOMATO.NS','NYKAA.NS','PAYTM.NS','IRCTC.NS','PERSISTENT.NS',
    'MPHASIS.NS','LTIM.NS','HAL.NS','BEL.NS','TATAPOWER.NS',
    'PIDILITIND.NS','BERGEPAINT.NS','MARICO.NS','GODREJCP.NS','DABUR.NS',
]

SECTOR_PE = {
    'Technology': 28, 'Financial Services': 22, 'Consumer Cyclical': 30,
    'Healthcare': 35, 'Energy': 15, 'Industrials': 25, 'Materials': 18,
    'Consumer Defensive': 40, 'Utilities': 20, 'Communication Services': 25,
    'Basic Materials': 18, 'Unknown': 25
}

# ── OTP Helpers ───────────────────────────────────────────────────────────────

def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

def save_otp(email, code, purpose='register'):
    """Save OTP to Supabase one_time_codes table with 10-min expiry."""
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    try:
        # Delete any existing codes for this email+purpose first
        supabase.table("one_time_codes").delete().eq("email", email).eq("purpose", purpose).execute()
        supabase.table("one_time_codes").insert({
            "email": email.lower().strip(),
            "code": code,
            "purpose": purpose,
            "expires_at": expires_at,
            "used": False
        }).execute()
        return True
    except Exception as e:
        print(f"Error saving OTP: {e}")
        return False

def verify_otp(email, code, purpose='register'):
    """Verify OTP — returns True if valid and not expired."""
    try:
        res = supabase.table("one_time_codes") \
            .select("*") \
            .eq("email", email.lower().strip()) \
            .eq("code", code) \
            .eq("purpose", purpose) \
            .eq("used", False) \
            .execute()
        if not res.data:
            return False
        record = res.data[0]
        expires_at = datetime.fromisoformat(record['expires_at'])
        if datetime.now(timezone.utc) > expires_at:
            return False
        # Mark as used
        supabase.table("one_time_codes").update({"used": True}).eq("id", record['id']).execute()
        return True
    except Exception as e:
        print(f"Error verifying OTP: {e}")
        return False

def send_otp_email(email, code, purpose='register'):
    """Send OTP email via Resend."""
    subject = "Verify your Zenvest account" if purpose == 'register' else "Reset your Zenvest password"
    action_text = "complete your registration" if purpose == 'register' else "reset your password"
    try:
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": email,
            "subject": subject,
            "html": f"""
            <div style="font-family:Inter,sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#fff;border-radius:12px;border:1px solid #e5e7eb">
              <div style="text-align:center;margin-bottom:24px">
                <div style="width:48px;height:48px;background:#6c47ff;border-radius:10px;display:inline-flex;align-items:center;justify-content:center;font-size:24px;font-weight:800;color:#fff">Z</div>
                <h2 style="margin:12px 0 4px;font-size:20px;font-weight:700;color:#0d0d0d">Zen<span style="color:#6c47ff">vest</span></h2>
                <p style="color:#888;font-size:13px;margin:0">AI-powered stock intelligence</p>
              </div>
              <hr style="border:none;border-top:1px solid #e5e7eb;margin:0 0 24px">
              <p style="color:#444;font-size:14px;margin:0 0 8px">Hi there 👋</p>
              <p style="color:#444;font-size:14px;margin:0 0 24px">Use the code below to {action_text}. It expires in <strong>10 minutes</strong>.</p>
              <div style="background:#f3f4f6;border-radius:10px;padding:24px;text-align:center;margin-bottom:24px">
                <span style="font-size:36px;font-weight:800;letter-spacing:10px;color:#0d0d0d">{code}</span>
              </div>
              <p style="color:#aaa;font-size:12px;margin:0;text-align:center">If you didn't request this, you can safely ignore this email.</p>
            </div>
            """
        })
        return True
    except Exception as e:
        print(f"Error sending OTP email: {e}")
        return False

# ── User Helpers ──────────────────────────────────────────────────────────────

def get_user_by_email(email):
    try:
        response = supabase.table("users").select("*").eq("email", email.strip().lower()).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        print(f"Error fetching user: {e}")
        return None

def add_user(name, email, risk_level='Medium', budget=50000, password_hash=''):
    try:
        data = {
            "name": name.strip(),
            "email": email.strip().lower(),
            "risk_level": risk_level,
            "budget": budget,
            "password_hash": password_hash
        }
        response = supabase.table("users").insert(data).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        print(f"Error adding user: {e}")
        return None

def get_cached_prediction(symbol):
    try:
        response = supabase.table("predictions_cache").select("*").eq("symbol", symbol).execute()
        if response.data:
            record = response.data[0]
            if (time.time() - record['fetched_at']) < 120:
                return record['data']
        return None
    except Exception as e:
        print(f"Error fetching prediction cache: {e}")
        return None

def save_prediction_cache(symbol, data):
    try:
        record = {
            "symbol": symbol,
            "data": data,
            "fetched_at": time.time()
        }
        supabase.table("predictions_cache").upsert(record).execute()
    except Exception as e:
        print(f"Error saving prediction cache: {e}")

# ── Page Routes ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', user=getattr(g, 'user', None))

@app.route('/login')
def login():
    if getattr(g, 'user', None):
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    resp = make_response(redirect(url_for('login')))
    resp.delete_cookie('token')
    return resp

@app.route('/stock/<symbol>')
def stock_detail(symbol):
    return render_template('stock.html', symbol=symbol.upper(), user=getattr(g, 'user', None))

@app.route('/screener')
def screener():
    return render_template('screener.html', user=getattr(g, 'user', None))

@app.route('/portfolio')
@login_required
def portfolio():
    return render_template('portfolio.html', user=getattr(g, 'user', None))

@app.route('/recommendations')
def recommendations():
    return render_template('recommendations.html', user=getattr(g, 'user', None))

@app.route('/watchlist')
@login_required
def watchlist():
    return render_template('watchlist.html', user=getattr(g, 'user', None))

# ── Auth APIs ─────────────────────────────────────────────────────────────────

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data     = request.get_json()
    email    = data.get('email', '').strip().lower()
    password = data.get('password', '')
    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400
    user = get_user_by_email(email)
    if not user:
        return jsonify({'error': 'No account found with this email'}), 404
        
    if not user.get('password_hash'):
        return jsonify({'error': 'Incorrect password'}), 401
        
    try:
        is_valid = bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8'))
        if not is_valid and password != password.strip():
            is_valid = bcrypt.checkpw(password.strip().encode('utf-8'), user['password_hash'].encode('utf-8'))
    except Exception:
        is_valid = check_password_hash(user['password_hash'], password)
        if not is_valid and password != password.strip():
            is_valid = check_password_hash(user['password_hash'], password.strip())
        
    if not is_valid:
        print(f"Login failed: Incorrect password for {email}")
        return jsonify({'error': 'Incorrect password'}), 401

    payload = {
        'id': str(user['id']), 
        'name': str(user.get('name', 'User')), 
        'email': str(user['email']),
        'risk_level': str(user.get('risk_level', 'Medium')), 
        'budget': float(user.get('budget', 50000)),
        'exp': datetime.now(timezone.utc) + timedelta(days=7)
    }
    print(f"Login successful: {email} (ID: {user['id']})")
    
    token = jwt.encode(payload, app.config['SECRET_KEY'], algorithm="HS256")
    resp = jsonify({'status': 'success', 'user': payload})
    resp.set_cookie("token", token, httponly=True, secure=app.config['SESSION_COOKIE_SECURE'], samesite='Lax')
    return resp

@app.route('/api/auth/send-code', methods=['POST'])
def api_send_code():
    data    = request.get_json()
    email   = data.get('email', '').strip().lower()
    purpose = data.get('purpose', 'register')  # 'register' or 'forgot'
    if not email:
        return jsonify({'error': 'Email is required'}), 400
    if purpose == 'register':
        existing = get_user_by_email(email)
        if existing:
            return jsonify({'error': 'An account with this email already exists. Please sign in.'}), 409
    elif purpose == 'forgot':
        existing = get_user_by_email(email)
        if not existing:
            return jsonify({'error': 'No account found with this email'}), 404
    code = generate_otp()
    if not save_otp(email, code, purpose):
        return jsonify({'error': 'Failed to generate code. Try again.'}), 500
    sent = send_otp_email(email, code, purpose)
    if not sent:
        return jsonify({'error': 'Failed to send email. Check your email address.'}), 500
    return jsonify({'status': 'sent', 'message': f'Verification code sent to {email}'})

@app.route('/api/auth/verify-code', methods=['POST'])
def api_verify_code():
    data    = request.get_json()
    email   = data.get('email', '').strip().lower()
    code    = data.get('code', '').strip()
    purpose = data.get('purpose', 'register')
    if not email or not code:
        return jsonify({'error': 'Email and code are required'}), 400
    valid = verify_otp(email, code, purpose)
    if not valid:
        return jsonify({'error': 'Invalid or expired code. Please try again.'}), 400
    return jsonify({'status': 'verified'})

@app.route('/api/auth/register', methods=['POST'])
def api_register():
    data       = request.get_json()
    name       = data.get('name', '').strip()
    email      = data.get('email', '').strip().lower()
    password   = data.get('password', '')
    risk_level = data.get('risk_level', 'Medium')
    budget     = float(data.get('budget', 50000))
    if not name or not email or not password:
        return jsonify({'error': 'Name, email and password are required'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    if get_user_by_email(email):
        return jsonify({'error': 'Email already registered'}), 409
    
    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    name = escape(name) # XSS safety
    user = add_user(name, email, risk_level, budget, password_hash)
    
    if user:
        payload = {
            'id': str(user['id']), 
            'name': str(user['name']), 
            'email': str(user['email']),
            'risk_level': str(user['risk_level']), 
            'budget': float(user['budget']),
            'exp': datetime.now(timezone.utc) + timedelta(days=7)
        }
        token = jwt.encode(payload, app.config['SECRET_KEY'], algorithm="HS256")
        resp = jsonify({'status': 'success', 'user': payload})
        resp.set_cookie("token", token, httponly=True, secure=app.config['SESSION_COOKIE_SECURE'], samesite='Lax')
        print(f"Registration successful for {email}")
        return resp
    return jsonify({'error': 'Registration failed. Please try again.'}), 500

@app.route('/api/auth/reset-password', methods=['POST'])
def api_reset_password():
    data     = request.get_json()
    email    = data.get('email', '').strip().lower()
    password = data.get('password', '')
    if not email or not password:
        return jsonify({'error': 'Email and new password are required'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    user = get_user_by_email(email)
    if not user:
        return jsonify({'error': 'No account found with this email'}), 404
    try:
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        supabase.table("users").update({"password_hash": password_hash}).eq("email", email).execute()
        return jsonify({'status': 'success', 'message': 'Password updated successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Stock APIs (Data fetched via yfinance + Supabase cache) ───────────────────

@app.route('/api/search')
def api_search():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'results': []})
    matches = [s for s in NIFTY50 if q.upper() in s.upper()][:8]
    results = []
    for sym in matches:
        data = fetch_stock_data(sym)
        if not data.get('error'):
            results.append({
                'symbol': sym,
                'company_name': data.get('company_name', sym),
                'price': data.get('price', 0),
                'change_pct': data.get('change_pct', 0),
                'sector': data.get('sector', '')
            })
    return jsonify({'results': results})

@app.route('/api/stock/<symbol>')
def api_stock(symbol):
    data = fetch_stock_data(symbol.upper())
    if data.get('error'):
        return jsonify(data), 404
    return jsonify(data)

@app.route('/api/stock/<symbol>/chart')
def api_stock_chart(symbol):
    data = fetch_stock_data(symbol.upper())
    if data.get('error'):
        return jsonify(data), 404
    ohlcv = data.get('ohlcv', [])
    return jsonify({'symbol': symbol, 'ohlcv': ohlcv})

@app.route('/api/stock/<symbol>/prediction')
def api_stock_prediction(symbol):
    sym = symbol.upper()
    cached = get_cached_prediction(sym)
    if cached:
        cached['from_cache'] = True
        return jsonify(cached)
    stock_data = fetch_stock_data(sym)
    if stock_data.get('error'):
        return jsonify(stock_data), 404
    sector    = stock_data.get('sector', 'Unknown')
    sector_pe = SECTOR_PE.get(sector, 25)
    # Use Gemini AI for deep single-stock analysis; falls back to rule-based if unavailable
    prediction = predict_stock(
        stock_data,
        sector_pe_avg=sector_pe,
        gemini_client=gemini_model,
        use_ai=True
    )
    prediction['symbol']       = sym
    prediction['company_name'] = stock_data.get('company_name', sym)
    save_prediction_cache(sym, prediction)
    return jsonify(prediction)

@app.route('/api/stock/<symbol>/news')
def api_stock_news(symbol):
    news = fetch_news_sentiment(symbol.upper())
    return jsonify({'symbol': symbol, 'news': news})


@app.route('/api/backtest/<symbol>')
def api_backtest(symbol):
    years = int(request.args.get('years', 3))
    amount = float(request.args.get('amount', 50000))
    res = fetch_backtest_data(symbol, years=years)
    if res.get('error'):
        return jsonify(res), 400
    
    past = res['past_price']
    curr = res['current_price']
    shares = amount / past
    val_now = shares * curr
    profit = val_now - amount
    roi = (profit / amount) * 100
    
    return jsonify({
        'symbol': symbol,
        'years': years,
        'initial_amount': amount,
        'final_amount': round(val_now, 2),
        'profit': round(profit, 2),
        'roi_pct': round(roi, 2),
        'past_date': res['past_date']
    })

# ── Market Overview APIs ──────────────────────────────────────────────────────

@app.route('/api/market/overview')
def api_market_overview():
    INDEX_MAP = {
        '^NSEI':    'Nifty 50',
        '^BSESN':   'Sensex',
        '^NSEBANK': 'Bank Nifty',
    }
    indices = []
    symbols = list(INDEX_MAP.keys())
    with ThreadPoolExecutor(max_workers=4) as exc:
        futures = {exc.submit(fetch_stock_data, sym): sym for sym in symbols}
        for future in as_completed(futures, timeout=15):
            sym = futures[future]
            try:
                d = future.result()
                if not d.get('error'):
                    indices.append({
                        'symbol': sym,
                        'name': INDEX_MAP.get(sym, sym),
                        'price': d.get('price', 0),
                        'change': d.get('change', 0),
                        'change_pct': d.get('change_pct', 0),
                        'prev_close': d.get('prev_close', 0)
                    })
            except Exception:
                pass
    # Sort in the correct display order
    order = {sym: i for i, sym in enumerate(symbols)}
    indices.sort(key=lambda x: order.get(x['symbol'], 99))
    return jsonify({'indices': indices})

@app.route('/api/market/top-movers')
def api_top_movers():
    movers = []
    sample = NIFTY50[:20]
    with ThreadPoolExecutor(max_workers=8) as exc:
        futures = {exc.submit(fetch_stock_data, sym): sym for sym in sample}
        for future in as_completed(futures, timeout=20):
            sym = futures[future]
            try:
                d = future.result()
                if not d.get('error'):
                    movers.append({'symbol': sym, 'company_name': d.get('company_name', sym),
                                   'price': d.get('price'), 'change_pct': d.get('change_pct', 0)})
            except Exception:
                pass
    movers.sort(key=lambda x: x['change_pct'], reverse=True)
    return jsonify({'gainers': movers[:5], 'losers': movers[-5:][::-1]})

@app.route('/api/market/nifty-history')
def api_nifty_history():
    """Return Nifty 50 price data for the given period (TODAY, 3M, 6M, 1Y)."""
    raw_period = request.args.get('period', 'TODAY').upper()
    try:
        ticker = yf.Ticker('^NSEI')
        if raw_period == 'TODAY':
            hist = ticker.history(period='1d', interval='5m')
            if hist.empty:
                return jsonify({'labels': [], 'values': [], 'error': 'No data'})
            labels = [d.strftime('%H:%M') for d in hist.index]
        else:
            period_map = {'3M': '3mo', '6M': '6mo', '1Y': '1y'}
            yf_period  = period_map.get(raw_period, '3mo')
            hist = ticker.history(period=yf_period, interval='1d')
            if hist.empty:
                return jsonify({'labels': [], 'values': [], 'error': 'No data'})
            labels = [d.strftime('%d %b') for d in hist.index]
        values = [round(float(v), 2) for v in hist['Close'].tolist()]
        return jsonify({'labels': labels, 'values': values, 'period': raw_period})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Recommendations & Screener ─────────────────────────────────────────────────

@app.route('/api/recommendations')
def api_recommendations():
    risk   = request.args.get('risk', 'Medium')
    budget = float(request.args.get('budget', 50000))
    sector_filter = request.args.get('sector', '')
    goal   = request.args.get('goal', 'Long-term')
    
    scored = []
    universe = NIFTY50[:20]

    def process_sym(sym):
        stock_data = fetch_stock_data(sym)
        if stock_data.get('error'): return None
        sector_pe = SECTOR_PE.get(stock_data.get('sector', 'Unknown'), 25)
        # Apply goal logic in weighting
        pred = predict_stock(stock_data, sector_pe, goal=goal)
        pred['symbol'] = sym
        pred['company_name'] = stock_data.get('company_name', sym)
        pred['price'] = stock_data.get('price', 0)
        pred['sector'] = stock_data.get('sector', '')
        return pred

    with ThreadPoolExecutor(max_workers=8) as exc:
        futures = {exc.submit(process_sym, sym): sym for sym in universe}
        for future in as_completed(futures, timeout=30):
            try:
                result = future.result()
                if result: scored.append(result)
            except Exception:
                pass

    if sector_filter:
        scored = [s for s in scored if sector_filter.lower() in s.get('sector', '').lower()]
    if risk == 'Low':
        scored = [s for s in scored if s.get('score', 50) >= 60]
    scored.sort(key=lambda x: x.get('score', 0), reverse=True)
    return jsonify({'recommendations': scored[:10], 'total': len(scored[:10])})

@app.route('/api/screener', methods=['POST'])
def api_screener():
    filters = request.get_json() or {}
    results = []
    for sym in NIFTY50:
        d = fetch_stock_data(sym)
        if d.get('error'): continue
        passed = True
        for field, condition in filters.items():
            val = d.get(field)
            if val is None: passed = False; break
            
            if field == 'sector':
                if 'contains' in condition and condition['contains'].lower() not in str(val).lower(): passed = False; break
                continue

            try:
                val = float(val)
                if 'lt' in condition and val >= float(condition['lt']): passed = False; break
                if 'gt' in condition and val <= float(condition['gt']): passed = False; break
            except: passed = False; break
        if passed:
            pred = get_cached_prediction(sym) or predict_stock(d, SECTOR_PE.get(d.get('sector','Unknown'),25))
            results.append({
                'symbol': sym, 'company_name': d.get('company_name', sym),
                'sector': d.get('sector', ''), 'price': d.get('price', 0),
                'change_pct': d.get('change_pct', 0), 'score': pred.get('score', 50),
                'recommendation': pred.get('recommendation', 'HOLD'),
                'pe_ratio': d.get('pe_ratio'), 'pb_ratio': d.get('pb_ratio'),
                'roe': d.get('roe'), 'debt_to_equity': d.get('debt_to_equity'),
                'revenue_growth': d.get('revenue_growth'), 'profit_growth': d.get('profit_growth'),
                'eps': d.get('eps'),
            })
    results.sort(key=lambda x: x['score'], reverse=True)
    return jsonify({'total': len(results), 'results': results})

# ── Portfolio Management (Supabase) ───────────────────────────────────────────

@app.route('/api/portfolio', methods=['GET'])
def api_get_portfolio():
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    try:
        response = supabase.table("portfolio").select("*").eq("user_id", user['id']).execute()
        rows = response.data
        positions = []
        total_inv = 0; total_cur = 0

        def enrich_position(row):
            sym = row['symbol']
            d = fetch_stock_data(sym)
            cur_price = d.get('price', row['buy_price']) if not d.get('error') else row['buy_price']
            inv = row['buy_price'] * row['quantity']
            cur = cur_price * row['quantity']
            sector = d.get('sector', 'Unknown')
            # Get AI prediction for recommendation
            try:
                sector_pe = SECTOR_PE.get(sector, 25)
                pred = predict_stock(d, sector_pe_avg=sector_pe)
                recommendation = pred.get('recommendation', 'HOLD')
                score = pred.get('score', 50)
                expected_growth_label = pred.get('expected_growth_label', '')
            except Exception:
                recommendation = 'HOLD'
                score = 50
                expected_growth_label = ''
            return {
                'id': row['id'], 'symbol': sym, 'company_name': row.get('company_name', sym),
                'sector': sector,
                'quantity': row['quantity'], 'buy_price': row['buy_price'],
                'current_price': round(cur_price, 2),
                'current_value': round(cur, 2),
                'invested_value': round(inv, 2),
                'pnl': round(cur - inv, 2),
                'pnl_pct': round(((cur - inv) / inv) * 100, 2) if inv else 0,
                'recommendation': recommendation,
                'score': score,
                'expected_growth_label': expected_growth_label
            }, inv, cur

        with ThreadPoolExecutor(max_workers=6) as exc:
            futures = [exc.submit(enrich_position, row) for row in rows]
            for future in as_completed(futures, timeout=30):
                try:
                    pos, inv, cur = future.result()
                    positions.append(pos)
                    total_inv += inv
                    total_cur += cur
                except Exception:
                    pass

        return jsonify({
            'positions': positions,
            'summary': {
                'total_invested': round(total_inv, 2), 'total_current': round(total_cur, 2),
                'total_pnl': round(total_cur - total_inv, 2),
                'total_pnl_pct': round(((total_cur - total_inv) / total_inv) * 100, 2) if total_inv else 0
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/portfolio', methods=['POST'])
def api_add_portfolio():
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    data = request.get_json()
    symbol = data.get('symbol', '').upper()
    qty = int(data.get('quantity', 1))
    buy_price = float(data.get('buy_price', 0))
    d = fetch_stock_data(symbol)
    company_name = d.get('company_name', symbol)
    try:
        entry = {
            "user_id": user['id'], "symbol": symbol, "company_name": company_name,
            "quantity": qty, "buy_price": buy_price
        }
        supabase.table("portfolio").insert(entry).execute()
        return jsonify({'status': 'added'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/portfolio/<int:pid>', methods=['DELETE'])
def api_delete_portfolio(pid):
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    try:
        supabase.table("portfolio").delete().eq("id", pid).eq("user_id", user['id']).execute()
        return jsonify({'status': 'deleted'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/portfolio/optimize')
def api_portfolio_optimize():
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    try:
        # Get user preferences
        try:
            pref_res = supabase.table('user_preferences').select('*').eq('user_id', user['id']).execute()
            pref = pref_res.data[0] if pref_res.data else {}
        except Exception:
            pref = {}
        goal = pref.get('goal', 'Long-term')

        # Get current holdings for context-aware insights
        try:
            port_res = supabase.table("portfolio").select("*").eq("user_id", user['id']).execute()
            holdings = port_res.data or []
        except Exception:
            holdings = []

        target_allocation = {}
        insights = []

        if 'Short' in goal:
            target_allocation = {'Technology': 40, 'Financial Services': 30, 'Energy': 20, 'Consumer': 10}
            insights.append("Short-term profile detected: Overweight Technology & Financials for momentum-driven returns.")
            insights.append("Allocate 40% to high-beta IT stocks (TCS, INFY, WIPRO) for short-term alpha.")
            insights.append("Keep 5-10% cash buffer to capitalize on intraday dips.")
        elif 'Long' in goal:
            target_allocation = {'Financial Services': 35, 'Consumer Defensive': 25, 'Healthcare': 25, 'Technology': 15}
            insights.append("Long-term wealth building: Diversify across Financials, Healthcare & Consumer for compounding growth.")
            insights.append("HDFC Bank & SBI offer reliable dividend yields — ideal for long-term compounders.")
            insights.append("Healthcare allocation (SUN PHARMA, DR REDDY) provides defensive downside protection.")
        else:
            target_allocation = {'Financial Services': 30, 'Technology': 25, 'Consumer': 20, 'Energy': 15, 'Healthcare': 10}
            insights.append("Balanced medium-term allocation: Equal weight across growth and defensive sectors.")
            insights.append("Ensure no single sector exceeds 35% of portfolio to maintain optimal Sharpe ratio.")
            insights.append("Rebalance quarterly to lock in gains and reduce concentration risk.")

        # Personalize based on number of holdings
        if len(holdings) == 0:
            insights.append("Your portfolio is empty. Start with 3-5 diversified blue-chip stocks across different sectors.")
        elif len(holdings) == 1:
            insights.append(f"Single holding detected: Add 2-3 more stocks from different sectors to reduce concentration risk.")
        elif len(holdings) >= 8:
            insights.append("Portfolio has 8+ holdings — consider consolidating into your top 5-6 high-conviction positions.")

        return jsonify({
            'target_allocation': target_allocation,
            'insights': insights
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Watchlist Management (Supabase) ───────────────────────────────────────────

@app.route('/api/watchlist', methods=['GET'])
def api_get_watchlist():
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    try:
        response = supabase.table("watchlist").select("*").eq("user_id", user['id']).execute()
        rows = response.data
    except Exception as e:
        print("\nWATCHLIST TABLE MISSING IN SUPABASE")
        print("Run this SQL in Supabase: CREATE TABLE watchlist ( id bigint generated by default as identity primary key, user_id uuid references auth.users(id), symbol text );\n")
        return jsonify({'watchlist': [], 'results': []})
        
    try:
        results = []
        for row in rows:
            sym = row['symbol']
            d = fetch_stock_data(sym)
            if not d.get('error'):
                results.append({
                    'id': row['id'], 'symbol': sym, 'company_name': d.get('company_name', sym),
                    'price': d.get('price', 0), 'change_pct': d.get('change_pct', 0)
                })
        return jsonify({'watchlist': results, 'results': results})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/watchlist', methods=['POST'])
def api_add_watchlist():
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    data = request.get_json()
    symbol = data.get('symbol', '').upper()
    try:
        existing = supabase.table("watchlist").select("*").eq("user_id", user['id']).eq("symbol", symbol).execute()
        if existing.data:
            return jsonify({'status': 'already exists'})
        entry = {"user_id": user['id'], "symbol": symbol}
        supabase.table("watchlist").insert(entry).execute()
        return jsonify({'status': 'added'})
    except Exception as e:
        print(f"Error adding to watchlist: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/watchlist/<int:wid>', methods=['DELETE'])
def api_delete_watchlist(wid):
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    try:
        supabase.table("watchlist").delete().eq("id", wid).eq("user_id", user['id']).execute()
        return jsonify({'status': 'deleted'})
    except Exception as e:
        print(f"Error deleting from watchlist: {e}")
        return jsonify({'error': str(e)}), 500

# ── Personalization (Preferences) ──────────────────────────────────────────────

@app.route('/api/preferences', methods=['GET', 'POST'])
def api_preferences():
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    
    if request.method == 'GET':
        try:
            res = supabase.table("user_preferences").select("*").eq("user_id", user['id']).execute()
            if res.data: return jsonify({'preferences': res.data[0]})
            return jsonify({'preferences': {}})
        except Exception as e:
            print("\nTABLE MISSING: CREATE TABLE user_preferences (user_id uuid primary key, goal text, experience text, custom_filters jsonb);")
            return jsonify({'preferences': {}})
            
    if request.method == 'POST':
        data = request.get_json()
        payload = {"user_id": user['id']}
        if 'goal' in data: payload['goal'] = data['goal']
        if 'experience' in data: payload['experience'] = data['experience']
        if 'custom_filters' in data: payload['custom_filters'] = data['custom_filters']
        
        try:
            supabase.table("user_preferences").upsert(payload).execute()
            return jsonify({'status': 'saved'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

# ── Notifications & Run ───────────────────────────────────────────────────────

@app.route('/api/alerts', methods=['GET'])
def api_get_alerts():
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    res = supabase.table("alerts").select("*").eq("user_id", user['id']).execute()
    return jsonify({'alerts': res.data})

@app.route('/api/alerts', methods=['POST'])
def api_add_alert():
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    data = request.get_json()
    entry = {
        "user_id": user['id'], "symbol": data.get('symbol','').upper(),
        "target_price": data.get('target_price',0), "alert_type": data.get('alert_type','price')
    }
    supabase.table("alerts").insert(entry).execute()
    return jsonify({'status': 'alert set'})

# ── Chatbot NLP (ZenBot 2.0 — Gemini AI) ─────────────────────────────────
@app.route('/api/chat', methods=['POST'])
def api_chat():
    data = request.get_json()
    msg = data.get('message', '')
    user = getattr(g, 'user', None)

    # Build context for AI
    portfolio_context = ""
    if user:
        try:
            port_res = supabase.table("portfolio").select("*").eq("user_id", user['id']).execute()
            holdings = port_res.data or []
            if holdings:
                holding_strs = [f"{h['symbol']} (qty:{h['quantity']}, buy:₹{h['buy_price']})" for h in holdings[:5]]
                portfolio_context = f"User's portfolio: {', '.join(holding_strs)}. "
        except Exception:
            pass

    if gemini_model:
        try:
            system_prompt = f"""You are ZenBot, an expert Indian stock market AI assistant for the Zenvest platform.
You help users with stock analysis, portfolio advice, and investment strategies for NSE/BSE markets.
{portfolio_context}
Key rules:
- Always refer to Indian stocks (NSE/BSE), mention Rs for prices
- Give specific, actionable advice mentioning real stock symbols
- Keep responses concise (2-4 sentences max)
- Add **bold** around key stock names and numbers
- Always add disclaimer: "Not financial advice - for educational purposes only"
- Be friendly and professional"""
            full_prompt = f"{system_prompt}\n\nUser: {msg}\nZenBot:"
            # New google-genai SDK
            try:
                result = gemini_model.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=full_prompt
                )
                reply = result.text.strip()
            except AttributeError:
                # Legacy SDK fallback
                result = gemini_model.generate_content(full_prompt)
                reply = result.text.strip()
            return jsonify({'reply': reply, 'ai_powered': True})
        except Exception as e:
            print(f"Gemini error: {e}")
            # Fall through to rule-based


    # Rule-based fallback
    msg_lower = msg.lower()
    response = "I am ZenBot, your AI financial assistant. Ask me about stocks, portfolio advice, or investment strategies! 🚀"
    if 'invest' in msg_lower or 'buy' in msg_lower or 'recommend' in msg_lower:
        if '10000' in msg_lower or '10k' in msg_lower:
            response = "For ₹10,000, consider **TATA POWER** or **IRCTC** — strong mid-cap growth stories with solid fundamentals. *Not financial advice.*"
        elif 'tech' in msg_lower or 'it' in msg_lower:
            response = "Top IT picks: **TCS**, **INFY**, and **PERSISTENT** show strong buy signals based on SMA crosses and revenue growth. *Not financial advice.*"
        else:
            response = "**RELIANCE** and **HDFCBANK** are reliable blue-chip picks. Check the 'AI Recommendations' tab for your personalized Top 10! *Not financial advice.*"
    elif 'portfolio' in msg_lower or 'optimize' in msg_lower:
        response = "Go to your **Portfolio** tab → click 'Generate Optimal Allocation' for AI-driven sector rebalancing suggestions. *Not financial advice.*"
    elif 'risk' in msg_lower or 'safe' in msg_lower or 'dividend' in msg_lower:
        response = "For low-risk, use the **Screener** with Div Yield > 2% filter. **ITC**, **COALINDIA**, and **HINDUNILVR** are classic defensive plays. *Not financial advice.*"
    elif 'midcap' in msg_lower or 'small' in msg_lower:
        response = "Check **ZOMATO**, **IRCTC**, **PERSISTENT** in our expanded Midcap universe via the Screener. High growth potential, higher risk. *Not financial advice.*"
    return jsonify({'reply': response, 'ai_powered': False})

# ── Portfolio Edit ──────────────────────────────────────────────────────────
@app.route('/api/portfolio/<int:pid>', methods=['PUT'])
def api_edit_portfolio(pid):
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    data = request.get_json()
    update = {}
    if 'quantity' in data: update['quantity'] = int(data['quantity'])
    if 'buy_price' in data: update['buy_price'] = float(data['buy_price'])
    if not update: return jsonify({'error': 'Nothing to update'}), 400
    try:
        supabase.table("portfolio").update(update).eq("id", pid).eq("user_id", user['id']).execute()
        return jsonify({'status': 'updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Portfolio History ───────────────────────────────────────────────────────
@app.route('/api/portfolio/history', methods=['GET'])
def api_portfolio_history():
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    days = int(request.args.get('days', 30))
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        res = supabase.table("portfolio_history").select("*") \
            .eq("user_id", user['id']) \
            .gte("snapshot_date", since) \
            .order("snapshot_date").execute()
        return jsonify({'history': res.data or []})
    except Exception as e:
        return jsonify({'history': [], 'error': str(e)})

@app.route('/api/portfolio/snapshot', methods=['POST'])
def api_portfolio_snapshot():
    """Called to take a daily snapshot of portfolio value."""
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    try:
        port_res = supabase.table("portfolio").select("*").eq("user_id", user['id']).execute()
        rows = port_res.data or []
        if not rows: return jsonify({'status': 'no holdings'})
        total = 0
        for row in rows:
            d = fetch_stock_data(row['symbol'])
            cur_price = d.get('price', row['buy_price']) if not d.get('error') else row['buy_price']
            total += cur_price * row['quantity']
        today = datetime.now(timezone.utc).date().isoformat()
        supabase.table("portfolio_history").upsert({
            "user_id": user['id'], "total_value": round(total, 2), "snapshot_date": today
        }, on_conflict="user_id,snapshot_date").execute()
        return jsonify({'status': 'snapshot saved', 'total_value': round(total, 2)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── User Profile ────────────────────────────────────────────────────────────
@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=getattr(g, 'user', None))

@app.route('/api/user/profile', methods=['GET', 'PUT'])
def api_user_profile():
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    if request.method == 'GET':
        try:
            res = supabase.table("users").select("id,name,email,risk_level,budget,created_at").eq("id", user['id']).execute()
            return jsonify({'profile': res.data[0] if res.data else {}})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    if request.method == 'PUT':
        data = request.get_json()
        update = {}
        if 'name' in data and data['name'].strip(): update['name'] = escape(data['name'].strip())
        if 'risk_level' in data: update['risk_level'] = data['risk_level']
        if 'budget' in data: update['budget'] = float(data['budget'])
        if not update: return jsonify({'error': 'Nothing to update'}), 400
        try:
            supabase.table("users").update(update).eq("id", user['id']).execute()
            return jsonify({'status': 'updated'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

# ── Multi-stock Compare ─────────────────────────────────────────────────────
@app.route('/compare')
def compare():
    return render_template('compare.html', user=getattr(g, 'user', None))

@app.route('/api/compare')
def api_compare():
    symbols_raw = request.args.get('symbols', '')
    symbols = [s.strip().upper() for s in symbols_raw.split(',') if s.strip()][:3]
    if len(symbols) < 2:
        return jsonify({'error': 'Provide at least 2 symbols'}), 400
    results = []
    for sym in symbols:
        if not sym.endswith('.NS'): sym = sym + '.NS'
        d = fetch_stock_data(sym)
        if not d.get('error'):
            sector_pe = SECTOR_PE.get(d.get('sector', 'Unknown'), 25)
            pred = predict_stock(d, sector_pe_avg=sector_pe)
            results.append({
                'symbol': sym, 'company_name': d.get('company_name', sym),
                'price': d.get('price', 0), 'change_pct': d.get('change_pct', 0),
                'sector': d.get('sector', ''), 'pe_ratio': d.get('pe_ratio'),
                'pb_ratio': d.get('pb_ratio'), 'roe': d.get('roe'),
                'debt_to_equity': d.get('debt_to_equity'),
                'revenue_growth': d.get('revenue_growth'),
                'profit_growth': d.get('profit_growth'),
                'eps': d.get('eps'), 'beta': d.get('beta'),
                'market_cap': d.get('market_cap'),
                'dividend_yield': d.get('dividend_yield'),
                'score': pred.get('score', 50),
                'recommendation': pred.get('recommendation', 'HOLD'),
                'summary': pred.get('summary', '')
            })
    return jsonify({'results': results})

# ── News Aggregation ────────────────────────────────────────────────────────
@app.route('/news')
def news_page():
    return render_template('news.html', user=getattr(g, 'user', None))

@app.route('/api/news/market')
def api_market_news():
    top_stocks = NIFTY50[:12]
    all_news = []
    def fetch_for(sym):
        items = fetch_news_sentiment(sym)
        for n in (items or [])[:3]:
            n['stock'] = sym.replace('.NS', '')
        return (items or [])[:3]
    with ThreadPoolExecutor(max_workers=6) as exc:
        futures = [exc.submit(fetch_for, sym) for sym in top_stocks]
        for f in as_completed(futures, timeout=20):
            try:
                all_news.extend(f.result())
            except Exception:
                pass
    all_news.sort(key=lambda x: x.get('published_at', ''), reverse=True)
    return jsonify({'news': all_news[:40]})

# ── Alert Delete ─────────────────────────────────────────────────────────────
@app.route('/api/alerts/<int:aid>', methods=['DELETE'])
def api_delete_alert(aid):
    user = getattr(g, 'user', None)
    if not user: return jsonify({'error': 'Not authenticated'}), 401
    try:
        supabase.table("alerts").delete().eq("id", aid).eq("user_id", user['id']).execute()
        return jsonify({'status': 'deleted'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── APScheduler: Live Price Alert Checker ─────────────────────────────────
def check_price_alerts():
    """Background job: checks all alerts every 30 min and fires emails."""
    try:
        res = supabase.table("alerts").select("*").execute()
        alerts = res.data or []
        for alert in alerts:
            sym = alert.get('symbol', '')
            target = float(alert.get('target_price', 0))
            alert_type = alert.get('alert_type', 'price')
            user_id = alert.get('user_id', '')
            if not sym or not target or not user_id: continue
            try:
                user_res = supabase.table("users").select("email,name").eq("id", user_id).execute()
                user_data = user_res.data[0] if user_res.data else {}
                user_email = user_data.get('email', '')
                user_name = user_data.get('name', 'Investor')
            except Exception:
                continue
            d = fetch_stock_data(sym)
            cur = d.get('price', 0)
            if not cur: continue
            triggered = False
            direction = ''
            if alert_type in ('above', 'price') and cur >= target:
                triggered = True; direction = f"risen above ₹{target:,.2f}"
            elif alert_type == 'below' and cur <= target:
                triggered = True; direction = f"fallen below ₹{target:,.2f}"
            if triggered and RESEND_API_KEY and user_email:
                try:
                    resend.Emails.send({
                        "from": FROM_EMAIL, "to": user_email,
                        "subject": f"🔔 Zenvest Alert: {sym} {direction}",
                        "html": f"""<div style="font-family:Inter,sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#fff;border-radius:12px;border:1px solid #e5e7eb">
  <div style="text-align:center;margin-bottom:20px"><div style="width:48px;height:48px;background:#6c47ff;border-radius:10px;display:inline-flex;align-items:center;justify-content:center;font-size:24px;font-weight:800;color:#fff">Z</div></div>
  <h2 style="color:#6c47ff">🔔 Price Alert Triggered!</h2>
  <p>Hi <b>{user_name}</b>, your price alert for <b>{sym}</b> has been triggered.</p>
  <p style="font-size:20px"><b>{sym}</b> has {direction}.<br>Current Price: <b style="color:#0d0d0d">₹{cur:,.2f}</b></p>
  <p style="color:#888;font-size:12px">⚠️ Not financial advice — Zenvest Educational Platform</p>
</div>"""
                    })
                    supabase.table("alerts").delete().eq("id", alert['id']).execute()
                    print(f"✅ Alert email sent to {user_email}: {sym} @ ₹{cur}")
                except Exception as e:
                    print(f"Alert email error: {e}")
    except Exception as e:
        print(f"Alert checker error: {e}")

# ── Screener Full Universe ──────────────────────────────────────────────────
@app.route('/api/screener/full', methods=['POST'])
def api_screener_full():
    filters = request.get_json() or {}
    results = []
    for sym in FULL_UNIVERSE:
        d = fetch_stock_data(sym)
        if d.get('error'): continue
        passed = True
        for field, condition in filters.items():
            val = d.get(field)
            if val is None: passed = False; break
            if field == 'sector':
                if 'contains' in condition and condition['contains'].lower() not in str(val).lower():
                    passed = False; break
                continue
            try:
                val = float(val)
                if 'lt' in condition and val >= float(condition['lt']): passed = False; break
                if 'gt' in condition and val <= float(condition['gt']): passed = False; break
            except: passed = False; break
        if passed:
            pred = get_cached_prediction(sym) or predict_stock(d, SECTOR_PE.get(d.get('sector','Unknown'),25))
            results.append({
                'symbol': sym, 'company_name': d.get('company_name', sym),
                'sector': d.get('sector', ''), 'price': d.get('price', 0),
                'change_pct': d.get('change_pct', 0), 'score': pred.get('score', 50),
                'recommendation': pred.get('recommendation', 'HOLD'),
                'pe_ratio': d.get('pe_ratio'), 'pb_ratio': d.get('pb_ratio'),
                'roe': d.get('roe'), 'debt_to_equity': d.get('debt_to_equity'),
                'revenue_growth': d.get('revenue_growth'), 'profit_growth': d.get('profit_growth'),
                'eps': d.get('eps'),
            })
    results.sort(key=lambda x: x['score'], reverse=True)
    return jsonify({'total': len(results), 'results': results})

# ════════════════════════════════════════════════════════════════════════════
# FEATURE: Technical Indicators (RSI, MACD, Bollinger Bands via pandas-ta)
# ════════════════════════════════════════════════════════════════════════════
@app.route('/api/stock/<symbol>/technicals')
def api_technicals(symbol):
    """Return RSI, MACD, Bollinger Bands for the last 120 trading days."""
    try:
        import yfinance as yf
        import pandas as pd
        ticker = yf.Ticker(symbol if symbol.endswith('.NS') else symbol + '.NS')
        hist = ticker.history(period='6mo', interval='1d')
        if hist.empty:
            return jsonify({'error': 'No data'}), 404

        close = hist['Close']
        n = len(close)

        # ── RSI (14) ──────────────────────────────────────────────────────
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss
        rsi = (100 - 100 / (1 + rs)).round(2)

        # ── MACD (12,26,9) ────────────────────────────────────────────────
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = (ema12 - ema26).round(2)
        signal_line = macd_line.ewm(span=9, adjust=False).mean().round(2)
        macd_hist = (macd_line - signal_line).round(2)

        # ── Bollinger Bands (20, 2) ───────────────────────────────────────
        sma20 = close.rolling(20).mean().round(2)
        std20 = close.rolling(20).std().round(2)
        bb_upper = (sma20 + 2 * std20).round(2)
        bb_lower = (sma20 - 2 * std20).round(2)

        dates = hist.index.strftime('%Y-%m-%d').tolist()
        closes = close.round(2).tolist()

        def safe_list(series):
            return [None if pd.isna(x) else float(x) for x in series.tolist()]

        return jsonify({
            'dates':        dates,
            'close':        closes,
            'rsi':          safe_list(rsi),
            'macd':         safe_list(macd_line),
            'macd_signal':  safe_list(signal_line),
            'macd_hist':    safe_list(macd_hist),
            'bb_upper':     safe_list(bb_upper),
            'bb_middle':    safe_list(sma20),
            'bb_lower':     safe_list(bb_lower),
            'latest': {
                'rsi':         round(float(rsi.dropna().iloc[-1]), 2) if not rsi.dropna().empty else None,
                'macd':        round(float(macd_line.dropna().iloc[-1]), 2) if not macd_line.dropna().empty else None,
                'macd_signal': round(float(signal_line.dropna().iloc[-1]), 2) if not signal_line.dropna().empty else None,
                'bb_upper':    round(float(bb_upper.dropna().iloc[-1]), 2) if not bb_upper.dropna().empty else None,
                'bb_lower':    round(float(bb_lower.dropna().iloc[-1]), 2) if not bb_lower.dropna().empty else None,
                'close':       round(float(close.iloc[-1]), 2)
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ════════════════════════════════════════════════════════════════════════════
# FEATURE: Options Chain Viewer (NSE via yfinance options)
# ════════════════════════════════════════════════════════════════════════════
@app.route('/options')
def options_page():
    return render_template('options.html', user=getattr(g, 'user', None))

@app.route('/api/options/<symbol>')
def api_options_chain(symbol):
    """Fetch options chain for a symbol from yfinance."""
    try:
        import yfinance as yf
        import pandas as pd
        import math

        # Map common names to yfinance symbols
        sym_map = {
            'NIFTY':     '^NSEI',
            'BANKNIFTY': '^NSEBANK',
            'SENSEX':    '^BSESN',
        }
        yfym = sym_map.get(symbol.upper(), symbol.upper() + '.NS' if not symbol.endswith('.NS') else symbol)
        ticker = yf.Ticker(yfym)
        expirations = ticker.options
        if not expirations:
            return jsonify({'error': f'No options data for {symbol}'}), 404

        # Default to nearest expiry
        exp = request.args.get('expiry', expirations[0])
        if exp not in expirations:
            exp = expirations[0]

        chain = ticker.option_chain(exp)
        spot = ticker.info.get('regularMarketPrice') or ticker.history(period='1d')['Close'].iloc[-1]

        def clean_df(df, kind):
            rows = []
            for _, r in df.iterrows():
                iv = r.get('impliedVolatility', 0)
                rows.append({
                    'type':           kind,
                    'strike':         round(float(r['strike']), 2),
                    'lastPrice':      round(float(r.get('lastPrice', 0)), 2),
                    'bid':            round(float(r.get('bid', 0)), 2),
                    'ask':            round(float(r.get('ask', 0)), 2),
                    'volume':         int(r.get('volume', 0) or 0),
                    'openInterest':   int(r.get('openInterest', 0) or 0),
                    'iv':             round(float(iv) * 100, 1) if iv and not math.isnan(iv) else 0,
                    'inTheMoney':     bool(r.get('inTheMoney', False)),
                })
            return rows

        calls = clean_df(chain.calls, 'call')
        puts  = clean_df(chain.puts, 'put')

        # Build strikes table merging calls & puts
        call_map = {r['strike']: r for r in calls}
        put_map  = {r['strike']: r for r in puts}
        all_strikes = sorted(set(call_map) | set(put_map))

        chain_rows = []
        for strike in all_strikes:
            c = call_map.get(strike, {})
            p = put_map.get(strike, {})
            chain_rows.append({
                'strike':      strike,
                'call_ltp':    c.get('lastPrice', '-'),
                'call_oi':     c.get('openInterest', 0),
                'call_iv':     c.get('iv', 0),
                'call_vol':    c.get('volume', 0),
                'call_itm':    c.get('inTheMoney', False),
                'put_ltp':     p.get('lastPrice', '-'),
                'put_oi':      p.get('openInterest', 0),
                'put_iv':      p.get('iv', 0),
                'put_vol':     p.get('volume', 0),
                'put_itm':     p.get('inTheMoney', False),
            })

        return jsonify({
            'symbol':      symbol.upper(),
            'spot':        round(float(spot), 2),
            'expiry':      exp,
            'expirations': list(expirations[:8]),
            'chain':       chain_rows
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ════════════════════════════════════════════════════════════════════════════
# FEATURE: Sector Heatmap (treemap-style data)
# ════════════════════════════════════════════════════════════════════════════
@app.route('/heatmap')
def heatmap_page():
    return render_template('heatmap.html', user=getattr(g, 'user', None))

@app.route('/api/heatmap')
def api_heatmap():
    """Return sector + stock performance data for treemap rendering."""
    try:
        stocks_to_scan = NIFTY50  # Use Nifty50 for speed
        results = []

        def fetch_basic(sym):
            try:
                d = fetch_stock_data(sym)
                if d.get('error'): return None
                return {
                    'symbol':       sym.replace('.NS', ''),
                    'company_name': d.get('company_name', sym),
                    'sector':       d.get('sector', 'Unknown'),
                    'price':        d.get('price', 0),
                    'change_pct':   round(d.get('change_pct', 0), 2),
                    'market_cap':   d.get('market_cap', 0) or 0,
                }
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=10) as exc:
            futures = {exc.submit(fetch_basic, sym): sym for sym in stocks_to_scan}
            for f in as_completed(futures, timeout=25):
                try:
                    r = f.result()
                    if r: results.append(r)
                except Exception:
                    pass

        # Group by sector
        sectors = {}
        for r in results:
            sec = r['sector'] or 'Unknown'
            if sec not in sectors:
                sectors[sec] = {'sector': sec, 'stocks': [], 'avg_change': 0, 'total_market_cap': 0}
            sectors[sec]['stocks'].append(r)
            sectors[sec]['total_market_cap'] += r['market_cap']

        # Compute sector averages
        sector_list = []
        for sec, sdata in sectors.items():
            stocks = sdata['stocks']
            if not stocks: continue
            avg_chg = round(sum(s['change_pct'] for s in stocks) / len(stocks), 2)
            sector_list.append({
                'sector':          sec,
                'avg_change':      avg_chg,
                'total_market_cap': sdata['total_market_cap'],
                'stock_count':     len(stocks),
                'stocks':          sorted(stocks, key=lambda x: abs(x['change_pct']), reverse=True)[:8]
            })

        sector_list.sort(key=lambda x: x['avg_change'], reverse=True)
        return jsonify({'sectors': sector_list, 'total_stocks': len(results)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _warm_cache():
    """Background thread: pre-fetches Nifty50 + index data at startup.
    Waits for network to be available before fetching.
    """
    import socket
    # Wait for the OS network stack + DNS to be ready (avoids getaddrinfo failures on fast startup)
    time.sleep(8)

    # Quick connectivity probe — bail silently if we're offline
    try:
        socket.getaddrinfo('query2.finance.yahoo.com', 443)
    except OSError:
        print('[Cache Warmer] No internet connection detected — skipping startup cache warm-up.')
        return

    print('[Cache Warmer] Starting background cache warm-up for top stocks...')
    INDEX_SYMS = ['^NSEI', '^BSESN', '^NSEBANK']
    warm_targets = INDEX_SYMS + NIFTY50[:20]  # top-20 Nifty stocks + indices
    from fetch_stock import prefetch_symbols
    prefetch_symbols(warm_targets, max_workers=10)
    print(f'[Cache Warmer] Done — {len(warm_targets)} symbols pre-cached.')

if __name__ == '__main__':
    if SCHEDULER_AVAILABLE:
        scheduler = BackgroundScheduler()
        scheduler.add_job(check_price_alerts, 'interval', minutes=30, id='alert_checker')
        scheduler.start()
        print('✅ APScheduler started — price alerts checked every 30 min')

    # Kick off cache warming in a background thread (non-blocking)
    threading.Thread(target=_warm_cache, daemon=True).start()

    print('Zenvest (Supabase Mode) starting on http://localhost:5001')
    app.run(debug=True, port=5001)

