import pandas as pd
from neo4j import GraphDatabase
from flask import Flask, request, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from sentence_transformers import SentenceTransformer, util
import faiss
import numpy as np
import json
import requests

# สร้างโมเดล SentenceTransformer
encoder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")

# เชื่อมต่อกับ Neo4j
URI = "neo4j://localhost:7687"
AUTH = ("neo4j", "ponkai517")

def run_query(query, parameters=None):
    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            result = session.run(query, parameters)
            return [record for record in result]
    driver.close()

# ค้นหาข้อมูลคำถามและคำตอบทั้งหมดจาก Neo4j
def get_all_questions():
    query = "MATCH (n:HerbInfo) RETURN n.question as question, n.answer as answer"
    results = run_query(query)
    questions = []
    answers = []
    for record in results:
        questions.append(record['question'])
        answers.append(record['answer'])
    return pd.DataFrame({'คำถาม': questions, 'คำตอบ': answers})

# ดึงข้อมูลคำถาม-คำตอบจาก Neo4j
df = get_all_questions()

# แปลงคำถามเป็นเวกเตอร์ด้วย FAISS
def create_faiss_index(df):
    text = df['คำถาม']
    vectors = encoder.encode(text)

    # สร้างดัชนี FAISS
    vector_dimension = vectors.shape[1]
    index = faiss.IndexFlatL2(vector_dimension)
    faiss.normalize_L2(vectors)
    index.add(vectors)
    
    return index, vectors

index, vectors = create_faiss_index(df)

# ค้นหาข้อความที่ใกล้เคียงที่สุดด้วย FAISS
def faiss_search(search_text):
    search_vector = encoder.encode(search_text)
    _vector = np.array([search_vector])
    faiss.normalize_L2(_vector)

    k = index.ntotal
    distances, ann = index.search(_vector, k=k)

    # ตั้งค่า threshold สำหรับ distance
    distance_threshold = 0.4

    if distances[0][0] > distance_threshold:
        return 'ไม่รู้', None  # คืนค่า 'ไม่รู้' และ None เพื่อสอดคล้องกับฟังก์ชัน compute_response
    else:
        return df['คำถาม'][ann[0][0]], df['คำตอบ'][ann[0][0]]  # คืนทั้งคำถามและคำตอบ

# เรียก Ollama API ในกรณีที่ไม่พบคำตอบ
def llama_search(prompt):
    OLLAMA_API_URL = "http://localhost:11434/api/generate"
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "model": "supachai/llama-3-typhoon-v1.5",  # Adjust the model name as needed
        # เพิ่มข้อความเพื่อรีเซ็ต context ให้แน่ใจว่า prompt เริ่มต้นใหม่ทุกครั้ง
        "prompt": f"Forget all previous instructions. Respond concisely to: {prompt}\nou can answer basic questions about herbs  in no more than 20 words. End the response with 'Answer from llama3.. End the response with คำตอบจากllama3.",
        "stream": False
    }
    
    try:
        response = requests.post(OLLAMA_API_URL, headers=headers, data=json.dumps(payload))
        if response.status_code == 200:
            response_data = response.json()
            return response_data["response"]
        else:
            return "ขอโทษครับ ไม่สามารถประมวลผลได้ในขณะนี้"
    except Exception as e:
        return f"เกิดข้อผิดพลาด: {e}"


# ฟังก์ชันคำนวณการตอบสนอง
def compute_response(sentence):
    question, answer = faiss_search(sentence)
    
    if question != 'ไม่รู้':
        # ใช้คำถามที่ได้จาก FAISS ไปค้นใน Neo4j เพื่อความแม่นยำ
        query = f"MATCH (n:HerbInfo) WHERE n.question = '{question}' RETURN n.answer AS answer"
        neo4j_response = run_query(query)
        if neo4j_response:
            return neo4j_response[0]['answer']  # ส่งคำตอบจาก Neo4j
        return answer  # ถ้าไม่พบใน Neo4j ให้ใช้คำตอบจาก FAISS
    else:
        # เรียก Ollama API เมื่อไม่พบคำตอบ
        return llama_search(sentence)

# เชื่อมต่อกับ Line API
app = Flask(__name__)

@app.route("/", methods=['POST'])
def linebot():
    body = request.get_data(as_text=True)
    try:
        json_data = json.loads(body)
        access_token = '2h5B+6TZellUgtBUJke0dQvrWsKiSxnwNPOCsOpjixABRzME0XhakcDdfeMwlyLxI/fIpCTOHLDduCINBUCGwzzi7fDSNg10MDWqn8twIhETIJBrdA8yAHHD4PWMeJvmAlOrVe+cKApTJga+C+OorQdB04t89/1O/w1cDnyilFU='
        secret = 'dd1ed20330791ca4762c5910ab155d57'
        line_bot_api = LineBotApi(access_token)
        handler = WebhookHandler(secret)
        signature = request.headers['X-Line-Signature']
        handler.handle(body, signature)
        msg = json_data['events'][0]['message']['text']
        tk = json_data['events'][0]['replyToken']
        response_msg = compute_response(msg)
        line_bot_api.reply_message(tk, TextSendMessage(text=response_msg))
        print(msg, tk)
    except Exception as e:
        print(body)
        print(f"Error: {e}")
    return 'OK'

if __name__ == '__main__':
    app.run(port=5000)
