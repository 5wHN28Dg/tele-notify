<div align="center">

[🇬🇧 English](README.md)

# tele-notify

**يراقب قنوات الوظائف على تيليجرام ويحوّل لك المنشورات التي تناسبك فقط.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Nim](https://img.shields.io/badge/Nim-2.2-yellow)](https://nim-lang.org)
[![CI](https://github.com/5wHN28Dg/tele-notify/actions/workflows/nim.yml/badge.svg)](https://github.com/5wHN28Dg/tele-notify/actions/workflows/nim.yml)

</div>

<div dir="rtl">

أغلب الوظائف في العراق تُنشر على تيليجرام، في مئات القنوات ومئات المنشورات يوميًا. والبحث فيها عن الوظائف القليلة المناسبة لحديثي التخرج في تقنية المعلومات وهندسة السيطرة والحاسبات في البصرة يشبه البحث عن إبرة في كومة قش. tele-notify يقرأ القنوات بدلًا منك ويحوّل المنشورات المطابقة فقط إلى قناة خاصة بك ([مثل هذه](https://t.me/BQTechJobs)). القصة الكاملة في مقالة [*إبرة في كومة قش*](https://medium.com/@TogataMirio/a-needle-in-a-haystack-and-the-deadly-pursuit-to-prove-your-worth-88c34f1df79f).

صُمّم لهذه الحاجة بالذات، لكن الكلمات المفتاحية والأماكن والقنوات كلها من اختيارك.

</div>

[![asciicast](https://asciinema.org/a/h8qYjhK6LsRqMchP6L93N1qu5.svg)](https://asciinema.org/a/h8qYjhK6LsRqMchP6L93N1qu5)

<div dir="rtl">

## الحالة: إعادة كتابة بلغة Nim

| | ما هو | الحالة |
|---|---|---|
| **الإصدار 3، بايثون** (`main.py`) | البوت الذي يعمل حاليًا | يعمل، ومجمّد: لا ميزات جديدة |
| **الإصدار 4، Nim** (`src/`) | النسخة الجديدة، تُبنى على مراحل | اكتملت المراحل 1 إلى 3: الفلتر، وقراءة الصور، وقراءة مواقع الوظائف، والاستماع لتيليجرام. المرحلة 4، تجربته بجانب بوت بايثون، جارية (`telenotify compare`) |

سبب إعادة الكتابة أن فلتر بايثون كان ينشر الكثير من المنشورات الخاطئة: في اختبار على 196 منشورًا حقيقيًا، كان 96 من أصل 115 منشورًا نشرها خاطئًا، مقابل 4 فقط في الفلتر الجديد. [تقرير المشاكل](docs/problems-report.md) يشرح ما كان خاطئًا، و[دليل إعادة الكتابة](docs/rewrite-guide.md) يشرح كيف يعمل الكود الجديد (كلاهما بالإنجليزية).

## كيف يقرر الفلتر الجديد

كل منشور يحصل على واحد من ثلاثة أحكام، **مع الأسباب**:

| الحكم | متى |
|---|---|
| ✅ `publish` (نشر) | اختصاصك، في البصرة (أو عن بعد)، *ومعه* ما يدل على أنه للمبتدئين: "حديث التخرج"، "متدرب"، "خبرة 0–1 سنة"، دعم فني… |
| 🤔 `review` (مراجعة) | الاختصاص والمكان مناسبان، لكن لا شيء يحدد المستوى. القرار لك |
| ❌ `reject` (رفض) | ليس إعلان وظيفة، أو مدينة أخرى، أو يطلب سنوات خبرة، أو منصب أقدم، أو شهادة إلزامية… |

قبل أن يقرر، يجمع كل ما يمكن قراءته:
- نص المنشور؛
- النص الموجود في الصور (OCR، عربي وإنجليزي)؛
- صفحة الوظيفة إذا كان في المنشور رابط واحد فقط لوظيفة على موقع وظائف معروف: خانة، تعيين، يدنو، لينكدإن، Shift، Workable، mselect. إذا كان فيه عدة روابط فهي غالبًا عدة وظائف، فلا يُفتح أيٌّ منها.

المنشورات التي تحتوي على عدة وظائف تُقسَّم، ويُحكم على كل وظيفة وحدها.

</div>

```
$ ./telenotify check --config:config.json post.txt
note: read Khana (2952 characters)
verdict: publish
  - role (rkTitle): مهندس كهرباء
  - location line: 'location: البصره - basra'
  - entry level: حديث التخرج
```

<div dir="rtl">

## جرّب الفلتر الجديد

تحتاج [Nim](https://nim-lang.org/install.html) 2.2 وبرنامج tesseract مع اللغة العربية:

</div>

```bash
sudo apt install tesseract-ocr tesseract-ocr-ara tesseract-ocr-eng   # Ubuntu/Debian
git clone https://github.com/5wHN28Dg/tele-notify.git && cd tele-notify
nimble build -d:release
```

<div dir="rtl">

ثم:

</div>

```bash
./telenotify check --config:config.json post.txt                  # منشور واحد
./telenotify check --config:config.json --image:ad.jpg post.txt   # مع صورة
./telenotify check --config:config.json --show-text post.txt      # اعرض كل ما قرأه
./telenotify eval --config:config.json posts.jsonl                # مجموعة منشورات مصنّفة
```

<div dir="rtl">

الأمر `nimble test` يشغّل الاختبارات.

## تشغيل بوت Nim (نسخة تجريبية)

بوت Nim يتصل بتيليجرام عبر [TDLib](https://core.telegram.org/tdlib)، المكتبة الرسمية من تيليجرام. ابنِها مرة واحدة (حوالي ساعة على نواة واحدة؛ تحتاج cmake ومترجم C++ وملفات zlib وOpenSSL):

</div>

```bash
JOBS=2 deploy/build-tdlib.sh      # يضع lib/libtdjson.so بجانب البرنامج
```

<div dir="rtl">

ثم:

</div>

```bash
./telenotify login --config:config.json          # مرة واحدة: رقم الهاتف، الرمز، كلمة مرور التحقق بخطوتين
./telenotify run --config:config.json --dry-run  # يحكم على كل شيء ولا يحوّل شيئًا
./telenotify run --config:config.json            # التشغيل الفعلي
./telenotify review --config:config.json         # المنشورات غير الواضحة: وافق أو تجاهل (يعمل والبوت شغّال)
./telenotify stats --config:config.json --days:7 # ما جلبته كل قناة
./telenotify compare --config:config.json        # التجربة مقارنةً ببوت بايثون (المرحلة 4)
```

<div dir="rtl">

ما الذي تغيّر عن بوت بايثون:
- المنشورات غير الواضحة تنتظر في قائمة المراجعة الخاصة بـ tele-notify (`telenotify review`) بدل الرسائل المحفوظة.
- كل رسالة تُحفظ (في `~/.local/share/tele-notify/state.sqlite3`)، فلا يُحكم على شيء أو يُحوَّل مرتين، حتى بعد إعادة التشغيل.
- التكرار يُفحص مقابل كل ما نُشر في آخر 14 يومًا، بالرابط وبالنص.
- إذا توقف البوت أو انقطع اتصاله أكثر من 10 دقائق، يخبرك في الرسائل المحفوظة عند عودته.

للتشغيل التلقائي، راجع [`deploy/tele-notify.service`](deploy/tele-notify.service).

## تشغيل البوت (بايثون، إلى أن يكتمل الإصدار 4)

1. احصل على بيانات Telegram API: الـ API ID والـ API hash من [my.telegram.org](https://my.telegram.org) ([الطريقة](https://docs.telethon.dev/en/stable/basic/signing-in.html#signing-in)).
2. التثبيت:

</div>

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

<div dir="rtl">

   وتحتاج tesseract أيضًا كما في الأعلى. لمستخدمي أندرويد: راجع [الويكي](https://github.com/5wHN28Dg/tele-notify/wiki/Android-Instructions).

3. اعرف معرّفات (IDs) محادثاتك بتشغيل هذا الكود مرة واحدة، بعد إدخال الـ API ID والـ hash:

</div>

```python
from telethon import TelegramClient
client = TelegramClient("test", API_ID, "API_HASH")
async def main():
    async for d in client.iter_dialogs():
        print(f"{d.id:>15}: {d.title}")
with client:
    client.loop.run_until_complete(main())
```

<div dir="rtl">

4. املأ ملف `config.json` (انظر الجدول أدناه)، ثم شغّل `python main.py`. في المرة الأولى سيطلب رقم هاتفك وكود الدخول.

يمرّ البوت أولًا على المنشورات غير المقروءة، ثم يراقب المنشورات الجديدة. المنشور المطابق يُحوَّل إلى `target_chat`، إلا إذا كان تكرارًا لأحد آخر 10 منشورات محوَّلة. المنشورات غير الواضحة تذهب إلى رسائلك المحفوظة (Saved Messages) لتراجعها.

الملف `main_simple.py` هو النسخة الأصلية الأبسط: يطابق المنشور إذا احتوى على كلمة من كل قائمة من ثلاث قوائم. يصلح لأي موضوع، وليس للوظائف فقط.

## الإعدادات

النسختان تقرآن ملف `config.json` نفسه:

| المفتاح | ما هو |
|---|---|
| `api_id`، `api_hash` | بيانات Telegram API الخاصة بك. **احتفظ بها لنفسك** |
| `chats` | معرّفات القنوات والمجموعات المراد مراقبتها |
| `target_chat` | معرّف المحادثة التي تُحوَّل إليها المنشورات المطابقة |
| `level_keywords_en/_ar` | كلمات تعني "للمبتدئين": intern، حديث التخرج… |
| `entry_level_role_en/_ar` | وظائف مبتدئة بطبيعتها: help desk، فني دعم… |
| `mid_level_general_role_en/_ar` | مسمياتك الوظيفية واختصاصاتك: electrical engineer، هندسة حاسبات… |
| `location_keywords_en/_ar` | الأماكن المقبولة: البصرة وأقضيتها، الحقول النفطية، "عن بعد"… |
| `certifications` | شهادات تستبعد المنشور إذا كانت *مطلوبة* |
| `recent_messages` | يستخدمها بوت بايثون لكشف التكرار؛ لا تعدّلها |

النسخة الجديدة تضيف مفاتيح اختيارية مثل `publish_when_unsure` و`max_experience_years` و`data_dir` و`dry_run` و`alerts`، وهي مذكورة في [دليل إعادة الكتابة](docs/rewrite-guide.md#5-changing-the-filter-without-breaking-it). كلماتي المفتاحية موجودة في [`example_keywords.txt`](example_keywords.txt).

## المزيد

- [الويكي](https://github.com/5wHN28Dg/tele-notify/wiki): الأسئلة الشائعة، أندرويد، الفلتر الجديد، مواقع الوظائف المدعومة
- [Issues](https://github.com/5wHN28Dg/tele-notify/issues): الخطط والمشاكل المعروفة

**تنويه:** هذه أداة شخصية صُنعت لبحث شخص واحد عن عمل. ليست مخصصة للاستخدام الكثيف أو لأي شيء يقترب من حدود تيليجرام. استخدم حسابك بمسؤولية.

## الترخيص

[AGPL-3.0](LICENSE)

</div>
