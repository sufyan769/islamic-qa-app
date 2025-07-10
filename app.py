# استيراد المكتبات الضرورية
from flask import Flask, request, jsonify
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException
import os
import sys
import google.generativeai as genai
from flask_cors import CORS

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
    print(f"خطأ في المصادقة مع Elasticsearch عند بدء تشغيل الـ API (AuthenticationException): {ae}")
    print("يرجى التحقق من اسم المستخدم وكلمة المرور الخاصة بـ Elastic Cloud. تأكد من أنها مطابقة تمامًا.")
    sys.exit(1)
except Exception as e:
    print(f"خطأ في الاتصال بـ Elasticsearch عند بدء تشغيل الـ API: {e}")
    sys.exit(1)

# اسم الفهرس الذي قمنا بإنشائه
INDEX_NAME = "islamic_texts"

# تمت إزالة قائمة EXCLUDED_AUTHORS لأنك لا ترغب في استثناء أي مؤلفين

# 2. إعدادات Gemini API
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") # يتم جلبها من متغيرات البيئة في Render

if not GEMINI_API_KEY:
    print("تحذير: لم يتم تعيين مفتاح Gemini API. لن تعمل وظيفة الذكاء الاصطناعي.")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

# 3. نقطة نهاية (Endpoint) للبحث في Elasticsearch ودمج Gemini
@app.route('/ask', methods=['GET'])
def ask_gemini():
    query = request.args.get('q', '')

    if not query:
        return jsonify({"error": "يرجى تقديم سؤال."}), 400

    try:
        # بناء استعلام Elasticsearch لتحسين دقة البحث
        search_body = {
            "query": {
                "bool": {
                    "should": [
                        {
                            # تفضيل البحث عن العبارة الدقيقة في محتوى النص
                            "match_phrase": {
                                "text_content": {
                                    "query": query,
                                    "boost": 2 # إعطاء أولوية أعلى للمطابقة الدقيقة للعبارة
                                }
                            }
                        },
                        {
                            # استخدام multi_match للبحث الأوسع في الحقول الأخرى
                            "multi_match": {
                                "query": query,
                                "fields": ["text_content", "book_title", "author_name"],
                                "type": "most_fields"
                            }
                        }
                    ],
                    "minimum_should_match": 1 # يجب أن يتطابق واحد على الأقل من شروط 'should'
                }
            },
            "size": 15 # عدد النتائج المسترجعة
        }
        
        res = es.search(index=INDEX_NAME, body=search_body)

        context_texts = []
        for hit in res['hits']['hits']:
            source = hit['_source']
            context_texts.append(
                f"الكتاب: {source.get('book_title', 'غير معروف')}\n"
                f"المؤلف: {source.get('author_name', 'غير معروف')}\n"
                f"الجزء: {source.get('part_number', 'غير معروف')}\n"
                f"الصفحة: {source.get('page_number', 'غير معروف')}\n"
                f"النص: {source.get('text_content', 'لا يوجد نص.')}"
            )
        
        if not context_texts:
            return jsonify({
                "question": query,
                "answer": "عذراً، لم أجد معلومات ذات صلة في المكتبة لفهم سؤالك."
            })

        combined_context = "\n---\n".join(context_texts)

        # المطالبة الموجهة لنموذج Gemini
        prompt = (
            f"بناءً على النصوص التالية من الكتب الإسلامية، أجب عن السؤال: '{query}'.\n"
            f"يجب أن تبحث في كلل النصوصا. "
            f"التزم فقط بالمعلومات الموجودة في النصوص المتاحة ولا تضف أي معرفة خارجية أو آراء شخصية. "
            f"تأكد من تضمين اسم المؤلف، اسم الكتاب، رقم الجزء، ورقم الصفحة لكل معلومة تذكرها. "
            f"**لا تستخدم أي رموز نقطية (مثل * أو -) في الإجابة.** "
            f"**افصل كل معلومة أو استشهاد جديد بسطرين فارغين (أي سطر جديد ثم سطر فارغ آخر).** "
            f"اكتب ٥٠٠ نتيجة عن كل سوال. "
            f"إكتب اوللا رآآي ابن تيمية وعلمااء السلف .\n\n"
            f"النصوص المتاحة:\n---\n{combined_context}\n---"
        )

        print(f"إرسال مطالبة إلى Gemini:\n{prompt[:500]}...")
        gemini_response = model.generate_content(prompt)
        
        answer = gemini_response.text
        
        return jsonify({"question": query, "answer": answer, "sources_retrieved": context_texts})

    except Exception as e:
        print(f"خطأ أثناء معالجة السؤال أو استدعاء Gemini: {e}")
        return jsonify({"error": "حدث خطأ أثناء معالجة طلبك."}), 500

# 4. تشغيل تطبيق Flask
if __name__ == '__main__':
    app.run(debug=True, port=os.environ.get("PORT", 5000)) # استخدام متغير البيئة PORT الذي يوفره Render
