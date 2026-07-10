import os
import datetime
import sqlite3
import json
import requests
import base64
from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd

app = Flask(__name__)

# ดึงค่าความปลอดภัยจาก Railway Variables
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
LINE_NOTIFY_TOKEN = os.environ.get("LINE_NOTIFY_TOKEN", "").strip()
DB_PATH = "nutrition_tracker.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # 1. ตารางบันทึกสารอาหารเดิม
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
    # 2. ตารางบันทึกการทำ IF เพิ่มเติมเพื่อจัดช่วงเวลาการกิน
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS if_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT DEFAULT (date('now', 'localtime')),
            fast_start TEXT,
            fast_end TEXT,
            eat_start TEXT,
            eat_end TEXT,
            if_type TEXT DEFAULT '16:8'
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
    if not GEMINI_API_KEY:
        return jsonify({"error": "ไม่พบ API Key ในระบบ กรุณาเช็กแท็บ Variables บน Railway"}), 400

    if 'image' not in request.files:
        return jsonify({"error": "No image file provided"}), 400
        
    image_file = request.files['image']
    image_content_type = image_file.content_type
    image_data = image_file.read()
    
    PROTEIN_TARGET = 140.0 
    base64_image = base64.b64encode(image_data).decode('utf-8')
    
    # ดึงประวัติอาหารของวันนี้มาให้ AI วิเคราะห์ประกอบการแนะนำ
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT meal_name, protein, calories FROM daily_logs WHERE date = ?", (today_str,))
    history_rows = cursor.fetchall()
    conn.close()
    
    history_context = ""
    if history_rows:
        history_context = "Today the user already ate: " + ", ".join([f"{r[0]} (P:{r[1]}g, Cal:{r[2]})" for r in history_rows])

    # อัปเดต Prompt ให้ AI ทำงานฉลาดขึ้น มีการคำนวณ แนะนำอาหาร และวิเคราะห์การจัดสรรโควตา
    prompt = f"""
    You are an expert sports nutritionist and diet coach. Analyze this Thai food image.
    Estimate macronutrients (Protein, Carbs, Fat) and Total Calories.
    
    Context: {history_context}
    The user's daily protein target is {PROTEIN_TARGET}g.
    
    Provide your analysis and tailored advice in Thai based on their target.
    Respond ONLY with a valid JSON object matching this structure (No markdown fences like ```json):
    {{
      "mealName": "ชื่ออาหารภาษาไทย",
      "calories": 120.0,
      "protein": 15.0,
      "carbs": 20.0,
      "fat": 8.0,
      "explanation": "อธิบายสั้นๆ เกี่ยวกับสารอาหารในจานนี้",
      "aiAdvice": "คำแนะนำอัจฉริยะ: วิเคราะห์ว่ามื้อนี้ดีต่อเป้าหมายโปรตีน {PROTEIN_TARGET}g ไหม และแนะนำอาหารเมนูถัดไปที่ควรทานเพิ่มในวันนี้เพื่อให้สารอาหารครบถ้วนตามเป้า"
    }}
    """
    
    url = f"[https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=](https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=){GEMINI_API_KEY}"
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
        
        if "error" in response_json:
            return jsonify({"error": f"Google API Error: {response_json['error']['message']}"}), 400
            
        text_result = response_json['candidates'][0]['content']['parts'][0]['text'].strip()
        
        if text_result.startswith("```json"):
            text_result = text_result.split("```json")[1].split("```")[0].strip()
        elif text_result.startswith("```"):
            text_result = text_result.split("```")[1].split("```")[0].strip()
            
        nutrition_data = json.loads(text_result)
        
        # บันทึกสารอาหารลงฐานข้อมูล
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO daily_logs (meal_name, calories, protein, carbs, fat, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            nutrition_data
