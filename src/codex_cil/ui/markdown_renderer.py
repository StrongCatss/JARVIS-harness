from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import markdown
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.style import Style
from pygments.token import (
    Comment,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
    Whitespace,
)

from codex_cil.ui.theme_system import ThemeSpec


_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "del",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}

_VOID_TAGS = {"br", "hr"}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "code": {"class"},
    "div": {"class", "style"},
    "pre": {"class", "style"},
    "span": {"class", "style"},
    "table": {"cellpadding", "cellspacing", "style", "width"},
    "td": {"style"},
}
_SAFE_LINK_SCHEMES = {"", "http", "https", "mailto"}
_FORBIDDEN_STYLE_VALUE = re.compile(
    r"(?:url\s*\(|expression\s*\(|javascript\s*:|behavior\s*:)",
    re.IGNORECASE,
)
_FENCED_CODE_PATTERN = re.compile(
    r"(?ms)^[ \t]*```([^\n`]*)\n(.*?)^[ \t]*```[ \t]*$"
)


class _SafeHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized = tag.lower()
        if normalized not in _ALLOWED_TAGS:
            return
        rendered_attrs = self._render_attributes(normalized, attrs)
        self.parts.append(f"<{normalized}{rendered_attrs}>")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized = tag.lower()
        if normalized not in _ALLOWED_TAGS:
            return
        rendered_attrs = self._render_attributes(normalized, attrs)
        self.parts.append(f"<{normalized}{rendered_attrs}>")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in _ALLOWED_TAGS and normalized not in _VOID_TAGS:
            self.parts.append(f"</{normalized}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        return

    def _render_attributes(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> str:
        allowed = _ALLOWED_ATTRIBUTES.get(tag, set())
        rendered: list[str] = []
        for name, value in attrs:
            normalized_name = name.lower()
            if normalized_name not in allowed or value is None:
                continue
            if normalized_name == "href" and not _is_safe_link(value):
                continue
            if normalized_name == "style":
                value = _sanitize_style(value)
                if not value:
                    continue
            rendered.append(
                f' {normalized_name}="{html.escape(value, quote=True)}"'
            )
        return "".join(rendered)


def render_markdown(
    content: str,
    theme: ThemeSpec,
    *,
    copy_prefix: str | None = None,
) -> str:
    """将智能体 Markdown 转成 QTextBrowser 可安全显示的 HTML。"""

    code_index = 0

    def replace_code(match: re.Match[str]) -> str:
        nonlocal code_index
        href = (
            f"{copy_prefix}{code_index}"
            if copy_prefix is not None
            else None
        )
        code_index += 1
        return _render_code_block(
            match.group(2).rstrip("\n"),
            match.group(1).strip(),
            theme,
            copy_href=href,
        )

    content = _FENCED_CODE_PATTERN.sub(replace_code, content)
    rendered = markdown.markdown(
        content,
        extensions=[
            "tables",
            "sane_lists",
            "nl2br",
        ],
        output_format="html5",
    )
    return sanitize_html(rendered)


def extract_code_blocks(content: str) -> list[str]:
    return [
        match.group(2).rstrip("\n")
        for match in _FENCED_CODE_PATTERN.finditer(content)
    ]


def sanitize_html(content: str) -> str:
    parser = _SafeHtmlParser()
    parser.feed(content)
    parser.close()
    return "".join(parser.parts)


def _is_safe_link(value: str) -> bool:
    candidate = value.strip()
    if candidate.startswith("#"):
        return True
    if candidate.startswith("jarvis-"):
        return True
    parsed = urlparse(candidate)
    return parsed.scheme.lower() in _SAFE_LINK_SCHEMES


def _sanitize_style(value: str) -> str:
    if _FORBIDDEN_STYLE_VALUE.search(value):
        return ""
    declarations: list[str] = []
    for declaration in value.split(";"):
        if ":" not in declaration:
            continue
        property_name, property_value = declaration.split(":", 1)
        property_name = property_name.strip().lower()
        property_value = property_value.strip()
        if not property_name or not property_value:
            continue
        declarations.append(f"{property_name}: {property_value}")
    return "; ".join(declarations)


def _render_code_block(
    code: str,
    language: str,
    theme: ThemeSpec,
    *,
    copy_href: str | None = None,
) -> str:
    lexer = _find_lexer(language, code)
    highlighted = highlight(
        code,
        lexer,
        HtmlFormatter(
            nowrap=True,
            noclasses=True,
            style=_code_style(theme),
        ),
    ).rstrip("\n")
    background = _code_background(theme)
    border = theme.border_strong
    copy_link = (
        f'<a href="{copy_href}" style="color:{theme.accent};'
        f'text-decoration:none;font-size:10px;">复制代码</a>'
        if copy_href
        else ""
    )
    return (
        f'<table width="100%" cellspacing="0" cellpadding="0" '
        f'style="margin:8px 0;background:{background};border:1px solid {border};">'
        f'<tr><td style="padding:7px 11px;background:{background};">'
        f'<div style="text-align:right;margin-bottom:4px;">{copy_link}</div>'
        f'<pre style="margin:0;line-height:135%;color:{theme.text};'
        f'font-family:\'Cascadia Code\',\'Consolas\';white-space:pre-wrap;">'
        f"{highlighted}</pre></td></tr></table>"
    )


def _find_lexer(language: str, code: str):  # type: ignore[no-untyped-def]
    aliases = {
        "c#": "csharp",
        "c++": "cpp",
        "pwsh": "powershell",
        "shell": "powershell" if language.lower() in {"ps", "ps1"} else "bash",
        "ts": "typescript",
        "yml": "yaml",
    }
    normalized = aliases.get(language.lower(), language.lower())
    if not normalized:
        return TextLexer()
    try:
        return get_lexer_by_name(normalized, stripnl=False)
    except Exception:
        return TextLexer()


def _code_style(theme: ThemeSpec) -> type[Style]:
    if theme.is_light:
        function_color = "#005a9c"
        builtin_color = "#7b2cbf"
        number_color = "#b45309"
        operator_color = "#3f4f5f"
        comment_color = "#526b63"
    else:
        function_color = "#74d7ff"
        builtin_color = "#d5a6ff"
        number_color = "#ffb86b"
        operator_color = theme.text
        comment_color = "#8fb7a0" if theme.identifier == "hacker" else theme.muted

    return type(
        "JarvisCodeStyle",
        (Style,),
        {
            "background_color": "transparent",
            "default_style": theme.text,
            "styles": {
                Text: theme.text,
                Whitespace: theme.text,
                Comment: f"italic {comment_color}",
                Keyword: f"bold {theme.accent}",
                Keyword.Type: f"bold {builtin_color}",
                Name: theme.text,
                Name.Builtin: f"bold {builtin_color}",
                Name.Class: f"bold {theme.accent}",
                Name.Function: f"bold {function_color}",
                Name.Tag: f"bold {theme.accent}",
                String: theme.warning,
                String.Doc: f"italic {comment_color}",
                Number: number_color,
                Operator: operator_color,
                Operator.Word: f"bold {theme.accent}",
                Punctuation: theme.muted,
                Generic.Deleted: theme.danger,
                Generic.Inserted: theme.accent,
                Generic.Heading: f"bold {theme.accent}",
                Generic.Subheading: f"bold {theme.accent}",
            },
        },
    )


def _code_background(theme: ThemeSpec) -> str:
    if theme.is_light:
        return "#f3f7fb"
    if theme.identifier == "hacker":
        return "#0b1510"
    if theme.identifier == "black_empire":
        return "#050805"
    return theme.input_bg
