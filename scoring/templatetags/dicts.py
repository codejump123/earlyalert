"""One filter, for reading a dict by a key the template holds in a variable."""

from django import template

register = template.Library()


@register.filter
def dictkey(mapping, key):
    """Input: a dict and a key. Output: the value, or empty."""
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""
