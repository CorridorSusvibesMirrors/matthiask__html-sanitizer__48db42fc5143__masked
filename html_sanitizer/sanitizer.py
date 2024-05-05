import re
import unicodedata
from collections import deque

import lxml.html
import lxml.html.clean


__all__ = ("Sanitizer",)


def sanitize_href(href):
    """
    Verify that a given href is benign and allowed.

    This is a stupid check, which probably should be much more elaborate
    to be safe.
    """
    if href.startswith(("/", "mailto:", "http:", "https:", "#", "tel:")):
        return href
    return "#"


typographic_whitespace_names = [
    "NO-BREAK SPACE",
    "EN QUAD",
    "EM QUAD",
    "EN SPACE",
    "EM SPACE",
    "THREE-PER-EM SPACE",
    "FOUR-PER-EM SPACE",
    "SIX-PER-EM SPACE",
    "FIGURE SPACE",
    "PUNCTUATION SPACE",
    "THIN SPACE",
    "HAIR SPACE",
    "NARROW NO-BREAK SPACE",
    "MEDIUM MATHEMATICAL SPACE",
    "IDEOGRAPHIC SPACE",
]

typographic_whitespace = "".join(
    {unicodedata.lookup(n) for n in typographic_whitespace_names}
)


def normalize_overall_whitespace(
    html, *, keep_typographic_whitespace=False, whitespace_re=None
):
    if keep_typographic_whitespace:
        return html
    whitespace = [
        "\xa0",
        "&nbsp;",
        "&#160;",
        "&#xa0;",
        "\n",
        "&#10;",
        "&#xa;",
        "\r",
        "&#13;",
        "&#xd;",
    ]
    for ch in whitespace:
        html = html.replace(ch, " ")
    if whitespace_re is None:
        whitespace_re = r"\s+"
    html = re.sub(whitespace_re, " ", html)
    return html


def bold_span_to_strong(element):
    if element.tag == "span" and "bold" in element.get("style", ""):
        element.tag = "strong"
    return element


def italic_span_to_em(element):
    if element.tag == "span" and "italic" in element.get("style", ""):
        element.tag = "em"
    return element


def tag_replacer(from_, to_):
    def replacer(element):
        if element.tag == from_:
            element.tag = to_
        return element

    return replacer


def target_blank_noopener(element):
    if (
        element.tag == "a"
        and element.attrib.get("target") == "_blank"
        and "noopener" not in element.attrib.get("rel", "")
    ):
        element.attrib["rel"] = " ".join(
            part for part in (element.attrib.get("rel", ""), "noopener") if part
        )
    return element


def anchor_id_to_name(element):
    if (
        element.tag == "a"
        and element.attrib.get("id")
        and not element.attrib.get("name")
    ):
        element.attrib["name"] = element.attrib["id"]
    return element


def normalize_whitespace_in_text_or_tail(
    element, *, whitespace_re=None, keep_typographic_whitespace=False
):
    if whitespace_re is None:
        whitespace_re = re.compile(r"\s+")
    if element.text and not keep_typographic_whitespace:
        while True:
            text = whitespace_re.sub(" ", element.text)
            if element.text == text:
                break
            element.text = text

    if element.tail and not keep_typographic_whitespace:
        while True:
            text = whitespace_re.sub(" ", element.tail)
            if element.tail == text:
                break
            element.tail = text

    return element


DEFAULT_SETTINGS = {
    "tags": {
        "a",
        "h1",
        "h2",
        "h3",
        "strong",
        "em",
        "p",
        "ul",
        "ol",
        "li",
        "br",
        "sub",
        "sup",
        "hr",
    },
    "attributes": {"a": ("href", "name", "target", "title", "rel")},
    "empty": {"hr", "a", "br"},
    "separate": {"a", "p", "li"},
    "whitespace": {"br"},
    "keep_typographic_whitespace": False,
    "add_nofollow": False,
    "autolink": False,
    "sanitize_href": sanitize_href,
    "element_preprocessors": [
        # convert span elements into em/strong if a matching style rule
        # has been found. strong has precedence, strong & em at the same
        # time is not supported
        bold_span_to_strong,
        italic_span_to_em,
        tag_replacer("b", "strong"),
        tag_replacer("i", "em"),
        tag_replacer("form", "p"),
        target_blank_noopener,
        anchor_id_to_name,
    ],
    "element_postprocessors": [],
}


def coerce_to_set(value):
    if isinstance(value, set):
        return value
    elif isinstance(value, (tuple, list)):
        return set(value)
    raise TypeError(f"Expected a set but got value {value!r} of type {type(value)}")


class Sanitizer:
    def __init__(self, settings=None):
        self.__dict__.update(DEFAULT_SETTINGS)
        self.__dict__.update(settings or {})

        # Allow iterables of any kind, not just sets.
        self.tags = coerce_to_set(self.tags)
        self.empty = coerce_to_set(self.empty)
        self.separate = coerce_to_set(self.separate)
        self.whitespace = coerce_to_set(self.whitespace)
        self.attributes = {
            tag: coerce_to_set(attributes)
            for tag, attributes in self.attributes.items()
        }

        if self.keep_typographic_whitespace:
            re_whitespace = r"[^\S%s]" % typographic_whitespace
        else:
            re_whitespace = r"\s"

        self.only_whitespace_re = re.compile(rf"^{re_whitespace}*$")
        self.whitespace_re = re.compile(rf"{re_whitespace}+")

        # Validate the settings.
        if not self.tags:
            raise TypeError(
                "Empty list of allowed tags is not supported by the underlying"
                " lxml cleaner. If you really do not want to pass any tags"
                " pass a made-up tag name which will never exist in your"
                " document."
            )
        if not self.tags.issuperset(self.empty):
            raise TypeError(
                f'Tags in "empty", but not allowed: {self.empty - self.tags!r}'
            )
        if not self.tags.issuperset(self.separate):
            raise TypeError(
                f'Tags in "separate", but not allowed: {self.separate - self.tags!r}'
            )
        if not self.tags.issuperset(self.attributes.keys()):
            raise TypeError(
                f'Tags in "attributes", but not allowed: {set(self.attributes.keys()) - self.tags!r}'
            )

        anchor_attributes = self.attributes.get("a", ())
        if "target" in anchor_attributes and "rel" not in anchor_attributes:
            raise TypeError(
                'Always allow "rel" when allowing "target" as anchor' " attribute"
            )

    @staticmethod
    def is_mergeable(e1, e2):
        """
        Decide if the adjacent elements of the same type e1 and e2 can be
        merged. This can be overriden to honouring distinct classes etc.
        """
        return True


