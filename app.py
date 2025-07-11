# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import requests
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException
import sys
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
# تم حذف متغير البيئة لمفتاح OpenAI API (GPT)

# مفتاح API الخاص بـ Claude (يجب أن يُقرأ من متغيرات البيئة لضمان الأمان)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")


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
except ConnectionError as ce:
    print(f"خطأ في الاتصال بـ Elasticsearch (ConnectionError): {ce}")
    print("يرجى التحقق من اتصالك بالإنترنت، إعدادات جدار الحماية، وفلاتر IP في Elastic Cloud.")
    sys.exit(1)
except AuthenticationException as ae:
    print(f"خطأ في المصادقة مع Elasticsearch (AuthenticationException): {ae}")
    print("يرجى التحقق من اسم المستخدم وكلمة المرور الخاصة بـ Elastic Cloud. تأكد من أنها مطابقة تمامًا.")
    sys.exit(1)
except Exception as e:
    print(f"حدث خطأ غير متوقع أثناء الاتصال بـ Elasticsearch: {e}")
    sys.exit(1)


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
    query = request.args.get('q', '')
    author_query = request.args.get('author', '')

    if not query and not author_query:
        return jsonify({"error": "يرجى تقديم سؤال أو اسم مؤلف."}), 400

    sources_retrieved = []
    try:
        if es:
            # بناء استعلام Elasticsearch لتحسين دقة البحث
            query_clauses = [] # شروط البحث الخاصة بالسؤال
            author_clauses = [] # شروط البحث الخاصة بالمؤلف

            if query:
                # البحث عن الكلمات الرئيسية في حقول متعددة مع تعزيزات مختلفة
                query_clauses.append({
                    "multi_match": {
                        "query": query,
                        "fields": [
                            "text_content^3",         # تعزيز عالي لمحتوى النص
                            "text_content.ngram^2",   # تعزيز للبحث الجزئي (N-gram)
                            "book_title^1.5",         # تعزيز لعنوان الكتاب
                            "author_name^1.2"         # تعزيز لاسم المؤلف
                        ],
                        "type": "best_fields",  # أخذ النقاط من أفضل حقل مطابق
                        "fuzziness": "AUTO",    # السماح ببعض الأخطاء الإملائية
                        "operator": "or"        # مطابقة أي من المصطلحات
                    }
                })
                # مطابقة العبارة الدقيقة مع تعزيز عالي جداً
                query_clauses.append({
                    "match_phrase": {
                        "text_content": {
                            "query": query,
                            "boost": 100,           # تعزيز عالي جداً للمطابقة الدقيقة للعبارة
                            "slop": 2               # السماح بوجود الكلمات متباعدة بمقدار 2 موضع
                        }
                    }
                })
                # مطابقة جميع الكلمات الرئيسية كشروط إلزامية (AND)
                query_clauses.append({
                    "match": {
                        "text_content": {
                            "query": query,
                            "operator": "and",      # يجب أن تكون جميع المصطلحات موجودة
                            "boost": 20             # تعزيز عالي لوجود جميع المصطلحات
                        }
                    }
                })

            if author_query:
                # البحث عن اسم المؤلف في حقول متعددة مع تعزيزات
                author_clauses.append({
                    "multi_match": {
                        "query": author_query,
                        "fields": [
                            "author_name^5",          # تعزيز عالي جداً لاسم المؤلف
                            "author_name.ngram^3"     # تعزيز للبحث الجزئي عن اسم المؤلف
                        ],
                        "type": "best_fields",
                        "fuzziness": "AUTO"
                    }
                })
                # مطابقة العبارة الدقيقة لاسم المؤلف مع تعزيز أعلى
                author_clauses.append({
                    "match_phrase": {
                        "author_name": {
                            "query": author_query,
                            "boost": 150 # تعزيز أعلى لمطابقة عبارة المؤلف الدقيقة
                        }
                    }
                })

            final_query = {}

            if query and author_query:
                # إذا كان هناك سؤال ومؤلف، يجب أن تتطابق كليهما
                final_query = {
                    "bool": {
                        "must": [
                            {"bool": {"should": query_clauses, "minimum_should_match": 1}},
                            {"bool": {"should": author_clauses, "minimum_should_match": 1}}
                        ]
                    }
                }
            elif query:
                # إذا كان البحث بالسؤال فقط
                final_query = {
                    "bool": {
                        "should": query_clauses,
                        "minimum_should_match": 1
                    }
                }
            elif author_query:
                # إذا كان البحث بالمؤلف فقط
                final_query = {
                    "bool": {
                        "should": author_clauses,
                        "minimum_should_match": 1
                    }
                }
            else:
                # هذا الشرط لا ينبغي أن يحدث بسبب التحقق الأولي، ولكن كاحتياطي
                final_query = {"match_all": {}}

            search_body = {
                "query": final_query,
                "size": 50 # تم زيادة عدد النتائج المسترجعة إلى 50
            }
            
            res = es.search(index=INDEX_NAME, body=search_body)

            for hit in res['hits']['hits']:
                source = hit['_source']
                sources_retrieved.append({
                    "book_title": source.get('book_title', 'غير معروف'),
                    "author_name": source.get('author_name', 'غير معروف'),
                    "part_number": source.get('part_number', 'غير معروف'),
                    "page_number": source.get('page_number', 'غير معروف'),
                    "text_content": source.get('text_content', 'لا يوجد نص.')
                })
            print(f"Retrieved {len(sources_retrieved)} sources from Elasticsearch.")
        else:
            print("Elasticsearch client not initialized. Skipping source retrieval.")

        if not sources_retrieved:
            # إذا لم يتم العثور على مصادر، لا تزال تحاول توليد إجابة عامة من AI
            # ولكن قم بتعديل المطالبة لتعكس عدم وجود مصادر محددة
            print("No sources retrieved from Elasticsearch. AI will answer based on general knowledge.")

        # تحضير السياق لنماذج الذكاء الاصطناعي
        context_string = "\n\n".join([
            f"الكتاب: {s['book_title']}\nالمؤلف: {s['author_name']}\nالجزء: {s['part_number']}\nالصفحة: {s['page_number']}\nالنص: {s['text_content']}"
            for s in sources_retrieved
        ])

        # --- توليد الإجابة من Gemini 2.0 Flash ---
        gemini_answer = ""
        if GEMINI_API_KEY:
            try:
                gemini_prompt = f"""
                بناءً على النصوص التالية من الكتب الإسلامية، أجب عن السؤال: '{query}'.
                يجب أن تتضمن إجابتك اسم المؤلف، اسم الكتاب، رقم الجزء، ورقم الصفحة لكل معلومة تذكرها.
                إذا لم تجد الإجابة في النصوص المقدمة، اذكر ذلك.

                النصوص المتاحة:
                ---
                {context_string if sources_retrieved else "لا توجد مصادر محددة. أجب بناءً على معرفتك العامة."}
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
        claude_answer = ""
        if claude_client:
            try:
                claude_prompt = f"""
                بناءً على النصوص التالية من الكتب الإسلامية، أجب عن السؤال: '{query}'.
                يجب أن تتضمن إجابتك اسم المؤلف، اسم الكتاب، رقم الجزء، ورقم الصفحة لكل معلومة تذكرها.
                إذا لم تجد الإجابة في النصوص المقدمة، اذكر ذلك.

                النصوص المتاحة:
                ---
                {context_string if sources_retrieved else "لا توجد مصادر محددة. أجب بناءً على معرفتك العامة."}
                ---
                """
                
                message = claude_client.messages.create(
                    model="claude-3-5-sonnet-20241022", # نموذج Claude Sonnet
                    max_tokens=1000, # الحد الأقصى للتوكنات في الإجابة
                    messages=[
                        {"role": "user", "content": claude_prompt}
                    ]
                )
                claude_answer = message.content[0].text
                print("Claude API call successful.")
            except Exception as e:
                claude_answer = f"حدث خطأ أثناء استدعاء Claude API: {e}"
                print(f"ERROR: Claude API call failed: {e}")
        else:
            claude_answer = "لم يتم تفعيل نموذج Claude (مفتاح API غير متوفر في متغيرات البيئة)."
        
        return jsonify({
            "question": query,
            "gemini_answer": gemini_answer,
            "claude_answer": claude_answer,
            "sources_retrieved": sources_retrieved
        })

    except Exception as e:
        print(f"خطأ عام أثناء معالجة السؤال أو استدعاء AI: {e}")
        # يجب أن نكون أكثر تحديدًا هنا، ولكن هذا سيساعد في اكتشاف الأخطاء
        return jsonify({"error": f"حدث خطأ أثناء معالجة طلبك: {e}"}), 500

# 4. تشغيل تطبيق Flask
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=os.environ.get("PORT", 5000)) # استخدام متغير البيئة PORT الذي يوفره Render
