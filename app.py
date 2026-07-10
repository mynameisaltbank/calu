import os
import datetime
import sqlite3
import json
import requests
import base64
from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd

app = Flask(__name__)

# Configuration (Railway environment variables)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
LINE_NOTIFY_TOKEN = os.environ.get("LINE_NOTIFY_TOKEN", "")
DB_PATH = "nutrition_tracker.db"

# Initialize Database
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT DEFAULT (date('now', 'localtime')),
            time TEXT DEFAULT (time('now', 'localtime')),
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
    image_content_type = image_file.content_type
    image_data = image_file.read()
    
    PROTEIN_TARGET = 140.0 
    
    # แปลงรูปภาพเป็น Base64 สำหรับส่งผ่าน REST API ของ Google
    base64_image = base64.b64encode(image_data).decode('utf-8')
    
    prompt = """
    You are an expert nutritionist AI. Analyze this Thai food image and estimate its macronutrients (Protein, Carbs, Fat) and Total Calories.
    Be mindful of hidden oils in Thai stir-fried or deep-fried dishes. 
    Respond ONLY with a valid JSON object matching this structure (No markdown fences like ```json):
    {
      "mealName": "Name of the dish in Thai",
      "calories": 120.0,
      "protein": 15.0,
      "carbs": 20.0,
      "fat": 8.0,
      "explanation": "Brief explanation in Thai why you gave these values"
    }
    """
    
    # ยิงเข้า REST API โดยตรงเพื่อเลี่ยงปัญหาไลบรารีพัง
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {
                    "inlineData": {
                        "mimeType": image_content_type,
                        "data": base64_image
                    }
                }
            ]
        }]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response_json = response.json()
        
        # ดักจับ Error จากทาง Google Direct API
        if "error" in response_json:
            return jsonify({"error": f"Google API Error: {response_json['error']['message']}"}), 400
            
        # ดึง Text ผลลัพธ์ออกมา
        text_result = response_json['candidates'][0]['content']['parts'][0]['text'].strip()
        
        # คลีนฟอร์แมตหาก AI เผลอใส่โครงสร้างครอบสัญลักษณ์โค้ดมา
        if text_result.startswith("```json"):
            text_result = text_result.split("```json")[1].split("```")[0].strip()
        elif text_result.startswith("```"):
            text_result = text_result.split("```")[1].split("```")[0].strip()
            
        nutrition_data = json.loads(text_result)
        
        # บันทึกลง SQLite
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO daily_logs (meal_name, calories, protein, carbs, fat, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            nutrition_data['mealName'], 
            nutrition_data['calories'], 
            nutrition_data['protein'], 
            nutrition_data['carbs'], 
            nutrition_data['fat'], 
            text_result
        ))
        conn.commit()
        
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        cursor.execute("SELECT SUM(protein) FROM daily_logs WHERE date = ?", (today_str,))
        total_protein_today = cursor.fetchone()[0] or 0.0
        conn.close()
        
        send_line_notification(nutrition_data, total_protein_today, PROTEIN_TARGET)
        
        return jsonify({
            "status": "success",
            "data": nutrition_data,
            "total_protein_today": total_protein_today,
            "target_protein": PROTEIN_TARGET
        })
        
    except Exception as e:
        return jsonify({"error": f"System Exception: {str(e)}"}), 500

def send_line_notification(meal_data, current_total, target):
    if not LINE_NOTIFY_TOKEN or LINE_NOTIFY_TOKEN == "YOUR_LINE_NOTIFY_TOKEN" or LINE_NOTIFY_TOKEN == "":
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
        
    url = "[https://notify-api.line.me/api/notify](https://notify-api.line.me/api/notify)"
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

@app.route('/api/export-excel')
def export_excel():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT date, time, meal_name, calories, protein, carbs, fat FROM daily_logs ORDER BY id DESC", conn)
    conn.close()
    
    filename = "nutrition_history.xlsx"
    df.to_excel(filename, index=False, sheet_name="ประวัติการทานอาหาร")
    return send_file(filename, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=False)
