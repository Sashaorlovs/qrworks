import re
import unicodedata


def normalize_purchase_name(value):
    """Stable key for names that differ only by harmless punctuation/spacing."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold().replace("ё", "е")
    text = text.translate(str.maketrans({"×": "x", "х": "x", "–": "-", "—": "-", "−": "-"}))
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"(?<!\d)[.,;:]+|[.,;:]+(?!\d)", "", text)
    text = re.sub(r"\s*([-x/])\s*", r"\1", text)
    return " ".join(text.split())


def clean_purchase_name(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = text.translate(str.maketrans({"–": "-", "—": "-", "−": "-"}))
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"[.,;:]+$", "", text).strip()
    return " ".join(text.split())

