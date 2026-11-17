<div align="center">

[🇬🇧 English](README.md)

</div>

<div dir="rtl">

# tele-notify

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![Telethon](https://img.shields.io/badge/Library-Telethon-green)](https://docs.telethon.dev/)

[![asciicast](https://asciinema.org/a/h8qYjhK6LsRqMchP6L93N1qu5.svg)](https://asciinema.org/a/h8qYjhK6LsRqMchP6L93N1qu5)

سكربت مكتوب بلغة Python يقوم بمراقبة محادثات Telegram المحددة وتحويل الرسائل التي تطابق عوامل التصفية المخصصة الخاصة بك إلى محادثة أخرى.
يدعم أيضًا خاصية OCR للتعرف على النصوص في الصور المرفقة ويتجنب إعادة إرسال الرسائل المكررة.

**⚠️ تنويه**: هذا المشروع هو حل مخصص لمشكلة محددة واجهتها، تم تصميمه لتلبية احتياجاتي الشخصية فقط (اقرأ المقالة التي قمت بنشرها [هنا](https://medium.com/@TogataMirio/a-needle-in-a-haystack-and-the-deadly-pursuit-to-prove-your-worth-88c34f1df79f)).
لم يُؤخذ بعين الاعتبار استخدامه في المهام ذات الحجم الكبير أو الحالات التي قد تصل إلى حدود معدلات استخدام API.
لم يتم اضافة اي ميزات لا احتاجها او استعملها شخصياً، لذا قد تحتاج إلى تعديل الكود ليتناسب مع احتياجك.

## ✨ المزايا

* **تصفية بناءً على الكلمات المفتاحية**: يطابق على الأقل كلمة مفتاحية واحدة من كل فئة من الفئات الثلاث.
* **التعرف على النصوص في الصور (OCR)**: يقوم بتحميل الصور المرفقة واستخراج النصوص منها باستخدام مكتبة `pytesseract`.
* **منع التكرار**: يتحقق من آخر 10 رسائل تم تحويلها قبل إرسال رسالة جديدة.
* **عوامل تصفية قابلة للتخصيص**: خزّن الكلمات المفتاحية وبيانات اعتماد Telegram API في ملف JSON.
* **مراقبة اكثر من محادثة**: راقب عدة محادثات Telegram في وقت واحد.

## 🎯 دليل الإصدارات

في **7 أكتوبر 2025** وصل المشروع إلى نقطة تفرّع — لحظة اضطررت فيها للاختيار بين طريقين:
الـ**تخصيص** على حساب سهولة إعادة الاستخدام، أو الـ**تعميم** على حساب الموثوقية في حالتي الحالية.

وفي النهاية، قررت أن أحتفظ بكليهما. أردت نقاط القوة في كل نهج، لذا أصبح المشروع الآن يقدّم **نسختين متميزتين**، كل واحدة منهما مصمّمة لتلبية احتياجات مختلفة:
    
### **(الحالي)** - مُرشِّح وظائف متخصص

**الأفضل لـ:** تصفية إعلانات الوظائف مع اكتشاف ذكي لمستوى الخبرة

* تصفية على مرحلتين باستخدام منطق استنتاجي
* تصنيف بين مستوى مبتدئ ومتوسط
* مطابقة أنماط الخبرة، الشهادات، والمسؤوليات
* مُحسّنة لمصطلحات سوق العمل باللغتين العربية والإنجليزية
* **العيب:** متخصصة جدًا؛ تتطلب تعديلات كبيرة لاستخدامها في مجالات أخرى

### **(القديم)** - مُرشِّح عام للكلمات المفتاحية

**الأفضل لـ:** التصفية البسيطة بناءً على الكلمات المفتاحية لأي نوع من المحتوى

* منطق مباشر يعتمد على (المستوى + الدور + الموقع)
* سهلة التخصيص لمجالات مختلفة (مثل العقارات، الفعاليات، أو المنتجات)
* تتطلب إعدادًا بسيطًا للغاية
* **العيب:** أقل ذكاءً؛ قد تفوّت بعض التطابقات الدقيقة

📁 **الملفات:**

* ا`main.py` — النسخة المتخصصة الحالية (v2)
* `main_simple.py` — النسخة الأصلية العامة (v1)

💡 **أيّها يجب أن تستخدم؟**

* تريد تصفية إعلانات وظائف بالتحديد؟ → استخدم **v2**
* تحتاج مُرشّح كلمات مفتاحية بسيط لمحتوى آخر؟ → استخدم **v1**
* ترغب في بناء شيء مخصّص؟ → ابدأ من **v1** كنموذج أساسي

## 🔧 صعوبة التخصيص

| الميزة                    | v1 (بسيطة) | v2 (متخصصة) |
| ------------------------- | ---------- | ----------- |
| إضافة كلمات مفتاحية جديدة | سهل        | سهل         |
| تغيير منطق التصفية        | متوسط      | معقّد       |
| إعادة التوظيف لمجال مختلف | متوسط      | معقّد جدًا  |
| إضافة لغات جديدة          | متوسط      | صعب         |


</div>

<div dir="rtl">

## 📦 المتطلبات
* بايثون 3.8 او احدث
* بيانات اعتماد Telegram API (API ID و API Hash — راجع [وثائق Telethon](https://docs.telethon.dev/en/stable/basic/signing-in.html#signing-in) للحصول على التعليمات)
* تثبيت `tesseract-ocr` لدعم وظيفة OCR

## 📚 المكتبات المستخدمة
</div>

```txt
telethon
pillow
pytesseract
scikit-learn
cryptg
tenacity
Beautiful Soup
lxml
````

<div dir="rtl">

## ⚙️ التثبيت

1. استنسخ المستودع:

</div>

```bash
git clone https://github.com/5wHN28Dg/tele-notify.git
cd tele-notify
```

<div dir="rtl">

2. أنشئ بيئة افتراضية:

</div>

```bash
python3 -m venv venv
source venv/bin/activate
```

<div dir="rtl">

3. ثبّت المكتبات:

</div>

```bash
pip install -r requirements.txt
```

<div dir="rtl">

4. ثبّت Tesseract OCR:

* **Ubuntu/Debian**:

</div>

```bash
sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-ara
```

<div dir="rtl">

* **Windows**: [رابط التحميل](https://github.com/UB-Mannheim/tesseract/wiki)
* **MacOS**:

</div>

```bash
brew install tesseract tesseract-lang
```

<div dir="rtl">

* **ملاحظة**: إذا واجهت مشاكل في تثبيت Tesseract، راجع [الوثائق الرسمية](https://tesseract-ocr.github.io/tessdoc/Installation.html).

> 📱 **مستخدمي الاندرويد**: لكافة التفاصيل تحقق من [الويكي](https://github.com/5wHN28Dg/tele-notify/wiki/Android-Instructions)

## 🛠 الإعداد

1. شغّل الكود التالي (بعد إدخال API ID و API Hash) للحصول على قائمة محادثاتك مع أسمائها و**معرّفاتها**:

</div>

```python
from telethon import TelegramClient

api_id = YOUR_API_ID
api_hash = 'YOUR_API_HASH'

client = TelegramClient('session_name', api_id, api_hash)

async def main():
    async for dialog in client.iter_dialogs():
        print('{:>14}: {}'.format(dialog.id, dialog.title))

with client:
    client.loop.run_until_complete(main())
```

<div dir="rtl">

2. أفتح ملف `config.json` داخل مجلد المشروع وادخل المعلومات الاتية:

* الAPI ID و API Hash الخاصين بك.
* ال ID الخاص بالحادثة التي ترغب بتحويل الرسائل لها.
* ال IDs الخاص بالمحادثات التي ترغب بمراقبتها.
* الكلمات المفتاحية التي تريد فلترة الرسائل بناء عليها.

**ملاحظه**: اترك recent messages بحالها.

</div>

<div dir="rtl">

## 🚀 الاستخدام

لتشغيل البرنامج:

</div>

```bash
python main.py
```


<div dir="rtl">

سيقوم البرنامج بـ:

١. مطالبتك بتسجيل الدخول عن طريق إدخال رقم هاتفك والكود.

٢. البدء في مراقبة المحادثات المحددة.

٣. معالجة أي رسائل غير مقروءة ومراقبة الرسائل الجديدة:

   * استخراج النص من الرسالة والصورة (إن وجدت).
   * التحقق من الكلمات المفتاحية المطلوبة.
   * تجاهل الرسالة في حال مطابقتها لواحدة من اخر ١٠ رسائل مرسلة.
   * إعادة توجيهها إلى المحادثة الهدف في حال استوفائها للشروط اعلاه
* اذا كان لديك اي سؤال يمكنك اخذ نظرة على قسم [الاسئلة الشائعة](https://github.com/5wHN28Dg/tele-notify/wiki/FAQ)

</div>

<div dir="rtl">

## 🗺️ خارطة الطريق

يُنظّم التطوير حسب المراحل.

اطّلع على التقدم الكامل ← [مراحل GitHub](https://github.com/5wHN28Dg/tele-notify/milestones)

اطّلع على ما يجري العمل عليه ← [لوحة المشروع](https://github.com/users/5wHN28Dg/projects/1)
## 📜 الترخيص

هذا المشروع مرخّص تحت رخصة AGPL.

</div>
