from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from rest_framework.response import Response
from rest_framework.decorators import api_view
from django.conf import settings
from django.conf.urls.static import static
from dashboard.views import dashboard_router

@api_view(['GET'])
def test_api(request):
    return Response({"message": "API working!"})

urlpatterns = [
    path('admin/', admin.site.urls),
    path('customer/', include('customer.urls')),

    # ✅ FIXED LOGIN ROUTE (matches LOGIN_URL = '/login/')
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('payments/', include('payments.urls')),

    # ✅ API
    path('api/test/', test_api),
    path('api/', include('orders.urls')),
    path('api/notifications/', include('notifications.urls')),

    # ✅ DASHBOARD ROUTER (ONLY ONE ROOT ENTRY)
    path('', dashboard_router, name='dashboard_router'),

    # ✅ OTHER DASHBOARD ROUTES (waiter/, kitchen/, etc.)
    path('dashboard/', include('dashboard.urls')),
    path('', include('orders.urls')),
    path('', include('dashboard.urls')),
]