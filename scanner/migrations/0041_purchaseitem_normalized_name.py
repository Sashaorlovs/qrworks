import re
import unicodedata

from django.db import migrations, models


def normalise(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold().replace("ё", "е")
    text = text.translate(str.maketrans({"×": "x", "х": "x", "–": "-", "—": "-", "−": "-"}))
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"(?<!\d)[.,;:]+|[.,;:]+(?!\d)", "", text)
    text = re.sub(r"\s*([-x/])\s*", r"\1", text)
    return " ".join(text.split())


def populate_normalized_names(apps, schema_editor):
    PurchaseItem = apps.get_model("scanner", "PurchaseItem")
    for item in PurchaseItem.objects.all().only("id", "item_name").iterator():
        PurchaseItem.objects.filter(pk=item.pk).update(normalized_name=normalise(item.item_name))


class Migration(migrations.Migration):
    dependencies = [("scanner", "0040_auxiliary_material_requirement")]

    operations = [
        migrations.AddField(
            model_name="purchaseitem",
            name="normalized_name",
            field=models.CharField(blank=True, db_index=True, max_length=500, verbose_name="Нормализованное наименование"),
        ),
        migrations.RunPython(populate_normalized_names, migrations.RunPython.noop),
    ]

