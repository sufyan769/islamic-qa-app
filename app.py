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
    print(f"خطأ في المصادقة مع Elasticsearch (AuthenticationException): {ae}")
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
    author_query = request.args.get('author', '') # جلب اسم المؤلف من الطلب

    if not query and not author_query:
        return jsonify({"error": "يرجى تقديم سؤال أو اسم مؤلف."}), 400

    try:
        # بناء استعلام Elasticsearch لتحسين دقة البحث
        query_conditions = []
        if query:
            query_conditions.append({
                # الأولوية القصوى: مطابقة العبارة الدقيقة في محتوى النص
                "match_phrase": {
                    "text_content": {
                        "query": query,
                        "boost": 50 # تعزيز عالي جداً للمطابقة الدقيقة للعبارة
                    }
                }
            })
            query_conditions.append({
                # البحث عن الكلمات في محتوى النص، مع اشتراط وجود نسبة معينة منها
                # مثلاً: "75%" تعني أن 75% من الكلمات في الاستعلام يجب أن تكون موجودة في النص
                "match": {
                    "text_content": {
                        "query": query,
                        "minimum_should_match": "75%", 
                        "boost": 5 # تعزيز متوسط
                    }
                }
            })
            # إضافة بحث مرن (fuzzy) في حقل text_content.ngram
            query_conditions.append({
                "match": {
                    "text_content.ngram": { # البحث في الحقل الفرعي N-gram
                        "query": query,
                        "fuzziness": "AUTO", # السماح ببعض الأخطاء الإملائية
                        "boost": 10 # تعزيز جيد للبحث المرن
                    }
                }
            })
            query_conditions.append({
                # البحث الأوسع في عناوين الكتب وأسماء المؤلفين
                "multi_match": {
                    "query": query,
                    "fields": ["book_title", "author_name"],
                    "type": "most_fields",
                    "boost": 3 # تعزيز أقل قليلاً من مطابقة النص
                }
            })
        
        author_conditions = []
        if author_query:
            author_conditions.append({
                "match_phrase": {
                    "author_name": {
                        "query": author_query,
                        "boost": 100 # تعزيز عالي جداً لضمان أولوية البحث عن المؤلف كعبارة دقيقة
                    }
                }
            })
            author_conditions.append({
                "match": {
                    "author_name": {
                        "query": author_query,
                        "operator": "and", # يجب أن تكون جميع الكلمات موجودة في اسم المؤلف
                        "boost": 80 # تعزيز عالٍ، لكن أقل قليلاً من المطابقة الدقيقة للعبارة
                    }
                }
            })
            # إضافة بحث مرن (fuzzy) في حقل author_name.ngram
            author_conditions.append({
                "match": {
                    "author_name.ngram": { # البحث في الحقل الفرعي N-gram
                        "query": author_query,
                        "fuzziness": "AUTO", # السماح ببعض الأخطاء الإملائية
                        "boost": 90 # تعزيز عالٍ للبحث المرن عن المؤلف
                    }
                }
            })

        final_query = {}

        if query and author_query:
            # إذا كان هناك سؤال ومؤلف، يجب أن تتطابق كليهما
            final_query = {
                "bool": {
                    "must": [
                        {"bool": {"should": query_conditions, "minimum_should_match": 1}},
                        {"bool": {"should": author_conditions, "minimum_should_match": 1}} 
                    ]
                }
            }
        elif query:
            # إذا كان البحث بالسؤال فقط
            final_query = {
                "bool": {
                    "should": query_conditions,
                    "minimum_should_match": 1
                }
            }
        elif author_query:
            # إذا كان البحث بالمؤلف فقط
            final_query = {
                "bool": {
                    "should": author_conditions, 
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
