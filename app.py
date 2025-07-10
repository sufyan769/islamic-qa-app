# استيراد المكتبات الضرورية
from flask import Flask, request, jsonify
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException
import os
import sys
# استيراد مكتبة OpenAI بدلاً من Google Generative AI
# import openai # لم نعد نحتاجها إذا لم نولد إجابات، ولكن سنبقيها لكي لا نكسر requirements.txt

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

# 2. إعدادات OpenAI API (لـ ChatGPT) - لم نعد نستخدمها لتوليد الإجابات
# OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") 
# if not OPENAI_API_KEY:
#     print("تحذير: لم يتم تعيين مفتاح OpenAI API. لن تعمل وظيفة الذكاء الاصطناعي (إذا تم تفعيلها).")
# client = openai.OpenAI(api_key=OPENAI_API_KEY)
# OPENAI_MODEL = "gpt-3.5-turbo" 

# 3. نقطة نهاية (Endpoint) للبحث في Elasticsearch ودمج ChatGPT
@app.route('/ask', methods=['GET'])
def ask_gemini(): # تم الإبقاء على اسم الدالة ask_gemini لعدم كسر التوافق مع الواجهة الأمامية
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
                        },
                        {
                            # إضافة مطابقة العبارة الدقيقة لاسم المؤلف مع تعزيز عالي جدًا
                            "match_phrase": {
                                "author_name": {
                                    "query": query,
                                    "boost": 10 # تعزيز عالي جدًا لأسماء المؤلفين المطابقة تمامًا
                                }
                            }
                        },
                        {
                            # إضافة مطابقة العبارة الدقيقة لعنوان الكتاب مع تعزيز عالي جدًا
                            "match_phrase": {
                                "book_title": {
                                    "query": query,
                                    "boost": 10 # تعزيز عالي جدًا لعناوين الكتب المطابقة تمامًا
                                }
                            }
                        }
                    ],
                    "minimum_should_match": 1 # يجب أن يتطابق واحد على الأقل من شروط 'should'
                }
            },
            "size": 5000 # تم زيادة عدد النتائج المسترجعة إلى 5000
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
                "answer": "عذراً، لم أجد معلومات ذات صلة في المكتبة لفهم سؤالك.",
                "sources_retrieved": [] 
            })

        # تم إزالة استدعاء نموذج الذكاء الاصطناعي
        # الإجابة الآن ستكون فارغة تمامًا
        answer = "" 
        
        return jsonify({"question": query, "answer": answer, "sources_retrieved": context_texts})

    except Exception as e:
        print(f"خطأ أثناء معالجة السؤال أو استدعاء OpenAI (إذا كان لا يزال مفعلاً): {e}")
        return jsonify({"error": "حدث خطأ أثناء معالجة طلبك."}), 500

# 4. تشغيل تطبيق Flask
if __name__ == '__main__':
    app.run(debug=True, port=os.environ.get("PORT", 5000)) # استخدام متغير البيئة PORT الذي يوفره Render
