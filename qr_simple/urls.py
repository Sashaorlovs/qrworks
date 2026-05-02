
from django.contrib import admin
admin.site.site_header = 'Администрирование производства'
admin.site.site_title = 'QR Производство'
admin.site.index_title = 'Панель управления'
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),  # логин/логаут
    path('', include('scanner.urls')),                      # главная страница и всё остальное
]
