# Translating Seatplan

The source of truth is [`seatplan/locales/messages.csv`](../seatplan/locales/messages.csv). It contains the English interface and message text, plus empty `ro`, `de`, and `hu` columns for Romanian, German, and Hungarian. The `context` column says where a phrase appears. The application needs no machine-translation service or network connection at runtime.

## Fill a language

1. Edit one language column in a spreadsheet or CSV editor that preserves UTF-8 and quoted CSV cells. Leave `source`, `context`, and `en` unchanged.
2. Fill **every** row of that column. A blank cell keeps the language out of the public selector, so visitors never see a partly translated interface.
3. Keep numbered placeholders such as `{0}` and `{1}` in the translation. They stand for counts, seat names, or references; they may be reordered to fit the language. Do not put HTML in translated cells.
4. Run `python -m pytest -q tests/test_i18n.py` and reload the website. A newly complete language appears in the selector. The choice is remembered for this browser and, after sign-in, for reservation emails.

To add another language, add a column headed by its language code, such as `fr` or `pt-BR`, and fill every row. The selector uses the browser's native name for the code. Browser language is used on a first visit when a complete matching column exists; visitors can then switch languages. A changed CSV is read on the next request, and existing pages need a reload to pick up new columns or wording.

The CSV covers the website, booking and administrator actions, accessible labels, sign-in and reservation emails, API errors, CSV export headings, and printable plans. Organizer-entered event titles, dates, descriptions, seat names, and private notes remain exactly as entered. Audit action codes and technical identifiers also remain unchanged.

Translations are plain text. When the application renders HTML, it inserts translated text safely rather than interpreting it as markup. The server checks CSV headers, duplicate English sources, and placeholder preservation. If an edit is invalid, fix the CSV before deploying it.

## Updating English text

When changing a visible English phrase in the code, update its `source` and `en` entry in the CSV, revise `context`, and translate the new row before enabling other languages. Keep the old row only when another screen still uses it. Numbered placeholders in a source must agree with the corresponding translated cells.
