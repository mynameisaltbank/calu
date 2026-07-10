import os
import datetime
import sqlite3
import json
import requests
from flask import Flask, render_template, request, jsonify, send_file
import google.generativeai as genai
import pandas as pd

app = Flask(__name__)

# Configuration (Railway environment variables or fallback)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
LINE_NOTIFY_TOKEN = os.environ.get("LINE_NOTIFY_TOKEN", "YOUR_LINE_NOTIFY_TOKEN")
DB_PATH = "nutrition_tracker.db"

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Initialize Database
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # กำหนดโครงสร้างคอลัมน์เก็บข้อมูลวันที่ เวลา และสารอาหารหลักให้ชัดเจน
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
            meal_name TEXT,
            calories REAL,
            protein REAL,
            carbs REAL,
            fat REAL,
            raw_json TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/scan', methods=['POST'])
def scan_food():
    if 'image' not in request.files:
        return jsonify({"error": "No image file provided"}), 400
        
    image_file = request.files['image']
    image_data = image_file.read()
    
    PROTEIN_TARGET = 140.0 
    
    prompt = """
    You are an expert nutritionist AI. Analyze this Thai food image and estimate its macronutrients (Protein, Carbs, Fat) and Total Calories.
    Be mindful of hidden oils in Thai stir-fried or deep-fried dishes. 
    Respond ONLY with a valid JSON object matching this structure:
    {
      "mealName": "Name of the dish in Thai",
      "calories": 120.0,
      "protein": 15.0,
      "carbs": 20.0,
      "fat": 8.0,
      "explanation": "Brief explanation in Thai why you gave these values"
    }
    """
    
    try:
        # บันทึกวันเวลาตามจริง ณ โมเมนต์ที่กดสแกนด้วย Server Python
        now = datetime.datetime.now()
        current_date = now.strftime('%Y-%m-%d')
        current_time = now.strftime('%H:%M:%S')

        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # บังคับรูปแบบการตอบกลับเป็น JSON ป้องกัน Error ในขั้นตอนการ parse string
        response = model.generate_content(
            [prompt, {"mime_type": image_file.content_type, "data": image_data}],
            generation_config={"response_mime_type": "application/json"}
        )
        
        text = response.text.strip()
        nutrition_data = json.loads(text)
        
        # บันทึกลงระบบฐานข้อมูล
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO daily_logs (date, time, meal_name, calories, protein, carbs, fat, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            current_date,
            current_time,
            nutrition_data['mealName'], 
            nutrition_data['calories'], 
            nutrition_data['protein'], 
            nutrition_data['carbs'], 
            nutrition_data['fat'], 
            text
        ))
        conn.commit()
        
        # คำนวณปริมาณโปรตีนสะสมเฉพาะของวันนี้
        cursor.execute("SELECT SUM(protein) FROM daily_logs WHERE date = ?", (current_date,))
        total_protein_today = cursor.fetchone()[0] or 0.0
        conn.close()
        
        # ส่งแจ้งเตือนผ่านทาง LINE Notify
        send_line_notification(nutrition_data, total_protein_today, PROTEIN_TARGET)
        
        return jsonify({
            "status": "success",
            "data": nutrition_data,
            "scan_date": current_date,
            "scan_time": current_time,
            "total_protein_today": total_protein_today,
            "target_protein": PROTEIN_TARGET
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def send_line_notification(meal_data, current_total, target):
    if not LINE_NOTIFY_TOKEN or LINE_NOTIFY_TOKEN == "YOUR_LINE_NOTIFY_TOKEN":
        return
        
    remaining = max(0.0, target - current_total)
    message = (
        f"\n🍽️ บันทึกเมนูอาหารเรียบร้อย!\n"
        f"เมนู: {meal_data['mealName']}\n"
        f"🔥 พลังงาน: {meal_data['calories']} kcal\n"
        f"💪 โปรตีนมื้อนี้: {meal_data['protein']} กรัม\n"
        f"----------------------\n"
        f"📊 รวมวันนี้กินโปรตีนไปแล้ว: {current_total:.1f} / {target} กรัม\n"
    )
    
    if current_total >= target:
        message += "🎉 ยินดีด้วยครับ! วันนี้คุณกินโปรตีนถึงเป้าหมายแล้ว เส้นผมและกล้ามเนื้อแข็งแรงแน่นอน! 🦁"
    else:
        message += f"⚠️ วันนี้โปรตีนยังขาดอีก {remaining:.1f} กรัม อย่าลืมเติมเวย์โปรตีนหรือไข่ต้มนะครับ! 🥚🥤"
        
    url = "https://notify-api.line.me/api/notify"
    headers = {"Authorization": f"Bearer {LINE_NOTIFY_TOKEN}"}
    data = {"message": message}
    try:
        requests.post(url, headers=headers, data=data)
    except Exception:
        pass

@app.route('/api/dashboard')
def dashboard_data():
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(calories), SUM(protein), SUM(carbs), SUM(fat) FROM daily_logs WHERE date = ?", (today_str,))
    row = cursor.fetchone()
    
    cursor.execute("SELECT id, time, meal_name, calories, protein FROM daily_logs WHERE date = ? ORDER BY id DESC", (today_str,))
    meals = [{"id": r[0], "time": r[1][:5], "name": r[2], "calories": r[3], "protein": r[4]} for r in cursor.fetchall()]
    conn.close()
    
    return jsonify({
        "calories": row[0] or 0,
        "protein": row[1] or 0,
        "carbs": row[2] or 0,
        "fat": row[3] or 0,
        "meals": meals
    })

# --- ส่วนดึงข้อมูลสำหรับนำไปทำกราฟเปรียบเทียบสารอาหารแต่ละวัน ---
@app.route('/api/chart-data')
def chart_data():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # ดึงสรุปยอดรวมแบ่งรายวัน ย้อนหลังสูงสุด 7 วันที่มีประวัติการทาน
        cursor.execute('''
            SELECT date, 
                   SUM(calories) as total_cal, 
                   SUM(protein) as total_protein, 
                   SUM(carbs) as total_carbs, 
                   SUM(fat) as total_fat 
            FROM daily_logs 
            GROUP BY date 
            ORDER BY date DESC 
            LIMIT 7
        ''')
        rows = cursor.fetchall()
        conn.close()
        
        # เรียงข้อมูลกลับด้านให้อดีตไปหาปัจจุบันเพื่อให้พล็อตกราฟจากซ้ายไปขวาได้อย่างสวยงาม
        chart_list = []
        for r in reversed(rows):
            chart_list.append({
                "date": r[0],
                "calories": round(r[1], 1) if r[1] else 0,
                "protein": round(r[2], 1) if r[2] else 0,
                "carbs": round(r[3], 1) if r[3] else 0,
                "fat": round(r[4], 1) if r[4] else 0
            })
            
        return jsonify({"status": "success", "chart_data": chart_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/export-excel')
def export_excel():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT date, time, meal_name, calories, protein, carbs, fat FROM daily_logs ORDER BY id DESC", conn)
    conn.close()
    
    filename = "nutrition_history.xlsx"
    df.to_excel(filename, index=False, sheet_name="ประวัติการทานอาหาร")
    return send_file(filename, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=True)
