from django.db import migrations, models


PROFILE_CHOICES = [
    ("sheet", "Лист"),
    ("round_pipe", "Труба круглая"),
    ("rect_tube", "Труба профильная"),
    ("round_bar", "Круг"),
    ("square_bar", "Квадрат"),
    ("hex_bar", "Шестигранник"),
    ("rect_bar", "Полоса / прямоугольник"),
    ("angle", "Уголок"),
    ("channel", "Швеллер"),
    ("beam", "Двутавр / балка"),
    ("bulb_flat", "Полособульб"),
    ("other", "Другой профиль"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("scanner", "0038_auxiliary_material_warehouse"),
    ]

    operations = [
        migrations.AlterField(
            model_name="materialrequirement",
            name="profile_type",
            field=models.CharField(choices=PROFILE_CHOICES, max_length=20, verbose_name="Профиль"),
        ),
        migrations.AlterField(
            model_name="materialstocklot",
            name="profile_type",
            field=models.CharField(choices=PROFILE_CHOICES, max_length=20, verbose_name="Профиль"),
        ),
    ]
