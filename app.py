# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import requests
from elasticsearch import Elasticsearch
# تم حذف استيراد OpenAI (GPT)
from anthropic import Anthropic # استيراد مكتبة Anthropic لـ Claude
import json # تم إضافة هذا لمعالجة استجابات JSON من Gemini

app = Flask(__name__)
CORS(app) # تمكين CORS لجميع المسارات

# متغيرات البيئة لـ Elasticsearch
CLOUD_ID = os.environ.get("CLOUD_ID")
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD")

# متغيرات البيئة لمفاتيح API الخاصة بنماذج الذكاء الاصطناعي
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") # مفتاح API لـ Gemini

# مفتاح API الخاص بـ Claude (يجب أن يُقرأ من متغيرات البيئة لضمان الأمان)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") # تم التعديل: قراءة المفتاح من متغيرات البيئة


# تهيئة عميل Elasticsearch
es = None
try:
    if CLOUD_ID and ELASTIC_USERNAME and ELASTIC_PASSWORD:
        es = Elasticsearch(
            cloud_id=CLOUD_ID,
            basic_auth=(ELASTIC_USERNAME, ELASTIC_PASSWORD),
            verify_certs=True,
            ssl_show_warn=False,
            request_timeout=60 
        )
        # التحقق من الاتصال
        if not es.ping():
            raise ValueError("Connection to Elasticsearch failed!")
        print("Successfully connected to Elasticsearch!")
    else:
        print("Elasticsearch environment variables not fully set. Skipping connection.")
except Exception as e:
    print(f"Error connecting to Elasticsearch: {e}")
    es = None # التأكد من أن es هو None إذا فشل الاتصال

# تهيئة عميل Claude
claude_client = None
if ANTHROPIC_API_KEY:
    try:
        claude_client = Anthropic(api_key=ANTHROPIC_API_KEY)
        print("Claude client initialized successfully.")
    except Exception as e:
        print(f"Error initializing Claude client: {e}")
else:
    print("ANTHROPIC_API_KEY not set. Claude API will not be available.")

# اسم الفهرس الذي قمنا بإنشائه
INDEX_NAME = "islamic_texts"

# نقطة نهاية (Endpoint) للبحث في Elasticsearch ودمج AI
@app.route('/ask', methods=['GET'])
def ask_ai():
    query_text = request.args.get('q', '')
    author_name = request.args.get('author', '')

    if not query_text and not author_name:
        return jsonify({"error": "Please provide a query or an author name."}), 400

    sources_retrieved = []
    if es:
        try:
            search_body = {
                "query": {
                    "bool": {
                        "must": []
                    }
                },
                "size": 5 # تحديد عدد المصادر المسترجعة بـ 5
            }

            if query_text:
                search_body["query"]["bool"]["must"].append({
                    "multi_match": {
                        "query": query_text,
                        "fields": ["text_content", "book_title", "author_name"],
                        "fuzziness": "AUTO" # السماح بالأخطاء الإملائية البسيطة
                    }
                })
            
            if author_name:
                search_body["query"]["bool"]["must"].append({
                    "match": {
                        "author_name.keyword": author_name # البحث الدقيق عن اسم المؤلف
                    }
                })

            # إذا لم يتم توفير استعلام أو مؤلف، احصل على بعض المستندات الحديثة
            if not query_text and not author_name:
                 search_body = {
                    "query": {
                        "match_all": {} # مطابقة كل المستندات
                    },
                    "size": 5,
                    "sort": [{"_score": {"order": "desc"}}] # الترتيب حسب الصلة (relevance)
                }

            response = es.search(index="islamic_texts", body=search_body)
            
            for hit in response['hits']['hits']:
                source = hit['_source']
                sources_retrieved.append({
                    "book_title": source.get("book_title", "N/A"),
                    "author_name": source.get("author_name", "N/A"),
                    "part_number": source.get("part_number", "N/A"),
                    "page_number": source.get("page_number", "N/A"),
                    "text_content": source.get("text_content", "N/A")
                })
            print(f"Retrieved {len(sources_retrieved)} sources from Elasticsearch.")

        except Exception as e:
            print(f"Error searching Elasticsearch: {e}")
            # لا ترجع خطأ هنا، استمر في توليد الإجابة حتى لو لم يتم العثور على مصادر
    else:
        print("Elasticsearch client not initialized. Skipping source retrieval.")

    gemini_answer = None # تهيئة إجابة Gemini
    claude_answer = None # تهيئة إجابة Claude

    # --- توليد الإجابة من Gemini 2.0 Flash ---
    if GEMINI_API_KEY:
        try:
            gemini_prompt = f"""
            بناءً على النصوص التالية من الكتب الإسلامية، أجب عن السؤال: '{query_text}'.
            يجب أن تتضمن إجابتك اسم المؤلف، اسم الكتاب، رقم الجزء، ورقم الصفحة لكل معلومة تذكرها.
            إذا لم تجد الإجابة في النصوص المقدمة، اذكر ذلك.

            النصوص المتاحة:
            ---
            {"\n\n".join([
                f"الكتاب: {s['book_title']}\nالمؤلف: {s['author_name']}\nالجزء: {s['part_number']}\nالصفحة: {s['page_number']}\nالنص: {s['text_content']}"
                for s in sources_retrieved
            ])}
            ---
            """
            
            gemini_payload = {
                "contents": [{"role": "user", "parts": [{"text": gemini_prompt}]}],
                "generationConfig": {
                    "responseMimeType": "text/plain"
                }
            }
            gemini_api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
            
            gemini_response = requests.post(gemini_api_url, headers={'Content-Type': 'application/json'}, json=gemini_payload, timeout=30) # زيادة المهلة
            gemini_response.raise_for_status() 
            gemini_result = gemini_response.json()
            
            if gemini_result.get('candidates') and gemini_result['candidates'][0].get('content') and gemini_result['candidates'][0]['content'].get('parts'):
                gemini_answer = gemini_result['candidates'][0]['content']['parts'][0]['text']
            else:
                gemini_answer = "عذراً، لم يتمكن نموذج Gemini من توليد إجابة (هيكل استجابة غير متوقع)."
                print(f"DEBUG: Gemini API response structure unexpected: {gemini_result}")

        except requests.exceptions.RequestException as req_err:
            gemini_answer = f"خطأ في الاتصال بنموذج Gemini: {req_err}"
            print(f"ERROR: Gemini API request failed: {req_err}")
        except Exception as e:
            gemini_answer = f"خطأ في معالجة استجابة Gemini: {e}"
            print(f"ERROR: Processing Gemini response failed: {e}")
    else:
        gemini_answer = "لم يتم تفعيل نموذج Gemini (مفتاح API غير متوفر في متغيرات البيئة)."

    # --- توليد الإجابة من Claude ---
    if claude_client:
        try:
            prompt_parts = []
            if query_text:
                prompt_parts.append(f"السؤال: {query_text}\n")
            
            if sources_retrieved:
                prompt_parts.append("المصادر المسترجعة:\n")
                for i, source in enumerate(sources_retrieved):
                    prompt_parts.append(f"--- مصدر {i+1} ---\n")
                    prompt_parts.append(f"الكتاب: {source['book_title']}\n")
                    prompt_parts.append(f"المؤلف: {source['author_name']}\n")
                    prompt_parts.append(f"الجزء: {source['part_number']}, الصفحة: {source['page_number']}\n")
                    prompt_parts.append(f"النص: {source['text_content']}\n")
                prompt_parts.append("\n")
                prompt_parts.append("بناءً على المصادر أعلاه، أجب على السؤال. إذا لم تجد إجابة في المصادر، فاذكر ذلك.\n")
            else:
                prompt_parts.append("لم يتم العثور على مصادر. يرجى الإجابة على السؤال بناءً على معرفتك العامة.\n")

            full_prompt = "".join(prompt_parts)

            message = claude_client.messages.create(
                model="claude-3-5-sonnet-20241022", # نموذج Claude Sonnet
                max_tokens=1000, # الحد الأقصى للتوكنات في الإجابة
                messages=[
                    {"role": "user", "content": full_prompt}
                ]
            )
            claude_answer = message.content[0].text
            print("Claude API call successful.")
        except Exception as e:
            claude_answer = f"حدث خطأ أثناء استدعاء Claude API: {e}"
            print(f"Error calling Claude API: {e}")
    else:
        claude_answer = "لم يتم تهيئة عميل Claude API. قد يكون مفتاح ANTHROPIC_API_KEY مفقودًا أو غير صالح."
        print("Claude API client not available.")

    return jsonify({
        "gemini_answer": gemini_answer,
        "claude_answer": claude_answer,
        "sources_retrieved": sources_retrieved
    })

if __name__ == '__main__':
    # تشغيل التطبيق في وضع التصحيح (debug mode) على المنفذ المحدد بواسطة Render
    app.run(debug=True, host='0.0.0.0', port=os.environ.get('PORT', 5000))
