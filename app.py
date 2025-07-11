# استيراد المكتبات الضرورية
from flask import Flask, request, jsonify
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException
import os
import sys
import requests # تم إضافة هذا لاستدعاء Gemini API
# تم حذف استيراد OpenAI (GPT)
import json # تم إضافة هذا لمعالجة استجابات JSON من Gemini

from flask_cors import CORS
from anthropic import Anthropic # استيراد مكتبة Anthropic لـ Claude

# تهيئة تطبيق Flask
app = Flask(__name__)
CORS(app)

# 1. إعدادات Elasticsearch (للاتصال بـ Elastic Cloud)
# يتم جلب بيانات الاعتماد من متغيرات البيئة في Render
CLOUD_ID = os.environ.get("CLOUD_ID")
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD")

# قم بتهيئة عميل Elasticsearch باستخدام CLOUD_ID
print(f"DEBUG: Attempting to connect to Elastic Cloud with CLOUD_ID: {CLOUD_ID}")
print(f"DEBUG: Username: {ELASTIC_USERNAME}, Password length: {len(ELASTIC_PASSWORD) if ELASTIC_PASSWORD else 0}")

es = Elasticsearch(
    cloud_id=CLOUD_ID,
    basic_auth=(ELASTIC_USERNAME, ELASTIC_PASSWORD),
    verify_certs=False, # تم تعيين هذا إلى False لتجنب مشاكل الشهادات (غير آمن للإنتاج)
    ssl_show_warn=True # تمكين تحذيرات SSL لمزيد من التفاصيل
)

# تحقق من الاتصال بـ Elasticsearch عند بدء تشغيل الـ API
try:
    info_response = es.info()
    print("تم الاتصال بـ Elasticsearch بنجاح عند بدء تشغيل الـ API.")
    print(f"DEBUG: Elasticsearch Info: {info_response.body['version']['number']}")
except ConnectionError as ce:
    print(f"خطأ في الاتصال بـ Elasticsearch عند بدء تشغيل الـ API (ConnectionError): {ce}")
    print("يرجى التحقق من اتصالك بالإنترنت، إعدادات جدار الحماية، وفلاتر IP في Elastic Cloud.")
    sys.exit(1)
except AuthenticationException as ae:
    print(f"خطأ في المصادقة مع Elasticsearch (AuthenticationException): {ae}")
    print("يرجى التحقق من اسم المستخدم وكلمة المرور الخاصة بـ Elastic Cloud. تأكد من أنها مطابقة تمامًا.")
    sys.exit(1)
except Exception as e:
    print(f"خطأ في الاتصال بـ Elasticsearch عند بدء تشغيل الـ API: {e}")
    sys.exit(1)

# اسم الفهرس الذي قمنا بإنشائه
INDEX_NAME = "islamic_texts"

# 2. إعدادات AI API (Gemini و Claude)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") # مفتاح API لـ Gemini
# تم حذف متغير البيئة لمفتاح OpenAI API (GPT)

# مفتاح API الخاص بـ Claude (يجب أن يُقرأ من متغيرات البيئة لضمان الأمان)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")


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

# 3. نقطة نهاية (Endpoint) للبحث في Elasticsearch ودمج AI
@app.route('/ask', methods=['GET'])
def ask_ai(): # تم تغيير اسم الدالة ليعكس دعم كلا النموذجين
    query = request.args.get('q', '')
    author_query = request.args.get('author', '') # جلب اسم المؤلف من الطلب

    if not query and not author_query:
        return jsonify({"error": "يرجى تقديم سؤال أو اسم مؤلف."}), 400

    try:
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

        context_texts = []
        for hit in res['hits']['hits']:
            source = hit['_source']
            context_texts.append({
                "book_title": source.get('book_title', 'غير معروف'),
                "author_name": source.get('author_name', 'غير معروف'),
                "part_number": source.get('part_number', 'غير معروف'),
                "page_number": source.get('page_number', 'غير معروف'),
                "text_content": source.get('text_content', 'لا يوجد نص.')
            })
        
        if not context_texts:
            return jsonify({
                "question": query,
                "gemini_answer": "عذراً، لم أجد معلومات ذات صلة في المكتبة لنموذج Gemini.",
                "claude_answer": "عذراً، لم أجد معلومات ذات صلة في المكتبة لنموذج Claude.",
                "sources_retrieved": [] 
            })

        # تحضير السياق لنماذج الذكاء الاصطناعي
        context_string = "\n\n".join([
            f"الكتاب: {s['book_title']}\nالمؤلف: {s['author_name']}\nالجزء: {s['part_number']}\nالصفحة: {s['page_number']}\nالنص: {s['text_content']}"
            for s in context_texts
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
                {context_string}
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
                {context_string}
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
            "sources_retrieved": context_texts
        })

    except Exception as e:
        print(f"خطأ عام أثناء معالجة السؤال أو استدعاء AI: {e}")
        return jsonify({"error": "حدث خطأ أثناء معالجة طلبك."}), 500

# 4. تشغيل تطبيق Flask
if __name__ == '__main__':
    app.run(debug=True, port=os.environ.get("PORT", 5000)) # استخدام متغير البيئة PORT الذي يوفره Render
