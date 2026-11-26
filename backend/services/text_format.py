"""
Question text for display.

Question banks are plain text with a little Markdown: ```fenced``` blocks for
code and `backticks` for inline code, and some imports carry a literal "\\n"
for a line break. Everything is HTML-escaped first, so an imported question can
never inject markup; only the code formatting is added back.
"""

import re

from markupsafe import Markup, escape

_FENCE = re.compile(r'```(?:[\w+-]*\n)?(.*?)```', re.S)
_INLINE = re.compile(r'`([^`\n]+)`')


def format_question(text) -> Markup:
    """Jinja filter ``qtext``: safe HTML for question and option text."""
    if not text:
        return Markup('')
    raw = str(text).replace('\r\n', '\n').replace('\\n', '\n').replace('\\t', '    ')
    out, last = [], 0
    for m in _FENCE.finditer(raw):
        out.append(_inline(raw[last:m.start()]))
        out.append(Markup('<pre class="nx-code"><code>{}</code></pre>').format(m.group(1).strip('\n')))
        last = m.end()
    out.append(_inline(raw[last:]))
    return Markup('').join(out)


def _inline(chunk: str) -> Markup:
    escaped = str(escape(chunk))
    return Markup(_INLINE.sub(r'<code>\1</code>', escaped))


def init_app(app) -> None:
    app.jinja_env.filters['qtext'] = format_question
