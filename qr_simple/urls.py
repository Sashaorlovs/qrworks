from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from scanner import views
from scanner.purchase_urls import urlpatterns as purchase_urls

urlpatterns = [
    path("materials/", include("scanner.material_urls")),
    path("purchases/", include(purchase_urls)),
    path('admin/', admin.site.urls),
    path('accounts/login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('accounts/logout/', views.logout_view, name='logout'),
    path('', include('scanner.urls')),
]
